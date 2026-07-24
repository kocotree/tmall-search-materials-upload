# 首次运行指南

## 1. 安装与检查

在本 skill 目录使用 `uv` 管理 Python 3.11、锁文件和虚拟环境：

```powershell
uv sync --extra test
uv run tmall-materials --help
$quickValidate = Join-Path $env:USERPROFILE ".codex\skills\.system\skill-creator\scripts\quick_validate.py"
uv run python -X utf8 $quickValidate .
```

`uv` 根据 `.python-version` 使用 Python 3.11，并依据 `uv.lock` 创建或同步 `.venv`。首次同步需要访问 Python 包索引；后续验收使用 `uv lock --check` 检查锁文件是否与 `pyproject.toml` 一致。不要向系统 Python 或 Conda 基础环境直接安装本项目依赖。

### 1.1 每台电脑只配置一次路径

首次启动后可直接在任务配置页新增、删除和检测图片源，并点击“保存为本机配置”。页面支持 1–50 个“来源名称 + 根路径”。也可以复制 `config/local-paths.example.json` 为 `config/local-paths.json` 后手工修改。`local-paths.json` 已被 Git 忽略，不会影响其他电脑。

每个图片源行的“选择文件夹”由用户点击后打开 Windows 原生目录选择窗口，只回填完整路径，不扫描图片；UNC 或无界面环境可继续手工粘贴路径。

项目内输入默认无需配置：程序先定位包含 `docs/` 与 `upload-search-materials/` 的项目根目录，再在 `docs/` 中按以下受控模式查找：

- 商品表：`天猫商品信息表*产品数据表*数据总表.csv`
- 规则表：`天猫商品信息表*每月推品规则*Grid View.csv`

只有唯一命中才会自动采用。没有命中时页面显示“未找到”；多个命中时显示歧义并等待显式配置，不按时间或文件名猜测。共享盘不执行全盘扫描；盘符或 UNC 路径只从本机配置读取。

解析优先级与覆盖入口：

1. `tmall-materials interact --config <配置.json>` 指定配置文件。
2. `TMALL_CONFIG_FILE` 指定配置文件；`TMALL_WORKSPACE_ROOT`、`TMALL_PRODUCTS_CSV`、`TMALL_RULES_CSV`、`TMALL_RUNS_ROOT` 可单项覆盖。
3. 项目内 `upload-search-materials/config/local-paths.json`。
4. 项目结构自动发现；运行目录默认使用 `<项目根目录>/runs/`。

路径未配置不会导致交互页面崩溃；只有实际依赖该输入的阶段会保持待配置。不要把个人用户名、桌面绝对路径或本机盘符写回 `SKILL.md`、Python 源码或已提交的配置。

## 2. 创建隔离任务

任务配置页让用户确认店铺、月份和本次图片源，不配置商品范围或搜推采集页数。商品表、规则表及运行目录自动发现并只读展示；图片源可配置一个或多个，检测只读可访问性后可保存为本机默认值。搜推素材标记为“搜推高价值”全量自动采集；本分支不准备基础素材。人工图片素材清单和历史推广素材状态位于高级设置，日常执行保持为空。

Agent 接收 setup handoff 后，在当前 `runs/<session_id>/` 中创建输入快照和自动采集目录：

- `inputs/`：复制商品表、规则表并生成输入清单与 SHA-256。
- `collected/promotion/`：保存 `promotion-material-status.csv`、checkpoint 和页面证据。
- `folder-review/`、`assets/`、`dry-run/`、`approval/`、`results/`：只保存当前任务的候选、决定和结果。

共享文件夹索引数据库、选择器与策略配置不重复复制；NAS 原图只读且不复制。页面只保存 handoff，不直接启动 Playwright；由收到 handoff 的 Agent 执行复制、导出和采集。

编辑时页面停止输入约 1 秒会自动保存草稿。草稿只写当前任务，不通知 Agent；“保存为本机配置”只更新当前电脑默认图片源；“提交给 Agent”才生成可认领 handoff。提交后表单冻结。Agent 尚未认领时可撤回；进入处理中后不可撤回或覆盖。阶段目录的 `revisions/<revision>/` 保留输入和正式 handoff 快照。

## 3. 文件夹索引优先

默认先建立三源文件夹级索引，只保存目录名称、完整路径和商品匹配，不打开或哈希图片：

```powershell
uv run tmall-materials index-folders `
  --products "<商品总表.csv>" `
  --root "model_nas=Y:\视觉部\1-模特图" `
  --root "xhs_taobao=Z:\浙江酷趣\运营中心\营销板块\小红书koc置换&淘宝买家秀\优质买家秀" `
  --root "xhs_buyer=Z:\浙江酷趣\运营中心\营销板块\小红书koc置换&买家秀\优质买家秀" `
  --output "<隔离输出目录>\folder-index"
```

检查 `folder-scan-summary.json` 和 `folder-candidates.csv`。目录新增、删除或改名后使用相同参数加 `--refresh`；只调整名称、货号或别名匹配规则时加 `--rematch-only`，后者只读取本地 SQLite，不重新遍历 NAS。

候选只按文件夹自身名称匹配，父目录命中不会让 `KV`、`合成`、`1` 等普通子目录重复成为候选。包含完整 SKU 的组合名称（例如 `KQ23002-商品名`）可视为货号命中；同货号异名、名称候选和主副链接关系仍需用户确认。确认文件夹归属后，才按需枚举其中图片、读取尺寸并计算 SHA-256。

### 3.1 生成文件夹归属审查

把候选转换成素材匹配页面可读取的数据：

```powershell
uv run tmall-materials prepare-folder-review `
  --candidates "<隔离输出目录>\folder-index\folder-candidates.csv" `
  --output "<隔离输出目录>\folder-review.json"
```

把 `folder-review.json` 作为素材匹配阶段 `result.json.data` 写入当前精确 `session_id`。页面此时只展示目录元数据，不预览、统计或哈希图片。用户选择“确认归属”“确认归属并记录别名”或“排除该文件夹”后，保存草稿或提交会把决定写入同一阶段 `input.json.values.folder_decisions`。再次生成审查数据时可传 `--decisions "<folder-decisions.json>"` 保留已有决定。

只有 `confirmed` 或 `confirmed_alias` 的文件夹可进入按需图片枚举；`pending` 和 `rejected` 都不得读取其中图片。货号命中也不得跳过人工检查，尤其要核对同货号异名、主副链接和历史目录。

## 3.2 可选：分片增量图片索引

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

### 3.3 生成第二阶段完整度矩阵

```powershell
uv run --project .\upload-search-materials --locked tmall-materials inspect-completeness `
  --products <任务目录>\inputs\products.csv `
  --promotion-status <任务目录>\promotion\promotion-material-status.csv `
  --output <任务目录>\02-completeness\completeness-matrix.json
```

该命令只读输入，不访问图片源、不调用浏览器、不上传。检查输出中未知目标仍为 `null`，且不包含基础素材字段完整度；再将矩阵作为第二阶段的 Agent 结果写入。用户可搜索、筛选、逐项或批量选择商品，提交后从 `selected_product_ids` 读取下一阶段范围。

第二阶段商品范围严格等于“商品分类 → 搜推高价值”的全量采集结果。商品总表仅补充商品名称和货号，不扩展范围。

### 3.4 生成素材候选画廊

先完成推广素材状态扫描，再把缺失篇数与索引候选合并：

```powershell
uv run tmall-materials prepare-gallery `
  --index "<隔离输出目录>\asset-index\asset-index.sqlite3" `
  --status "<隔离输出目录>\promotion-material-status.csv" `
  --output "<隔离输出目录>\asset-gallery.json" `
  --images-per-material 3 `
  --license-decisions "<逐文件授权决定.json>" `
  --alias-decisions "<名称候选归属决定.json>"
```

`--license-decisions`、`--alias-decisions` 和 `--remote-fingerprints` 均为可选输入。首次审查可以不提供，让页面先展示候选并由用户确认；确认后重新生成结果。候选顺序固定，不随机。“换一批”按固定窗口取下一批，单张替换通过先取消再采用另一张完成。

把 `asset-gallery.json` 的对象作为素材匹配阶段 `result.json.data` 写入当前精确 `session_id`。页面只允许预览该结果列出的、且位于当前 `image_roots` 下的图片；保存草稿或提交后，授权和选择分别进入当前阶段 `input.json` 的 `license_decisions` 与 `asset_decisions`。

当后台已有素材只有远端素材 ID、没有图片 SHA-256 或等价内容指纹时，不传 `--remote-fingerprints`。此时输出和页面必须保持 `remote_dedupe_status=not_available`，不得声称已经排除与远端重复；生产上传前仍需人工核对。提供可信远端指纹后，已命中的候选才会被自动排除。

## 4. 启动用户控制的 CDP 浏览器

关闭正在使用同一 profile 的 Chromium 后，以独立 profile 和仅本机监听的调试端口启动 Chrome 或 Edge。例如将实际可执行文件路径替换进下列命令：

```powershell
& "<chrome-or-edge.exe>" --remote-debugging-port=9222 --remote-debugging-address=127.0.0.1 --user-data-dir="<独立profile目录>"
```

CDP URL 为 `http://127.0.0.1:9222`。用户必须在该窗口自行登录、处理验证码/短信/扫码/风控，并确认页面可见店铺名。不要把 profile、Cookie 或登录信息放入版本库。

## 5. 导出与只读检查

所有时间使用带时区 ISO 8601，例如 `2026-07-17T10:00:00+08:00`。导出 `run-id` 使用不可重复的可读值，例如 `20260717T100000+0800-kktree-export`。

```powershell
uv run tmall-materials export --store "<精确店铺名>" --selectors "<生产selectors.yaml>" --output "<空的隔离导出目录>" --run-id "<导出run-id>" --downloaded-at "<ISO时间>" --product-status "售卖中" --report basic --cdp-url "http://127.0.0.1:9222"
```

`--report promotion|both` 目前仅作为旧版经营数据导出兼容入口；其结果不是商品素材与坑位真相源，不得据此判定完整或作为生产批准输入。搜推素材现状必须通过 `supplement` 按精确商品 ID 从实时 DOM 采集。生产 `supplement` 尚未更新到 2026-07-24 验证的新 DOM 契约前，停止在阶段 5，不进入全量 dry-run。

## 6. 首次 dry-run 与 Playwright 补采

```powershell
uv run tmall-materials run --mode dry-run --month <1-12> --store "<店铺名>" --products "<商品总表.csv>" --rules "<月度规则.csv>" --basic "<基础素材.xlsx>" --search "<搜推经营.xlsx>" --output "<首次批次目录>" --started-at "<ISO时间>"
uv run tmall-materials supplement --store "<店铺名>" --selectors "<生产selectors.yaml>" --output "<promotion-material-status.csv>" --checkpoint "<promotion-material-status.checkpoint.json>" --collected-at "<ISO时间>" --cdp-url "http://127.0.0.1:9222"
uv run tmall-materials supplement --scan-mode exact --store "<店铺名>" --selectors "<生产selectors.yaml>" --candidates "<异常商品.csv>" --output "<exact-material-status.csv>" --collected-at "<ISO时间>" --cdp-url "http://127.0.0.1:9222"
```

第一条 `supplement` 命令默认选择“商品分类 → 搜推高价值”，串行遍历全部分页并在每页后原子更新 CSV 与 checkpoint。生产流程不从任务配置读取页数，也不传 `--max-pages`；该参数只保留给显式 CLI 诊断测试。`--scan-mode recommended` 仅用于兼容旧证据。第二条仅用于异常商品的精确 ID 兜底。`--search` 现阶段仅保留经营指标兼容性，不能替代实时 `promotion-material-status.csv`；任何使用它推断搜推坑位完整性的结果均无效。

## 7. 素材、授权、文案与最终 dry-run

目录型素材只搜索 `<asset-root>/<商品ID>/`，其次 `<asset-root>/<货号>/`。`--license-status confirmed` 表示用户确认该批目录中的每个文件均已授权；若授权状态不统一，当前 CLI 不能正式发布，应保持 blocked，待接入逐文件素材清单。当前视频元数据入口也未闭合，视频任务应保持 `VIDEO_METADATA_UNAVAILABLE`。

AI 文案 JSON 使用 `<商品ID>:<坑位号>` 作为键，值包含 `title` 和 `description`；详细结构见 [data-schema.md](data-schema.md)。

```powershell
uv run tmall-materials run --mode dry-run --month <1-12> --store "<店铺名>" --products "<商品总表.csv>" --rules "<月度规则.csv>" --basic "<基础素材.xlsx>" --search "<搜推经营.xlsx>" --backend-status "<backend-material-status.csv>" --asset-root "<素材根目录>" --license-status confirmed --media-policy "<生产media-policy.yaml>" --copy-responses "<AI文案.json>" --prohibited-term "<禁用词>" --output "<最终审核批次目录>" --started-at "<ISO时间>"
```

打开 `review.html`，只选择 `ready_for_review` 的精确 task ID。

## 8. 精确批准、发布与恢复

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

## 9. 交互式任务执行

新任务使用以时间戳命名的独立会话目录；恢复旧任务时必须指定原 `session_id`，不得默认选择最新目录。在项目根目录运行：

```powershell
uv run --project .\upload-search-materials --locked tmall-materials interact
uv run --project .\upload-search-materials --locked tmall-materials wait-handoff --runs-root .\runs --session 20260721_143025 --stage asset_matching
```

Agent 按以下顺序处理每一阶段：

1. 解析精确 `session_id`。
2. 仅对新任务创建时间戳隔离目录。
3. 启动仅监听 `localhost` 的 UI。
4. 等待精确当前阶段的 handoff。
5. 对照 `input.json` 验证 `session_id`、`stage_id`、`revision` 和 `input_sha256`。
6. 将该阶段标记为 `processing`。
7. 只运行当前阶段允许的动作。
8. 写入与同一组四个值绑定的 `result.json`。
9. 停止，或明确进入下一阶段。

`wait-handoff` 只返回已验证的当前 handoff，并领取该阶段为 `processing`。页面阶段 08/09 只保存输入并生成 handoff；它们不直接运行 `approve` 或 `publish`。这两个命令必须由 Agent 分开调用，且 1–3 个商品的生产测试需在当前对话再次获得用户显式授权。素材变化会使旧批准失效，页面上的旧提交不授权发布新内容。

若页面没有 Agent 心跳，或原 Codex 任务已结束，请用户把页面显示的恢复指令完整粘贴到新建或当前 Codex 任务。页面不会在后台继续执行 Agent，也无法唤醒已结束的任务。
