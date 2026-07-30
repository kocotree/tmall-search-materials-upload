# 首次运行指南

## 新任务第三至第五阶段

素材选择页保存草稿不会运行预检或编排。正式提交时只复查采用图片，写入
`selected-asset-preflight.json` 并同步创建确定性 `current-slot-plan.json`。
成功后不领取 `image_review` 或坑位 Agent request；直接打开 `slots_copy` 检查
自动分组并按需人工调整。确认精确 plan revision 后第一页才展开裁剪和压缩；
实际输出复核通过后，第二页才允许创建 `copy_draft` 文案请求。

`prepare-image-review`、`prepare-slot-board` 和坑位类 Agent request 命令仅供
历史任务恢复，不用于新任务。

兼容的旧 suitability snapshot 可显式迁移，原阶段文件不会删除：

```powershell
uv run tmall-materials migrate-deterministic-selection --runs-root <runs目录> --session <session_id>
```

## 1. 安装与检查

在本 skill 目录使用 `uv` 管理 Python 3.11、锁文件和虚拟环境：

```powershell
.\scripts\bootstrap.cmd -Mirror official -WithTests
$quickValidate = Join-Path $env:USERPROFILE ".codex\skills\.system\skill-creator\scripts\quick_validate.py"
uv run python -X utf8 $quickValidate .
```

`uv` 根据 `.python-version` 使用 Python 3.11，并依据 `uv.lock` 创建或同步 `.venv`。首次同步需要访问 Python 包索引；后续验收使用 `uv lock --check` 检查锁文件是否与 `pyproject.toml` 一致。不要向系统 Python 或 Conda 基础环境直接安装本项目依赖。

环境检查是启动配置页前唯一允许的阻断。若 `.venv\Scripts\tmall-materials.exe` 已可执行，可以直接启动页面；否则准备 `uv` 并同步环境。此时不得要求用户在聊天中提供店铺名、月份或图片根目录，也不得把这些阶段 1 字段与 `uv` 安装合并成一个前置问题。

如果电脑已有兼容的 Python 3.11 或 3.12，可显式传入路径，避免 `uv` 自动下载解释器：

```powershell
.\scripts\bootstrap.cmd -Python "<python.exe>" -Mirror official -WithTests
```

网络受限时可把 `-Mirror` 改为 `tuna`、`aliyun` 或 `tencent`。镜像选择会影响 `uv.lock` 的依赖来源；只有明确切换锁文件来源时才传 `-UpdateLock`，并在提交前复核锁文件差异。公开仓库默认保留 `official`，不在源码或配置中保存镜像凭证。`.cmd` 入口只为当前子进程绕过 PowerShell 脚本执行限制，不修改系统执行策略。脚本使用项目内 `.uv-cache`、系统证书和 `--no-managed-python`，避免用户缓存权限及解释器自动下载造成的长时间等待。

### 1.1 每台电脑只配置一次路径

首次启动后可直接在任务配置页新增、删除和检测图片源，并点击“保存为本机配置”。页面支持 1–50 个“来源名称 + 根路径”。保存时每个来源使用稳定 `source_id + label`，实际盘符或 UNC 是当前电脑的本机绑定；旧的 `label + path` 文件读取时自动补 source ID。另一台电脑可把同一 source ID 绑定到不同盘符或 UNC，无需修改项目文件。也可以复制 `config/local-paths.example.json` 为 `config/local-paths.json` 后手工修改。`local-paths.json` 已被 Git 忽略，不会影响其他电脑，也不得保存 NAS 凭据、目录清单或图片内容。

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

项目内输入默认无需配置：程序先定位包含 `docs/` 与 `upload-search-materials/` 的项目根目录，再在 `docs/` 中按以下受控模式查找：

- 商品表：`天猫商品信息表*产品数据表*数据总表.csv`
- 规则表：`天猫商品信息表*每月推品规则*Grid View.csv`

只有唯一命中才会自动采用。没有命中时页面显示“未找到”；多个命中时显示歧义并等待显式配置，不按时间或文件名猜测。共享盘不执行全盘扫描；盘符或 UNC 路径只从本机配置读取。

解析优先级与覆盖入口：

1. `tmall-materials interact --config <配置.json>` 指定配置文件。
2. `TMALL_CONFIG_FILE` 指定配置文件；`TMALL_WORKSPACE_ROOT`、`TMALL_PRODUCTS_CSV`、`TMALL_RULES_CSV`、`TMALL_RUNS_ROOT` 可单项覆盖。
3. 项目内 `upload-search-materials/config/local-paths.json`。
4. 项目结构自动发现；运行目录默认使用 `<项目根目录>/runs/`。

共享文件夹索引路径按 `TMALL_FOLDER_INDEX_ROOT`、本机配置 `folder_index_root`、`<项目根目录>/.local-cache/folder-index/` 解析。它是每台电脑唯一维护的机器级缓存，不放入时间戳任务目录。

路径未配置不会导致交互页面崩溃；只有实际依赖该输入的阶段会保持待配置。路径可用性以受管 UI 服务的 Windows 身份为准；用户在另一个资源管理器窗口能访问，不代表服务会话拥有相同盘符映射或权限。不要把个人用户名、桌面绝对路径或本机盘符写回 `SKILL.md`、Python 源码或已提交的配置。

## 2. 创建隔离任务

先创建时间戳会话并启动任务配置页，再让用户确认店铺、月份和本次图片源；不配置商品范围或搜推采集页数。缺少这些业务值不得阻止页面启动。商品表、规则表及运行目录自动发现并只读展示；图片源可配置一个或多个，检测只读可访问性后可保存为本机默认值。搜推素材标记为“搜推高价值”全量自动采集；本分支不准备基础素材。人工图片素材清单和历史推广素材状态位于高级设置，日常执行保持为空。

日常唯一推荐入口是：

```powershell
cd .\upload-search-materials
.\scripts\start-ui.cmd
```

它返回精确 `session_id`、PID、端口、日志和 URL 的 JSON 后立即结束。Agent 优先用 Codex 内置浏览器打开 URL；只有内置浏览器不可用时才传 `-OpenSystemBrowser`。恢复任务时使用 `-Session "<session-id>" -RunsRoot "<runs-root>"`，不得猜测最新目录。`tmall-materials interact` 只保留为会持续占用终端的前台调试入口。

当任务需要映射盘、NAS 或原生目录窗口，且普通 Codex 启动上下文看不到用户桌面的盘符时，先取得页面显示的精确 session，再由用户授权运行固定桌面入口：

```powershell
cd .\upload-search-materials
.\scripts\start-managed-workbench.ps1 `
  -RunsRoot "<项目内精确 runs_root>" `
  -Session "<精确 session_id>" `
  -PortStart 8765 `
  -PortEnd 8795 `
  -Config ".\config\local-paths.json"
```

该入口只接受项目内 runs root、格式合法的精确 session、项目 `config/` 下的 JSON 和最多 101 个本机端口；不能传任意命令、外部监听地址、上传或发布参数。服务始终监听 `127.0.0.1`。启动状态记录服务和启动者的 Windows SID、登录会话 ID、交互桌面可用性及可见网络盘盘符，不记录共享内容。若原服务属于另一可见盘上下文，桌面入口会先安全停止其 ownership token 对应服务再启动新服务。路径检测、picker、索引和采集 Worker 在读取素材前校验同一身份，失败返回 `LOCAL_RESOURCE_IDENTITY_MISMATCH`。

图片源检测可保留 canonical UNC 作为当前电脑的回退建议。映射盘缺失时仅在已配置该 UNC 的情况下做有界元数据检测；系统不会自动映射网络盘、请求密码或绕过 Windows/NAS 权限。

Agent 接收 setup handoff 后，在当前 `runs/<session_id>/` 中创建输入快照和自动采集目录：

- `inputs/`：复制商品表、规则表并生成输入清单与 SHA-256。
- `collected/promotion/`：保存 `promotion-material-status.csv`、checkpoint 和页面证据。
- `folder-review/`、`assets/`、`dry-run/`、`approval/`、`results/`：只保存当前任务的候选、决定和结果。

共享文件夹索引数据库、选择器与策略配置不重复复制；任务只保存当前商品的 `folder-candidates.csv`、审查数据和用户决定。NAS 原图只读且不复制。页面只保存 handoff，不直接启动 Playwright；由收到 handoff 的 Agent 执行复制、导出和采集。

Agent 只能在 setup handoff 通过 `session_id`、`stage_id`、`revision` 和 `input_sha256` 校验后读取店铺、月份与图片源并继续。用户在聊天中主动说出的值不代替页面提交；页面未提交时保持等待，不提前运行后续动作。

编辑时页面停止输入约 1 秒会自动保存草稿。草稿只写当前任务，不通知 Agent；“保存为本机配置”只更新当前电脑默认图片源；“提交给 Agent”才生成可认领 handoff。提交后表单冻结。Agent 尚未认领时可撤回；进入处理中后不可撤回或覆盖。阶段目录的 `revisions/<revision>/` 保留输入和正式 handoff 快照。

恢复历史任务时只需使用原 `session_id` 打开页面。页面应直接进入 `session.current_stage`，并在首次显示时同步全部阶段状态；如果停留在已完成阶段，保存和提交会保持锁定并提供“进入当前阶段”。仅打开或刷新页面不会产生新 revision。若用户在自动保存进行时点击“提交给 Agent”，页面显示提交已排队，保存成功后再提交最新值；保存失败会保留编辑内容并暂停提交，切换阶段会取消旧阶段的排队操作。

## 3. 文件夹索引优先

每台电脑默认只建立并维护一份共享文件夹级索引，只保存目录名称、完整路径和商品匹配，不打开或哈希图片。首次不存在时执行：

```powershell
uv run tmall-materials index-folders `
  --products "<商品总表.csv>" `
  --root "model_nas=Y:\视觉部\1-模特图" `
  --root "xhs_taobao=Z:\浙江酷趣\运营中心\营销板块\小红书koc置换&淘宝买家秀\优质买家秀" `
  --root "xhs_buyer=Z:\浙江酷趣\运营中心\营销板块\小红书koc置换&买家秀\优质买家秀" `
  --output "<共享文件夹索引目录>"
```

检查共享目录中的 `folder-scan-summary.json` 和 `folder-candidates.csv`。目录新增、删除或改名后使用相同参数和输出目录加 `--refresh`；它会重新遍历目录树发现差异，但在同一 SQLite 中增量更新 active/inactive 状态。只调整名称或货号匹配规则时加 `--rematch-only`，后者只读取本地 SQLite，不重新遍历 NAS。不得因为单次任务等待较久就改用任意历史任务索引；只能使用当前本机配置指向且身份校验通过的共享索引。

第二阶段选择商品后，只把对应候选复制到当前任务：

```powershell
uv run tmall-materials snapshot-folder-candidates `
  --candidates "<共享文件夹索引目录>\folder-candidates.csv" `
  --product-id "<商品ID 1>" `
  --product-id "<商品ID 2>" `
  --output "<任务目录>\03-asset-matching\folder-candidates.csv"
```

任务目录不保存 `folder-index.sqlite3`。共享索引缺失时当前任务等待首次建立；共享索引过期时执行 `--refresh`，而不是新建任务级索引。

候选只按文件夹自身名称匹配，父目录命中不会让 `KV`、`合成`、`1` 等普通子目录重复成为候选。包含完整 SKU 的组合名称（例如 `KQ23002-商品名`）可视为货号命中；同货号异名、名称候选和主副链接关系仍需用户确认。确认文件夹归属后，才按需枚举其中图片、读取尺寸并计算 SHA-256。

### 3.1 生成文件夹归属审查

把候选转换成素材匹配页面可读取的数据：

```powershell
uv run tmall-materials prepare-folder-review `
  --candidates "<任务目录>\03-asset-matching\folder-candidates.csv" `
  --output "<任务目录>\03-asset-matching\folder-review.json"
```

把 `folder-review.json` 作为素材匹配阶段 `result.json.data` 写入当前精确 `session_id`。页面此时只展示目录元数据，不预览、统计或哈希图片。页面只提供“采用 / 排除该文件夹”：商品 ID、完整货号和完整基础名称候选默认采用；50% 连续名称粗略候选默认排除。保存草稿或提交会把二态决定写入同一阶段 `input.json.values.folder_decisions`。素材匹配返回 `needs_user_input`/`blocked` 时必须保留 `review-context.json`。再次生成审查数据时可传 `--decisions "<folder-decisions.json>"` 保留已有决定；历史 `pending` 按采用读取，新保存结果不再写入 `pending`。

只有最终采用的文件夹可进入按需图片枚举，`rejected` 不得读取。候选优先来自商品 ID、完整货号或去除末尾“（主）/（副）”后的基础名称；最长公共连续部分覆盖基础名称至少 50% 且不少于 5 个字符时，可作为默认排除的粗略候选展示。用户仍应核对同货号异名、主副链接和历史目录，并排除错误来源。

若商品标题与素材文件夹名称不同，但用户明确提供了完整文件夹名，可在本次任务追加 `--exact-folder "PRODUCT_ID=FOLDER_NAME"`。例如 `--exact-folder "886506466908=分龄成长太阳镜"`。该参数按规范化后的完整文件夹名精确相等，只产生本次任务的 `exact_folder_query` 候选；它不是别名，不写入共享配置，也不自动影响后续任务。

## 3.2 默认：确认文件夹后按需准备候选

正常的 1–3 商品试跑不建立全量图片索引。用户提交文件夹决定后，直接读取当前任务 `input.json` 中的 `confirmed` 目录，并结合搜推素材状态生成任务级候选清单：

```powershell
uv run tmall-materials prepare-confirmed-gallery `
  --products "<商品总表.csv>" `
  --status "<任务目录>\promotion\promotion-material-status.csv" `
  --input "<任务目录>\03-asset-matching\input.json" `
  --output "<任务目录>\03-asset-matching\confirmed-gallery.json" `
  --candidate-limit 100 `
  --page-size 30
```

该命令只遍历当前商品使用中的文件夹。每个商品发现 1–100 张时全部进入候选池；超过 100 张时，先按各文件夹图片数量占比分配 100 个名额，再使用任务 ID 作为种子在每个文件夹内稳定随机抽取。例如三个文件夹数量比例为 5:3:2，则 100 张候选分别抽取 50、30、20 张。同一任务重跑结果一致，新任务可重新抽样。只对抽中的候选读取尺寸、校验、计算 SHA-256 并生成预览，不建立全量图片数据库。输出中的 `candidate_strategy=proportional_task_sample`、`folder_allocations`、`scan_summary` 和逐商品 `requirements` 用于解释发现数、各目录抽样数和候选数。

第三阶段只保留单一“采用”操作，勾选即确认该图片可用于本次发布；前端同步写入采用与授权状态，后端按采用项重新生成授权记录。不设置“应选满 N 张”的上限或不足判定。用户选中的图片只记录稳定 `selection_order`，不提前写入坑位分组。第五阶段再决定本次编排的坑位数量，并按每个图文坑位 3–9 张、同坑位只能使用 3:4 或 1:1 且不得混合比例的规则分组；未在本次填充的后台空坑位继续保留为缺失。`confirmed-gallery.json` 只属于当前时间戳任务，不作为跨任务共享索引。候选准备阶段同步生成最长边不超过 640 像素的 JPEG 到 `03-asset-matching/preview-cache/`；Web 页面只传本地预览，原图保持只读并留给最终上传。旧任务首次请求时按需补建预览。Web 预览仍只允许结果中列出的文件，并额外校验原图位于已确认文件夹下，以兼容配置中的 Y/Z 映射盘与 Agent 实际解析到的 UNC 路径不同。预览响应使用短时私有缓存，勾选时只更新当前卡片和决定，不重新创建整个图片网格。

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

优先在阶段一页面的“采集运行环境”卡片完成独立 readiness 检查。没有生产 profile
时点击“创建本机候选”，系统只生成 `production=false` 的 Git 忽略文件；在 CDP
Chrome 登录、打开官方素材中心并填入目标店铺后点击“验证当前页面”。全部必需字段、
页面和店铺通过后才提升为生产 profile。配置保存到 Git 忽略的
`upload-search-materials/config/local-paths.json`，不能使用仓库示例。

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
`opening_promotion`、`selecting_high_value`、`collecting_page`、
`writing_checkpoint`、`building_completeness` 和终态。首个 checkpoint 前只显示 phase
与 heartbeat，不显示“0 行完成”。日志位于当前 attempt 目录。重复执行同一绑定时复用
活 Worker 或完成结果，不启动第二个 Worker。

已确认归属的 Worker 退出后，页面立即显示可恢复；未知 PID、token 不匹配或进程身份
无法读取时必须等待租约过期，不得结束进程。恢复前核对 revision、input SHA、selector
SHA、店铺、attempt、CSV SHA、行数、唯一商品 ID 和最后完成页；从下一页继续。

若返回 `SELECTOR_INVALID`，先保留 checkpoint 和错误证据。只有错误可稳定复现时，
才用 Playwright 检查真实 DOM；修复现有 selector profile 或 collector、增加回归
测试，再重跑原 `process-setup`/`supplement`。不要把诊断脚本保留为第二条生产路径。

日常启动和恢复直接调用 `.venv\Scripts\tmall-materials.exe`；若 console-script
缺失但 Python 和源码已准备，调用 `.venv\Scripts\python.exe -m
upload_search_materials.cli`，不使用 `uv run`。
`scripts/bootstrap.cmd` 是唯一依赖同步入口，使用项目 `.uv-cache` 并写入环境指纹；
已有环境的采集恢复不会访问全局 uv cache、解析或下载包。

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

```powershell
uv run tmall-materials approve --run-dir "<最终审核批次目录>" --task-id "<task-id-1>" --confirmed-by "<批准人>" --confirmed-at "<ISO时间>" --valid-until "<ISO时间>"
uv run tmall-materials publish --run-dir "<最终审核批次目录>" --store "<店铺名>" --selectors "<生产selectors.yaml>" --cdp-url "http://127.0.0.1:9222"
```

`approve` 生成并持久化 `approval-manifest.json` 及 approved 状态；发布命令只接受该目录中的不可变清单。发布结果不确定时立即暂停，执行：

```powershell
uv run tmall-materials resume --run-dir "<最终审核批次目录>" --store "<店铺名>" --selectors "<生产selectors.yaml>" --cdp-url "http://127.0.0.1:9222"
uv run tmall-materials report --run-dir "<最终审核批次目录>"
```

生产前还必须满足 [production-acceptance.md](production-acceptance.md)。示例选择器和示例媒体策略不能直接用于生产。

## 10. 交互式任务执行

新任务使用以时间戳命名的独立会话目录；恢复旧任务时必须指定原 `session_id`，不得默认选择最新目录。在项目根目录运行：

```powershell
.\upload-search-materials\scripts\start-ui.cmd
uv run --project .\upload-search-materials --locked tmall-materials wait-handoff --runs-root .\runs --session 20260721_143025 --stage asset_matching
```

服务状态、恢复和停止：

```powershell
uv run --project .\upload-search-materials --locked tmall-materials ui-status --runs-root "<runs-root>" --session "<session-id>"
uv run --project .\upload-search-materials --locked tmall-materials ui-restart --runs-root "<runs-root>" --session "<session-id>"
uv run --project .\upload-search-materials --locked tmall-materials ui-stop --runs-root "<runs-root>" --session "<session-id>"
```

`.ui-service.json` 和 `logs/` 位于精确 session 目录。stop/restart 只管理健康端点返回 PID 和 ownership token 同时匹配的服务；端口被未知进程占用时选择 `8765–8795` 中的下一端口，不结束未知进程。

Agent 按以下顺序处理每一阶段：

1. 解析精确 `session_id`。
2. 仅对新任务创建时间戳隔离目录。
3. 启动仅监听 `localhost` 的 UI。
4. 等待精确当前阶段的 handoff。
5. 对照 `input.json` 验证 `session_id`、`stage_id`、`revision` 和 `input_sha256`。
6. 读取 `<stage>/decision-modes/<decision_id>.json`；不存在时按 [decision-boundaries.md](decision-boundaries.md) 使用 Skill 默认模式。
7. `manual/manual_only` 等待用户；`rules` 运行确定性规则；只有 `agent_assisted` 才发现和领取 AI 请求。
8. 将该阶段标记为 `processing`。
9. 只运行当前模式与阶段都允许的动作。
10. 写入与同一组四个值绑定的 `result.json`。
11. 停止，或明确进入下一阶段。

`wait-handoff` 使用最长 30 秒的心跳租约，以 10–15 秒片段续租同一 `wait_id`；setup 默认总预算为 2 分钟。页面分别显示在线心跳与总等待剩余。命令只返回已验证的当前 handoff，并领取该阶段为 `processing`；正常预算超时、错误、阶段变化或成功认领都会清理自己拥有的 wait，异常退出才依赖自然过期。当前九阶段流程中，第二阶段自动排除五类商品并直接交给第三阶段素材匹配；旧任务的历史编号目录仍可读取。页面阶段 07/08 只保存输入并生成 handoff；它们不直接运行 `approve` 或 `publish`。这两个命令必须由 Agent 分开调用，且 1–3 个商品的生产测试需在当前对话再次获得用户显式授权。素材变化会使旧批准失效，页面上的旧提交不授权发布新内容。

若页面没有 Agent 心跳，或原 Codex 任务已结束，请用户把页面显示的恢复指令完整粘贴到新建或当前 Codex 任务。页面不会在后台继续执行 Agent，也无法唤醒已结束的任务。

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

第三阶段已提交并由 Agent 写入 `completed` 结果后：

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

第五阶段分为三个递进子页面。第一页上方按商品分页展示第四阶段候选，下方展示唯一当前坑位草稿；默认由 Codex 在一次请求中完成图片分析、主题聚类与坑位编排，规则和完全人工仅作兜底。点击“确认坑位并进入图片裁剪”只锁定每坑 3–9 张、有序图片和唯一比例，不生成派生图。第二页按坑位显示原图尺寸、原始大小、目标比例、裁剪方式和压缩确认；点击“完成图片处理并进入文案生成”才在任务目录生成派生图并复核实际文件。第三页以最终有序图片为依据生成标题和描述，逐坑人工确认后只能进入 dry-run，不会触发阶段 07/08 或真实上传。

可选的 Codex 建议必须由用户点击“提交给 Agent 生成建议”创建。页面不会自动唤醒 Codex；复制页面恢复提示词到当前 Codex 任务，或由 Agent 执行：

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
- 图片输出完成后单独领取 `copy_draft` 请求；文案确认之前不得提交第五阶段。
# 有界等待与“已提交”恢复

在精确 session 的人工阶段使用 15 秒等待片段：

```powershell
tmall-materials wait-handoff --runs-root "<runs-root>" --session "<session-id>" --stage "<stage-id>" --segment-seconds 15
```

页面正式提交后，当前片段会校验并认领 handoff。命令在一个等待回合内续租同一最长
30 秒的 `agent_wait`，不重置 `started_at` 或总预算。setup 默认总预算为 2 分钟；
复杂人工阶段可使用更长配置值。正常总预算结束会清理 wait 并返回聊天恢复提示，批准和
生产确认超时永远不会形成授权。

Codex 等待已结束时，在原聊天收到“已提交”后运行：

```powershell
tmall-materials resume-session --runs-root "<runs-root>" --session "<session-id>" --ack "已提交"
```

必须显式提供已绑定的 session；不得按 runs 目录时间猜测。命令只解析
completed/processing/recoverable/ready/blocked/draft 并在身份匹配时认领，不会批准、
上传或发布。

采集 attempt 的私有写入位于
`collected/promotion/attempts/a-<attempt-prefix>/`，成功发布位于
`collected/promotion/current/`；旧共享 CSV/checkpoint 只是兼容投影。
