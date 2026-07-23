---
name: upload-search-materials
description: Use when preparing, validating, reviewing, publishing, resuming, or auditing Tmall search-recommendation image-text and video materials from product CSV, backend XLSX, Playwright page state, and local or NAS media sources.
---
# Upload Search Materials

## Overview

将商品、月度规则、天猫后台状态和已授权素材转换成可恢复、可审计的商品级与坑位级任务。默认只运行 `dry-run`；正式发布仅接受当前批次不可变批准清单，并使用用户已登录会话中的 Playwright DOM 操作。

## Required Inputs

- 目标月份和页面可见的准确店铺名。
- 商品总表 CSV、月度规则 CSV。
- Playwright 导出的基础素材 XLSX 和同批次 `export-manifest.json`。搜推页的“导出数据”仅是经营指标，不是商品素材与坑位真相源；推广素材现状必须由正确店铺中的实时 DOM 按精确商品 ID 采集。
- 生产选择器运行时配置；示例文件不能直接用于生产。
- NAS/本地素材目录或素材清单、逐文件授权状态。
- AI 文案响应、禁用词政策；视频任务还需要完整视频规格。

缺少信息时保留明确 blocked/needs_manual_review 结果，不猜测、不静默跳过。

## Workflow

素材阶段按以下顺序执行：

1. 完成环境与商品表预检。只有商品表读取失败、缺少/重复必需表头等 schema 或批次级错误、以及空表才停止 raw indexing。`MISSING_PRODUCT_ID`、`INVALID_PRODUCT_ID`、`DUPLICATE_PRODUCT_ID` 是行级 blocked：在 `scan-summary.json` 留下 source row 与 reason codes，只排除对应行的 ID/SKU/名称匹配，其余有效行继续；非空但全部行为行级 blocked 时仍完成纯 metadata 索引。
2. 对声明的图片 roots 运行 `index-assets`：首次使用 new；中断、持久 checkpoint 或部分失败后使用 `--resume`；素材新增、修改或删除后使用 `--refresh`。
3. 检查同一隔离输出目录中的 `scan-summary.json` 和 `match-candidates.csv`；前者包含最小商品校验证据及本轮逐 root/partition 状态、计数和稳定错误码，`asset-index.sqlite3` 是可恢复索引数据库。
4. 人工确认名称候选和逐文件授权，之后才生成或接受 `confirmed-assets.csv`。ID/SKU 命中仍为 `matched_unlicensed`，名称候选为 `needs_manual_confirmation`，授权一律从 `unknown` 开始。
5. 再进入素材完整性可视化审查、生产选择器、全量 dry-run、1–3 商品生产验收，以及文档/发布状态更新。

可复制的 new、`--resume`、`--refresh` 三源命令见 [operations-guide.md](references/operations-guide.md)。raw indexing 不要求月份、店铺或坑位；这些值只在后续 eligible、完整性和生产阶段使用。原始 NAS 文件保持只读，视频继续延期。索引器只生成审查候选和扫描状态：不生成批准清单，不执行 dry-run、上传或发布。

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
3. 启动仅监听 `localhost` 的交互页面。
4. 等待该会话“当前阶段”的精确 `handoff.json`。
5. 验证 `session_id`、`stage_id`、`revision` 和 `input_sha256` 与当前 `input.json` 全部一致。
6. 将该阶段标记为 `processing`。
7. 只执行该 `stage_id` 允许的动作。
8. 写入与同一组 `session_id`、`stage_id`、`revision` 和 `input_sha256` 绑定的 `result.json`。
9. 根据结果停止，或明确进入下一阶段。

页面无 Agent 心跳或 Codex 任务已结束时，告知用户把页面显示的恢复指令粘贴到新建或当前 Codex 任务。不得声称 Agent 仍在后台执行，也不得声称页面能够唤醒已结束的任务。

页面阶段 08/09 只产生输入和 handoff；`approve` 与 `publish` 是分开的、由 Agent 控制的 CLI 动作。对 1–3 个商品的生产测试必须在当前对话获得显式授权；素材改变后，旧批准永远不得授权发布。可复制的启动和等待命令见 [operations-guide.md](references/operations-guide.md)。

## Safety Contract

- 不保存或输出密码、Cookie、Token、短信码、二维码登录数据。
- 同一个 `asset-index.sqlite3` 同一时刻只能由一个 Agent 或进程运行 new、`--resume` 或 `--refresh`；禁止并发索引同一数据库。
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
