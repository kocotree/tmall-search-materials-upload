# 数据结构

## 输入表

商品总表 CSV 必需字段：`商品ID`、`商品名称（查找引用）`、`货号（查找引用）`、`产品等级`、`链接`、`运营`、`组别`、`品类-公司维度划分`。

月度规则 CSV 必需字段：`月份`、`品类`、`要推等级`。

基础素材 XLSX 必需字段：`商品ID`、`商品标题`、`商品白底图`、`短标题` 或其标准化别名。推广素材 XLSX（内部兼容旧称“搜推经营”）当前最低必需字段：`商品id/商品ID`、`素材类型`。推广字段契约仍为 `draft`，最低 schema 通过不等于坑位完整性已确认。

缺少必需表头阻断整批；单行缺 ID、非法 ID 或重复 ID 只阻断受影响行。商品 ID 全程保持字符串。

## Export Manifest

`export-manifest.json` 保存 `schema_version`、`run_id`、准确店铺、下载时间、商品状态筛选条件、批次状态、原因码、推广契约状态和报表记录。每条报表记录包含路径、原始文件名、报表类型、SHA-256、数据行数、schema 校验状态、契约状态和原因码。

一次任务每种报表只允许一个文件。输出目录非空时禁止启动浏览器导出。基础素材空单元格保持空值；不得把 Excel 样式编号或第三方读取器的显示值当作素材内容。

## Eligibility Audit

每个输入行包含 `source_row`、商品字段、`status`、`reason_codes` 和 `evidence_json`。所有输入行都必须出现一次。

## Browser Supplement

`backend-material-status.csv` 字段：`商品ID`、`目标坑位`、`现有素材数`、`空坑位`、`审核状态`、`审核状态完整`、`采集时间`、`证据`。

未知值保留未知；缺失选择器不能写成 0 或空列表。`证据` 至少包含商品 ID、目标坑位、页面可见店铺、选择器配置版本、失败字段或选择器、采集时间，以及截图或 DOM 摘要文件的路径和 SHA-256。发生 `SELECTOR_INVALID` 时，数值字段保持未知，另记录原因码和受影响坑位的 `needs_manual_review` 状态。

## Asset Record

字段：`asset_id`、`product_id`、`sku`、`asset_type`、`source_system`、`source_path`、`license_status`、`sha256`、`width`、`height`、`duration`、`validation_status`、`reason_codes`。

目录型素材默认只在 `<asset-root>/<商品ID>/` 查找，其次在 `<asset-root>/<货号>/` 查找。逐文件授权必须由素材清单表示；批次级 `--license-status confirmed` 只能用于该目录内所有文件已由用户确认同一授权状态的情形。

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
