---
name: upload-search-materials
description: Use when starting, configuring, testing, preparing, validating, reviewing, resuming, auditing, dry-running, approving, publishing, or uploading Tmall search-recommendation materials. Always start or resume the managed interaction UI before requesting structured business inputs such as store, month, image-source roots, product choices, media decisions, slots, copy, approval, or production confirmation in chat.
---
# Upload Search Materials

## Overview

## 前端优先启动与交互路由（每次触发首先执行）

1. 完整读取本文件后，从 Skill 目录执行 `scripts/start-ui.cmd`；恢复任务时必须传精确 `-Session` 和需要时的 `-RunsRoot`。该命令在后台启动服务、轮询健康状态并及时返回 JSON，不会像前台 `interact` 一样长期占用调用终端。
2. 启动成功后，优先用 Codex 内置浏览器打开 JSON 中的精确 `url`。内置浏览器不可用时才传 `-OpenSystemBrowser` 或把 URL 交给用户。浏览器失败不等于服务失败。
3. 新任务没有店铺、月份、图片源、NAS 映射或登录状态时仍须先打开阶段一页面；这些都是页面字段或后续原生登录动作，不是启动阻断。
4. 每一阶段先打开当前页面并等待精确 handoff。结构化配置和人工决定不得先在聊天中索取。
5. 只有启动器/API 返回允许的稳定原因码后，才能对声明为 `frontend_preferred` 的字段使用 `tmall-materials chat-fallback`；写入仍绑定同一 session、stage、revision、schema 和审计历史。页面恢复后立即回到页面。
6. 安装 uv 或系统运行权限可在 UI 前通过聊天申请；店铺/NAS 等业务值不可以。批准和生产确认即使降级也必须使用精确清单与哈希，模糊的“全部继续”无效。

日常状态与恢复：

```powershell
scripts\start-ui.cmd
tmall-materials ui-status --runs-root <runs-root> --session <session-id>
tmall-materials ui-restart --runs-root <runs-root> --session <session-id>
tmall-materials ui-stop --runs-root <runs-root> --session <session-id>
```

`tmall-materials interact` 只用于前台调试，不是日常入口。完整原因码、字段路由、聊天降级信封和跨电脑约束见 [frontend-interaction-contract.md](references/frontend-interaction-contract.md)；长命令见 [operations-guide.md](references/operations-guide.md)。

## 执行模式契约（每一阶段必读）

本 Skill 在不同阶段使用 `manual`、`rules`、`deterministic`、`agent_assisted`、`manual_only` 执行模式。
Agent 在处理任何 handoff 前，必须先读取该阶段当前 revision 的持久化
decision-mode；不存在时使用 Skill 的阶段默认值。第五阶段新任务的坑位编排只允许
`deterministic` 与 `manual` 两个入口；历史规则/AI 草稿只读展示，不得重新生成、
自动采用或覆盖。`agent_assisted` 只用于最终图片确定后的标题和描述。
模式选择决定 Agent 是等待用户、运行确定性规则，还是领取受控 AI 请求。

素材选择提交后，同步运行确定性编排器并生成唯一未确认
`current_slot_plan`。用户必须审核或人工调整草稿，再点击“确认坑位并进入图片裁剪”。
AI 不参与选图、坑位数量、分组、顺序、比例、裁剪或压缩，也不能触发 dry-run、
批准或上传。
`approval` 和 `production_confirmation` 永远是 `manual_only`，任何 Agent、规则或
页面轮询都不能代替当前用户的精确授权。完整九阶段边界、回退和恢复协议见
[decision-boundaries.md](references/decision-boundaries.md)；字段见
[data-schema.md](references/data-schema.md)，稳定错误见
[error-handling.md](references/error-handling.md)。

## 第三至第五阶段：图片合规、裁剪与坑位编排

1. 第三阶段候选卡片显示原图宽高、比例、格式、大小、200KiB–20MiB 范围，以及 1:1/3:4 的最小/推荐分辨率评估。小于 200KiB、无法生成宽高均至少 720px 的目标比例、不可读或格式不支持时不得采用；超过 20MiB 只有压缩提供方可用时才可采用。
2. 用户采用图片时只更新当前商品的选择预览；保存 1–2 张草稿合法，但提交时每个商品必须至少有 3 张预检通过且源 SHA-256 唯一的图片。提交动作同步重新检查采用图片并保存 `03-asset-matching/selected-asset-preflight.json`；不得遍历未采用共享盘图片，不得生成派生图片。
3. 确定性坑位数为 `K=min(后台缺失坑位, floor(可用唯一图片数/3))`，最多使用 `min(N,K*9)` 张并均衡分配。示例：9 张且缺 3/2/1 个坑位时分别为 `3+3+3`、`5+4`、`9`。图片先按用户选择顺序，再按来源文件夹稳定轮询交错。
4. 每坑比例必须是全部成员共同可行的 3:4 或 1:1，并依次按原生比例数量、推荐分辨率通过数、画面保留率、较少压缩和最终 3:4 同分优先进行确定性评分。策略 ID、版本和 SHA-256 与输入 revision 一起写入草稿；相同输入必须产生相同结果。
5. 新任务不显示或提交独立 `image_review` 阶段；内部 stage ID 和旧 `04-image-review/` 文件仅用于历史恢复。新任务也不得创建 `slot_plan` / `slot_plan_with_analysis` Agent request。
6. 第五阶段只使用两个递进子页面：`图片裁剪和压缩 → AI生成标题和描述`。第一页顶部先显示自动坑位摘要、依据、未使用候选和人工编辑器；计划未按精确 revision 确认前隐藏裁剪区。人工可增删坑位、增删/调序图片及修改比例，一张图片只能属于一个坑位。确认后才在同页展开可视化 3:4/1:1 裁剪和压缩，输出只写任务目录并由服务端复核实际文件。
7. 坑位输出达到 `outputs_ready` 后才允许创建独立 `copy_draft` 请求。请求绑定计划 revision、最终输出 SHA-256 和图片顺序；响应逐坑给出标题、描述、依据和风险。文案只能使用可信商品字段，必须逐坑人工确认；图片、比例、顺序、裁剪结果或输出 SHA 改变时，对应文案过期。

详细字段、判定规则和恢复方式见 [asset-requirements.md](references/asset-requirements.md)、[data-schema.md](references/data-schema.md) 与 [operations-guide.md](references/operations-guide.md)。

将商品、月度规则、天猫后台状态和已授权素材转换成可恢复、可审计的商品级与坑位级任务。默认只运行 `dry-run`；正式发布仅接受当前批次不可变批准清单，并使用用户已登录会话中的 Playwright DOM 操作。

## 启动契约（兼容说明）

把“运行环境准备”和“阶段 1 业务配置”严格分开：

1. 启动前只检查运行条件：当前 Skill 目录、可执行的既有 `.venv`，或用于创建环境的 `uv`。若必须安装 `uv`，只请求安装权限；安装完成后继续启动配置页。
2. 不得在配置页启动前通过聊天索取店铺名、月份、图片源名称或图片根目录，也不得把缺少这些值报告为启动阻断。
3. 先通过 `scripts/start-ui.cmd` 创建时间戳会话并运行受管 UI；`tmall-materials interact` 只保留为前台调试入口。即使尚未配置店铺或图片源，交互页面也必须正常打开。
4. 让用户在阶段 1 配置页填写并确认店铺、月份和一个或多个图片源；只从当前会话经过校验的 setup `input.json`/`handoff.json` 读取这些值。
5. setup handoff 尚未提交时，只等待页面提交或提供恢复指令；不得自行采集、索引、dry-run、上传或发布。

聊天中用户主动提供的值可以用于解释或预填建议，但不能代替配置页提交，也不能跳过 setup handoff。

## 阶段输入（不是启动前置条件）

- 用户在配置页确认页面可见的准确店铺名和目标月份；月份默认当前月。第一阶段不再配置商品范围或搜推采集页数。
- 商品总表和月度规则从项目目录自动发现。图片源在任务配置页维护，可配置 1–50 个“来源名称 + 根路径”并保存为本机配置。创建任务后把商品表与规则表复制到时间戳任务目录并记录 SHA-256；不复制 NAS 原图。
- 本轮上传范围仅为搜推素材，不导出、不审查也不补传基础素材。搜推页的“导出数据”仅是经营指标；第二阶段商品范围必须来自正确店铺实时 DOM 中“商品分类 → 搜推高价值”的全量采集结果。
- 生产选择器运行时配置；示例文件不能直接用于生产。
- 人工图片素材清单、历史基础素材表和历史推广素材状态都是高级可选导入，不是日常任务必填项。
- AI 文案响应、禁用词政策；视频任务还需要完整视频规格。

缺少信息时保留明确 blocked/needs_manual_review 结果，不猜测、不静默跳过。

## Workflow

素材阶段按以下顺序执行：

1. 先创建或复用精确时间戳会话并启动配置页；用户提交 setup handoff 后，才把自动发现的商品表、规则表复制为本次任务输入快照。本分支不导出或审查基础素材。搜推素材必须使用 `supplement --scan-mode high-value` 选择“商品分类 → 搜推高价值”，不传 `--max-pages`，串行遍历全部分页并把结果写入当前任务目录。完成环境与商品表预检。只有商品表读取失败、缺少/重复必需表头等 schema 或批次级错误、以及空表才停止 raw indexing。`MISSING_PRODUCT_ID`、`INVALID_PRODUCT_ID`、`DUPLICATE_PRODUCT_ID` 是行级 blocked：在 `scan-summary.json` 留下 source row 与 reason codes，只排除对应行的 ID/SKU/名称匹配，其余有效行继续；非空但全部行为行级 blocked 时仍完成纯 metadata 索引。
2. 每台电脑只维护一份由 `folder_index_root` 指定的共享文件夹索引，不得为每个时间戳任务重新遍历全部图片 roots，也不得复制其他历史任务中的临时索引。首次缺少共享索引时运行 `index-folders`；素材目录新增、删除或改名后对同一索引运行 `--refresh`；只调整名称或货号匹配规则时运行 `--rematch-only`。这些操作只记录文件夹名称、路径和商品匹配，不读取图片内容。`--refresh` 会遍历目录树发现变化，但在同一数据库中增量维护 active/inactive 状态。
3. 从共享 `folder-candidates.csv` 使用 `snapshot-folder-candidates` 仅提取第二阶段所选商品，把候选 CSV、扫描摘要和后续 `folder-review.json` 保存到当前任务目录；任务目录不得包含 `folder-index.sqlite3`。默认只按商品 ID、完整货号或完整商品名称命中，不维护别名映射；仅共享部分词语不得建立候选。若用户明确给出完整文件夹名，可用 `prepare-folder-review --exact-folder PRODUCT_ID=FOLDER_NAME` 做当前任务的一次性精确查询；不得把该查询写入别名表或自动复用于其他任务。页面必须先展示商品 ID、货号、来源、命中类型、文件夹名和完整路径；候选文件夹默认“采用”，用户只需将错误来源改为“排除该文件夹”，决定写入当前时间戳会话的 `folder_decisions`。
4. 文件夹决定提交后，默认运行 `prepare-confirmed-gallery`：只枚举当前商品使用中的文件夹。每个商品候选池最少 1 张、最多 100 张；发现不足 100 张时全部采用，超过 100 张时按照各文件夹图片数占比分配名额，并用当前任务 `session_id` 作为种子在各文件夹内稳定随机抽样。同一任务重复生成结果不变，新任务可重新抽样。页面每批最多显示 30 张，超过 30 张时启用“换一批”，最后一批按实际余数显示。只对抽中的最多 100 张读取尺寸、校验、计算 SHA-256 和生成预览；结果写入当前任务的 `confirmed-gallery.json`，不得建立全量图片数据库。
5. 人工审查并排除错误的完整名称候选、同货号不同名称文件夹，再逐文件选择本次采用素材；采用图片时同步生成本次授权。文件夹自身名称中的完整 SKU 可为 `matched_unlicensed`；历史 `pending` 文件夹按默认采用读取，新提交不得继续保存 `pending`。
6. 搜推素材实时采集完成后，运行 `tmall-materials inspect-completeness --products <商品表> --promotion-status <promotion-material-status.csv> --output <任务目录>/02-completeness/completeness-matrix.json`。只有“搜推高价值”采集结果中的商品进入第二阶段，商品表只补充名称和货号，不得扩展商品范围。把 JSON 写入当前 revision 的 `result.json.data`，页面展示搜推素材目标/已有/缺失篇数、候选素材状态和后台证据。用户可搜索、筛选、逐项或批量选择商品；提交后从 `02-completeness/input.json.values.selected_product_ids` 读取下阶段商品范围，禁止要求用户直接编辑 JSON。
7. 完成素材完整性可视化审查后，再进入生产选择器、全量 dry-run、1–3 商品生产验收，以及文档/发布状态更新。

可复制的文件夹索引、`--refresh`、`--rematch-only` 与可选全量图片索引命令见 [operations-guide.md](references/operations-guide.md)。raw folder indexing 不要求月份、店铺或坑位；这些值只在后续 eligible、完整性和生产阶段使用。原始 NAS 文件保持只读，视频继续延期。索引器只生成审查候选和扫描状态：不生成批准清单，不执行 dry-run、上传或发布。

### 素材可视化选择

推广素材状态采集和文件夹归属审查完成后，默认使用 `tmall-materials prepare-confirmed-gallery` 将当前 `input.json` 的采用文件夹与 `promotion-material-status.csv` 合并为任务级候选 JSON。第三阶段不计算“必须选择的图片总数”，用户可从实际候选中选择任意数量；后台缺失篇数只作为上下文。第五阶段才决定本次创建几个坑位，并为每个坑位安排 3–9 张、统一为 3:4 或 1:1 的图片；本次任务不要求填满后台全部空坑位。超过 100 张时按文件夹图片数量比例、以任务 ID 为种子稳定随机抽样；同一任务结果稳定。只有显式离线审计场景才使用 `prepare-gallery` 从 `asset-index.sqlite3` 生成候选。

首次进入“素材匹配”阶段时提交本次配置的一个或多个图片根目录，由 Agent 从本机共享文件夹索引生成当前商品候选。页面先执行文件夹归属审查，所有文件夹默认“采用”，只提供“采用 / 排除该文件夹”二态决定并保存到 `folder_decisions`。排除文件夹必须立即从同商品画廊移除其候选，并同步取消来自该文件夹的已选素材与本次授权，显示准确取消数量；重新采用只恢复已有候选，不恢复旧选择。过滤后必须重新计算候选数、30 张分页、页码和换批按钮。提交文件夹决定时只校验 `image_roots` 为非空路径列表；当前 Web 服务进程是否能读取映射盘不能阻断决定保存，只有 Agent 在准备枚举图片时才检查实时可访问性。`needs_user_input`/`blocked` 的素材匹配结果必须保存为只读 `review-context.json`。页面展示缩略图、来源、匹配方式和单一“采用”选择；勾选“采用”即确认该图片可用于本次发布。前端一次操作同时写入 `asset_decisions` 和对应的 `license_decisions`，后端必须按最终文件夹决定再次过滤并规范化授权记录。新图片候选必须携带稳定 `folder_id` 和 `folder_path`；历史候选缺少 `folder_id` 时按最长规范化父路径关联，无法关联时保留并标记。候选准备时把最长边不超过 640 像素的 JPEG 预览写入当前任务 `03-asset-matching/preview-cache/`，页面不得直接传输共享盘原图；旧任务缺少预览时按需生成。预览使用短时私有缓存，选择图片时不得重建整组图片卡片。

本地重复使用 SHA-256 排除，同一任务内同一图片不得跨商品重复选择。只有提供后台已有图片指纹时才可声称远端去重完成；后台仅提供素材 ID 而没有图片指纹时，页面必须显示“远端去重未完成”，最终上传前继续保持人工核对门禁。完整名称候选在文件夹归属确认前不可选择，逐文件授权未确认的候选也不可选择。

完成素材阶段后：

1. 阅读 [business-rules.md](references/business-rules.md)、[data-schema.md](references/data-schema.md) 和 [asset-requirements.md](references/asset-requirements.md)。
2. 通过 `tmall-materials supplement --scan-mode high-value` 扫描“搜推高价值”全部分页并读取实时 DOM；目标容量、当前篇数、远端素材 ID 和可见状态必须来自同一页面证据。搜推页“导出数据”若保留，只能标记为经营指标。
3. 用户在第二阶段选择商品后，以 `selected_product_ids` 作为后续唯一商品边界；月度规则可用于后续文案或业务校验，不得重新扩展或替换该边界。
4. 默认运行 `tmall-materials supplement` 扫描“搜推高价值”的全部分页，每页增量写入 CSV 和 checkpoint；只对目标容量不明确、解析失败或状态异常的商品，再带 `--scan-mode exact --candidates supplement-candidates.csv` 按精确商品 ID 补采。`--scan-mode recommended` 仅为旧证据和恢复命令保留。
5. 带 `--backend-status`、已人工确认的素材配置和 AI 文案响应再次运行 dry-run，生成两级任务和 `review.html`。
6. 用户选择精确 task ID 后运行 `tmall-materials approve`，生成不可变 `approval-manifest.json`。
7. 运行 `tmall-materials publish`。发布前重新核对店铺、商品、坑位和批准内容哈希；发布后回查远端状态。
8. 上传中断后运行 `tmall-materials resume`；已有远端证据的任务不会重复上传。使用 `tmall-materials report` 重新生成中文报告。

运行 `uv run tmall-materials --help` 查看参数。所有命令从本 skill 目录执行；首次使用优先运行 `scripts/bootstrap.cmd`。启动器支持官方源及显式选择的 HTTPS 镜像、项目内缓存、已有 Python 3.11/3.12 和锁文件一致性检查；只有开发测试才传 `-WithTests`，只有明确切换锁文件来源才传 `-UpdateLock`。

首次使用先按 [operations-guide.md](references/operations-guide.md) 完成安装、CDP 浏览器启动和六阶段命令。所有时间参数必须是带时区的 ISO 8601。

## 交互式任务执行

1. 先解析用户指定的精确 `session_id`；不得默认选择 `runs` 中最新的会话。
2. 只有新任务才创建以时间戳命名的隔离会话目录；恢复时显式复用原 `session_id`。
3. 任务配置页只要求店铺确认、月份和图片源配置；不提供商品范围或搜推采集页数。商品表、规则表和运行目录只读展示；图片源组件允许新增、删除、检测并保存任意 1–50 个来源，人工素材清单与历史文件只放在高级设置。
4. 启动仅监听 `localhost` 的交互页面。
5. 第二阶段先将“搜推高价值”全量结果与商品表合并，并自动排除标题或等级命中 `uvno`、`积分`、`清仓`、`好物体验`、`会员日` 的商品。排除项保留在页面和结果中供审计，但不可选择；用户提交其余商品后直接进入第三阶段“素材匹配”，不再设置独立的“维护范围确认”阶段。
6. 等待该会话“当前阶段”的精确 `handoff.json`。
7. 验证 `session_id`、`stage_id`、`revision` 和 `input_sha256` 与当前 `input.json` 全部一致。
8. 将该阶段标记为 `processing`。
9. 只执行该 `stage_id` 允许的动作。
10. 写入与同一组 `session_id`、`stage_id`、`revision` 和 `input_sha256` 绑定的 `result.json`。
11. 根据结果停止，或明确进入下一阶段。

提交前允许编辑并在停止输入约 1 秒后自动保存草稿；草稿不得生成 handoff 或触发 Agent。正式提交后冻结该阶段的全部配置。只有状态仍为 `ready_for_agent`、尚未被 Agent 认领时，用户才能显式撤回并继续修改；`processing` 和 `completed` 禁止覆盖。`needs_user_input` 或 `blocked` 才重新开放输入。每次草稿和正式提交都保存到阶段目录的 `revisions/<revision>/`，活动 `handoff.json` 只代表当前可认领提交。

恢复已有 `session_id` 时，页面必须先读取 `session.json` 状态并直接进入有效的 `current_stage`，同时一次性显示全部阶段的真实状态。只打开、刷新、水合图片决定或构建默认坑位不得标记 dirty、自动保存或增加 revision。草稿保存进行中收到“提交给 Agent”时必须明确显示已排队，并在保存成功后使用最新 revision 继续提交；失败或阶段切换时必须明确暂停或取消，禁止静默丢弃点击。查看已完成阶段时显示锁定原因和“进入当前阶段”，不得重新启用写入。

图片源行提供“选择文件夹”，仅由用户点击后打开本机原生目录窗口并回填完整路径；仍保留手工输入用于 UNC、远程或无界面环境。选择目录时不得枚举或读取图片。页面必须区分“保存为本机配置”“保存草稿”和“提交给 Agent”。

页面无 Agent 心跳或 Codex 任务已结束时，告知用户把页面显示的恢复指令粘贴到新建或当前 Codex 任务。不得声称 Agent 仍在后台执行，也不得声称页面能够唤醒已结束的任务。

恢复指令必须包含 `runs_root`、精确 `session_id`、`stage_id` 和 revision，并要求校验 `handoff.json.input_sha256`；禁止按目录新旧猜测 session。前一阶段未写入 `completed` 结果时，不得提交下一阶段。

页面阶段 07/08 只产生输入和 handoff；`approve` 与 `publish` 是分开的、由 Agent 控制的 CLI 动作。对 1–3 个商品的生产测试必须在当前对话获得显式授权；素材改变后，旧批准永远不得授权发布。可复制的启动和等待命令见 [operations-guide.md](references/operations-guide.md)。

## Safety Contract

### 跨电脑路径解析

- 不得把用户名、桌面绝对路径或某台电脑的盘符写入 Skill 逻辑。启动时按 `--config`、`TMALL_CONFIG_FILE`、项目内 `config/local-paths.json` 的顺序读取本机配置；该本机文件不得提交到仓库。
- 未显式配置商品表或规则表时，从项目根目录的 `docs/` 分别按 `天猫商品信息表*产品数据表*数据总表.csv` 和 `天猫商品信息表*每月推品规则*Grid View.csv` 查找。仅唯一命中时自动采用；零命中标记 `missing`，多命中标记 `ambiguous`，不得猜测最新文件。
- 共享图片目录从前端配置页读取，并可保存到本机配置；不得扫描盘符或假设所有电脑都映射为 `Y:`、`Z:`。必须至少配置 1 个名称与路径均非空且不重复的来源。目录未配置或当前不可访问时仍允许交互页面启动，但依赖素材源的阶段必须停在待配置状态。
- `--runs-root` 优先；否则使用 `TMALL_RUNS_ROOT` 或本机配置；均未提供时使用项目根目录下的 `runs/`。所有任务继续按时间戳目录隔离。
- 共享文件夹索引路径优先使用 `TMALL_FOLDER_INDEX_ROOT`，其次使用本机配置的 `folder_index_root`，默认使用项目根目录下 Git 忽略的 `.local-cache/folder-index/`。它是机器级缓存，不属于任何时间戳任务；任务只保存候选快照。
- 本机配置格式和环境变量见 [operations-guide.md](references/operations-guide.md)。

- 不保存或输出密码、Cookie、Token、短信码、二维码登录数据。
- 同一个 `asset-index.sqlite3` 同一时刻只能由一个 Agent 或进程运行 new、`--resume` 或 `--refresh`；禁止并发索引同一数据库。
- 同一个共享 `folder-index.sqlite3` 同一时刻只能有一个 Agent 执行 new、`--refresh` 或 `--rematch-only`；普通任务只读共享候选并生成任务快照。
- 文件夹索引是默认入口：只保存目录元数据，不读取、哈希或统计所有图片。候选文件夹默认采用；用户排除错误文件夹后，才按需读取其余文件夹中的图片。
- 文件夹决定必须绑定 `folder_id + product_id + source_system + folder_path`；排除文件夹时必须同步移除其候选、采用和授权决定，后端提交边界再次检查一致性。
- 索引时只读声明的原始图片 roots，不修改、移动或删除源文件；视频不进入本阶段。
- 不调用未公开的天猫内部 API，不绕过登录、验证码、扫码、短信、风控或权限。
- 当前店铺与目标店铺不一致：整批进入 blocked，任何任务都不得搜索或发布。
- 批次 blocked 是运行门禁：保留各 item 原状态并标记 held，不把坑位任务强改成不存在于其状态机的 blocked；店铺恢复正确并重新核验后再决定是否解除门禁。
- 批准清单总 SHA-256 必须覆盖 run ID、店铺、输入哈希、批准人、有效期和全部 item；发布使用系统时钟并重算输入及媒体文件哈希。口头“全部发”不能替代批次清单。
- 单个 item 的媒体、标题、描述、动作或坑位变化：只撤销该 item 的批准。店铺、schema、manifest 总哈希或批次输入哈希变化：阻断整批。
- 单项批准撤销时从 `approved` 退回 `ready_for_review`，记录 `APPROVED_CONTENT_CHANGED`，重新生成该项内容哈希并重新批准；其他未变化 item 保留批准但可被批次门禁暂时 held。
- 发布按钮每个 item 最多点击一次。点击后结果不可信时，该 item 进入 `publish_uncertain`，同时暂停批次并先回查。
- 远端“不存在”只有在正确店铺、精确商品 ID、目标坑位、批准指纹和提交时间窗口均完成可信查询后成立；重新提交仍需显式执行决定。
- `publish_uncertain` 确认远端不存在后，原 task 仍不得再次点击；如决定重提，创建新 task ID、新批准清单并再次显式执行。
- 页面选择器失效时返回 `SELECTOR_INVALID`，不得把缺失元素解释为 0 个素材或空坑。商品 eligibility 保持原判定，受影响坑位进入 `needs_manual_review`。

## State Contract

商品任务：`discovered → excluded|eligible|blocked → ready_for_review → approved → uploading → partially_completed|completed|failed`。

坑位任务：`pending_validation → needs_manual_review|ready_for_review → approved → uploading → submitted|publish_uncertain|failed → under_review|success|failed`。

`publish_uncertain`、`submitted`、`under_review`、`success` 或带远端素材 ID/证据的任务不得回到自动上传队列。

## Completion Output

必须报告输入与 SHA-256、月份、店铺、eligible/excluded/blocked/approved/submitted/success/failed 数量、人工任务及原因、远端素材 ID/审核状态、输出目录，以及本次是否停在 dry-run 或执行了已批准发布。
