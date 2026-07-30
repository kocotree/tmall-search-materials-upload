## Context

参见 `proposal.md`。真实会话 `20260730_004051` 的首次草稿写入依次成功生成 `input.json` 与 `revisions/0001/input.json`，随后替换 `session.json` 时出现 Windows `WinError 5`。重试看到已存在快照便返回 409，造成持久化事实与会话状态永久分叉。该任务还在每个等待片段重新创建 90 秒 `agent_wait`，而不是续租并清理同一 lease。

现有页面服务由 Codex 沙箱进程直接派生。当前机器实测同一 Y:/Z: 路径在沙箱身份下不可见，在桌面 Administrator 上下文中全部可访问；因此仅改提示语、只将 picker 移出 Flask 线程或只使用 UNC 都不能保证后续索引与素材读取成功。

## Goals / Non-Goals

**Goals:**

- 让 revision 写入在任意单点失败后可确定重试并自动收敛。
- 让页面监听状态对应一个真实 waiter，短租约、单一身份、正常退出即清理。
- 通过一次受限授权让完整文件访问链路继承可访问映射盘的桌面用户身份。
- 让原生选择窗口可见、可激活、可取消、可超时，并始终保留手工输入。
- 在不同电脑上保留稳定素材源语义，同时允许盘符或 UNC 各自配置。

**Non-Goals:**

- 不实现分布式事务、数据库或外部队列。
- 不自动创建盘符映射，不管理 NAS 账号密码，不修改 Windows 注册表或 UAC 策略。
- 不让桌面启动授权覆盖任意 shell 命令。
- 不改变批准、上传、生产确认和登录边界。

## Decisions

### 1. 使用可恢复事务信封与幂等请求 ID

前端每次显式/自动保存或提交生成稳定请求 ID；排队的“保存后提交”分别拥有两个请求 ID。session 阶段目录增加小型事务信封，绑定：

- session、stage、base/target revision；
- operation（draft/submit）；
- request ID 与规范化输入 SHA；
- `prepared`、`content_written`、`snapshot_written`、`state_committed` 进度；
- 创建与更新时间。

操作在 session 锁内重放。已有快照与请求内容完全一致时视为已完成步骤；不一致才是冲突。读取会话或下一次写入时可完成匹配事务。终态写入审计事件后移除或归档信封。

仅调整写入顺序不能解决问题：无论先写 session 还是先写快照，进程都可能在两者之间退出。数据库对于当前本地文件协议过重，因此选择可重放事务信封。

### 2. Windows access-denied 只在证明可写时作为瞬时占用重试

共享 atomic writer 保留 sharing violation 重试，并对 `WinError 5` 增加受限分类：

1. 目标存在、不是只读；
2. 父目录允许创建并清理自有探针；
3. 当前事务仍持有目标锁；
4. 在短退避窗口内最多重试固定次数。

任一证明失败即返回 `PERSISTENCE_ACCESS_DENIED`。这样兼容 Windows 读取句柄造成的短暂 replace 拒绝，同时避免对真实 ACL 错误长时间重试。

### 3. 一个 wait ID 覆盖完整等待回合

`wait-handoff` 首次创建 30 秒 lease，此后每个 10–15 秒片段携带原 `wait_id` 续租。`started_at` 不变，服务端另存 `budget_expires_at`；页面分别显示“Agent 在线”和“最长等待剩余”。

默认 setup 总预算为两分钟。CLI 使用 `finally` 在正常超时、取消和错误时清理自己拥有的 lease；进程异常退出仍由 30 秒 expiry 兜底。成功 processing claim 在同一锁内清理 wait。

### 4. 用固定的桌面用户启动入口承载完整文件访问链路

Codex 不再先在沙箱中启动 Flask 再临时弹 picker。固定 launcher 在用户许可后，以桌面用户上下文启动 prepared project Python，随后 UI、路径探测、picker、索引和 Worker 都作为其受管子进程继承身份。

启动命令只接受配置文件、runs root、session 和端口范围等结构化参数；路径规范化后必须落在允许范围。服务仅绑定 loopback，继续使用 ownership token 与 PID/creation identity。状态文件增加 SID、Windows session ID、interactive-desktop 状态和映射盘可见性摘要。

“单独的文件访问 broker”会引入第二套进程协议与更大的安全面；“所有路径改 UNC”不能复用只存在于桌面用户凭据会话中的 SMB 授权。因此选择统一启动身份。

### 5. picker helper 证明可见性而不只证明进程启动

picker 使用一个桌面 helper，满足：

- 单实例锁；
- 显式用户点击对应的 request ID；
- STA 原生目录窗口；
- 父窗口/前台激活；
- `started → window_visible → selected/cancelled` 心跳；
- 可见性与总选择两个独立超时；
- 仅终止匹配 token/PID 的 helper；
- 失败不清空输入，始终提供手工粘贴。

如果已有 picker 活跃，优先激活已有窗口；无法激活则返回 busy。后台启动成功但没有 `window_visible` 证据不能报告“窗口已打开”。

### 6. 稳定素材源名称与每机路径分离

业务阶段只携带素材源稳定 ID/名称；`config/local-paths.json` 保持每机私有，记录当前路径、可选 canonical UNC、最后验证身份和时间。换电脑后重新绑定，不修改版本库。

UNC 是推荐备用地址而非强制迁移。运行前用当前桌面身份做只读元数据验证；不得把凭据、目录清单或图片内容写入诊断。

### 7. 真实故障成为回归与迁移入口

建立脱敏 fixture 表达：

```text
session revision 0
current input revision 1
revision snapshot 1
no handoff
active advisory wait
```

回放必须自动修复为一致 draft 或继续同一 submit，不能删除 revision 目录。现有损坏真实会话只用于取证，不原地修改；修复验证使用复制 fixture 和全新会话。

## Risks / Trade-offs

- [桌面用户启动扩大本地文件可见范围] → 固定入口、结构化参数、loopback、随机 token、路径白名单和无任意命令执行。
- [事务信封本身写入失败] → 信封使用同一原子 writer；没有 durable prepared 信封时保持旧权威状态，下次请求可重新开始。
- [内容相同但 request ID 不同] → 相同 target revision 与规范化 SHA 可作为恢复候选，但必须记录合并审计；内容不同始终 fail closed。
- [30 秒 lease 在系统繁忙时误过期] → 10–15 秒心跳并允许有限时钟余量；wait 仍不授予业务执行权。
- [原生 picker 被其他窗口遮挡] → 显式前台激活与 `window_visible` 证明，失败后快速回退手工输入。
- [其他电脑没有相同 NAS 权限] → 每机重新绑定和预检；不承诺绕过 Windows/NAS 授权。
- [与两个进行中变更重复] → 在任务中建立 supersede 表，只保留本变更负责新的真实回归，原变更的历史测试与证据继续复用。

## Migration Plan

1. 固化真实 session 矛盾状态、wait 事件和桌面/沙箱路径差异为脱敏回归证据。
2. 增加请求 ID、事务信封与读取时修复，保持现有 session/input/handoff 格式兼容。
3. 扩展 atomic writer 的 Windows 瞬时占用分类与测试。
4. 将 wait CLI 改为单 ID 续租、30 秒 expiry、两分钟 setup 预算和 `finally` 清理。
5. 增加固定桌面用户 launcher 与身份状态；先作为映射盘配置的受控路径启用。
6. 让 picker、探测、索引和 Worker 全部由同一服务派生，增加可见性协议。
7. 将素材源转换为稳定名称 + 每机绑定，兼容读取当前 `local-paths.json`。
8. 运行自动化、Windows 双身份和全新前端会话验收；停止在批准、上传与发布之前。
9. 更新 Skill、操作指南和相关 OpenSpec 任务的 supersede 关系。

回滚时可关闭桌面 launcher 并恢复手工 UNC 输入；事务读取器必须保留，以免已产生的恢复信封无人处理。任何回滚不得删除 revision、handoff 或用户本机路径配置。
