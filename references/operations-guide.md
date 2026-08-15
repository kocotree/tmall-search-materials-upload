# 首次运行指南

## 新任务第三至第五阶段

素材选择页保存草稿不会运行预检或编排。正式提交时只复查采用图片，写入
`selected-asset-preflight.json` 并同步创建确定性 `current-slot-plan.json`。
成功后不领取 `image_review` 或坑位 Agent request；直接打开 `slots_copy` 检查
自动分组并按需人工调整。确认精确 plan revision 后第一页才展开裁剪和压缩；
独立“裁剪预校验”复核实际输出通过后才启用完成按钮；完成图片处理时自动创建
`copy_draft` 文案请求，不再要求首次点击生成按钮。

`prepare-image-review`、`prepare-slot-board` 和坑位类 Agent request 命令仅供
历史任务恢复，不用于新任务。

兼容的旧 suitability snapshot 可显式迁移，原阶段文件不会删除：

```powershell
uv run tmall-materials migrate-deterministic-selection --runs-root <runs目录> --session <session_id>
```

## 1. 安装与检查

在本 Plugin 目录使用 `uv` 管理 Python 3.11、锁文件和依赖虚拟环境：

```powershell
.\scripts\bootstrap.cmd -Mirror official -WithTests
$quickValidate = Join-Path $env:USERPROFILE ".codex\skills\.system\skill-creator\scripts\quick_validate.py"
uv run python -X utf8 $quickValidate .
```

`uv` 根据 `.python-version` 使用 Python 3.11，并依据 `uv.lock` 在用户数据目录创建或同步独立 `.venv`。该环境只保存 Python 和第三方依赖，不安装 Plugin 自身的 `upload_search_materials` 业务包。所有启动器都用这个 Python 执行当前 Plugin 目录的 `scripts/run-plugin.py`，因此页面、Worker 和素材执行器始终加载同一版本的 `src/`。若本机没有 Python 3.11，bootstrap 会通过 `uv` 安装受管解释器。首次准备需要网络访问；不要向系统 Python、Plugin 安装缓存或 Conda 基础环境直接安装依赖。

环境检查是启动配置页前唯一允许的阻断。用户级依赖环境和当前 Plugin 启动器均就绪时直接启动页面；否则准备 `uv` 并同步依赖。不得以 Plugin 缓存目录中缺少 `.venv` 或缓存目录不可写为失败依据。此时不得要求用户在聊天中提供店铺名、月份或图片根目录，也不得把这些阶段 1 字段与环境安装合并成一个前置问题。

文档中的 `tmall-materials ...` 表示 CLI 子命令。安装后的实际调用入口是
`scripts\run-plugin.cmd ...`；`uv run`
只用于开发测试，不用于已安装 Plugin 的日常任务。

如果电脑已有兼容的 Python 3.11 或 3.12，可显式传入路径，避免 `uv` 自动下载解释器：

```powershell
.\scripts\bootstrap.cmd -Python "<python.exe>" -Mirror official -WithTests
```

网络受限时可把 `-Mirror` 改为 `tuna`、`aliyun` 或 `tencent`。镜像选择会影响 `uv.lock` 的依赖来源；只有明确切换锁文件来源时才传 `-UpdateLock`，并在提交前复核锁文件差异。公开仓库默认保留 `official`，不在源码或配置中保存镜像凭证。脚本使用用户数据目录中的 runtime、uv-cache 和系统证书。默认位置为 `%LOCALAPPDATA%\tmall-search-materials`；可分别用 `TMALL_USER_DATA_ROOT` 和 `TMALL_RUNTIME_ROOT` 覆盖。

### 1.1 每台电脑只配置一次路径

首次启动后可直接在任务配置页的“本次图片源”新增、删除和检测图片源，并点击“保存为本机配置”。配置页不再显示独立“公司共享盘”模块；用户从 Windows 原生目录窗口选择已经挂载的 Y 盘、Z 盘等目录，或直接粘贴 UNC 路径。页面支持 1–50 个“来源名称 + 根路径”。保存时每个来源使用稳定 `source_id + label`，实际盘符或 UNC 是当前电脑的本机绑定；旧的 `label + path` 文件读取时自动补 source ID。另一台电脑可把同一 source ID 绑定到不同盘符或 UNC，无需修改项目文件。页面默认写入用户数据目录的 `config/runtime.json`；`config/local-paths.example.json` 仅用于了解字段，不应复制回 Plugin 安装目录。配置不得保存 NAS 凭据、目录清单或图片内容。

每个图片源行的“选择文件夹”由用户点击后启动独立的 Windows STA 助手并打开原生目录选择窗口，只回填已验证的绝对目录，不扫描图片。助手依次记录 `started`、`window_visible`、`selected/cancelled`；窗口无法在可见性期限内证明已显示时返回 `FOLDER_PICKER_NOT_VISIBLE`，只结束本次 helper，保留原输入并继续允许手工输入。取消、窗口忙碌、选择超时、无桌面会话、启动失败或返回无效路径同样不会清空输入。UNC 或无界面环境可直接粘贴路径。

“检测路径”只读取目录本身的元数据，不列举子目录或图片。页面逐行返回：

| 原因码 | 含义 | 恢复动作 |
|---|---|---|
| `PATH_AVAILABLE` | 当前服务身份可访问目录 | 可继续保存配置 |
| `INVALID_PATH` | 不是有效绝对路径 | 修正本机、映射盘或 UNC 路径 |
| `DRIVE_NOT_MAPPED` | 当前服务会话没有该盘符映射 | 在同一 Windows 身份建立映射，或粘贴 UNC |
| `NETWORK_HOST_UNAVAILABLE` | NAS 主机不可达 | 检查公司网络、VPN 和主机状态 |
| `NETWORK_SHARE_NOT_FOUND` | 共享名不存在或不可见 | 核对共享名和当前账号权限 |
| `PATH_NOT_FOUND` | 共享内子目录不存在 | 核对目录层级和名称 |
| `ACCESS_DENIED` | 当前服务身份无读取权限 | 由管理员授予权限；程序不索取凭据 |
| `PATH_CHECK_TIMEOUT` | NAS 元数据响应超时 | 检查网络后重试 |
| `PATH_CHECK_FAILED` | 未分类检测失败 | 重试并手工核对路径 |

映射盘可访问且 Windows 能解析其 UNC 目标时，页面只显示“采用 UNC”建议，不会自动替换。跨电脑配置优先使用 UNC；映射盘仍可用于当前受管服务身份能够访问的电脑。路径检测不能创建映射、挂载共享、取得凭据或绕过 NAS 权限。

项目内输入默认无需配置：程序先定位包含 `SKILL.md`、`pyproject.toml` 与 `src/upload_search_materials/` 的 Skill 根目录，再在 `src/upload_search_materials/docs/` 中按以下受控模式查找：

- 商品表：`天猫商品信息表*产品数据表*数据总表.csv`
- 规则表：`天猫商品信息表*每月推品规则*Grid View.csv`

只有唯一命中才会自动采用。没有命中时页面显示“未找到”；多个命中时显示歧义并等待显式配置，不按时间或文件名猜测。共享盘不执行全盘扫描；盘符或 UNC 路径只从本机配置读取。

解析优先级与覆盖入口：

1. `tmall-materials interact --config <配置.json>` 指定配置文件。
2. `TMALL_CONFIG_FILE` 指定配置文件；`TMALL_WORKSPACE_ROOT`、`TMALL_PRODUCTS_CSV`、`TMALL_RULES_CSV`、`TMALL_RUNS_ROOT` 可单项覆盖。
3. 用户数据目录中的 `config/runtime.json`；旧版项目内 `config/local-paths.json` 仅作兼容读取。
4. 项目结构自动发现；运行目录默认使用用户数据目录下的 `runs/`。

共享文件夹索引路径按 `TMALL_FOLDER_INDEX_ROOT`、本机配置 `folder_index_root`、用户数据目录下的 `cache/folder-index/` 解析。它是每台电脑唯一维护的机器级缓存，不放入 Plugin 安装缓存或时间戳任务目录。

路径未配置不会导致交互页面崩溃；只有实际依赖该输入的阶段会保持待配置。路径可用性以受管 UI 服务的 Windows 身份为准；用户在另一个资源管理器窗口能访问，不代表服务会话拥有相同盘符映射或权限。不要把个人用户名、桌面绝对路径或本机盘符写回 `SKILL.md`、Python 源码或已提交的配置。

## 2. 创建隔离任务

先创建时间戳会话并启动任务配置页，再让用户确认店铺和本次图片源；不配置目标月份、商品范围或搜推采集页数。缺少这些业务值不得阻止页面启动。商品表、规则表及运行目录自动发现并只读展示；图片源可配置一个或多个，检测只读可访问性后可保存为本机默认值。搜推素材标记为“搜推高价值”全量自动采集；本分支不准备基础素材。人工图片素材清单和历史推广素材状态位于高级设置，日常执行保持为空。

交互页面与素材读取进程分离。Windows 使用受管桌面入口：

```powershell
cd .\upload-search-materials
.\scripts\start-managed-workbench.ps1 `
  -PortStart 8765 `
  -PortEnd 8795 `
  -Config ".\config\local-paths.json"
```

新任务省略 `--runs-root`，默认使用用户数据目录中的稳定 `runs/`
（`%LOCALAPPDATA%\tmall-search-materials\runs`），不得写入 Plugin 安装目录或版本缓存。
恢复已有任务时才传启动结果或恢复指令记录的精确 `--runs-root` 与 `--session`。
启动器会把当前 Plugin 根目录和运行时工作区显式传给托管子进程；不得依赖
当前工作目录或 Plugin 缓存链接推断配置位置。恢复已有任务时增加
`-Session "<精确 session_id>"`；不得按目录时间猜测会话。

用户点击“确认文件夹并加载图片”或“重试加载图片”后，页面自动启动一次性素材执行器，不需要另开终端。执行器通过现有 Explorer shell 的普通桌面令牌启动，以继承 RaiDrive、映射盘或 UNC 会话；按 `source_id + relative_path` 绑定当前电脑的根目录，处理任务后立即退出，不安装系统服务。`start-material-executor.ps1` 仅用于开发诊断。

Windows 中由 Codex 启动上述工作台时，必须复用运行环境准备阶段已经允许的固定
`start-managed-workbench.ps1` 桌面入口，直接启动，不得在每个任务或阶段再次请求批准。
当前电脑尚未允许该固定入口且宿主明确阻止启动时，才以
`SYSTEM_PERMISSION_REQUIRED` 完成一次范围精确的环境准备；不得扩大为临时 PowerShell
或通用命令授权。启动返回后核对
`launcher_runtime_identity.sid == runtime_identity.sid`，并核对登录会话一致。
`remote_drive_letters` 只用于诊断展示，不参与启动验收，也不得用历史本机配置中的
盘符阻止配置页打开。`healthy=true` 只代表 HTTP 服务可用；用户提交配置页时才检测
本次页面里最终填写的图片源。若服务运行在 Codex 沙箱账户，先停止该精确 session
的服务，再通过同一固定桌面入口恢复；不要让用户通过重复点击图片按钮解决身份错误。

它返回精确 `session_id`、PID、端口、日志、URL、Windows SID、登录会话、交互桌面状态和可见网络盘。Agent 优先用 Codex 内置浏览器打开 URL；恢复任务时增加 `-Session "<session-id>"`，不得猜测最新目录。此后每次提醒用户登录、配置、选择、审查、确认或恢复时，Agent 都必须先从启动结果或该精确 session 的 `ui-status` 读取当前 `url`，并在同一条消息中提供可点击链接；不得硬编码端口或复用旧 URL。只有明确不访问任何本机素材资源的只读流程才使用 `scripts\start-ui.cmd`；`tmall-materials interact` 只保留为会持续占用终端的前台调试入口。

恢复已有任务时：

```powershell
cd .\upload-search-materials
.\scripts\start-managed-workbench.ps1 `
  -RunsRoot "<项目内精确 runs_root>" `
  -Session "<精确 session_id>" `
  -PortStart 8765 `
  -PortEnd 8795 `
  -Config ".\config\local-paths.json"
```

该入口只接受项目内 runs root、格式合法的精确 session、项目 `config/` 下的 JSON 和最多 101 个本机端口；不能传任意命令、外部监听地址、上传或发布参数。服务始终监听 `127.0.0.1`。启动后可以查看 `visible_network_drives` 诊断，但不得据此假定历史图片源就是本次配置，也不得阻止用户进入配置页。用户提交配置页时先检测本次最终填写的路径；通过后才生成 setup handoff。若原服务属于另一个桌面身份，桌面入口会先安全停止其 ownership token 对应服务再启动新服务。路径检测、picker、索引和采集 Worker 在读取素材前校验同一 SID 与登录会话，失败返回 `LOCAL_RESOURCE_IDENTITY_MISMATCH`，保留文件夹决定并允许同一 session 重试。

图片源检测可保留 canonical UNC 作为当前电脑的回退建议。映射盘缺失时仅在已配置该 UNC 的情况下做有界元数据检测；系统不会自动映射网络盘、请求密码或绕过 Windows/NAS 权限。

工作台后台接收 setup handoff 后，在当前 `runs/<session_id>/` 中创建输入快照和自动采集目录：

- `inputs/`：复制商品表、规则表并生成输入清单与 SHA-256。
- `collected/promotion/`：保存 `promotion-material-status.csv`、checkpoint、分页起点/翻页/末页证据和页面证据。
- `folder-review/`、`assets/`、`dry-run/`、`approval/`、`results/`：只保存当前任务的候选、决定和结果。

共享文件夹索引数据库、团队 `folders.csv` 快照、选择器与策略配置不重复复制；任务只保存按当前商品快照和匹配器即时生成的 `folder-candidates.csv`、审查数据和用户决定。NAS 原图只读且不复制。页面保存 handoff；工作台后台在同一受管服务中启动确定性处理器，Codex 不在正常流程中领取或运行等价 CLI。

后台调度器只能在 setup handoff 通过 `session_id`、`stage_id`、`revision` 和 `input_sha256` 校验后读取店铺与图片源并继续。用户在聊天中主动说出的值不代替页面提交；页面未提交时保持等待，不提前运行后续动作。

编辑时页面停止输入约 1 秒会自动保存草稿。草稿只写当前任务，不通知工作台后台；“保存为本机配置”只更新当前电脑默认图片源；“提交给工作台”才生成可认领 handoff。提交后表单冻结。后台调度器尚未认领时可撤回；进入处理中后不可撤回或覆盖。阶段目录的 `revisions/<revision>/` 保留输入和正式 handoff 快照。

恢复历史任务时只需使用原 `session_id` 打开页面。页面应直接进入 `session.current_stage`，并在首次显示时同步全部阶段状态；如果停留在已完成阶段，保存和提交会保持锁定并提供“进入当前阶段”。仅打开或刷新页面不会产生新 revision。若用户在自动保存进行时点击“提交给工作台”，页面显示提交已排队，保存成功后再提交最新值；保存失败会保留编辑内容并暂停提交，切换阶段会取消旧阶段的排队操作。

## 3. 文件夹索引优先

团队索引只长期保存目录元数据，不打开或哈希图片，也不长期保存商品匹配结果。本机把已校验的不可变 `folders.csv` 快照缓存到 `folder_index_root/team-cache`；每个任务提交商品后再按当前商品表和当前匹配器即时生成候选。首次不存在时执行：

```powershell
uv run tmall-materials index-folders `
  --products "<商品总表.csv>" `
  --root "model_nas=Y:\视觉部\1-模特图" `
  --root "xhs_taobao=Z:\浙江酷趣\运营中心\营销板块\小红书koc置换&淘宝买家秀\优质买家秀" `
  --root "xhs_buyer=Z:\浙江酷趣\运营中心\营销板块\小红书koc置换&买家秀\优质买家秀" `
  --output "<共享文件夹索引目录>"
```

维护端检查原始索引的 `folder-scan-summary.json` 与 SQLite 完整性，并把完整结果发布为不可变团队 `folders.csv` 快照。目录新增、删除或改名后使用相同参数和输出目录加 `--refresh`；它会重新遍历目录树发现差异，但在同一 SQLite 中增量更新 active/inactive 状态。普通上传任务不运行 `--rematch-only`，而是在任务内直接读取缓存文件夹快照重新匹配。不得因为单次任务等待较久就改用任意历史候选 CSV；只能使用当前本机配置指向且身份校验通过的团队快照或最后一次校验通过的 `team-cache`。

运行中读取同目录的 `folder-index-progress.json`：其中包含 `status`、`phase`、
`source_system`、`current_relative_path`、发现/命中/错误计数、PID、心跳和耗时。
维护端只有在 SQLite 与扫描摘要完整时才允许发布；上传工作台以有效团队快照或本机 `team-cache` 为“可复用”依据，不要求全局候选 CSV。同一目录的
`.folder-index.lock` 强制单写；`FOLDER_INDEX_BUSY` 表示已有活跃构建，应观察既有进度，
不得并发启动第二个构建。进程异常退出会保留 `active_scan` 目录队列检查点，使用原参数
加 `--resume` 精确继续；商品表或 roots 身份变化时拒绝续跑。

已知变化范围时可避免全树扫描：`--refresh-source SOURCE` 刷新一个完整来源，
`--refresh-prefix SOURCE=RELATIVE_PATH` 只刷新一个稳定相对路径子树；二者均可重复。
定向刷新只会在已完整扫描的范围内标记 inactive，不影响其他来源或兄弟子树；仍应
定期执行全量 `--refresh` 作为审计。商品表、名称或货号规则变化时直接使用新的商品表
执行 `--rematch-only`；roots 身份与商品匹配身份分离，因此不会重新访问 NAS。

目录枚举采用 60 秒无进展超时：只要目录项仍持续返回，大目录可继续扫描；若单个
SMB/NAS 目录的 `readdir` 或元数据读取停止响应，该目录记录
`FOLDER_ENUMERATION_TIMEOUT`，已发现目录立即提交检查点并停止继续调度本轮目录 I/O，
从而避免为多个阻塞目录累计不可取消的线程。此时命令
返回非零且摘要为 `complete=false`；不得把部分索引解释为完整结果，应检查摘要中的
稳定 `source_system + relative_path`，恢复共享后对同一索引运行 `--refresh`。

第二阶段选择商品并正式提交后，工作台后台调度器自动调用 `process-product-selection`，携带精确 revision 与 `input_sha256`。以下等价 CLI 只供历史兼容和稳定异常诊断：

```powershell
.\.venv\Scripts\tmall-materials.exe process-product-selection `
  --runs-root "<runs-root>" `
  --session "<session-id>"
```

该入口内部完成精确 handoff 校验与领取，从 `team-cache/**/folders.csv` 使用当前任务商品快照和当前匹配器重新生成候选，然后完成文件夹审查数据生成、结果写入和阶段推进。任务摘要绑定商品 SHA-256、匹配器版本和来源 snapshot ID。全局 `folder_index_root/folder-candidates.csv` 即使存在也不会读取。日常流程不得再手工串联 `snapshot-folder-candidates`、`prepare-folder-review` 或直接写 `result.json`。任务目录不保存 `folder-index.sqlite3`。团队快照缺失或损坏时，入口在 `02-completeness/product-selection-diagnostic.json` 写入失败阶段、稳定原因码、异常、输入身份、相关文件状态、traceback 和恢复动作；Codex 修复或同步团队快照后重跑同一入口。共享索引过期时只有在用户明确要求更新指定来源后才执行增量刷新和发布，而不是新建任务级索引。

`resume-session`、`listen-handoff`、旧兼容 `wait-handoff` 和页面“恢复过期处理”不得先领取 completeness
handoff；它们只返回 `SPECIALIZED_PROCESSOR_REQUIRED` 和上述唯一入口。该入口自己
负责首次领取、过期租约回收以及 blocked 后恢复，避免同一交接被通用恢复流程和专用
处理器重复领取。

候选只按文件夹自身名称匹配，父目录命中不会让 `KV`、`合成`、`1` 等普通子目录重复成为候选。包含完整 SKU 的组合名称（例如 `KQ23002-商品名`）可视为货号命中；同货号异名、名称候选和主副链接关系仍需用户确认。确认文件夹归属后，才按需枚举其中图片、读取尺寸并计算 SHA-256。

文件夹名完整包含商品基础名称时形成名称候选。标题包含 `/`、`／`、`、` 或 `|` 时只按字面拆分并检查完整片段包含关系，不拼接公共前缀或生成推导名称；不少于 5 字的完整片段默认采用，3–4 字完整短片段默认排除，少于 3 字忽略。

### 3.1 生成文件夹归属审查

上述唯一入口同时把候选转换成素材匹配页面可读取的数据；这里不再运行第二条命令。

入口自动把 `folder-review.json` 写入素材匹配阶段的 `review-context.json.data`，并设置 `workflow_step=folder_review`。页面先展示目录元数据，再由独立本机请求异步回填每个文件夹的原始递归素材数；计数期间显示“素材数统计中”，不可访问时显示“素材数未知”，计数过程不打开、解码或哈希图片。页面只提供“采用 / 排除该文件夹”：商品 ID、完整货号和完整基础名称候选默认采用；50% 连续名称粗略候选默认排除。“确认文件夹并加载图片”必须放在文件夹列表下方、候选图片上方，是独立的本机固定操作；点击后直接调用本地 `prepare-gallery` API 保存二态决定并启动 gallery job，不经过通用阶段提交处理器。这一步不创建 handoff、不等待 Codex，也不需要回复“已提交”。文件夹审查和加载中隐藏页面底部提交按钮；进入选图后才显示原位置的“确认选图并提交给工作台”。仅加载或刷新页面不得写草稿。素材匹配返回 `needs_user_input`/`blocked` 时必须保留 `review-context.json`。历史任务需要一次性精确文件夹查询时仍可单独使用 `prepare-folder-review --exact-folder`，但它不得成为新任务的正常阶段推进路径。

只有最终采用的文件夹可进入按需图片枚举，`rejected` 不得读取。候选优先来自商品 ID、完整货号或去除末尾“（主）/（副）”后的基础名称；最长公共连续部分覆盖基础名称至少 50% 且不少于 5 个字符时，可作为默认排除的粗略候选展示。用户仍应核对同货号异名、主副链接和历史目录，并排除错误来源。

组合标题的拆分片段命中在完整基础名称之后、50% 粗略候选之前判定；只要文件夹名包含整个片段即可，不对短片段补全系列前缀。页面将 `split_name_candidate` 显示为“完整名称片段命中”，将 `short_split_name_candidate` 显示为“短名称片段候选”。

若商品标题与素材文件夹名称不同，但用户明确提供了完整文件夹名，可在本次任务追加 `--exact-folder "PRODUCT_ID=FOLDER_NAME"`。例如 `--exact-folder "886506466908=分龄成长太阳镜"`。该参数按规范化后的完整文件夹名精确相等，只产生本次任务的 `exact_folder_query` 候选；它不是别名，不写入共享配置，也不自动影响后续任务。

## 3.2 默认：确认文件夹后按需准备候选

正常的 1–3 商品试跑不建立全量图片索引。用户确认文件夹后，页面把当前 `source_id + relative_path` 决定写入 `input.json`、创建 `queued` gallery job，并自动请求 Windows 桌面启动一次性素材执行器。执行器把 source ID 绑定到本机盘符或 UNC 根目录后生成任务级候选清单；不得直接继承 UI/Codex 令牌，而必须委托 Explorer 启动。页面服务和 Codex 都不直接读取 NAS。加载失败时在页面点击“重试加载图片”，页面会自动启动新的 attempt；旧 attempt 保留供审计。

用户完成选图并点击“确认选图并提交给工作台”后，后台调度器只调用最终素材正式处理器：

```powershell
uv run tmall-materials process-final-material-handoff `
  --runs-root "<runs-root>" `
  --session "<session-id>"
```

本地 Worker 只遍历当前商品采用中的文件夹，候选上限按商品独立计算。每个商品发现 1–100 张唯一图片路径时全部进入候选池；超过 100 张时，先为每个非空采用文件夹分配 1 个基础名额，再按扣除基础名额后的剩余唯一图片数比例分配余量。例如单商品采用 61 个非空文件夹且发现总数超过 100 张时，先分配 61 张，再按比例分配剩余 39 张。若非空文件夹超过 100 个，候选仍限制为 100 张，确定性决定得到名额的文件夹，并返回 `FOLDER_COVERAGE_LIMIT_EXCEEDED`；空文件夹、完全重叠文件夹和未得到名额的文件夹仍保留零分配审计行。系统使用任务 ID、商品 ID、稳定文件夹 ID 和策略版本执行任务内稳定伪随机抽样；同一任务输入不变时重跑结果一致，新任务可重新抽样。只对抽中的候选读取尺寸、校验、计算 SHA-256 并生成预览，不建立全量图片数据库。同一商品内 SHA-256 相同的内容只保留一张；因此“计划检查 300 张、成功检查 300 张、内容重复 14 张、最终候选 286 张”是正常且可解释的结果。运行中页面显示发现路径和检查 X/Y，并分别显示失败、重复、最终候选和待处理；完成结果必须覆盖最后一次运行消息，刷新后从完成快照恢复相同数字。旧任务没有新字段时显示“历史进度口径”，不能猜测重复数。输出中的 `candidate_strategy=proportional_task_sample`、`candidate_strategy_version`、`sampling_identity_sha256`、`folder_allocations`、`scan_summary` 和逐商品 `requirements` 用于解释发现数、各目录名额、覆盖状态和候选数。

第三阶段只保留单一“采用”操作，候选首次显示时全部保持未选中；采用文件夹或进入候选池都不能自动采用图片。用户勾选即确认该图片可用于本次发布，前端同步写入采用与授权状态，后端按采用项重新生成授权记录。提交时每个商品至少需要 3 张可读、策略兼容且源 SHA-256 唯一的图片；除此之外不设置低于每商品 100 张候选池的采用上限。用户选中的图片只记录稳定 `selection_order`，不提前写入坑位分组。第五阶段按 `K=min(后台缺失坑位,floor(有效唯一采用图片数/3))` 决定坑位数，并最多使用 `min(有效唯一采用图片数,K*9)` 张图片；每个图文坑位使用 3–9 张、同坑只能使用 3:4 或 1:1 且不得混合比例，超出容量的采用图片保留在未使用候选池，未在本次填充的后台空坑位继续保留为缺失。`confirmed-gallery.json` 只属于当前时间戳任务，不作为跨任务共享索引。候选准备阶段同步生成最长边不超过 640 像素的 JPEG 到 `03-asset-matching/preview-cache/`；Web 页面只传本地预览，原图保持只读并留给最终上传。旧任务首次请求时按需补建预览。Web 预览仍只允许结果中列出的文件，并额外校验原图位于已确认文件夹下，以兼容配置中的 Y/Z 映射盘与 Agent 实际解析到的 UNC 路径不同。预览响应使用短时私有缓存，勾选时只更新当前卡片和决定，不重新创建整个图片网格。

每条新候选同时记录 `folder_id` 和 `folder_path`。文件夹改为排除后，页面立即移除对应候选并同步取消其已选和授权记录，显示取消数量，随后重新计算候选数和分页；重新采用只恢复候选，不恢复旧选择。后端保存和提交时再次按最终文件夹决定过滤矛盾图片。历史候选缺少 `folder_id` 时按规范化后的最长父目录路径归属，无法唯一归属时保留并显示“历史候选未关联文件夹”。

## 3.3 可选：分片增量图片索引

先校验商品表，再对三个声明的图片来源建立只读索引。只有读取失败、缺少/重复必需表头等 schema 或批次级错误、以及空表会阻断整批。缺商品 ID、非法商品 ID、重复商品 ID 是行级 blocked：`scan-summary.json` 保留对应 source row 和 reason codes，这些行不参与 ID、SKU 或名称匹配，其余有效行继续。非空但全部行为行级 blocked 时仍可完成纯 metadata 索引。以下命令使用占位商品表与隔离输出目录，仅供复制后替换；它们不表示已经扫描真实 NAS。raw indexing 不需要月份、店铺或坑位。

首次 new：

```powershell
uv run tmall-materials index-assets `
  --products "<商品总表.csv>" `
  --root "model_nas=Y:\视觉部\1-模特图" `
  --root "xhs_taobao=Z:\浙江酷趣\运营中心\营销板块\小红书koc置换&淘宝买家秀\优质买家秀" `
  --root "xhs_buyer=Z:\浙江酷趣\运营中心\营销板块\小红书koc置换&买家秀\优质买家秀" `
  --output "<隔离输出目录>\asset-index" `
  --partition-depth 2 `
  --checkpoint-size 1000
```

中断、已有持久 checkpoint 或部分失败后，在同一商品表、roots、output 和参数上恢复：

```powershell
uv run tmall-materials index-assets `
  --products "<商品总表.csv>" `
  --root "model_nas=Y:\视觉部\1-模特图" `
  --root "xhs_taobao=Z:\浙江酷趣\运营中心\营销板块\小红书koc置换&淘宝买家秀\优质买家秀" `
  --root "xhs_buyer=Z:\浙江酷趣\运营中心\营销板块\小红书koc置换&买家秀\优质买家秀" `
  --output "<隔离输出目录>\asset-index" `
  --partition-depth 2 `
  --checkpoint-size 1000 `
  --resume
```

图片新增、修改或删除后，在同一索引身份上刷新：

```powershell
uv run tmall-materials index-assets `
  --products "<商品总表.csv>" `
  --root "model_nas=Y:\视觉部\1-模特图" `
  --root "xhs_taobao=Z:\浙江酷趣\运营中心\营销板块\小红书koc置换&淘宝买家秀\优质买家秀" `
  --root "xhs_buyer=Z:\浙江酷趣\运营中心\营销板块\小红书koc置换&买家秀\优质买家秀" `
  --output "<隔离输出目录>\asset-index" `
  --partition-depth 2 `
  --checkpoint-size 1000 `
  --refresh
```

每次运行后检查 `<隔离输出目录>\asset-index\scan-summary.json` 和 `match-candidates.csv`；`asset-index.sqlite3` 保存 checkpoint 与 active/inactive 状态。`scan-summary.json` 也包含等价的恢复命令、最小商品校验证据、本轮逐 root/partition 统计，以及 `PATH_OUTSIDE_ROOT`、`FILE_STAT_ERROR`、`FILE_INSPECTION_ERROR` 等稳定错误码。ID/SKU 精确候选仍是 `matched_unlicensed`，名称候选是 `needs_manual_confirmation`，所有候选的 `license_status` 都从 `unknown` 开始。索引器不会生成 `confirmed-assets.csv`、批准清单或生产任务，也不会 dry-run、上传或发布。

原始 NAS 图片只读，视频继续延期。同一个 `asset-index.sqlite3` 同一时刻只能有一个 Agent 或进程执行 new、`--resume`、`--refresh`，禁止并发。人工检查候选并确认逐文件授权后，才能生成或接受 `confirmed-assets.csv`，然后进入素材完整性审查、生产选择器、全量 dry-run 和 1–3 商品生产验收。

### 3.4 生成第二阶段完整度矩阵

```powershell
uv run --project .\upload-search-materials --locked tmall-materials inspect-completeness `
  --products <任务目录>\inputs\products.csv `
  --promotion-status <任务目录>\promotion\promotion-material-status.csv `
  --output <任务目录>\02-completeness\completeness-matrix.json
```

该命令只读输入，不访问图片源、不调用浏览器、不上传。检查输出中未知目标仍为 `null`，且不包含基础素材字段完整度；再将矩阵作为第二阶段的 Agent 结果写入。用户可搜索、筛选、逐项或批量选择商品，提交后从 `selected_product_ids` 读取下一阶段范围。

第二阶段商品范围严格等于“商品分类 → 搜推高价值”的全量采集结果。商品总表仅补充商品名称和货号，不扩展范围。

### 3.5 可选：从全量图片索引生成候选画廊

先完成推广素材状态扫描，再把缺失篇数与索引候选合并：

```powershell
uv run tmall-materials prepare-gallery `
  --index "<隔离输出目录>\asset-index\asset-index.sqlite3" `
  --status "<隔离输出目录>\promotion-material-status.csv" `
  --output "<隔离输出目录>\asset-gallery.json" `
  --images-per-material 3 `
  --license-decisions "<逐文件授权决定.json>"
```

`--license-decisions` 和 `--remote-fingerprints` 均为可选输入。首次审查可以不提供，让页面先展示候选。页面每批最多 30 张；候选池超过 30 张时启用“换一批”，最后一批显示实际余数。已选素材固定显示，不因换批消失。采用时系统同步生成授权确认。

把 `asset-gallery.json` 的对象作为素材匹配阶段 `result.json.data` 写入当前精确 `session_id`。页面只允许预览该结果列出的、且位于当前 `image_roots` 或当前任务已确认文件夹下的图片；保存草稿或提交后，采用结果进入 `asset_decisions`，对应授权确认由系统同步写入 `license_decisions`。

当后台已有素材只有远端素材 ID、没有图片 SHA-256 或等价内容指纹时，不传 `--remote-fingerprints`。此时输出和页面必须保持 `remote_dedupe_status=not_available`，不得声称已经排除与远端重复；生产上传前仍需人工核对。提供可信远端指纹后，已命中的候选才会被自动排除。

## 4. 阶段一提交后的受管采集

工作台启动时自动启动或恢复 CDP Chrome，并在显示阶段一业务配置前检查登录状态。
未登录时自动打开官方素材中心，业务页只显示简洁等待状态；用户在千牛原生窗口完成
扫码、短信、验证码或登录后自动继续，不再点击“验证当前页面”“创建本机候选”或
“重新检测”。配置提交后，处理器自动复核生产 profile、当前 DOM、店铺、素材中心和
图片源。技术异常写入 `agent-diagnostics/current.json`，Codex 使用下面的统一入口读取：

```powershell
.\.venv\Scripts\python.exe -m upload_search_materials.cli diagnose-session `
  --runs-root "<精确 runs_root>" `
  --session "<精确 session_id>"
```

诊断中包含原因、证据、责任边界和同一幂等处理器的重试命令。用户只处理扫码、验证码、
账号切换或业务配置；不得要求用户修复选择器、执行命令或重新填写已保存的业务数据。
本机配置保存到用户数据目录的 `config/runtime.json`，旧版项目内
`config/local-paths.json` 仅作兼容读取；不能使用仓库示例作为生产配置。

真实页面验证通过的生产选择器会原子安装到用户数据目录
`config/selectors.local.yaml`，`config/runtime.json` 只引用这个稳定副本。开发仓库、
Plugin 安装目录和版本缓存中的原文件都不是长期配置位置；升级 Plugin 不会覆盖稳定副本。
显式传入 `process-setup --selectors <文件>` 时也先完成同样的校验与安装，再把稳定路径
转发给已经运行的工作台服务。

若选择器缺失/无效或离线运行环境预检失败，启动器必须把 setup 写成
`needs_user_input`，生成 `agent-diagnostics/current.json` 并释放当前 processing claim。
出现“processing 但没有 result”的状态属于启动器故障，不应要求用户重新提交业务配置。
启动器的 `--project-root` 是内部兼容参数，可能从普通 `--help` 隐藏；判断版本能力时应
实际解析该参数或读取 `environment-status`，不能只凭帮助文本断言 CLI 过旧。

提交阶段一后执行：

```powershell
.\.venv\Scripts\python.exe -m upload_search_materials.cli process-setup `
  --runs-root "<精确 runs_root>" `
  --session "<精确 session_id>"
```

该命令在 readiness 通过后创建或复用 `attempt_id`，启动无可见控制台的受管 Worker，
输出 Worker/日志身份后立即返回。Worker 内部仍只调用
`supplement --scan-mode high-value`，不是第二个采集器。查看状态：

```powershell
.\.venv\Scripts\python.exe -m upload_search_materials.cli collection-status `
  --runs-root "<精确 runs_root>" `
  --session "<精确 session_id>"
```

页面和命令显示 `validating_profile`、`connecting_cdp`、`settling_popups`、
`opening_promotion`、`selecting_high_value`、`normalizing_pagination`、
`collecting_page`、`verifying_terminal`、
`writing_checkpoint`、`building_completeness` 和终态。首个 checkpoint 前只显示 phase
与 heartbeat，不显示“0 行完成”。日志位于当前 attempt 目录。重复执行同一绑定时复用
活 Worker 或完成结果，不启动第二个 Worker。

新采集复用已有 CDP 素材中心标签页时，先刷新页面并等待列表稳定，清除上一轮商品
ID 筛选。选择“搜推高价值”后读取选择框中的总数（例如“搜推高价值 262”），将其
写入分页起点证据；不带 `--max-pages` 的完整采集结束时，唯一商品数必须与该总数
一致，否则以 `HIGH_VALUE_TOTAL_MISMATCH` 保留 attempt，不发布 current 结果。

新采集即使复用已有 CDP 标签页，也必须读取可见当前页；若不是第 1 页，使用经过
当前 DOM 验证的第一页控件返回并等待页码和有序商品 ID 稳定后才能写首个
checkpoint。恢复时同样先回到第 1 页，再按照 pagination evidence 重放到最后完整页。
下一页 disabled 只是一项信号；必须同时证明当前页等于可见末页且行身份稳定，才能
发布 `current/`。无法证明时保留 attempt 并返回稳定分页原因码。

已确认归属的 Worker 退出后，页面立即显示可恢复；未知 PID、token 不匹配或进程身份
无法读取时必须等待租约过期，不得结束进程。恢复前核对 revision、input SHA、selector
SHA、店铺、attempt、CSV SHA、行数、唯一商品 ID 和最后完成页；从下一页继续。

若返回 `SELECTOR_INVALID`，先保留 checkpoint 和错误证据。只有错误可稳定复现时，
才用 Playwright 检查真实 DOM；修复现有 selector profile 或 collector、增加回归
测试，再重跑原 `process-setup`/`supplement`。不要把诊断脚本保留为第二条生产路径。

日常启动和恢复调用当前 Plugin 的 `scripts\run-plugin.cmd`，不使用 `uv run`。
`scripts/bootstrap.cmd` 是依赖同步入口，使用用户级 uv-cache 并写入
或校验环境指纹；业务代码只从当前 Plugin 加载。已有环境的采集恢复不会解析或下载包，
也不会向 Plugin 安装缓存写入数据。

## 5. 启动用户控制的 CDP 浏览器

关闭正在使用同一 profile 的 Chromium 后，以独立 profile 和仅本机监听的调试端口启动 Chrome 或 Edge。例如将实际可执行文件路径替换进下列命令：

```powershell
& "<chrome-or-edge.exe>" --remote-debugging-port=9222 --remote-debugging-address=127.0.0.1 --user-data-dir="<独立profile目录>"
```

CDP URL 为 `http://127.0.0.1:9222`。用户必须在该窗口自行登录、处理验证码/短信/扫码/风控，并确认页面可见店铺名。不要把 profile、Cookie 或登录信息放入版本库。

## 6. 导出与只读检查

所有时间使用带时区 ISO 8601，例如 `2026-07-17T10:00:00+08:00`。导出 `run-id` 使用不可重复的可读值，例如 `20260717T100000+0800-kktree-export`。

```powershell
uv run tmall-materials export --store "<精确店铺名>" --selectors "<生产selectors.yaml>" --output "<空的隔离导出目录>" --run-id "<导出run-id>" --downloaded-at "<ISO时间>" --product-status "售卖中" --report basic --cdp-url "http://127.0.0.1:9222"
```

`--report promotion|both` 目前仅作为旧版经营数据导出兼容入口；其结果不是商品素材与坑位真相源，不得据此判定完整或作为生产批准输入。搜推素材现状必须通过 `supplement` 按精确商品 ID 从实时 DOM 采集。生产 `supplement` 尚未更新到 2026-07-24 验证的新 DOM 契约前，停止在阶段 5，不进入全量 dry-run。

## 7. 首次 dry-run 与 Playwright 补采

```powershell
uv run tmall-materials run --mode dry-run --month <1-12> --store "<店铺名>" --products "<商品总表.csv>" --rules "<月度规则.csv>" --basic "<基础素材.xlsx>" --search "<搜推经营.xlsx>" --output "<首次批次目录>" --started-at "<ISO时间>"
uv run tmall-materials supplement --store "<店铺名>" --selectors "<生产selectors.yaml>" --output "<promotion-material-status.csv>" --checkpoint "<promotion-material-status.checkpoint.json>" --collected-at "<ISO时间>" --cdp-url "http://127.0.0.1:9222"
uv run tmall-materials supplement --scan-mode exact --store "<店铺名>" --selectors "<生产selectors.yaml>" --candidates "<异常商品.csv>" --output "<exact-material-status.csv>" --collected-at "<ISO时间>" --cdp-url "http://127.0.0.1:9222"
```

第一条 `supplement` 命令默认选择“商品分类 → 搜推高价值”，串行遍历全部分页并在每页后原子更新 CSV 与 checkpoint。生产流程不从任务配置读取页数，也不传 `--max-pages`；该参数只保留给显式 CLI 诊断测试。`--scan-mode recommended` 仅用于兼容旧证据。第二条仅用于异常商品的精确 ID 兜底。`--search` 现阶段仅保留经营指标兼容性，不能替代实时 `promotion-material-status.csv`；任何使用它推断搜推坑位完整性的结果均无效。

## 8. 素材、授权、文案与最终 dry-run

目录型素材只搜索 `<asset-root>/<商品ID>/`，其次 `<asset-root>/<货号>/`。`--license-status confirmed` 表示用户确认该批目录中的每个文件均已授权；若授权状态不统一，当前 CLI 不能正式发布，应保持 blocked，待接入逐文件素材清单。当前视频元数据入口也未闭合，视频任务应保持 `VIDEO_METADATA_UNAVAILABLE`。

AI 文案 JSON 使用 `<商品ID>:<坑位号>` 作为键，值包含 `title` 和 `description`；详细结构见 [data-schema.md](data-schema.md)。

```powershell
uv run tmall-materials run --mode dry-run --month <1-12> --store "<店铺名>" --products "<商品总表.csv>" --rules "<月度规则.csv>" --basic "<基础素材.xlsx>" --search "<搜推经营.xlsx>" --backend-status "<backend-material-status.csv>" --asset-root "<素材根目录>" --license-status confirmed --media-policy "<生产media-policy.yaml>" --copy-responses "<AI文案.json>" --prohibited-term "<禁用词>" --output "<最终审核批次目录>" --started-at "<ISO时间>"
```

打开 `review.html`，只选择 `ready_for_review` 的精确 task ID。

## 9. 精确批准、发布与恢复

页面授权形成持久化 handoff 后，工作台后台调度器从其验证身份调用唯一生产入口；`resume-session` 只供历史兼容和诊断，不负责领取或授权：

```powershell
.\.venv\Scripts\python.exe -m upload_search_materials.cli process-publish-authorization --runs-root "<runs-root>" --session "<session-id>"
```

该命令从本机配置解析生产 selectors 与 CDP URL，顺序完成交互任务桥接、不可变批准清单、正式发布和阶段结果写回。已有 `run.json`、`run.sqlite3` 与批准清单时进入幂等 resume，不重建任务或重置已尝试状态。禁止从仓库根目录的其他虚拟环境运行，禁止把上述动作拆成临时 Python/PowerShell 写文件命令。发布不确定时命令保存逐任务状态并停止；先按 `upload-results.json` 与远端证据处理，不得直接重发。

生产前还必须满足 [production-acceptance.md](production-acceptance.md)。示例选择器和示例媒体策略不能直接用于生产。

## 10. 交互式任务执行

新任务使用以时间戳命名的独立会话目录；恢复旧任务时必须指定原 `session_id`，不得默认选择最新目录。日常使用固定 `scripts/start-managed-workbench.ps1`；下列前台 UI 与监听命令只供开发诊断和旧版本兼容：

```powershell
.\scripts\start-ui.cmd
uv run --project .\upload-search-materials --locked tmall-materials listen-handoff --runs-root .\runs --session 20260721_143025 --stage asset_matching --segment-seconds 15
```

服务状态、恢复和停止：

```powershell
uv run --project .\upload-search-materials --locked tmall-materials ui-status --runs-root "<runs-root>" --session "<session-id>"
uv run --project .\upload-search-materials --locked tmall-materials ui-restart --runs-root "<runs-root>" --session "<session-id>"
uv run --project .\upload-search-materials --locked tmall-materials ui-stop --runs-root "<runs-root>" --session "<session-id>"
```

`.ui-service.json` 和 `logs/` 位于精确 session 目录。stop/restart 只管理健康端点返回 PID 和 ownership token 同时匹配的服务；端口被未知进程占用时选择 `8765–8795` 中的下一端口，不结束未知进程。

工作台后台按以下顺序处理每一阶段：

1. 解析精确 `session_id`。
2. 仅对新任务创建时间戳隔离目录。
3. 启动仅监听 `localhost` 的 UI。
4. 在启动恢复扫描或提交通知中取得精确当前阶段的 handoff。
5. 对照 `input.json` 验证 `session_id`、`stage_id`、`revision` 和 `input_sha256`。
6. 读取 `<stage>/decision-modes/<decision_id>.json`；不存在时按 [decision-boundaries.md](decision-boundaries.md) 使用 Skill 默认模式。
7. `manual/manual_only` 等待用户；`rules` 运行确定性规则；只有 `agent_assisted` 才发现和领取 AI 请求。
8. 将该阶段标记为 `processing`。
9. 只运行当前模式与阶段都允许的动作。
10. 写入与同一组四个值绑定的 `result.json`。
11. 停止，或明确进入下一阶段。

正常流程由托管工作台的单 session 后台调度器处理：提交事务先持久化 handoff 再立即通知队列；服务启动先检查积压，空闲时每 2 秒轻量恢复扫描。唯一处理器根据验证后的 `handoff_identity` 原子领取，旧 `listen-handoff`、`agent_wait` 和 `wait-handoff` 只保留兼容与诊断，不得由 Codex 在正常任务中调用。当前流程中，第二阶段自动排除五类商品并直接交给第三阶段素材匹配；旧任务的历史编号目录仍可读取。“上传任务确认”页保存输入并生成 `publish_authorization` handoff，HTTP 请求线程不直接写千牛；后台调度器取得身份后只调用 `process-publish-authorization`。素材变化会使旧批准失效，页面上的旧提交不授权发布新内容。

页面把阶段状态、`workflow_dispatch` 和处理结果统一投影为 `task_status`，每 2 秒显示整个任务当前阶段、业务进度、下一步动作以及已就绪、排队、处理中、完成或异常。`ui-status` 返回同一 `task_status`，因此 Codex 在启动、恢复、用户询问或异常时可以准确说明状态，但不通过终端命令持续轮询。Codex 对话结束不影响仍在运行的工作台后台；若工作台服务意外停止，使用原 `runs_root + session_id` 恢复服务即可自动扫描持久积压，不需要用户在聊天回复“已提交”。

只有“上传任务确认”阶段完成并写入逐任务上传结果时，整个任务才结束。此时工作台保留 10 分钟供用户查看结果并显示倒计时，随后自动关闭 Python 服务；session 目录、结果和审计记录继续保留。仅完成全量采集、商品选择、素材匹配、选图、坑位编排、文案或 dry-run 时都不能关闭。恢复一个已经完成的精确 session 会重新提供 10 分钟查看窗口。

### 9.1 前端不可用时的受控聊天保底

只有启动器或能力检查返回 [frontend-interaction-contract.md](frontend-interaction-contract.md) 中允许的稳定原因码，Agent 才能对 `frontend_preferred` 字段执行：

```powershell
uv run --project .\upload-search-materials --locked tmall-materials chat-fallback `
  --runs-root "<runs-root>" --session "<session-id>" --stage setup `
  --revision 0 --reason-code UI_START_FAILED `
  --reason-detail "<实际失败说明>" --values-json "<当前阶段字段.json>"
```

默认只保存草稿；`--mode submit` 必须通过完整必填字段、revision 和安全校验才生成 handoff。页面恢复后应检查带“Codex 对话保底”来源的值。空店铺、空月份、空图片源、NAS 不可访问和未登录都不是 UI 失败。
# 第四、第五阶段操作

第三阶段已提交并由工作台后台写入 `completed` 结果后：

```powershell
uv run tmall-materials prepare-image-review `
  --runs-root <runs目录> `
  --session <session_id> `
  --policy config/media-policy.example.yaml
```

打开交互页进入“图片适用性检测”。准备命令只重新检查当前第三阶段已选素材，并用任务内缓存避免重复读取；不会遍历全部共享盘。页面展示原图大小、尺寸、比例以及 1:1、3:4 两种裁剪可行性。用户只决定是否进入坑位候选，并可分别调整两个候选框；此时不生成正式派生文件。

用户提交第四阶段后，Agent须校验 handoff 的 `session_id`、`stage_id`、`revision` 和 `input_sha256`，完成结果，再运行：

```powershell
uv run tmall-materials prepare-slot-board `
  --runs-root <runs目录> `
  --session <session_id>
```

第五阶段分为三个递进子页面。第一页上方按商品分页展示第四阶段候选，下方展示唯一当前坑位草稿；默认由确定性规则生成坑位草稿，人工负责审核和调整。点击“确认坑位并进入图片裁剪”只锁定每坑 3–9 张、有序图片和唯一比例，不生成派生图。第二页按坑位显示原图尺寸、原始大小、目标比例、裁剪方式和压缩确认；用户先点击“裁剪预校验”，本地确定性处理器只对当前坑位图片生成并复核实际输出。未通过、未执行或之后修改任何图片/顺序/比例/裁剪/压缩参数时，“完成图片处理并进入文案生成”保持禁用。完成按钮只锁定已经通过的输出并自动创建 `copy_draft` 请求，同时对本次请求授予限定权限：逐坑上传首张已验证成品图、调用千牛内置 AI、读取文案并退出未发布表单。工作台后台收到该请求后直接调用固定处理器，不在聊天中再次索取“同意”或其他授权；该权限不包含填充、确认、正式上传或发布，且任一绑定图片或 SHA 变化都会使旧授权失效。第三页轮询工作台后台处理进度并逐坑回填千牛标题和描述，逐坑人工确认后只能进入 dry-run，不会触发阶段 07/08 或真实上传。

历史版本的可选 Agent 建议命令仅供旧会话兼容和开发诊断；新任务的 `copy_draft` 由工作台后台自动领取，不需要执行下列命令：

```powershell
uv run tmall-materials list-agent-requests --runs-root <runs目录> --session <session_id>
uv run tmall-materials wait-agent-request --runs-root <runs目录> --session <session_id> --timeout 30
uv run tmall-materials claim-agent-request --runs-root <runs目录> --session <session_id> --request <request_id>
uv run tmall-materials complete-agent-request --runs-root <runs目录> --session <session_id> --request <request_id> --response <response.json>
uv run tmall-materials cancel-agent-request --runs-root <runs目录> --session <session_id> --request <request_id>
```

请求优先读取当前任务第三/第四阶段缓存，最终只含请求目录缩略图。等待必须有界；
未领取、超时、超预算、缩略图不可用或响应无效时保留当前合法草稿，直接继续规则
方案或人工编排。AI 响应显示后必须由用户“采用”；采用只写坑位草稿，用户仍须
点击“确认计划并处理图片”才会生成派生文件。

Agent 对每张缩略图分析前调用同源 SHA、provider、模型和 schema 绑定的
`read_analysis_cache`，缺失时分析并用 `write_analysis_cache` 写入；用户点击
“换一套”时复用未变化图片的分析，只重新组合方案。缓存只减少重复分析，不授权
自动采用或处理图片。

两种第五阶段入口，共同编辑同一份活动草稿：

1. **AI 编排坑位**：显式创建请求 → Agent 有界发现、领取并回写 → 合法结果载入
   唯一当前草稿 → 用户继续增删坑位、加删图片或调序 → 确认坑位 → 在第二页完成
   可视化裁剪/压缩 → 校验派生图 → 在第三页生成并确认文案。
2. **人工添加坑位**：创建空坑位 → 展开坑位内“添加候选图片”组件 → 从商品级
   共享候选池加图并排序 → 保存不完整草稿 → 达到每坑 3–9 张后确认 → 在第二页
   完成可视化裁剪/压缩 → 校验派生图 → 在第三页生成并确认文案。

同一商品的所有候选组件派生自同一共享池。图片加入一个坑位后立即从其他坑位
候选组件消失，移出或删除坑位后重新出现；每批最多显示 30 张。新流程没有规则
入口，也不会在 AI 失败时自动创建或采用规则草稿。

任何路径恢复时都复用精确 session，并先读取 current stage、revision、decision
mode 和现有草稿。AI 超时、取消、无效或过期时不清空草稿，切回规则或人工继续；
页面重载和轮询不自动创建或采用请求。

排障：

- 图片卡片出现但原图破图：确认交互服务进程能够只读访问配置中的共享盘路径；策略准备成功不代表预览服务进程拥有同样权限。
- 页面显示结果过期：重新处理第三或第四阶段当前 handoff，再重新运行对应 prepare 命令，不得复用旧 revision。
- `IMAGE_SIZE_EXCEEDED`：第四阶段记录“需要压缩”；第五阶段确认坑位后才生成压缩输出。不可用时返回 `COMPRESSION_UNAVAILABLE`。
- `IMAGE_SIZE_BELOW_MINIMUM`：原图小于 200KiB，停止使用该图；不得通过填充文件绕过。
- `OUTPUT_DIMENSIONS_BELOW_MINIMUM`：所有目标比例都无法得到宽高至少 720px 的输出，返回第三阶段更换素材。
- `COMPRESSION_TARGET_UNREACHABLE`：在允许质量与最小尺寸内仍无法压到 20MiB，排除或更换素材。
- 所有正式裁剪/压缩输出只应出现在 `<session>/05-slots-copy/derived/`。验收前后核对源图大小、mtime 和 SHA-256。
## 第五阶段恢复与回滚

- `UPLOAD_SEARCH_MATERIALS_AI_DEFAULT_SLOT_PLANNING=1` 仅保留为历史兼容开关；新第五阶段始终只显示 AI 与人工入口。设为 `0` 不会创建规则入口，也不会删除历史请求、缓存或草稿。
- 页面首次进入会幂等创建 `slot_plan_with_analysis`；刷新只查询同 revision 请求。待领取且没有活动 Agent 时才复制页面恢复提示词。
- Agent 领取后应读取精确 request ID，生成 `asset_analysis / clusters / slot_plan`，再原子完成请求。不得直接修改共享盘或确认坑位。
- AI 失败、超时或非法响应时保留现有草稿；无草稿时保持空状态。用户可以重试 AI
  或人工添加坑位。用户开始人工编辑后旧请求会被取消/过期。
- 坑位确认前不生成派生图片。处理页使用 `3:4 / 1:1` 比例按钮与可拖动、可缩放、
  锁定比例的可视化裁剪框；页面不要求用户输入归一化坐标。单图裁剪变化只使该图
  输出与所属坑位文案失效，其他坑位文案保持有效。
- 图片完成按钮自动创建绑定 session、计划 revision、最终输出 SHA-256、坑位与首图清单的 `copy_draft` 限定授权请求；不得在聊天中追加“同意上传种子图”等二次确认。工作台后台调度器调用
  `process-copy-request`；该入口逐坑复用 `browser/qianniu_copy.py` 并写入 `progress.json`，不得在 HTTP 请求线程中同步运行 Playwright，也不得另写一套浏览器脚本。文案确认之前不得提交第五阶段。
# 后台恢复与旧监听兼容

新任务不运行监听命令。以下 15 秒等待片段只用于旧版本兼容或开发诊断：

```powershell
tmall-materials listen-handoff --runs-root "<runs-root>" --session "<session-id>" --stage "<stage-id>" --segment-seconds 15
```

兼容命令无论页面在命令之前还是之后正式提交，都会返回相同的验证 handoff 身份，但不会提前认领。命令在一个等待回合内续租同一最长
30 秒的 `agent_wait`，不重置 `started_at` 或总预算。setup 默认总预算为 2 分钟；
复杂人工阶段可使用更长配置值。正常总预算结束会清理 wait 并返回聊天恢复提示，批准和
生产确认超时永远不会形成授权。

旧会话诊断时可显式解析持久化状态：

```powershell
tmall-materials resume-session --runs-root "<runs-root>" --session "<session-id>" --ack "已提交"
```

必须显式提供已绑定的 session；不得按 runs 目录时间猜测。该命令不属于新任务恢复路径，只解析
completed/processing/recoverable/ready/blocked/draft；ready 时返回 `handoff_identity` 和唯一处理器但不领取，不会批准、
上传或发布。

采集 attempt 的私有写入位于
`collected/promotion/attempts/a-<attempt-prefix>/`，成功发布位于
`collected/promotion/current/`；旧共享 CSV/checkpoint 只是兼容投影。
