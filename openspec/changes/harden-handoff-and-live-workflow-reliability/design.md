## Context

参见 `proposal.md` 的动机。当前项目已经具备前端阶段、`handoff.json`、revision/SHA 身份、`wait-handoff`、processing claim、managed collection Worker 和 checkpoint，但“等待用户提交”仍依赖当前 Codex 回合是否存活；页面不能分辨 Agent 正在短期监听还是已经结束。真实会话 `20260729_171047` 又证明，浏览器浮层、Windows 启动器 PID、共享 checkpoint、状态恢复、环境选择和底层持久化会在同一条 live path 上串联放大。

本设计复用现有 session/claim/attempt/collector，不引入 thread 唤醒 API、常驻文件监听器、外部队列或第二套生产路径。工作树中已经验证的局部修复视为候选实现，必须经过本变更的规格、统一抽象和回归验收后才能闭合。

## Goals / Non-Goals

**Goals:**

- 让用户在常见人工操作时间内提交页面后，Codex 当前回合可以自动继续。
- Codex 中断时不丢失提交，用户在同一聊天发送“已提交”即可幂等恢复。
- 页面准确区分等待、待恢复、已认领、处理中和完成。
- 用单一恢复解析器和 attempt 隔离闭合本轮十类测试问题。
- 将 Windows、编码、原子写入、环境选择和真实 DOM 兼容纳入 release evidence。
- 保持精确批准、店铺、登录、人机验证和生产发布门禁不变。

**Non-Goals:**

- 不从页面主动唤醒已结束的 Codex 任务。
- 不绑定或持久化 `thread_id`，不运行常驻后台 handoff watcher。
- 不让“已提交”携带业务值、批准或发布授权。
- 不重写阶段业务算法，不增加第二个采集器。
- 不自动处理登录、短信、二维码、验证码、VPN、NAS 权限或生产风控。

## Decisions

### 1. 使用短期等待租约而不是 thread 唤醒

在 session 状态中增加可选 `agent_wait` 信封：

```json
{
  "schema_version": 1,
  "wait_id": "uuid",
  "claimant_id": "codex-agent",
  "session_id": "...",
  "stage_id": "...",
  "expected_revision": 3,
  "started_at": "...",
  "heartbeat_at": "...",
  "expires_at": "..."
}
```

`agent_wait` 只用于页面反馈，不参与业务授权或 processing 排他。Agent 打开人工阶段后以 30 秒为一个等待片段，至少每 45 秒刷新一次等待心跳，租约为最后心跳后 90 秒。总等待窗口按阶段类型固定：

- 简单配置/确认：5 分钟；
- 商品和文件夹选择：10 分钟；
- 图片、坑位、裁剪和文案：15 分钟；
- 批准和生产确认：10 分钟，但超时后永不自动授权。

片段超时不是错误；总窗口结束时 Agent 清晰告知恢复方式并结束当前回合。Agent 异常退出时租约自然过期，无需后台清理。

备选方案是后台 watcher 按 thread ID 唤醒 Codex。它要求新的授权、投递、线程生命周期和 exactly-once 模型，超出当前需求，因此不采用。

### 2. “已提交”进入确定性恢复路由

当前聊天必须先持有唯一的精确 session 绑定。收到“已提交”时不直接调用阶段命令，而是解析权威状态：

1. 加载 `session.json` 和 `current_stage`；
2. 读取当前 handoff、input、result、processing claim 和 Worker 状态；
3. 重算 input SHA；
4. 按 `completed → live processing → recoverable processing → ready handoff → needs-user-input/blocked → draft` 顺序解析；
5. 仅在 `ready_for_agent` 且身份完全匹配时原子认领。

没有唯一 session 绑定时必须要求页面恢复指令；禁止选择 `runs` 最新目录。重复“已提交”返回同一权威状态，不能产生新 attempt 或重复副作用。

### 3. 等待、认领和 Worker 三层归属互不替代

状态分为：

- `agent_wait`：短期 UI 提示，可过期、可被新的 wait 覆盖；
- `processing_claim`：阶段级唯一执行权，带 revision/input hash/lease；
- private Worker manifest：进程级归属，带 attempt、ownership token 和进程身份。

handoff 的发现和 claim 创建在 session 锁内完成。获得 claim 后清除匹配的 `agent_wait`。页面只依据权威 resolver 输出渲染，不直接拼接三个来源的 badge。

### 4. attempt 目录是采集写入边界

每个 attempt 使用：

```text
collected/promotion/attempts/a-<attempt-prefix>/
  worker.private.json
  worker.json
  worker.log
  promotion-material-status.csv
  promotion-material-status.checkpoint.json
  selector-error.json
  result.json
```

Worker 只写自己的 attempt 目录。成功终态在校验 checkpoint、CSV SHA、唯一商品 ID、revision、input/selector/store 身份后，原子发布或复制到 `collected/promotion/current/`，兼容读取器可由 current 再投影到旧正式文件名。失败 attempt 保持不可变历史。

同一 attempt 的可恢复 Worker 复用其 checkpoint；新的 attempt 永远不直接读取其他 attempt 的 checkpoint。迁移期发现旧共享 checkpoint 时，先验证除 attempt ID 外的身份，再归档到旧 attempt；任何其他身份不一致仍 fail closed。

### 5. 浏览器动作采用“识别—执行—验证”状态机

安全弹窗处理不再用固定 30 轮盲点循环：

1. 枚举当前可见白名单弹窗并计算稳定身份；
2. 执行普通点击；
3. 验证弹窗消失、步骤变化或目标变为可操作；
4. 连续两次无状态变化即停止该控制；
5. 关键只读目标普通点击被已识别 overlay 拦截时，先执行一次 DOM click；
6. DOM click 后必须验证筛选状态、tab、页码或列表首行身份；
7. 仍无变化则保存证据并终止。

不再把“click 没抛错”等同于成功。写操作、批准、发布按钮永不使用 DOM/force 回退。

### 6. 唯一项目入口负责环境选择

受管启动器和日常 CLI 入口从项目配置解析 canonical module root 和 runtime environment，优先使用 `upload-search-materials/.venv`，不依据当前工作目录猜测解释器。启动前只做离线预检：

- Python/Node 版本；
- 必需依赖可导入；
- environment fingerprint 与锁一致；
- 项目本地 cache 可写；
- 本机配置、CDP 和 selector 路径可解析。

不满足时在认领 handoff 前返回稳定原因码。依赖同步继续是显式 bootstrap 动作，恢复路径不得联网。

### 7. 统一原子持久化与编码

建立一个公共 atomic writer/reader，替代 session、setup、worker、checkpoint 和 service 中重复实现：

- 写入前创建父目录；
- 同目录唯一临时文件；
- 写入、flush、fsync；
- Windows `os.replace` 的有限瞬时重试；
- 失败后只清理自身临时文件；
- JSON 标准输出 UTF-8，无 BOM；
- JSON 输入兼容 UTF-8 和 UTF-8-SIG；
- CSV 保持 Excel 兼容的 UTF-8-SIG；
- 哈希在明确的 canonical bytes 上计算，编码兼容不改变身份边界。

pytest 统一使用项目可写、路径较短的 basetemp；清理权限失败需作为测试基础设施警告单独报告，不能掩盖测试结论。

### 8. 进度使用结构化动作而不是粗粒度 phase

保留现有 phase，并增加：

```json
{
  "action": "close_safe_popup",
  "target": "ImageTestGuide",
  "attempt": 2,
  "elapsed_ms": 1430,
  "current_page": 8,
  "last_completed_page": 7,
  "row_count": 70,
  "heartbeat_at": "...",
  "checkpoint_at": "...",
  "next_recovery": "..."
}
```

页面按“正在做什么、已完成什么、最后心跳、何时可恢复”展示。日志和 evidence 使用同一原因码词表，证据只保存必要 DOM 摘要或截图引用。

### 9. 商品表质量在阶段一形成独立摘要

启动昂贵的 CDP 采集前生成 input-quality summary：总行、有效行、重复 ID、缺失 ID、非法 ID、其他行级排除和批次级 schema 错误。行级异常不阻断有效商品，批次级空表/缺表头/不可读才阻断。完整性阶段复用同一摘要，不再次产生不同统计口径。

### 10. 以已有修复为迁移种子而不是旁路补丁

当前工作树中的 BOM 兼容、loopback proxy 绕过、Windows ctypes 签名、launcher PID 安全认领、父目录创建、checkpoint 归档、blocked 恢复和 DOM click 回退先被拆分映射到上述公共契约。实现阶段不得简单提交局部补丁后宣称完成；必须：

- 去重为公共抽象；
- 保留已有回归测试；
- 新增跨模块和真实会话验收；
- 与三个相关 OpenSpec change 的剩余任务建立明确 supersede/complete 关系。

## Risks / Trade-offs

- [有界等待占用 Codex 回合和工具调用] → 使用 30 秒片段、阶段级总窗口和每分钟反馈；超时安全回到“已提交”恢复。
- [页面把过期等待误认为在线] → `agent_wait` 只按服务端时钟和 90 秒租约显示，不能由浏览器本地时间延长。
- [多个 Codex 同时等待] → wait 租约仅提示，真正排他仍由 processing claim 保证；页面显示已认领者而非等待者数量。
- [attempt 目录迁移破坏旧路径消费者] → 先增加 current 投影和兼容读取，再切换 Worker 写入边界，旧共享文件只读保留。
- [DOM click 被滥用] → 仅允许显式只读目标和已识别 overlay，执行后强制验证业务状态；任何写目标 fail closed。
- [公共 atomic writer 改变历史哈希] → 对每类文件明确 canonical bytes，并为历史 BOM/无 BOM 文件提供兼容测试。
- [真实验收依赖当前天猫 DOM] → 自动化规格测试与 live evidence 分层；live 失败保留精确原因，不通过临时第二采集器绕过。
- [新变更与进行中 change 重叠] → 任务中要求建立映射表并只在一个位置保留未完成工作，archive 前明确 supersede 关系。

## Migration Plan

1. 建立本变更与三个相关 change、当前工作树修复和十类问题的追踪矩阵。
2. 增加 `agent_wait` 兼容字段、状态 API 和页面提示；旧 session 缺失该字段时视为未监听。
3. 将 Agent 行为改为阶段级有界分段等待，并实现当前聊天“已提交”恢复解析器。
4. 抽取公共原子持久化、编码和项目运行环境入口，迁移现有调用并保持兼容读。
5. 将采集输出切换到 attempt 目录，增加 current 发布和旧 checkpoint 归档迁移。
6. 收敛 Worker 身份和统一状态 resolver，纳入 Windows launcher/PID 与 blocked 恢复测试。
7. 重构弹窗/点击验证和结构化进度，保留唯一 maintained collector。
8. 在阶段一增加数据质量摘要和批次/行级错误展示。
9. 运行目标测试、全量测试、Windows 隔离测试和 session `20260729_171047` 重放。
10. 创建全新 dry-run 会话完成 live acceptance；确认没有批准、上传或发布。
11. 更新 Skill、操作指南和相关 OpenSpec 任务，明确已完成、迁移或被本变更 supersede 的条目。

回滚时可关闭有界等待展示并恢复旧正式路径读取；attempt 历史、handoff、result 和 checkpoint 不删除。公共持久化格式保持向后兼容，因此不需要回写历史任务。
