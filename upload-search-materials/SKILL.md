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
- Playwright 导出的基础素材 XLSX、搜推素材经营数据 XLSX。
- 生产选择器运行时配置；示例文件不能直接用于生产。
- NAS/本地素材目录或素材清单、逐文件授权状态。
- AI 文案响应、禁用词政策；视频任务还需要完整视频规格。

缺少信息时保留明确 blocked/needs_manual_review 结果，不猜测、不静默跳过。

## Workflow

1. 阅读 [business-rules.md](references/business-rules.md)、[data-schema.md](references/data-schema.md) 和 [asset-requirements.md](references/asset-requirements.md)。
2. 通过 `tmall-materials export` 在正确店铺导出两份 XLSX；无法使用浏览器时允许用户提供同格式人工导出文件。
3. 运行 `tmall-materials run`。先排除清仓、UVNO、好物体验、会员日和积分，再应用月度规则。
4. 对 `supplement-candidates.csv` 运行 `tmall-materials supplement`，只补采 3/9 坑、空坑和审核状态未知的 eligible 商品。
5. 带 `--backend-status`、素材配置和 AI 文案响应再次运行 dry-run，生成两级任务和 `review.html`。
6. 用户选择精确 task ID 后运行 `tmall-materials approve`，生成不可变 `approval-manifest.json`。
7. 运行 `tmall-materials publish`。发布前重新核对店铺、商品、坑位和批准内容哈希；发布后回查远端状态。
8. 中断后运行 `tmall-materials resume`；已有远端证据的任务不会重复上传。使用 `tmall-materials report` 重新生成中文报告。

运行 `python -m upload_search_materials.cli --help` 查看参数。所有命令从本 skill 目录执行，使用 Python 3.11+。

## Safety Contract

- 不保存或输出密码、Cookie、Token、短信码、二维码登录数据。
- 不调用未公开的天猫内部 API，不绕过登录、验证码、扫码、短信、风控或权限。
- 当前店铺与目标店铺不一致：整批进入 blocked，任何任务都不得搜索或发布。
- 批准必须引用 `approval-manifest.json` 的总 SHA-256；口头“全部发”不能替代批次清单。
- 单个 item 的媒体、标题、描述、动作或坑位变化：只撤销该 item 的批准。店铺、schema、manifest 总哈希或批次输入哈希变化：阻断整批。
- 发布按钮每个 item 最多点击一次。点击后结果不可信时，该 item 进入 `publish_uncertain`，同时暂停批次并先回查。
- 远端“不存在”只有在正确店铺、精确商品 ID、目标坑位、批准指纹和提交时间窗口均完成可信查询后成立；重新提交仍需显式执行决定。
- 页面选择器失效时返回 `SELECTOR_INVALID`，不得把缺失元素解释为 0 个素材或空坑。

## State Contract

商品任务：`discovered → excluded|eligible|blocked → ready_for_review → approved → uploading → partially_completed|completed|failed`。

坑位任务：`pending_validation → needs_manual_review|ready_for_review → approved → uploading → submitted|publish_uncertain|failed → under_review|success|failed`。

`publish_uncertain`、`submitted`、`under_review`、`success` 或带远端素材 ID/证据的任务不得回到自动上传队列。

## Completion Output

必须报告输入与 SHA-256、月份、店铺、eligible/excluded/blocked/approved/submitted/success/failed 数量、人工任务及原因、远端素材 ID/审核状态、输出目录，以及本次是否停在 dry-run 或执行了已批准发布。
