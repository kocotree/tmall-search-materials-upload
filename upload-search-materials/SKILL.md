---
name: upload-search-materials
description: Use when preparing, validating, reviewing, publishing, resuming, or auditing Tmall search-recommendation image-text and video materials from product CSV, backend XLSX, Playwright page state, and local or NAS media sources.
---
# Upload Search Materials

## Overview

将商品、月度规则、天猫后台状态和已授权素材转换成可恢复、可审计的商品级与坑位级任务。默认只运行 `dry-run`；正式发布仅接受当前批次不可变批准清单，并使用用户已登录会话中的 Playwright DOM 操作。

## Required Inputs

- 用户只需确认页面可见的准确店铺名、目标月份和商品范围；月份默认当前月，商品范围默认全部符合当月规则的商品。
- 商品总表和月度规则从项目目录自动发现。图片源在任务配置页维护，可配置 1–50 个“来源名称 + 根路径”并保存为本机配置。创建任务后把商品表与规则表复制到时间戳任务目录并记录 SHA-256；不复制 NAS 原图。
- 本轮上传范围仅为搜推素材。基础素材由 Playwright 自动导出后只用于确定 `商品状态=售卖中` 的商品范围，不审查、不补传其字段。搜推页的“导出数据”仅是经营指标；搜推素材现状必须从正确店铺的实时 DOM 自动采集到任务目录。
- 生产选择器运行时配置；示例文件不能直接用于生产。
- 人工图片素材清单、历史基础素材表和历史推广素材状态都是高级可选导入，不是日常任务必填项。
- AI 文案响应、禁用词政策；视频任务还需要完整视频规格。

缺少信息时保留明确 blocked/needs_manual_review 结果，不猜测、不静默跳过。

## Workflow

素材阶段按以下顺序执行：

1. 用户提交任务配置后创建或复用精确时间戳会话。把自动发现的商品表、规则表复制为本次任务输入快照；基础素材自动导出、推广素材自动采集，全部结果只写入当前任务目录。完成环境与商品表预检。只有商品表读取失败、缺少/重复必需表头等 schema 或批次级错误、以及空表才停止 raw indexing。`MISSING_PRODUCT_ID`、`INVALID_PRODUCT_ID`、`DUPLICATE_PRODUCT_ID` 是行级 blocked：在 `scan-summary.json` 留下 source row 与 reason codes，只排除对应行的 ID/SKU/名称匹配，其余有效行继续；非空但全部行为行级 blocked 时仍完成纯 metadata 索引。
2. 默认先对声明的图片 roots 运行 `index-folders`，只记录文件夹名称、路径和商品匹配，不读取图片内容。素材目录变化后使用 `--refresh`；只调整名称、货号或别名匹配规则时使用 `--rematch-only`，不得重新扫描 NAS。
3. 检查 `folder-scan-summary.json` 和 `folder-candidates.csv`，再运行 `prepare-folder-review` 生成文件夹归属审查数据。页面必须先展示商品 ID、货号、来源、命中类型、文件夹名和完整路径；用户逐项选择“确认归属 / 确认并记录别名 / 排除”，决定写入当前时间戳会话的 `folder_decisions`。只对已确认文件夹按需枚举图片、读取尺寸并计算 SHA-256。全量 `index-assets` 仅作为离线审计选项，不再阻断 1–3 商品试跑。
4. 人工确认名称/别名候选、同货号不同名称文件夹和逐文件授权，之后才生成或接受 `confirmed-assets.csv`。文件夹自身名称中的完整 SKU 可为 `matched_unlicensed`；名称候选为 `needs_manual_confirmation`，授权一律从 `unknown` 开始。
5. 基础素材导出和搜推素材实时采集完成后，运行 `tmall-materials inspect-completeness --products <商品表> --basic <基础素材.xlsx> --promotion-status <promotion-material-status.csv> --output <任务目录>/02-completeness/completeness-matrix.json`。基础素材只限定售卖中范围；把 JSON 写入当前 revision 的 `result.json.data`，页面只展示搜推素材目标/已有/缺失篇数、候选素材状态和后台证据。未知目标保持未知；用户通过页面确认、标记误判、排除或要求人工处理，禁止要求用户直接编辑 JSON。
6. 完成素材完整性可视化审查后，再进入生产选择器、全量 dry-run、1–3 商品生产验收，以及文档/发布状态更新。

可复制的文件夹索引、`--refresh`、`--rematch-only` 与可选全量图片索引命令见 [operations-guide.md](references/operations-guide.md)。raw folder indexing 不要求月份、店铺或坑位；这些值只在后续 eligible、完整性和生产阶段使用。原始 NAS 文件保持只读，视频继续延期。索引器只生成审查候选和扫描状态：不生成批准清单，不执行 dry-run、上传或发布。

### 素材可视化选择

推广素材状态采集完成后，使用 `tmall-materials prepare-gallery` 将 `asset-index.sqlite3` 与 `promotion-material-status.csv` 合并为页面所需的候选 JSON。每个商品的选择数量按 `缺失篇数 × 每篇图片数` 计算；候选按匹配可信度、来源、路径和 SHA-256 稳定排序，不随机抽取。

首次进入“素材匹配”阶段时提交本次配置的一个或多个图片根目录，由 Agent 生成文件夹候选。页面先执行文件夹归属审查，决定保存到 `folder_decisions`；未确认的文件夹不得展开为图片候选。确认后页面才展示缩略图、来源、匹配方式、授权确认、采用选择和“换一批”。“换一批”使用不重叠的稳定分片；用户也可以先取消一张再选择另一张。授权与素材选择分别写入当前时间戳会话的 `license_decisions` 和 `asset_decisions`，保存草稿或提交后才落盘到该阶段 `input.json`。

本地重复使用 SHA-256 排除，同一任务内同一图片不得跨商品重复选择。只有提供后台已有图片指纹时才可声称远端去重完成；后台仅提供素材 ID 而没有图片指纹时，页面必须显示“远端去重未完成”，最终上传前继续保持人工核对门禁。名称候选在别名归属确认前不可选择，逐文件授权未确认的候选也不可选择。

完成素材阶段后：

1. 阅读 [business-rules.md](references/business-rules.md)、[data-schema.md](references/data-schema.md) 和 [asset-requirements.md](references/asset-requirements.md)。
2. 通过 `tmall-materials export` 在正确店铺导出基础素材 XLSX。搜推页“导出数据”若保留，只能标记为经营指标，禁止据此判断当前素材数或坑位完整性。推广素材现状通过 `tmall-materials supplement` 按精确商品 ID 读取实时 DOM；目标容量、当前篇数、远端素材 ID 和可见状态必须来自同一页面证据。
3. 运行 `tmall-materials run`。先排除清仓、UVNO、好物体验、会员日和积分，再应用月度规则。
4. 默认运行 `tmall-materials supplement` 扫描“推荐补充素材”的全部分页，每页增量写入 CSV 和 checkpoint；只对目标容量不明确、解析失败或状态异常的商品，再带 `--scan-mode exact --candidates supplement-candidates.csv` 按精确商品 ID 补采。
5. 带 `--backend-status`、已人工确认的素材配置和 AI 文案响应再次运行 dry-run，生成两级任务和 `review.html`。
6. 用户选择精确 task ID 后运行 `tmall-materials approve`，生成不可变 `approval-manifest.json`。
7. 运行 `tmall-materials publish`。发布前重新核对店铺、商品、坑位和批准内容哈希；发布后回查远端状态。
8. 上传中断后运行 `tmall-materials resume`；已有远端证据的任务不会重复上传。使用 `tmall-materials report` 重新生成中文报告。

运行 `uv run tmall-materials --help` 查看参数。所有命令从本 skill 目录执行；使用 `uv sync --extra test` 根据 `.python-version` 和 `uv.lock` 同步 Python 3.11 环境。

首次使用先按 [operations-guide.md](references/operations-guide.md) 完成安装、CDP 浏览器启动和六阶段命令。所有时间参数必须是带时区的 ISO 8601。

## 交互式任务执行

1. 先解析用户指定的精确 `session_id`；不得默认选择 `runs` 中最新的会话。
2. 只有新任务才创建以时间戳命名的隔离会话目录；恢复时显式复用原 `session_id`。
3. 任务配置页要求店铺确认、月份、商品范围和图片源配置。商品表、规则表和运行目录只读展示；图片源组件允许新增、删除、检测并保存任意 1–50 个来源，人工素材清单与历史文件只放在高级设置。
4. 启动仅监听 `localhost` 的交互页面。
5. 等待该会话“当前阶段”的精确 `handoff.json`。
6. 验证 `session_id`、`stage_id`、`revision` 和 `input_sha256` 与当前 `input.json` 全部一致。
7. 将该阶段标记为 `processing`。
8. 只执行该 `stage_id` 允许的动作。
9. 写入与同一组 `session_id`、`stage_id`、`revision` 和 `input_sha256` 绑定的 `result.json`。
10. 根据结果停止，或明确进入下一阶段。

提交前允许编辑并在停止输入约 1 秒后自动保存草稿；草稿不得生成 handoff 或触发 Agent。正式提交后冻结该阶段的全部配置。只有状态仍为 `ready_for_agent`、尚未被 Agent 认领时，用户才能显式撤回并继续修改；`processing` 和 `completed` 禁止覆盖。`needs_user_input` 或 `blocked` 才重新开放输入。每次草稿和正式提交都保存到阶段目录的 `revisions/<revision>/`，活动 `handoff.json` 只代表当前可认领提交。

图片源行提供“选择文件夹”，仅由用户点击后打开本机原生目录窗口并回填完整路径；仍保留手工输入用于 UNC、远程或无界面环境。选择目录时不得枚举或读取图片。页面必须区分“保存为本机配置”“保存草稿”和“提交给 Agent”。

提交前允许编辑并在停止输入约 1 秒后自动保存草稿；草稿不得生成 handoff 或触发 Agent。正式提交后冻结该阶段的全部配置。只有状态仍为 `ready_for_agent`、尚未被 Agent 认领时，用户才能显式撤回并继续修改；`processing` 和 `completed` 禁止覆盖。`needs_user_input` 或 `blocked` 才重新开放输入。每次草稿和正式提交都保存到阶段目录的 `revisions/<revision>/`，活动 `handoff.json` 只代表当前可认领提交。

图片源行提供“选择文件夹”，仅由用户点击后打开本机原生目录窗口并回填完整路径；仍保留手工输入用于 UNC、远程或无界面环境。选择目录时不得枚举或读取图片。页面必须区分“保存为本机配置”“保存草稿”和“提交给 Agent”。

页面无 Agent 心跳或 Codex 任务已结束时，告知用户把页面显示的恢复指令粘贴到新建或当前 Codex 任务。不得声称 Agent 仍在后台执行，也不得声称页面能够唤醒已结束的任务。

恢复指令必须包含 `runs_root`、精确 `session_id`、`stage_id` 和 revision，并要求校验 `handoff.json.input_sha256`；禁止按目录新旧猜测 session。前一阶段未写入 `completed` 结果时，不得提交下一阶段。

恢复指令必须包含 `runs_root`、精确 `session_id`、`stage_id` 和 revision，并要求校验 `handoff.json.input_sha256`；禁止按目录新旧猜测 session。前一阶段未写入 `completed` 结果时，不得提交下一阶段。

页面阶段 08/09 只产生输入和 handoff；`approve` 与 `publish` 是分开的、由 Agent 控制的 CLI 动作。对 1–3 个商品的生产测试必须在当前对话获得显式授权；素材改变后，旧批准永远不得授权发布。可复制的启动和等待命令见 [operations-guide.md](references/operations-guide.md)。

## Safety Contract

### 跨电脑路径解析

- 不得把用户名、桌面绝对路径或某台电脑的盘符写入 Skill 逻辑。启动时按 `--config`、`TMALL_CONFIG_FILE`、项目内 `config/local-paths.json` 的顺序读取本机配置；该本机文件不得提交到仓库。
- 未显式配置商品表或规则表时，从项目根目录的 `docs/` 分别按 `天猫商品信息表*产品数据表*数据总表.csv` 和 `天猫商品信息表*每月推品规则*Grid View.csv` 查找。仅唯一命中时自动采用；零命中标记 `missing`，多命中标记 `ambiguous`，不得猜测最新文件。
- 共享图片目录从前端配置页读取，并可保存到本机配置；不得扫描盘符或假设所有电脑都映射为 `Y:`、`Z:`。必须至少配置 1 个名称与路径均非空且不重复的来源。目录未配置或当前不可访问时仍允许交互页面启动，但依赖素材源的阶段必须停在待配置状态。
- `--runs-root` 优先；否则使用 `TMALL_RUNS_ROOT` 或本机配置；均未提供时使用项目根目录下的 `runs/`。所有任务继续按时间戳目录隔离。
- 本机配置格式和环境变量见 [operations-guide.md](references/operations-guide.md)。

- 不保存或输出密码、Cookie、Token、短信码、二维码登录数据。
- 同一个 `asset-index.sqlite3` 同一时刻只能由一个 Agent 或进程运行 new、`--resume` 或 `--refresh`；禁止并发索引同一数据库。
- 文件夹索引是默认入口：只保存目录元数据，不读取、哈希或统计所有图片。只有用户确认文件夹归属后，才按需读取该文件夹中的图片。
- 文件夹归属决定必须绑定 `folder_id + product_id + source_system + folder_path`；仅货号命中也要检查同货号异名、主副链接和历史目录，不能自动批准。
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
