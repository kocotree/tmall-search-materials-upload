# 分片增量素材索引器设计

## 目标

为 `upload-search-materials` 增加只读的 NAS 图片索引能力，使三处真实素材根目录能够分片扫描、中断恢复、增量刷新，并安全地产生商品匹配候选。索引器不裁剪、不上传、不发布，也不把目录名称自动当成已确认商品匹配或素材授权。

## 范围

本功能处理 JPG、JPEG、PNG 和 WebP 图片。视频、飞书来源接入、人工确认页面改造以及生产上传不属于本次实现。

索引器读取商品总表，以商品 ID、SKU 和商品名称为匹配依据；它不依赖目标月份、店铺或后台坑位数据。月份、店铺和坑位只在后续确定本轮 eligible 商品与完整性矩阵时使用。

## 方案选择

采用 SQLite 索引库、聚合候选 CSV 和扫描摘要 JSON：

- SQLite 支持事务、幂等 upsert、分片状态和中断恢复，适合当前约 41 万张图片的数据量。
- CSV 仅承载按目录和商品聚合的人工审查候选，避免把全部文件一次性加载到页面。
- JSON 提供本次命令的来源数量、状态、错误、耗时和恢复信息。

不采用单个追加 CSV，因为中断后容易出现重复行和半写入状态；不采用纯 JSONL 分片，因为跨分片去重、刷新和状态更新复杂。

## CLI

新增命令：

```powershell
tmall-materials index-assets `
  --products <商品总表.csv> `
  --root model_nas=Y:\视觉部\1-模特图 `
  --root xhs_taobao_nas=Z:\...\小红书koc置换&淘宝买家秀\优质买家秀 `
  --root xhs_buyer_nas=Z:\...\小红书koc置换&买家秀\优质买家秀 `
  --output <时间戳任务目录> `
  --partition-depth 2 `
  --checkpoint-size 1000
```

参数约束：

- `--products` 必填，使用现有商品表结构校验；缺 ID 或重复 ID 的行保留 blocked 证据，但不参与自动匹配。
- `--root SOURCE_SYSTEM=PATH` 可重复，`SOURCE_SYSTEM` 在一次索引中必须唯一，路径必须存在且为目录。
- `--output` 必填；新扫描在空输出目录创建产物。
- `--partition-depth` 默认 `2`，按根目录以下指定深度形成稳定分片；不足深度的目录和根目录文件仍形成分片。
- `--checkpoint-size` 默认 `1000`，每处理相应数量文件提交一次事务并刷新进度。
- `--resume` 只继续同一配置和商品表哈希下未完成或失败的分片，跳过已完成分片。
- `--refresh` 重新枚举全部分片，按路径、大小和 mtime 更新文件；本轮未再出现的旧文件标记为 inactive。`--resume` 与 `--refresh` 互斥。
- 已存在索引时若未指定 `--resume` 或 `--refresh`，命令拒绝覆盖。

退出码：全部完成为 `0`；配置、schema 或身份不一致为 `2`；部分根目录或分片读取失败但证据已保存为 `1`。

## 架构与组件

新增 `upload_search_materials/asset_index.py`，职责限定为：

1. 解析和验证命名素材根目录。
2. 使用 `os.scandir` 只读遍历，默认不跟随符号链接或 Windows reparse point，防止越界和循环。
3. 按相对目录生成稳定分片 ID。
4. 将快速元数据写入 SQLite。
5. 基于已验证商品表生成匹配记录。
6. 只对产生匹配记录的图片计算 SHA-256、宽高和可读性。
7. 输出候选 CSV 与扫描摘要 JSON。

CLI 只负责参数解析、调用索引器、打印摘要和返回退出码。现有 `DirectoryAssetSource` 与 `ManifestAssetSource` 行为保持不变。

## SQLite 数据

`asset-index.sqlite3` 使用 schema version `1`，至少包含：

- `scan_meta`：商品表绝对路径与 SHA-256、索引配置、创建/更新时间、schema version。
- `roots`：来源名、规范化根路径、状态、错误和最后完成时间。
- `partitions`：稳定分片 ID、来源、相对路径、状态、已处理数量、错误和检查点时间。
- `files`：来源、相对路径、绝对路径、扩展名、大小、mtime_ns、active、SHA-256、宽高、可读性状态和错误码。
- `matches`：文件、商品 ID、SKU、匹配类型、候选目录、确认状态和原因码。

来源名与相对路径唯一标识一个文件。所有原始路径按实际形式保存；比较和匹配使用单独的规范化值。

每个检查点在单个事务中提交文件和匹配状态。中断不会把未提交文件标记为完成；重复执行使用 upsert，不产生重复记录。

## 两阶段处理

### 快速索引

对全部图片只读取目录项与 `stat`，记录：

- 来源系统
- 绝对路径和相对路径
- 扩展名
- 文件大小
- mtime_ns
- 所属候选目录

此阶段不打开图片、不计算 SHA-256。

### 候选深化

匹配顺序固定为：

1. 路径组件完整等于商品 ID：`exact_product_id`。
2. 路径组件完整等于 SKU，忽略大小写：`exact_sku`。
3. 对每个路径目录组件分别规范化；当规范化商品名称是组件的连续子串，或长度至少为 4 的规范化组件是商品名称的连续子串时：`name_candidate`。

名称规范化只处理空白、常见连接符、大小写和固定品牌前缀 `KK树`；不使用模糊距离，也不凭相似度自动确认。商品名称少于 4 个规范化字符时不产生名称候选。

产生任一匹配的文件才调用现有图片检查能力，计算 SHA-256、宽高和可读性。精确 ID/SKU 只代表商品匹配确定，授权仍保持 `unknown`；名称匹配状态固定为 `needs_manual_confirmation`。

## 输出

### `match-candidates.csv`

按来源、候选目录、商品和匹配类型聚合，包含：

- `source_system`
- `candidate_directory`
- `product_id`
- `sku`
- `product_title`
- `match_type`
- `match_status`
- `image_count`
- `hashes_complete`
- `license_status`，固定为 `unknown`
- `reason_codes`

索引器不生成 `confirmed-assets.csv`。后续只有人工确认商品别名、逐文件授权和采用决定后，才能从索引库展开生成与现有 `--asset-manifest` 兼容的 `confirmed-assets.csv`。

### `scan-summary.json`

包含 schema version、扫描模式、商品表 SHA-256、各根目录和分片的 discovered/indexed/matched/failed 数量、总耗时、错误、是否完整以及恢复命令。任何数量都来自 SQLite 查询，不使用预设值。

## 刷新与删除

`--refresh` 开始时为本轮扫描分配标识。成功完成某个分片后，该分片中未在本轮看到的旧文件标记为 inactive；读取失败的分片不得把旧文件误判为删除。大小或 mtime 改变时清除旧 SHA-256、宽高和匹配深化结果，再按当前匹配重新检查。

## 错误与安全

- 根目录不存在、来源名重复、输出配置不一致或商品表 schema 错误：退出 `2`，不得覆盖旧索引。
- 单个文件消失、拒绝访问或读取失败：记录错误码并继续当前分片。
- 分片无法枚举：状态为 failed，保留旧记录，不推断文件已删除，最终退出 `1`。
- 所有路径必须解析在声明根目录内；越界路径记录 `PATH_OUTSIDE_ROOT`。
- 不跟随符号链接或 reparse point。
- 不写入 NAS，不改变图片大小、mtime 或内容。
- 视频文件忽略且不计入本次图片统计。
- 所有授权默认 `unknown`；索引结果不能直接授权 dry-run 或生产发布。

## 测试

采用 TDD，覆盖：

- 命名根目录参数解析、重复来源和缺失路径。
- 分片深度及稳定 ID。
- 快速索引不打开图片内容。
- 商品 ID、SKU 和名称候选的固定优先级。
- 名称候选不会成为 confirmed。
- 只有匹配文件计算 SHA-256 和宽高。
- 检查点中断后 `--resume` 不重复记录并继续未完成分片。
- 相同输出目录无模式参数时拒绝覆盖。
- `--refresh` 处理新增、修改和删除；失败分片不误删旧记录。
- 路径越界、符号链接、权限错误和单文件消失。
- 候选 CSV 和摘要 JSON 的字段、数量和 UTF-8 输出。
- CLI 退出码和帮助文本。
- 测试前后源文件大小、mtime 和 SHA-256 不变。

完成自动化测试后，只对小型临时目录运行功能验收；真实三处 NAS 首轮运行必须使用新的时间戳任务目录，并保留进度、摘要和错误证据。

## 后续集成

本功能完成后，阶段 3 的下一步是把 `match-candidates.csv` 展示到现有“素材匹配”页面，收集别名、授权和采用决定，再生成 `confirmed-assets.csv`。该页面集成属于后续独立任务，不在本次索引器实现中。
