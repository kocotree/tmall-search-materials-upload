# 数据结构

## 输入表

商品总表 CSV 必需字段：`商品ID`、`商品名称（查找引用）`、`货号（查找引用）`、`产品等级`、`链接`、`运营`、`组别`、`品类-公司维度划分`。

月度规则 CSV 必需字段：`月份`、`品类`、`要推等级`。

基础素材 XLSX 必需字段：`商品ID`、`商品标题`、`商品白底图`、`短标题` 或其标准化别名。搜推经营 XLSX 必需字段：`商品id/商品ID`、`素材类型`。

缺少必需表头阻断整批；单行缺 ID、非法 ID 或重复 ID 只阻断受影响行。商品 ID 全程保持字符串。

## Eligibility Audit

每个输入行包含 `source_row`、商品字段、`status`、`reason_codes` 和 `evidence_json`。所有输入行都必须出现一次。

## Browser Supplement

`backend-material-status.csv` 字段：`商品ID`、`目标坑位`、`现有素材数`、`空坑位`、`审核状态`、`审核状态完整`、`采集时间`、`证据`。

未知值保留未知；缺失选择器不能写成 0 或空列表。

## Asset Record

字段：`asset_id`、`product_id`、`sku`、`asset_type`、`source_system`、`source_path`、`license_status`、`sha256`、`width`、`height`、`duration`、`validation_status`、`reason_codes`。

## Two-Level Tasks

`product_task` 保存 `task_id`、`run_id`、商品 ID、资格状态、目标坑位数、负责人和原因码。

`material_item` 保存 `task_id`、商品 ID、媒体类型、slot index、媒体清单、标题、描述、内容哈希、状态、尝试次数、远端素材 ID 和原因码。

坑位 task ID 由 `run_id + product_id + material_type + slot_index` 确定性生成。

## Approval Manifest

必需字段：schema version、目标店铺、精确 task ID、商品 ID、媒体 SHA-256、标题、描述、动作、批准人、批准时间、有效期和 manifest SHA-256。

发布前对规范化 JSON 重新计算 SHA-256。批准后的内容变化不得沿用旧批准。
