# 错误处理与恢复规则

## 前端启动与交互路由

| 原因码 | 阻断范围 | 恢复 |
|---|---|---|
| `UI_START_FAILED` | 当前 UI 启动 | 查看 session 日志并运行精确 `ui-restart` |
| `UI_UNREACHABLE` | 当前 UI 连接 | 先运行 `ui-status`，再恢复同一 session |
| `BROWSER_OPEN_FAILED` | 只影响自动打开浏览器 | 服务保持可用，手动打开精确 URL |
| `SERVICE_OWNERSHIP_MISMATCH` | stop/restart | 不结束该 PID；检查 PID 复用或陈旧状态 |
| `SYSTEM_PERMISSION_REQUIRED` | 运行环境准备 | 只请求安装 uv 或所需系统权限 |
| `LOGIN_INTERACTION_REQUIRED` | 天猫原生登录 | 用户完成登录、验证码或扫码；不保存凭据 |
| `SCHEMA_GAP` | 缺少的页面字段 | 生成 `frontend-gap.json`，不得永久保存自由文本旁路 |
| `FALLBACK_REASON_REQUIRED` | 聊天降级写入 | 先取得真实、允许的稳定原因码 |
| `FRONTEND_REQUIRED` | 指定业务字段 | 回到对应页面完成 |
| `FALLBACK_VALIDATION_FAILED` | 当前降级草稿/提交 | 按相同字段 schema 修正，不生成 handoff |

缺少店铺、图片根目录、NAS 当前不可访问或未登录不是启动错误。

## 图片源路径与目录选择

| 原因码 | 含义 | 恢复 |
|---|---|---|
| `PATH_AVAILABLE` | 当前服务身份可访问目录 | 可继续保存配置 |
| `INVALID_PATH` | 路径语法无效或不是绝对路径 | 修正本机、映射盘或 UNC 路径 |
| `DRIVE_NOT_MAPPED` | 当前服务会话没有对应盘符映射 | 在同一身份连接网络盘，或手工粘贴 UNC |
| `NETWORK_HOST_UNAVAILABLE` | NAS 主机不可达 | 检查网络、VPN 和主机状态 |
| `NETWORK_SHARE_NOT_FOUND` | 共享不存在或当前账号不可见 | 核对共享名称和权限 |
| `PATH_NOT_FOUND` | 目录不存在 | 核对共享内目录层级 |
| `ACCESS_DENIED` | 当前服务身份无读取权限 | 由管理员授权；程序不得索取凭据 |
| `PATH_CHECK_TIMEOUT` | NAS 元数据检查超时 | 检查网络后重试 |
| `PATH_CHECK_FAILED` | 未分类的检测失败 | 重试或手工核对 |

| 原因码 | 含义 | 恢复 |
|---|---|---|
| `FOLDER_PICKER_BUSY` | 已有目录窗口 | 完成或取消现有窗口 |
| `FOLDER_PICKER_TIMEOUT` | 用户未在边界内完成选择 | 重试或手工粘贴路径 |
| `FOLDER_PICKER_NOT_VISIBLE` | helper 已启动但窗口可见性无法证明 | 保留原输入并使用手工输入，或从桌面入口重启 |
| `FOLDER_PICKER_GUI_UNAVAILABLE` | 服务会话无法显示原生窗口 | 手工粘贴本机或 UNC 路径 |
| `FOLDER_PICKER_UNSUPPORTED` | 非 Windows 环境 | 手工输入路径 |
| `FOLDER_PICKER_START_FAILED` | Windows 助手未启动 | 重试或手工输入 |
| `FOLDER_PICKER_PROTOCOL_ERROR` | 助手返回格式无效 | 重试；仍失败时手工输入 |
| `FOLDER_PICKER_INVALID_RESULT` | 选择结果不是可访问绝对目录 | 重新选择或手工输入 |

路径和窗口错误只阻断当前路径动作，不清空输入、不提交阶段。程序不得自动建立映射、
挂载共享、保存 NAS 凭据或把资源管理器用户权限误当成受管服务身份权限。

## 共享文件夹索引

| 原因码 | 含义 | 恢复 |
|---|---|---|
| `FOLDER_INDEX_BUSY` | 另一个活跃进程持有同一共享索引的单写租约 | 读取既有 `folder-index-progress.json`；不得启动第二个写入者 |
| `FOLDER_INDEX_BUILDING` | 商品选择已提交，但共享索引仍在生成候选文件 | 等待进度进入完成状态后重跑同一 handoff 处理入口 |
| `FOLDER_ENUMERATION_TIMEOUT` | 一个 NAS 目录连续 10 秒没有返回任何目录项或元数据进展 | 保留已提交的目录元数据并停止继续调度本轮目录 I/O；检查该相对路径后运行 `--refresh` |
| `FOLDER_ENUMERATION_ACCESS_DENIED` | 当前桌面身份无权枚举该目录 | 修复共享权限后对同一索引运行 `--refresh`；不得索取或保存凭据 |
| `FOLDER_ENUMERATION_PATH_UNAVAILABLE` | 扫描期间目录被删除、断开或变为不可用 | 核对挂载和目录变更后运行 `--refresh` |
| `FOLDER_ENUMERATION_ERROR` | 其他目录枚举错误 | 查看 `folder-scan-summary.json` 中的相对路径和系统错误后运行 `--refresh` |
| `FOLDER_REFRESH_SCOPE_UNSAFE` | 定向刷新入口是符号链接或重解析点 | 改用同一 root 内的真实稳定相对路径；不得跟随该入口扫描 |

文件夹索引的超时是“无进展超时”，不是整个大目录的总耗时限制；持续返回目录项的
大型目录可以继续扫描。任一目录枚举错误都会令本轮摘要保持 `complete=false`，不会
把未扫描的子树当作空目录或完整索引。

索引异常退出时，目录级 frontier、扫描 ID、计数和已完成范围保存在 SQLite 的
`active_scan` metadata 中；`--resume` 只接受相同 roots 与商品表身份。正常结束或已明确
返回 partial 后清除检查点。定向刷新路径拒绝绝对路径、盘符、空段、`.` 和 `..`，且
只在本轮完整完成的来源/前缀范围内更新 inactive 状态。

| `LOCAL_RESOURCE_IDENTITY_MISMATCH` | 服务、picker、索引或 Worker 的 SID/登录会话不一致 | 从固定桌面入口重启工作台后重新检测 |
| `PERSISTENCE_ACCESS_DENIED` | 本地状态文件不是短暂占用而是只读或 ACL 拒绝 | 保留事务证据，修复工作目录权限后重试原请求 |
| `REVISION_CONTENT_CONFLICT` | 同一 request ID 或目标 revision 对应不同规范化内容 | 刷新权威状态，保留既有 revision，不自动覆盖 |

## 确定性编排

- `SLOT_AI_PLANNING_REMOVED`：新任务请求了坑位 AI；继续使用自动草稿或人工编辑。
- `NO_COMMON_SLOT_RATIO`：候选组没有共同可行的 3:4/1:1；退回候选池供人工处理。
- `CURRENT_SLOT_PLAN_REVISION_STALE`：其他页面已修改草稿；重新载入后再确认。
- 采用图片不足 3 张时返回逐商品“还差 N 张”；保留草稿，不创建坑位草稿。

| Code | 含义 | 默认动作 |
| --- | --- | --- |
| `DATA_SCHEMA` | CSV/XLSX 必需字段缺失或变化 | 阻断整批 |
| `MISSING_PRODUCT_ID` | 商品没有远端 ID | 阻断该商品 |
| `DUPLICATE_PRODUCT_ID` | 多条来源记录共享 ID | 阻断所有重复行 |
| `RULE_CONFLICT` | 月度来源冲突 | 转人工审核 |
| `ASSET_NOT_FOUND` | 没有匹配素材 | 阻断对应坑位 |
| `FOLDER_COVERAGE_LIMIT_EXCEEDED` | 单商品非空采用文件夹超过 100 个，100 张候选窗口无法覆盖全部文件夹 | 保留确定性候选和零名额审计行；提示用户筛减文件夹或在新任务中重新抽样，不自动扩大上限 |
| `ASSET_INVALID` | 数量、比例、尺寸、格式或可读性失败 | 修复本地素材，不上传 |
| `SOURCE_UNREADABLE` | 原图不存在、无权限、损坏或无法解析 | 阻断该图片，重新连接素材源或更换素材 |
| `SOURCE_METADATA_MISSING` | 历史候选缺少原图大小、宽高或比例 | 重新运行当前任务的图片预检 |
| `IMAGE_SIZE_BELOW_MINIMUM` | 原图小于 200KiB | 更换素材，不使用填充方式扩大文件 |
| `IMAGE_SIZE_EXCEEDED` | 原图或输出大于 20MiB | 第四阶段标记需压缩；第五阶段坑位确认后压缩，最终输出仍超限则阻断 |
| `OUTPUT_DIMENSIONS_BELOW_MINIMUM` | 目标比例输出宽或高低于 720px | 更换素材或目标比例 |
| `COMPRESSION_UNAVAILABLE` | 当前任务没有可用压缩提供方 | 禁止采用超限图片并恢复压缩配置 |
| `COMPRESSION_TARGET_UNREACHABLE` | 最低质量和最小尺寸内仍无法压缩达标 | 更换素材 |
| `OUTPUT_SIZE_BELOW_MINIMUM` | 处理后文件小于 200KiB | 更换素材，不制造无意义体积 |
| `CROP_PREFLIGHT_REQUIRED` | 尚未执行当前裁剪参数的预校验 | 保持完成按钮禁用，运行本地裁剪预校验 |
| `CROP_PREFLIGHT_STALE` | 图片、顺序、比例、裁剪框或压缩参数已变化 | 使旧预校验失效并重新校验当前参数 |
| `OUTPUT_IDENTITY_MISMATCH` | 第五阶段坑位输出文件已变化 | 使对应坑位输出和文案过期，重新处理和确认 |
| `SLOT_PLAN_REQUIRED` | 未确认坑位比例就请求正式图片处理 | 返回第五阶段先确认坑位计划 |
| `AGENT_IMAGE_BUDGET_EXCEEDED` | Codex 请求图片数超过配置 | 缩小候选范围或继续规则/人工方案 |
| `AGENT_THUMBNAIL_UNAVAILABLE` | 当前任务缓存和共享盘原图均不可用 | 重建任务缓存，或继续规则/人工方案；不得留下半成品请求 |
| `AGENT_REQUEST_STALE` | Agent 请求绑定的阶段 revision 已变化 | 标记 `superseded`，保留当前草稿并重新请求或回退 |
| `AGENT_REQUEST_CANCELLED` | 用户离开 AI 模式或取消请求 | 拒绝迟到响应，保留人工/规则草稿 |
| `AGENT_RESPONSE_SCHEMA_INVALID` | Codex 回写不符合请求 Schema | 拒绝响应并回退规则/人工方案 |
| `AGENT_SLOT_PLAN_HARD_RULE_REJECTED` | AI 方案违反商品归属、数量、唯一性或统一比例硬规则 | 拒绝方案并保留规则/人工草稿 |
| `AGENT_SLOT_PLAN_CAPACITY_EXCEEDED` | AI 生成坑位数超过商品剩余容量 | 只回退对应商品规则草稿，保留其他合法商品 |
| `CURRENT_SLOT_PLAN_REVISION_STALE` | 人工保存基于过期的当前草稿 revision | 刷新唯一草稿后重新编辑，不覆盖较新修改 |
| `CROP_BOX_INVALID` | 人工裁剪框越界或坐标顺序无效 | 保留计划，返回锁定比例的裁剪步骤修正 |
| `COMPRESSION_CONFIRMATION_REQUIRED` | 需压缩图片尚未获得人工确认 | 停留图片处理步骤，不生成派生输出 |
| `AGENT_COPY_RESPONSE_MISSING_SLOT` | AI 文案响应缺少当前坑位 | 保留已有文案版本，只重试当前输出身份的文案请求 |
| `DECISION_MODE_NOT_ALLOWED` | 当前阶段不允许所选执行模式 | 恢复阶段允许模式，不能绕过 `manual_only` |
| `DECISION_REVISION_STALE` | 模式选择绑定的 revision 已过期 | 刷新页面后基于当前 revision 重新选择 |
| `OUTPUT_PATH_OUTSIDE_TASK` | 派生输出试图写到当前任务目录外 | 拒绝写入并检查路径配置 |
| `LICENSE_UNKNOWN` | 使用权未确认 | 阻断对应坑位 |
| `STORE_IDENTITY_MISMATCH` | 当前店铺不是目标店铺 | 立即停止整批 |
| `PRODUCT_MISMATCH` | 页面商品 ID 与任务不同 | 立即停止当前批次 |
| `REMOTE_SLOT_CONFLICT` | 批准后的目标坑位已发生变化 | 任务退回审核 |
| `SELECTOR_INVALID` | 必需页面元素不存在或不可见 | 商品资格不变；受影响坑位进入 `needs_manual_review`，停止受影响页面且不写入零值 |
| `AUTH_EXPIRED` | 登录失效 | 暂停整批，用户重新登录 |
| `HUMAN_CHECK` | 验证码、扫码、短信或风控 | 保存当前 checkpoint 并暂停页面动作；用户验证通过后自动继续同一 attempt |
| `UPLOAD_REJECTED` | 页面拒绝文件或字段 | 记录原始提示，转人工审核 |
| `PUBLISH_UNCERTAIN` | 点击发布后没有可信结果 | 禁止重发，先远端回查 |
| `QIANNIU_MATERIAL_CARD_NOT_FOUND` | 本地上传后短唯一名称暂未命中素材卡 | 文案获取按单坑可恢复错误重试；正式上传停在发布前并按发布前失败规则重试 |
| `QIANNIU_MATERIAL_IDENTITY_AMBIGUOUS` | 本地上传后短唯一名称同时命中多张素材卡 | 停在发布前；按身份歧义处理，不点击发布或自动重试 |
| `QIANNIU_CONTENT_FIELD_MISSING` | 标题或正文控件不可识别，包括 Cangjie 代理 textarea 变化 | 停在发布前；更新现有表单定位并回归测试 |
| `COPY_DRAFT_AUTHORIZATION_INVALID` | 文案请求的限定授权缺失、被改写，或与当前首图/最终输出指纹不一致 | 不启动千牛浏览器动作；保留现场并从工作台当前有效图片输出重新创建请求 |
| `QIANNIU_PUBLISH_BUTTON_AMBIGUOUS` | 发布专用语义或明确发布按钮不是唯一可见 | 停在发布前；禁止降级点击通用确认按钮 |
| `QIANNIU_REMOTE_BASELINE_MISSING` | 发布前没有保存同商品远端 ID 集合 | 禁止点击发布 |
| `QIANNIU_REMOTE_ID_DELTA_AMBIGUOUS` | 发布后旧 ID 消失，或新增 ID 不是恰好一个 | 标记 `publish_uncertain`，禁止按坑位位置猜测或重发 |
| `PUBLISH_RUN_PARTIAL_STATE` | 交互任务到发布任务的持久化文件只存在一部分 | 禁止覆盖；保留现场并人工恢复 |
| `PUBLISH_RUN_IDENTITY_MISMATCH` | 已有发布批次与当前 session/store/task IDs 不一致 | 禁止复用或覆盖 |
| `REMOTE_EVIDENCE_MISMATCH` | 远端记录与批准指纹/坑位/时间不一致 | 保持不确定并转人工 |
| `LARK_UPLOAD_LOG_SCHEMA_INCOMPLETE` | 团队上传记录表缺少“原图 SHA-256”字段 | 保留上一次有效上传历史快照；主上传流程继续，维护者补齐文本字段后刷新飞书数据 |
| `UPLOAD_LOG_RECORD_ALIGNMENT_INVALID` | 已成功的上传任务与待写入飞书的记录无法一一对应 | 保留逐任务发布结果，不写入不确定记录；按原任务证据诊断并重试记录同步 |
| `MODERATION_FAILED` | 平台审核失败 | 记录平台原因，转人工处理 |
| `STATE_MISSING` | `run.sqlite3`、任务状态行或状态证据缺失/损坏 | 禁止自动上传，先做可信远端核验 |

## 采集、选择器和处理租约

| 原因码 | 含义 | 恢复 |
|---|---|---|
| `SELECTOR_PROFILE_NOT_FOUND` | 未发现本机生产选择器配置 | 在阶段一“采集运行环境”组件验证并保存配置 |
| `SELECTOR_EXAMPLE_NOT_PRODUCTION` | 尝试把仓库示例用于生产 | 改用 Git 忽略的本机 profile |
| `SELECTOR_PURPOSE_NOT_DECLARED` | profile 不支持当前操作用途 | 补充并验证对应 purpose |
| `SELECTOR_PLACEHOLDER_REJECTED` | profile 仍包含占位选择器 | 基于当前 DOM 修复本机 profile |
| `CDP_UNAVAILABLE` | 用户控制的 CDP Chrome 未连接 | 启动专用 profile，并打开官方素材中心 |
| `LOGIN_INTERACTION_REQUIRED` | 当前页面还不能验证登录店铺 | 写入统一 Agent 诊断，Codex 打开或置前 CDP Chrome；用户登录后自动重试同一 session |
| `HUMAN_CHECK` | 出现扫码、短信、验证码或风控 | 写入 attempt 绑定的 human checkpoint 并保持 Worker 心跳；提示用户在 CDP Chrome 完成验证，验证消失后自动继续，不自动操作滑块 |
| `STORE_IDENTITY_MISMATCH` | 当前店铺与阶段一目标不一致 | 阻断整批，不写采集行 |
| `PROCESSING_CLAIM_ACTIVE` | 同一阶段存在未过期处理租约 | 等待当前 Agent 或租约到期 |
| `PROCESSING_CLAIM_STALE` | 旧 Agent/旧 claim 尝试回写 | 拒绝旧写入，使用当前 claim 恢复 |
| `SETUP_INPUT_HASH_MISMATCH` | handoff 与当前 input 不一致 | 停止并重新提交正确 revision |
| `ENVIRONMENT_NOT_PREPARED` | 用户级运行环境缺失或与 `uv.lock` 不一致 | 运行 `scripts/bootstrap.cmd`；恢复采集不得隐式同步 |
| `SELECTOR_DOM_NOT_VALIDATED` | profile 未通过当前素材中心 DOM 验证 | Codex 读取 Agent 诊断，修复并验证本机 profile 后重跑同一处理入口 |
| `SELECTOR_FIELD_INVALID:<field>` | 当前 DOM 中某个必需字段验证失败 | 保留非生产候选并按字段修复 |
| `MATERIAL_PAGE_REQUIRED` | CDP 页面不是官方素材中心 | 用户在 CDP Chrome 打开官方页面 |
| `COLLECTION_WORKER_OWNERSHIP_INDETERMINATE` | PID 存在但 token/进程身份无法证明 | 不结束、不抢占，等待租约过期 |
| `COLLECTION_ATTEMPT_STALE` | 旧 attempt 尝试更新进度或结果 | 拒绝写入，保留当前 attempt |
| `COLLECTION_ATTEMPT_BINDING_MISMATCH` | revision、输入、选择器或店铺绑定变化 | 禁止复用 checkpoint |
| `CHECKPOINT_OUTPUT_SHA256_MISMATCH` | CSV 与 checkpoint 哈希不一致 | 保留证据并停止恢复 |
| `CHECKPOINT_OUTPUT_DUPLICATE_PRODUCT` | checkpoint CSV 含重复商品 ID | 停止恢复并修复采集证据 |
| `PAGINATION_ORIGIN_UNVERIFIED` | 无法唯一识别当前页、第一页控件或末页 | 修复本机选择器并重新验证当前 DOM |
| `PAGINATION_ORIGIN_RESET_FAILED` | 请求第一页后无法验证页面稳定在第 1 页 | 保持素材中心打开并重新开始采集 |
| `PAGINATION_CHECKPOINT_MISMATCH` | 恢复时逐页商品身份与 checkpoint 证据不一致 | 保留旧 attempt，创建新时间戳任务采集 |
| `PAGINATION_TRANSITION_MISMATCH` | 翻页后页码未加一、跳页或商品身份未变化 | 停止在最后完整 checkpoint 并检查页面加载 |
| `PAGINATION_TERMINAL_UNVERIFIED` | 下一页不可用，但当前页不能证明是稳定末页 | 不发布结果，修复末页选择器后重试 |
| `PAGINATION_EVIDENCE_LEGACY` | 历史 checkpoint 没有分页起点证据 | 分类为 legacy，不作为完整采集复用 |
| `PAGINATION_HISTORICAL_OUTPUT_QUARANTINED` | 命中已知错误起点产生的历史结果 | 保留原文件，使用新时间戳任务重新采集 |

选择器、导航、弹窗、解析、分页或页面状态故障必须先由维护的
`supplement --scan-mode high-value` 产生可复现错误证据。Playwright 只能用于诊断和
修复现有 profile/collector；修复后必须增加回归测试并重跑原入口。

## 重试边界

- 最终发布按钮出现前的瞬时页面或网络失败在首次失败后最多重试三次；每次重试前必须关闭当前未发布表单。仍失败时把当前 item 记录为已跳过并继续后续 item。
- 同一商品的正式上传正常流程复用一次商品搜索结果；只有表单、frame、商品行或列表上下文失效时，重试才重新进入列表并搜索该商品。
- 店铺不一致、人机验证、批准清单整体身份或运行状态库身份异常属于整批安全门禁，不得按单项跳过继续；单项批准内容变化只阻断该 item。
- 发布按钮在一个 `material_item` 上最多点击一次。
- 点击发布后的超时、页面关闭或成功信号缺失一律进入 `publish_uncertain`。
- `publish_uncertain` 必须通过正确店铺、精确商品 ID、目标坑位、批准内容指纹和提交时间窗口回查。
- `run.sqlite3` 及 manifest 中每个 task 的状态行必须存在；流程不得静默创建新状态库后重排队。
- 只有远端素材列表可见、精确检索成功且没有对应记录时，才能返回 `REMOTE_ABSENCE_CONFIRMED`；重新发布仍需要新的显式执行决定。
- `REMOTE_ABSENCE_CONFIRMED` 不会使原 task 回到可上传状态。若人工决定重提，必须生成新 task ID、重新生成批准清单并再次显式执行；原 task 保留审计历史。

## 审批失效范围

- 单个 `material_item` 的媒体、标题、描述、动作或坑位变化：只撤销该 item 的批准。
- 单项撤批使用 `approved → ready_for_review / APPROVED_CONTENT_CHANGED`，其他 item 保持原状态。
- 目标店铺、manifest schema、manifest 总哈希或批次输入哈希变化：阻断整批。
- 批次阻断只设置 run 级门禁并将 item 标记为 held，不批量改写 item 状态；解除门禁前必须重核店铺、manifest 和未执行项内容哈希。
- 已有远端素材 ID 或可信远端证据的 item 不得再次进入上传队列。

所有异常必须记录 task ID、商品 ID、目标店铺、旧状态、新状态、原因码、时间、尝试次数和页面证据。
# Handoff recovery reason codes

- `HANDOFF_SEGMENT_TIMEOUT`: 30 秒等待片段正常结束；可在阶段总预算内继续等待。
- `SPECIALIZED_PROCESSOR_REQUIRED`: 当前阶段由专用处理器拥有领取权；通用
  `resume-session` 和页面恢复只返回验证身份；`listen-handoff` 可维护咨询性在线租约，但三者都不得提前领取，必须按返回的唯一处理入口继续。旧兼容 `wait-handoff` 不用于该阶段。
- `HANDOFF_STAGE_CHANGED` / `HANDOFF_SESSION_CHANGED`: 等待身份已变化，重新读取精确
  session 当前阶段。
- `AGENT_WAIT_STALE` / `AGENT_WAIT_IDENTITY_MISMATCH`: 不得续租旧等待者，重新注册。
- `NOT_FORMALLY_SUBMITTED`: 页面仍是草稿；不得自行构造 handoff。
- `RECOVERY_HANDOFF_MISSING` / `RECOVERY_HANDOFF_REVISION_MISMATCH`: 保留现场并要求页面
  重新正式提交，禁止猜测或改写身份。
