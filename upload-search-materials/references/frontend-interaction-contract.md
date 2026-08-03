# 前端优先交互契约

## 现状基线

- 基线分支：`codex/non-ai-slot-planning`。
- canonical Skill：`upload-search-materials/SKILL.md`。
- Agent metadata：`upload-search-materials/agents/openai.yaml`。
- 历史日常入口 `tmall-materials interact` 是前台 Flask 服务：输出 URL 后持续占用调用终端，不负责后台生命周期、就绪轮询或打开浏览器。
- 阶段一页面只显示店铺、图片源等业务配置。工作台自动启动或恢复 CDP Chrome；登录尚未完成时用独立等待页遮住业务表单，用户在千牛原生窗口登录成功后自动继续。不得显示 `collection_readiness.checks`、CDP、DOM、SHA、选择器路径或“验证当前页面”等技术操作。
- setup handoff 提交后由处理器自动检查生产 profile、当前 DOM、店铺、素材中心和路径。失败统一写入 Agent-only `agent-diagnostics/current.json`；Codex 通过 `diagnose-session` 获取原因、证据和幂等重试入口。“创建本机候选”仍只生成 Git 忽略的 `production=false` profile，示例 profile 永远不能直接提升。
- setup 处理中显示 `collection_status` 的当前 attempt/worker；phase、heartbeat、页数、持久化行数、checkpoint 和日志优先于旧 `result.json`。旧结果在 history 中显示时间与 reason code，并明确为 superseded。
- 恢复按钮仅在租约已过期或拥有关系和进程死亡均已证明时可用。归属不确定时显示等待租约，页面不得提供强制结束 PID。
- 项目原先没有 `.codex/skills/upload-search-materials/`，fresh repository context 因而可能无法发现 canonical Skill，并可能直接询问店铺名或 NAS 路径。
- 运行目录、端口、PID、日志和浏览器打开结果原先没有统一的机器可读服务状态。

这些是可复现基线，不是允许继续使用旧行为的兼容承诺。日常入口改为受管 UI 启动器；`interact` 只保留前台调试用途。

## 采集运行环境与两窗口边界

阶段一页面必须优先承载生产选择器 profile 的路径、验证状态和保存动作，并展示 CDP
端点连接状态、当前页面 URL、可见店铺和下一步动作。配置页是结构化业务交互界面；
CDP Chrome 是用户登录、扫码、验证码和只读后台采集窗口，两者不得混称。

登录和平台人机验证属于 `chat_fallback`/原生页面边界，但 selector profile 选择、
商品选择、异常行审查和后续业务决定仍为 frontend-first。用户登录完成后，Agent
必须恢复精确 session 并重新运行受管 `process-setup`，不得要求用户手动导航到
“搜推高价值”或临时生成另一份采集脚本。

处理状态由 `processing_claim` 租约决定。页面应区分活动 Agent、租约过期、可从
checkpoint 恢复和需要人工修复；handoff 文件只展示原提交身份，不充当第二份状态源。

## 路由规则

每个 `StageDefinition` 和 `FieldDefinition` 都声明 `interaction_policy`：

- `frontend_required`：必须在对应页面完成；聊天只能解释恢复方式。
- `frontend_preferred`：必须先尝试页面；仅在出现字段允许的稳定原因码后，才可写入聊天降级草稿。
- `chat_fallback`：系统级安装、权限、登录协助等无法由业务页面完成的动作。

未声明的结构化业务字段默认是 `frontend_required`。页面健康且支持字段时，Agent 不得在聊天中索取同一数据。

| 阶段/动作 | 页面组件 | 默认策略 | 允许的聊天降级 |
|---|---|---|---|
| 任务配置：店铺、图片源、高级路径 | `setup_form`、原生目录选择器 | `frontend_preferred` | UI 启动失败、不可达或无可用浏览器 |
| 搜推高价值商品选择、搜索、筛选、排除 | `inspection_matrix` | `frontend_required` | 不允许业务值降级 |
| 文件夹采用/排除、换批、图片采用、预检、重复提示 | `asset_match_gallery` | `frontend_required` | 不允许业务值降级 |
| 坑位、顺序、比例、裁剪、压缩、独立裁剪预校验 | `slots_copy_editor` 第一子页 | `frontend_required` | 不允许业务值降级；预校验未通过时完成按钮必须禁用 |
| AI 文案任务进度、逐坑回填、标题、描述、风险和逐坑确认 | `slots_copy_editor` 第二子页 | `frontend_required` | 页面只创建/轮询 Agent 请求，不得同步运行 Playwright；schema gap 可记录，不得旁路发布 |
| dry-run 审查 | `dry_run_review` | `frontend_required` | 不允许业务值降级 |
| 精确批准 | `approval_table` | `frontend_preferred` | 页面不可用时仍须绑定不可变清单与哈希 |
| 生产确认 | `production_confirmation` | `frontend_preferred` | 页面不可用时仍须精确确认店铺、任务、坑位和 manifest 哈希 |
| 结果与恢复 | `result_timeline` | `frontend_required` | 只允许解释恢复动作 |
| 安装 uv/运行环境权限 | 无业务页面 | `chat_fallback` | `SYSTEM_PERMISSION_REQUIRED` |
| 登录、验证码或扫码 | 天猫原生页面 | `chat_fallback` | `LOGIN_INTERACTION_REQUIRED`；不得保存凭据 |

## 图片源检测与原生目录窗口

- 图片源检测对每行返回 `status`、`available`、`path_kind`、`reason_code`、
  `message`、`next_action`、`checked_at` 和可选
  `portable_path_suggestion`；旧前端仍可读取 `status=available|unavailable`。
- 检测只读取目录元数据，最多并发检查 8 个来源，并对单项和整批设置边界；不得列举
  目录、读取图片、创建标记文件、映射共享或请求凭据。
- 映射盘的 UNC 建议必须由用户点击后采用。检测、选择或显示建议都不得自动执行
  “保存为本机配置”或“提交给 Agent”。
- “选择文件夹”只在用户点击后调用 Windows 原生 STA 助手。同一服务只允许一个活动
  窗口；取消及任何失败都保留原输入，页面继续允许粘贴本机或 UNC 路径。
- 原生助手稳定结果包括 `FOLDER_PICKER_SELECTED`、`FOLDER_PICKER_CANCELLED`、
  `FOLDER_PICKER_BUSY`、`FOLDER_PICKER_TIMEOUT`、
  `FOLDER_PICKER_GUI_UNAVAILABLE`、`FOLDER_PICKER_UNSUPPORTED`、
  `FOLDER_PICKER_START_FAILED`、`FOLDER_PICKER_PROTOCOL_ERROR` 和
  `FOLDER_PICKER_INVALID_RESULT`。

## 稳定原因码

| 原因码 | 含义 | 恢复动作 |
|---|---|---|
| `UI_START_FAILED` | 受管服务在限定恢复次数后仍无法启动 | 查看会话日志，执行 `ui-restart` |
| `UI_UNREACHABLE` | 服务状态存在，但健康端点或精确 session 不可达 | 执行 `ui-status` 后 `ui-restart` |
| `BROWSER_OPEN_FAILED` | 服务健康，但客户端和系统浏览器均未成功打开 | 使用启动结果中的精确 URL |
| `SYSTEM_PERMISSION_REQUIRED` | 安装 uv、访问系统目录等运行权限缺失 | 只请求所需权限，完成后继续启动 UI |
| `LOGIN_INTERACTION_REQUIRED` | 天猫原生登录、验证码或扫码需要用户处理 | 打开原生登录页，完成后回到当前 session |
| `SCHEMA_GAP` | 当前页面 schema 没有承载所需结构化字段 | 记录缺口和前端补充任务；只允许 schema 兼容的临时草稿 |

空店铺、空月份、空图片源、NAS 当前不可访问或尚未登录都不是 UI 启动失败，必须先显示阶段一页面。

## 聊天降级审计信封

聊天降级仍写入当前阶段的权威 `input.json`，并保存：

```json
{
  "interaction_channel": "chat_fallback",
  "fallback_reason_code": "UI_START_FAILED",
  "fallback_detail": "bounded startup attempts exhausted",
  "recorded_at": "带时区的 ISO 8601",
  "recorded_by": "codex-agent",
  "session_id": "精确 session",
  "stage_id": "精确 stage",
  "base_revision": 2,
  "input_sha256": "写入后 input.json 的 SHA-256"
}
```

写入必须复用字段 allowlist、类型校验、revision 冲突和 handoff 规则。页面恢复后显示数据来源，用户可按正常 revision 规则修改；审计事件不得丢失。`SCHEMA_GAP` 还必须在阶段目录生成 `frontend-gap.json`。

## 跨电脑约束

- 所有路径从项目位置、`--runs-root`、环境变量或本机配置解析，不硬编码用户名、桌面路径或盘符。
- 默认端口范围为 loopback `8765–8795`；占用时选下一端口，不结束未知进程。
- 服务状态存放在精确 session 目录的 `.ui-service.json`，日志存放在同目录 `logs/`。
- 状态包含 session、runs root、PID、ownership token、端口、URL、日志、启动时间和健康状态。
- stop/restart 只有在健康端点返回的 PID 和 ownership token 同时匹配时才管理进程；PID 复用必须拒绝。
- 浏览器打开结果与服务健康分开记录；浏览器失败不等于服务失败。
- fresh-context 恢复必须使用精确 runs root、session、stage、revision 和 input SHA，不按目录新旧猜测。
# 等待租约和聊天恢复

- `agent_wait` 是最长 30 秒的在线心跳租约，绑定 session、stage、预期 revision 和
  独立 `budget_expires_at`；同一 waiter 在 10–15 秒片段间续租同一 wait ID。setup
  默认总预算为两分钟。它与 `processing_claim` 分离，不能授权任何业务动作。
- 页面只在服务端租约有效时显示“Codex 在线”，并把心跳剩余与单调递减的总等待剩余
  分开呈现。正常超时、错误、阶段变化和成功认领必须清理匹配租约。租约过期且 handoff 已
  ready 时，显示“在当前聊天输入已提交”。
- 当前聊天必须先唯一绑定精确 session。“已提交”只调用权威恢复解析器；draft 不生成
  handoff，processing 返回原进度，completed 复用原结果，身份不一致 fail closed。
- 新聊天、上下文丢失或多 session 歧义时必须使用页面的完整恢复指令。
