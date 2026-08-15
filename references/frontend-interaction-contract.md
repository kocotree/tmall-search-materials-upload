# 前端优先交互契约

## 现状基线

- 基线分支：`codex/non-ai-slot-planning`。
- canonical Skill：`SKILL.md`。
- Agent metadata：`agents/openai.yaml`。
- 历史日常入口 `tmall-materials interact` 是前台 Flask 服务：输出 URL 后持续占用调用终端，不负责后台生命周期、就绪轮询或打开浏览器。
- 阶段一页面只显示店铺、图片源等业务配置。工作台自动启动或恢复 CDP Chrome；登录尚未完成时用独立等待页遮住业务表单，用户在千牛原生窗口登录成功后自动继续。不得显示 `collection_readiness.checks`、CDP、DOM、SHA、选择器路径或“验证当前页面”等技术操作。
- setup handoff 提交后由处理器自动检查生产 profile、当前 DOM、店铺、素材中心和路径。失败统一写入 Agent-only `agent-diagnostics/current.json`；Codex 通过 `diagnose-session` 获取原因、证据和幂等重试入口。“创建本机候选”仍只生成 Git 忽略的 `production=false` profile，示例 profile 永远不能直接提升。
- setup 处理中显示 `collection_status` 的当前 attempt/worker；phase、heartbeat、页数、持久化行数、checkpoint 和日志优先于旧 `result.json`。旧结果在 history 中显示时间与 reason code，并明确为 superseded。
- Worker phase 为 `waiting_human_check` 时，页面必须明确显示最后完整页和“请在 CDP Chrome 完成滑动验证；验证通过后自动继续”，不得显示为 Worker 死亡、普通 selector 错误或要求用户重提 setup。只读随机动作显示为短暂的 `random_action`，不得暴露为上传或发布。
- 恢复按钮仅在租约已过期或拥有关系和进程死亡均已证明时可用。归属不确定时显示等待租约，页面不得提供强制结束 PID。
- Plugin 必须通过 `skills/upload-search-materials/` 暴露 discovery entry；该入口继续校验并加载根目录 canonical Skill，避免 fresh context 绕过前端直接询问店铺名或 NAS 路径。
- 运行目录、端口、PID、日志和浏览器打开结果原先没有统一的机器可读服务状态。

这些是可复现基线，不是允许继续使用旧行为的兼容承诺。日常入口改为受管 UI 启动器；`interact` 只保留前台调试用途。

## 正常业务流的审批路由

- 工作台内的选择与确认是业务授权；Codex 宿主命令审批是新增技术权限授权。正常流程不得把前者重复包装成后者。
- 已经允许的固定工作台入口启动/恢复、精确 session 状态读取、后台 handoff 调度、商品采集、已配置素材源索引同步、目录元数据读取、候选生成、用户点击后的图片加载、确定性图片处理、dry-run 和状态回写，均直接执行，不在聊天中询问“是否允许”。
- 工作台健康时，同一受管服务内的后台调度器从持久化 handoff 或绑定请求中取得精确身份，并调用 `process-setup`、`process-product-selection`、`process-final-material-handoff`、`process-copy-request` 与 `process-publish-authorization`。动作必须同时校验 session、stage/request、revision 和 SHA-256；Codex 不维护 `agent_wait`、不轮询领取，也不为同一动作另起终端命令。
- `copy_draft` 和 `publish_authorization` 已分别绑定 request/session/revision/SHA-256；处理器必须验证绑定后直接执行。上传任务确认页的精确提交已经同时构成批准清单与正式发布授权，不再追加命令确认。
- 首次运行缺少固定桌面入口权限时，只允许一次范围精确的 `SYSTEM_PERMISSION_REQUIRED` 环境准备。新 NAS/新位置访问、认证或映射盘、刷新已有共享索引、源素材写删改、运行环境修复、临时诊断旁路、选择器/代码修改和 `publish_uncertain` 后重新发布，属于需要新增权限的异常路径。
- 宿主强制策略不可由 Skill 绕过。固定入口被阻止时返回稳定原因码，不得用临时 PowerShell 或连续审批卡绕行。

## 正常流程与源码诊断边界

- stage `status` 在 handoff 准备后返回 `handoff_status.handoff_identity`，其中只包含经过服务端复核的 `session_id`、`stage_id`、`revision`、`input_sha256`、`handoff_kind`、`allowed_action`、`transport` 及固定 endpoint。后台调度器只接受该身份，Codex 不再从运行目录或源码推导这些字段。
- 正式提交事务必须先原子保存 input、revision 和 handoff，再通知单 session 后台队列。调度器在服务启动时先扫描积压，随后由提交事件立即唤醒，并以 2 秒周期扫描作为丢失通知或进程恢复的兜底；因此提交发生在启动前或启动后都不会丢失。正常流程不得使用 `agent-wait`、`listen-handoff`、多轮 `sleep`、终端状态查询或目录轮询。
- 工作台与正式处理器健康时，不得用 CodeGraph、`rg`、`Get-Content` 或其他方式搜索/阅读项目源码，不得枚举 Plugin 目录、版本缓存、runs 目录，不得直接读取 `handoff.json`、`input.json`、锁文件或进程文件，也不得重新发现已经在契约中声明的路由和命令。
- 浏览器宿主不提供同源请求原语不影响后台业务处理；页面通过现有状态轮询显示后台结果，Codex 只需提供工作台链接。禁止依次试探 `fetch`、`window.fetch`、XHR、页面脚本和后端路由；传输问题本身不授权源码研究。
- 只有固定 API/处理器返回稳定异常原因码，且当前 revision 已生成状态为 `open` 的 `agent-diagnostics/current.json`，才允许研究源码。Agent 先运行 `diagnose-session` 取得失败 phase、processor、证据和原幂等重试入口，再只检查该处理器及其直接调用链。登录、人机验证、等待超时、业务校验退回以及上述 API→CLI 降级不属于源码异常。

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

凡 Agent 在聊天中提醒用户执行交互操作，必须先从受管启动结果或精确 session 的
`ui-status` 读取当前 `url`，并在同一条消息中提供可点击的工作台链接。登录、验证码、
配置、选择、审查、确认和恢复提醒都适用；即使具体动作发生在千牛原生窗口，也要同时
给出当前工作台链接，方便用户查看进度。不得硬编码 `8765`、按最新目录猜 session、
复用旧任务 URL，或仅说“回到工作台”。服务不可达时先恢复精确 session，随后使用恢复
结果中的新 URL；没有有效 URL 时不得要求用户执行页面操作。

每个 `StageDefinition` 和 `FieldDefinition` 都声明 `interaction_policy`：

- `frontend_required`：必须在对应页面完成；聊天只能解释恢复方式。
- `frontend_preferred`：必须先尝试页面；仅在出现字段允许的稳定原因码后，才可写入聊天降级草稿。
- `chat_fallback`：系统级安装、权限、登录协助等无法由业务页面完成的动作。

未声明的结构化业务字段默认是 `frontend_required`。页面健康且支持字段时，Agent 不得在聊天中索取同一数据。

工作台正常业务动作使用以下同源接口：stage `status`、`agent-wait`，以及 `/api/sessions/<session_id>/agent-actions/{process-setup|process-product-selection|process-final-material-handoff}`。处理动作请求必须携带 `handoff_status.handoff_identity` 返回的 revision 与 SHA-256；动作集合固定，不能透传 shell、脚本路径或任意参数。Codex 内置浏览器已打开当前工作台且具备请求原语时直接执行这些同源请求，不触发终端命令审批；宿主明确缺少该原语时执行一次固定 CLI 降级，不做能力试探或源码研究。

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
  “保存为本机配置”或“提交给工作台”。
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
- 工作台受控动作端点只能暴露预定义处理器，不接受任意命令或参数拼接；候选匹配必须从当前任务商品快照和本机已校验的 `team-cache` 文件夹快照即时生成。
# 后台调度和服务恢复

- 每个托管工作台只为启动时绑定的精确 session 创建一个后台调度器。调度器维护内存队列，提交事务完成后立即通知；每 2 秒的轻量扫描只用于恢复漏失通知、服务重启和租约过期的 processing claim。
- 页面读取 `workflow_dispatch` 并显示“后台已就绪、已排队、处理中、完成、异常”。旧 `agent_wait` 数据只供历史兼容和诊断，不得用于显示“Codex 在线”或决定正常流程是否继续。
- 工作台服务停止时不会有后台执行；重新用同一 `runs_root + session_id` 启动后，恢复扫描必须先处理持久积压。用户无需在聊天输入“已提交”，聊天也不构成领取或业务授权。
- draft 不生成 handoff；ready 由调度器原子领取，processing 仅在合法续作或租约过期时恢复，completed 复用原结果，身份不一致 fail closed。
