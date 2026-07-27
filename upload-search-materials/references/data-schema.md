# 数据结构

## Task Setup

任务配置 handoff 的用户输入为：`store`、`store_confirmed` 和 `month`。第一阶段不接受 `product_scope`、`product_ids` 或 `promotion_max_pages`；商品范围由第二阶段的“搜推高价值”全量采集结果决定。`products_csv` 和 `rules_csv` 由项目自动发现；`image_source_labels` 与 `image_roots` 由可视化页面的动态图片源配置成对写入，可配置 1–50 个来源。

阶段根目录的 `input.json` 与 `handoff.json` 表示当前活动 revision；`revisions/<四位 revision>/input.json` 保存每次草稿或提交快照，正式提交另存同目录 `handoff.json`。草稿无 handoff。活动 handoff 被撤回或输入补充时可以失效，但历史快照不删除。

`asset_manifest`、`historical_basic_xlsx`、`historical_promotion_csv` 和 `user_notes` 均可选。正式流程只自动采集搜推素材并写入当前时间戳任务目录；基础素材字段仅为旧批次离线恢复兼容，不参与本分支范围计算。运行目录由 SessionStore 决定，不接受页面覆盖。

## 输入表

商品总表 CSV 必需字段：`商品ID`、`商品名称（查找引用）`、`货号（查找引用）`、`产品等级`、`链接`、`运营`、`组别`、`品类-公司维度划分`。

月度规则 CSV 必需字段：`月份`、`品类`、`要推等级`。

基础素材 XLSX 必需字段：`商品ID`、`商品标题`、`商品白底图`、`短标题` 或其标准化别名。搜推页“导出数据”是经营指标，不是商品素材与坑位真相源；即使包含 `商品ID`、`素材类型` 或素材 ID，也不得据此证明当前坑位完整。

缺少必需表头阻断整批；单行缺 ID、非法 ID 或重复 ID 只阻断受影响行。商品 ID 全程保持字符串。

## Export Manifest

`export-manifest.json` 保存 `schema_version`、`run_id`、准确店铺、下载时间、商品状态筛选条件、批次状态、原因码、推广契约状态和报表记录。每条报表记录包含路径、原始文件名、报表类型、SHA-256、数据行数、schema 校验状态、契约状态和原因码。

一次任务每种报表只允许一个文件。输出目录非空时禁止启动浏览器导出。基础素材空单元格保持空值；不得把 Excel 样式编号或第三方读取器的显示值当作素材内容。

## Eligibility Audit

每个输入行包含 `source_row`、商品字段、`status`、`reason_codes` 和 `evidence_json`。所有输入行都必须出现一次。

## Browser Supplement

`backend-material-status.csv` 字段：`商品ID`、`目标容量`、`现有素材数`、`缺失数量`、`空坑位`、`远端素材ID`、`素材状态`、`状态完整`、`采集时间`、`证据`。

这里的容量单位是“篇”。页面明确显示高价值商品“已上调发布坑位到 9 篇”时，`目标容量=9`；普通商品页面未明确目标时保持未知，不仅凭分类名称猜成 3。页面没有固定编号坑位时，`空坑位` 保持未知，只计算 `缺失数量=目标容量-现有素材数`。未知值保留未知；缺失选择器不能写成 0 或空列表。`证据` 至少包含商品 ID、目标容量原文、页面可见店铺、远端素材 ID、可见状态、选择器配置版本、失败字段或选择器、采集时间，以及截图或 DOM 摘要文件的路径和 SHA-256。发生 `SELECTOR_INVALID` 时，数值字段保持未知，另记录原因码和受影响商品的 `needs_manual_review` 状态。

分页扫描 checkpoint 保存 `schema_version`、`status`、`scan_mode`、`last_completed_page`、`row_count` 和 `collected_at`。每完成一页必须先原子更新 CSV，再更新 checkpoint；中断后不得把未完成页写成已完成。生产阶段不传 `--max-pages`，必须遍历“搜推高价值”的全部分页；该参数只保留给显式 CLI 诊断测试，测试结果不得作为完整的第二阶段候选集。

## Completeness Matrix

本轮只上传搜推素材，不审查或补传基础素材。运行 `tmall-materials inspect-completeness`，将搜推素材实时采集 CSV 转为第二阶段矩阵。矩阵范围严格等于“商品分类 → 搜推高价值”的全量采集结果；商品总表只补充名称与货号，不得扩入其他商品。输出使用 `contract_version=1`、`source_filter=search_recommend_high_value`，包含 `summary` 和 `products`。

每个商品必须包含：

- `product_id`、`sku`、`product_title`、整体 `status` 和布尔值 `selectable`。
- `eligibility.status/reason_codes/evidence`；标题或等级命中 `uvno`、`积分`、`清仓`、`好物体验`、`会员日` 时，写为 `status=excluded`、`selectable=false`，页面保留展示但禁选。
- `promotion.target_slots/current_count/missing_count`、远端素材 ID、原因码、采集时间和证据；无法识别容量时标记 `needs_manual_review`，不得根据分类猜测目标容量。
- `candidate_asset_count`；第二阶段尚未完成素材匹配时保持 `null`，页面显示“待素材匹配”，不得伪造为 0。

页面决定写入 `02-completeness/input.json.values`。`selected_product_ids` 只保存用户选择直接进入第三阶段“素材匹配”的可选商品 ID，至少选择一个才能提交。搜索和状态筛选只改变当前显示范围；“选择当前筛选结果”自动跳过排除项，“取消当前筛选结果”批量更新可见商品，不清除其他筛选条件下已经选择的商品。服务端提交时再次拒绝排除项或不属于当前矩阵的陈旧商品 ID。

第二阶段完整度巡检和第三阶段素材匹配在 Agent 返回 `needs_user_input` 或 `blocked` 时，同时保存只读 `review-context.json`。用户增量保存选择、文件夹确认或排除决定会更新 `input.json` revision，但必须继续展示该审查上下文；修改前置阶段时才使后续审查上下文失效。用户不能通过页面修改 `review-context.json`。

## Folder Index

`folder-index.sqlite3` 是每台电脑共享维护的机器级缓存，不属于任何时间戳任务，也不保存图片内容：

- `folder_id`：由来源与相对路径生成的稳定标识。
- `source_system`、`absolute_path`、`relative_path`、`folder_name`、`parent_relative_path`。
- `active`、`last_seen_scan_id`：用于 `--refresh` 标记新增、保留和已删除目录。
- 匹配表保存商品 ID、货号、商品名称、`match_type` 与 `match_status`。

共享 `folder-candidates.csv` 输出当前 active 且命中商品的文件夹。当前任务使用 `snapshot-folder-candidates` 按第二阶段 `selected_product_ids` 生成任务内候选快照，不复制 SQLite。候选按文件夹自身名称匹配，不继承父目录命中；`--rematch-only` 只使用本地文件夹记录重新计算匹配。确认文件夹归属前，不读取文件夹中的图片，也不计算图片 SHA-256。

`prepare-folder-review` 生成的 `folder-review.json` 使用 `review_type=folder_ownership` 和 `safety_status=folders_only`。`folder_candidates` 每项包含 `folder_id`、商品 ID/标题/货号、来源、文件夹名、完整路径、匹配类型、匹配状态以及当前决定；`folder_products` 提供逐商品候选数。候选默认 `confirmed`，页面只提供采用和排除；历史 `pending` 按采用读取。用户明确提供完整文件夹名时，当前任务可生成 `match_type=exact_folder_query` 的候选；该记录不是别名。

用户决定写入同一时间戳会话的 `input.json.values.folder_decisions`。每项必须包含：

- `folder_id`、`product_id`、`source_system`、`folder_path`，用于绑定明确目录和商品。
- `decision`：`confirmed` 或 `rejected`；未处理项不写入决定数组。
- `note`：可选人工说明。

最终采用决定可以驱动后续按需图片枚举；保存决定本身不读取图片。`rejected` 只排除其绑定的 `folder_id + product_id`，但必须立即从画廊移除对应候选并同步清理其采用和授权记录，不得推进阶段。素材匹配阶段提交文件夹决定时，`image_roots` 只校验为非空路径列表，不再次要求当前 Web 服务进程可读；Agent 仅在准备枚举图片时检查对应根目录和文件夹的实时可访问性。不可访问时返回 `blocked`，不得把文件夹决定误报为 validation failed。

## Asset Record

字段：`asset_id`、`product_id`、`sku`、`asset_type`、`source_system`、`source_path`、`license_status`、`sha256`、`width`、`height`、`duration`、`validation_status`、`reason_codes`。

目录型素材默认只在 `<asset-root>/<商品ID>/` 查找，其次在 `<asset-root>/<货号>/` 查找。逐文件授权必须由素材清单表示；批次级 `--license-status confirmed` 只能用于该目录内所有文件已由用户确认同一授权状态的情形。

## Asset Gallery Result

素材匹配阶段的 `result.json.data` 包含：

- `requirements`：逐商品 `product_id`、`product_title`、后台 `missing_materials`、本阶段实际 `candidate_count`、`slot_image_min=3`、`slot_image_max=9` 和 `slot_planning_stage=slots_copy`。第三阶段不得生成 `required_images`，因为后台缺失篇数不等于本次必须创建的篇数。
- `asset_candidates`：逐候选 `asset_id`、商品、稳定 `folder_id`、可审计 `folder_path`、来源、绝对只读路径、SHA-256、尺寸、匹配类型、匹配状态、授权状态、校验状态与远端重复标记。
- `candidate_strategy`：默认按使用中的文件夹比例抽样时为 `proportional_task_sample`；只有显式离线审计流程才从全量图片索引生成。
- `candidate_limit` 默认 100，`page_size` 默认 30，`sampling_seed` 使用当前任务 ID。
- `scan_summary`：记录使用中文件夹数、发现图片路径数、实际检查候选数、检查失败数，以及逐商品 `folder_allocations`；每项包含文件夹路径、发现数和抽样数。任务级按需候选不得把“发现路径数”表述成“已完成全量图片索引”。
- `remote_dedupe_status`：仅在提供可信远端内容指纹并完成比对时为 `checked`；后台只有素材 ID 时为 `not_available`。
- `reason_codes`：包含远端指纹不可用等批次级原因。

用户决定写入同一时间戳会话素材匹配阶段的 `input.json.values`：

- `license_decisions`：`asset_id` 与 `status=confirmed`；由同一批 `asset_decisions` 中的采用项自动生成，不要求用户逐图重复勾选授权。
- `asset_decisions`：`product_id`、`asset_id`、`sha256`、来源、`decision=selected`、`selection_order`。第三阶段不写 `group_index` 或坑位内 `position`；第五阶段完成编排后再生成。

候选选择按稳定顺序和固定窗口执行，不保存随机种子，也不随机抽样。默认只对当前任务预备窗口计算 SHA-256；本地与当前批次重复以 SHA-256 排除。远端内容指纹不可用时不得把远端素材 ID 当作图片去重证据。图片预览必须同时满足“出现在当前结果中”及“位于配置根目录或当前任务已确认文件夹下”，从而兼容映射盘与 UNC 路径差异但不扩大文件读取范围。

## AI 文案响应

JSON 顶层为对象，键使用 `<商品ID>:<坑位号>`，值至少包含 `title` 和 `description` 字符串。例如：

```json
{
  "123456:1": {
    "title": "儿童水杯",
    "description": "依据已提供商品属性生成的描述"
  }
}
```

文案只能使用已提供的商品属性作为事实依据；缺失响应或依据不足进入人工审核。

## Two-Level Tasks

`product_task` 保存 `task_id`、`run_id`、商品 ID、资格状态、目标坑位数、负责人和原因码。

`material_item` 保存 `task_id`、商品 ID、媒体类型、slot index、媒体清单、标题、描述、内容哈希、状态、尝试次数、远端素材 ID 和原因码。

坑位 task ID 由 `run_id + product_id + material_type + slot_index` 确定性生成。

## Approval Manifest

必需字段：schema version、run ID、目标店铺、批次输入 SHA-256、精确 task ID、商品 ID、媒体 SHA-256、标题、描述、动作、批准人、批准时间、有效期和 manifest SHA-256。

manifest SHA-256 覆盖除自身之外的完整规范化批准信封，不能只覆盖 entries。发布前使用可信系统时钟检查有效期，重算批次输入文件和每个媒体文件的 SHA-256；批准后的内容变化不得沿用旧批准。

## 时间与批次格式

所有 CLI 时间参数使用带时区的 ISO 8601，例如 `2026-07-17T10:00:00+08:00`。`run-id` 建议使用不可重复且可读的值，例如 `20260717T100000+0800-kktree-export`；导出批次 ID 与后续 dry-run 的内部 run ID 分开记录并通过来源文件哈希关联。
# 图片合规审查数据

第三阶段 `asset_candidates[]` 增加：

- `source_inspection`：原图路径、格式、`size_bytes`、EXIF 纠正后的 `width`/`height`、`ratio_value`、`ratio_display`、`sha256`、可读性、检查时间与原因码
- `target_assessments.<ratio>`：1:1/3:4 最大内接尺寸、最小尺寸状态与推荐分辨率状态
- `preflight.original_ratio`
- `preflight.size_display`、`min_size_bytes`、`max_size_bytes`、`size_below_minimum`、`size_exceeded`
- `preflight.matching_ratios`
- `preflight.resolution_checks.<ratio>`：`max_crop_width`、`max_crop_height`、`recommended_width`、`recommended_height`、`status`
- `preflight.status`：`direct|croppable|needs_compression|crop_and_compress|unusable`
- `preflight.selectable` 与 `preflight.reason_codes`

第四阶段 `review-context.json.data` 必须包含：

- `asset_matching_revision`、`policy`、`policy_sha256`
- `selected_count`、`reviewable_count`、`blocked_count`、`duplicate_count`
- `assets[]`：商品/素材标识、完整 `source_inspection`、`target_assessments`、预检结果和两个比例的 `crop_options`
- `capabilities.manual_crop=true`、`capabilities.ai_crop=false`、`capabilities.image_compression=true`，并记录压缩提供方及版本

第四阶段 `input.json.values.decisions[]`：

- 公共字段：`asset_id`、`product_id`、`action`
- `direct|compress`：必须有与原图匹配的 `target_ratio`
- `crop|crop_and_compress`：必须有 `target_ratio` 和归一化 `crop_box{x,y,width,height}`
- 提交后四种发布动作必须增加 `output`，记录 `kind`、路径、SHA-256、宽高、大小、比例、格式、质量、提供方/版本、源 SHA-256、策略 SHA-256 和来源链。

第五阶段 `review-context.json.data` 包含 `image_review_revision`、`products[].outputs[]`、`blocked_outputs[]`、`slot_image_min=3`、`slot_image_max=9`。`slot_assignments[]` 包含 `slot_id`、`product_id`、`asset_ids`、单一 `target_ratio` 与 `image_review_revision`。
