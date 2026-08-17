# 数据结构

## 采集与处理恢复字段

阶段处理 claim 保存在 `session.json.stages.<stage>.processing_claim`，包含
`claim_id`、`claimant_id`、`claimed_at`、`heartbeat_at`、`lease_expires_at`、
`revision` 和 `input_sha256`。`handoff.json` 是不可变提交证据，不作为第二份活动
claim 状态源。

阶段一采集证据至少包含：

- `inputs/input-manifest.json`：源路径、任务快照路径和 SHA-256；
- `inputs/scan-summary.json`：总行数、有效行数、异常行数及 source row reason codes；
- `collected/promotion/selector-profile.json`：profile 名称、版本、purpose 和 SHA-256；
- `collected/promotion/store-page-evidence.json`：目标/可见店铺、URL、页面身份和校验时间；
- `promotion-material-status.csv`、checkpoint 与 `pagination-evidence.json`；
- `02-completeness/completeness-matrix.json`；
- 与 session、stage、revision、input SHA-256 绑定的 `result.json`。

批量行使用 `目标容量/现有素材数/缺失数量`，并将 `精确坑位状态` 写为
`not_collected`。此时 `empty_slot_indexes` 必须为 `null`；只有后续逐商品精确复核
才能写具体空坑位编号。

## Managed UI Service

`.ui-service.json` 还记录启动者与服务的 Windows SID、登录会话 ID、交互桌面可用性、PID 创建身份及仅含盘符的网络盘可见性；不得记录目录列表或凭据。路径检测、picker、索引和素材 Worker 在读取源文件前必须匹配这些身份字段。

每个精确 session 可有一个 `.ui-service.json`，记录 schema version、session ID、runs root、PID、ownership token、loopback 端口、精确 URL、stdout/stderr 日志、启动/检查时间、健康状态、session 可读状态和浏览器打开状态。对外 CLI 结果不返回 ownership token；浏览器失败与服务失败分开记录。

## Interaction Policy 与聊天降级

阶段和字段 API 返回 `component`、`interaction_policy=frontend_required|frontend_preferred|chat_fallback` 和 `fallback_reason_codes`。聊天降级值仍写入阶段 `input.json.values`；`interaction_history[]` 保存 channel、reason、detail、actor、时间、session、stage、base revision 和输入 SHA。前端后续修改必须保留历史。

批准与生产确认另外保存 `safety_checklist_sha256` 和参与哈希的字段名。未知 schema 字段不得写入 `values`，只生成 `frontend-gap.json`。

## Selected-asset preflight 与确定性坑位草稿

新任务在 `03-asset-matching/selected-asset-preflight.json` 保存当前选图
revision、策略 SHA-256、源检查身份、检测提供方版本、统计及逐图检查记录；该文件
只能包含元数据，不得包含派生图片。

初始 `current-slot-plan.json` 使用 `decision_source=deterministic`。坑位上下文的
`deterministic_plan` 包含 `strategy_id`、`strategy_version`、
`strategy_sha256`、输入 revision、`assignments[]`、`unused_assets[]` 和逐商品
坑位数量摘要。人工编辑后来源改为 `manual` 或 `manual_override`。历史
`rules` / `agent_assisted` 文件保持可读，但新任务不得生成。

## Task Setup

每机图片源绑定项使用 `source_id`、`label`、`path`，可选 `canonical_unc`、`last_verified_sid`、`last_verified_at` 和 `last_status`。`source_id` 是跨电脑稳定业务引用；`path` 是当前电脑的盘符或 UNC。旧 `label + path` 项加载时确定性补 source ID。该配置不得进入版本库，也不得包含 NAS 凭据、目录清单或图片内容。

任务配置 handoff 的用户输入为：`store` 和 `store_confirmed`。第一阶段不接受 `month`、`product_scope`、`product_ids` 或 `promotion_max_pages`；商品范围由第二阶段的“搜推高价值”全量采集结果决定。`products_csv` 和 `rules_csv` 由项目自动发现；`image_source_labels` 与 `image_roots` 由可视化页面的动态图片源配置成对写入，可配置 1–50 个来源。

图片源检测 API 的每项诊断包含：

- 原输入 `label`、`path`，兼容字段 `status=available|unavailable` 和布尔
  `available`；
- `path_kind=local|mapped_drive|unc|invalid`；
- 稳定 `reason_code`、中文 `message`/`next_action`、`checked_at`；
- 仅在系统可解析时返回的 `portable_path_suggestion`，以及可选数值
  `windows_error`；不得返回凭据、用户信息、目录内容或文件名。

目录选择 API 成功/取消返回 `cancelled`、`path` 和
`FOLDER_PICKER_SELECTED|FOLDER_PICKER_CANCELLED`。失败返回安全的
`reason_code`、`message`、`next_action` 和可选无敏感信息的 `detail`。取消或失败
不写入 `input.json`；只有前端回填后发生的正常草稿保存才进入当前 revision。

阶段根目录的 `input.json` 与 `handoff.json` 表示当前活动 revision；`revisions/<四位 revision>/input.json` 保存每次草稿或提交快照，正式提交另存同目录 `handoff.json`。草稿无 handoff。活动 handoff 被撤回或输入补充时可以失效，但历史快照不删除。

`asset_manifest`、`historical_basic_xlsx`、`historical_promotion_csv` 和 `user_notes` 均可选。正式流程只自动采集搜推素材并写入当前时间戳任务目录；基础素材字段仅为旧批次离线恢复兼容，不参与本分支范围计算。运行目录由 SessionStore 决定，不接受页面覆盖。

草稿和提交使用 `stage-transaction.json` 作为版本化事务信封，至少包含 request ID、operation、session/stage、base/target revision、规范化输入 SHA、progress state、created/updated 时间和恢复所需输入。完成后归档到 `transactions/<request-id>.json`。同一 request ID 重试返回原结果且不重复事件；不同内容命中同一 request/revision 返回 `REVISION_CONTENT_CONFLICT`。读取阶段状态时可完成遗留 half-commit 的 input、snapshot、handoff 与 session state 对齐。

`agent_wait` 分别保存 `expires_at`（最长 30 秒心跳）和 `budget_expires_at`（setup 默认两分钟总预算）。同一 claimant/session/stage/expected revision 续租同一 wait ID，保留 `started_at`；正常终态清理，异常退出才自然过期。

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

分页扫描 checkpoint 保存 `schema_version`、`status`、`scan_mode`、`session_id`、`revision`、`input_sha256`、`selector_profile_sha256`、`target_store`、`attempt_id`、`last_completed_page`、`row_count`、`output_sha256`、`pagination_evidence_sha256` 和 `collected_at`。`pagination-evidence.json` 是版本化 attempt 证据，包含绑定后的 origin、transition、terminal 或 failure 事件；每个事件只保存真实页码、末页、下一页状态、有序商品 ID 哈希、选择器身份和时间，不保存凭据或完整 DOM。每完成一页必须先验证当前 claim，再原子更新 CSV，校验商品 ID 唯一，最后更新 checkpoint；中断后不得把未完成页写成已完成。生产阶段不传 `--max-pages`，必须遍历“搜推高价值”的全部分页；该参数只保留给显式 CLI 诊断测试，测试结果不得作为完整的第二阶段候选集。

采集中出现滑动验证、验证码或风控时，`human-checkpoint.json` 使用同一 session、revision、input SHA、selector SHA、target store 和 attempt 身份，保存 `status`、`reason_code`、`location`、`current_page`、`last_completed_page`、`row_count`、`events[]` 与时间。`events[]` 只记录 `detected/resolved`、检查位置和页码，不保存验证内容、Cookie 或 DOM。Worker 在 `waiting_human_check` 阶段保持心跳；验证元素消失后更新为 `resolved` 并继续同一 attempt。

只读随机动作按随机 1–2 页间隔运行，证据写入 `store-page-evidence.json.safe_actions`，至少包含页码、`view_filled_slot/open_empty_image_text`、商品 ID、坑位序号、状态、`source=current_page`、`read_only=true` 和 `page_state_restored`。候选目标只能来自当前采集页的可见行和坑位，不得输入或搜索商品 ID；进入“发图文”表单后优先点击表单框外的遮罩退出，退出详情或表单后必须核验当前 URL、发布表单状态和有序商品 ID 已恢复，再允许翻页。不得把动作产生的页面状态写入商品采集 CSV。

## Collection readiness and managed attempt

`collection_readiness` 使用 `schema_version=1`、整体 `ready/status/checked_at` 和独立
`checks[]`。每项至少包含 `id`、`ready`、`reason_code`、中文 `message`、
`category`、`next_action`、`checked_at` 和安全 evidence。固定检查为 environment、
selector schema、selector current DOM、CDP、login/human-check、material page 和
store identity。

`current-attempt.json` 使用 `attempt_id` 绑定 session、stage、revision、input SHA、
selector SHA、target store、claim 和 purpose。私有 worker manifest 额外包含 PID、
ownership token 与 process identity；前端公开副本不得包含 token/process identity。
公开进度包含 phase、heartbeat、分页起点、页面真实当前页、可见末页、末页证明、
last completed page、row count、last checkpoint、log path 和 terminal status。旧结果进入逐 attempt 历史并标记
`superseded=true`，不得继续作为当前 blocker。

## Completeness Matrix

本轮只上传搜推素材，不审查或补传基础素材。运行 `tmall-materials inspect-completeness`，将搜推素材实时采集 CSV 转为第二阶段矩阵。矩阵范围严格等于“商品分类 → 搜推高价值”的全量采集结果；商品总表只补充名称、货号与负责人（“运营”列），不得扩入其他商品。输出使用 `contract_version=1`、`source_filter=search_recommend_high_value`，包含 `summary` 和 `products`。

每个商品必须包含：

- `product_id`、`sku`、`product_title`、`owner`、整体 `status` 和布尔值 `selectable`；`owner` 来自商品总表“运营”列，未填写时为空字符串。
- `eligibility.status/reason_codes/evidence`；标题或等级命中 `uvno`、`积分`、`清仓`、`好物体验`、`会员日` 时，写为 `status=excluded`、`selectable=false`，页面保留展示但禁选。
- `promotion.target_slots/current_count/missing_count`、远端素材 ID、原因码、采集时间和证据；无法识别容量时标记 `needs_manual_review`，不得根据分类猜测目标容量。
- `candidate_asset_count`；第二阶段尚未完成素材匹配时保持 `null`，页面显示“待素材匹配”，不得伪造为 0。

页面决定写入 `02-completeness/input.json.values`。`selected_product_ids` 只保存用户选择直接进入第三阶段“素材匹配”的可选商品 ID，至少选择一个才能提交。搜索、状态筛选和负责人筛选只改变当前显示范围；负责人筛选支持精确负责人和“未分配负责人”。“选择当前筛选结果”自动跳过排除项，“取消当前筛选结果”批量更新可见商品，不清除其他筛选条件下已经选择的商品。服务端提交时再次拒绝排除项或不属于当前矩阵的陈旧商品 ID。

第二阶段完整度巡检和第三阶段素材匹配在 Agent 返回 `needs_user_input` 或 `blocked` 时，同时保存只读 `review-context.json`。用户增量保存选择、文件夹确认或排除决定会更新 `input.json` revision，但必须继续展示该审查上下文；修改前置阶段时才使后续审查上下文失效。用户不能通过页面修改 `review-context.json`。

## Folder Index

`folder-index.sqlite3` 是每台电脑共享维护的机器级缓存，不属于任何时间戳任务，也不保存图片内容：

- `folder_id`：由来源与相对路径生成的稳定标识。
- `source_system`、`absolute_path`、`relative_path`、`folder_name`、`parent_relative_path`。
- `active`、`last_seen_scan_id`：用于 `--refresh` 标记新增、保留和已删除目录。
- 匹配表保存商品 ID、货号、商品名称、`match_type` 与 `match_status`。
- metadata 中 `filesystem_identity` 只绑定排序后的 `source_id + 本机 root`；
  `products_sha256` 单独绑定当前匹配结果。商品表变化可执行 `--rematch-only`，不使目录
  身份失效。旧版组合 `identity` 在 roots 一致时原地迁移。
- metadata 中 `active_scan` 是异常中断时的目录 frontier 检查点，包含扫描 ID、范围、
  当前来源/前缀、待处理相对路径、计数、错误和已完成范围；正常终态会删除。

`folder-index-progress.json` 是可覆盖的机器级运行状态，包含 operation ID、模式、
`running/complete/partial/failed`、phase、PID、开始/心跳时间、耗时、当前来源与相对路径
及发现/命中/错误计数。`.folder-index.lock` 是带 PID 和 operation ID 的临时单写租约；
正常退出删除，进程已不存在时下一写入者可回收。二者不进入时间戳任务目录。

团队索引长期保存不可变 `folders.csv`，不保存可跨任务复用的商品候选。第二阶段提交后，处理器按 `selected_product_ids`，使用当前任务商品快照、当前匹配器版本和本机已校验的 `team-cache` 即时生成任务内 `folder-candidates.csv`，并记录商品快照 SHA-256 与来源 snapshot ID；不复制 SQLite，也不读取历史全局候选 CSV。候选按文件夹自身名称匹配，不继承父目录命中。确认文件夹归属前，不读取文件夹中的图片，也不计算图片 SHA-256。

`prepare-folder-review` 生成的 `folder-review.json` 使用 `review_type=folder_ownership` 和 `safety_status=folders_only`。`folder_candidates` 每项包含 `folder_id`、商品 ID/标题/货号、`source_id`、`relative_path`、来源、文件夹名、仅供审计的历史完整路径、匹配类型、匹配状态以及当前决定；`folder_products` 提供逐商品候选数。新任务读取图片只使用 `source_id + relative_path`，不得使用绝对路径作为跨电脑业务标识。文件夹计数由素材执行器在访问本机绑定后回填 `ready + raw_recursive_image_count`，或者回填 `unknown + image_count_reason_code`；未知不得降级成 0。计数只枚举受支持图片路径，不读取图片内容。确定性候选默认 `confirmed`；`match_type=fuzzy_name_candidate` 表示基础名称至少 50% 的最长公共连续字符命中，默认 `rejected`，页面仍只提供采用和排除。历史 `pending` 按采用读取。用户明确提供完整文件夹名时，当前任务可生成 `match_type=exact_folder_query` 的候选；该记录不是别名。

组合商品标题按 `/`、`／`、`、` 或 `|` 拆分后只保留完整字面片段，不生成拼接名称。`match_type=split_name_candidate` 表示文件夹名完整包含不少于 5 字的拆分片段，默认 `confirmed`；`match_type=short_split_name_candidate` 表示完整包含 3–4 字拆分片段，默认 `rejected`；少于 3 字的片段不参与匹配。50% 粗略候选继续基于未拆分基础名称计算。

用户决定写入同一时间戳会话的 `input.json.values.folder_decisions`。每项必须包含：

- `folder_id`、`product_id`、`source_id`、`relative_path`，用于跨电脑绑定明确目录和商品；`source_system` 与 `folder_path` 只作为兼容或审计字段。
- `decision`：`confirmed` 或 `rejected`；未处理项不写入决定数组。
- `note`：可选人工说明。

最终采用决定可以驱动后续按需图片枚举；保存决定本身不读取图片。`rejected` 只排除其绑定的 `folder_id + product_id`，但必须立即从画廊移除对应候选并同步清理其采用和授权记录，不得推进阶段。素材匹配阶段保存文件夹决定时，页面创建 `status=queued` 的 gallery job，并自动请求 Windows 桌面启动一次性执行器。启动通道为现有 Explorer shell，避免继承 UI/Codex 看不到映射盘的令牌。执行器使用每机配置解析 source ID，并在读取前拒绝绝对相对路径、`..` 和目录逃逸。启动失败记录 `MATERIAL_EXECUTOR_LAUNCH_FAILED`；不可访问时记录 `SOURCE_BINDING_MISSING`、`SOURCE_ACCESS_DENIED` 或 `SOURCE_PATH_INVALID` 并允许页面重试，不得创建 Codex handoff，也不得把文件夹决定误报为 validation failed。

## Asset Record

字段：`asset_id`、`product_id`、`sku`、`asset_type`、`source_system`、`source_path`、`license_status`、`sha256`、`width`、`height`、`duration`、`validation_status`、`reason_codes`。

目录型素材默认只在 `<asset-root>/<商品ID>/` 查找，其次在 `<asset-root>/<货号>/` 查找。逐文件授权必须由素材清单表示；批次级 `--license-status confirmed` 只能用于该目录内所有文件已由用户确认同一授权状态的情形。

## Asset Gallery Result

素材匹配阶段使用 `workflow_step` 区分同一阶段内的两步：目录审查为 `folder_review`，本地 Worker 生成图片期间为 `gallery_preparing`，可选图结果为 `image_selection`。文件夹确认使用 `local_action` 事务更新 revision，但不生成 `handoff.json`。`gallery-job.json` 绑定 session/stage、revision/input SHA-256、文件夹决定 SHA-256、商品边界、候选策略、图片策略和本地资源身份；每次执行保留在 `gallery-attempts/<attempt_id>/`。图片画廊还包含 `gallery_identity`：`session_id`、`stage_id`、`prepared_from_revision`、`prepared_from_input_sha256`、`folder_decisions_sha256`、`prepared_folder_keys`。勾选单张图片时的双比例预裁剪结果写入 `selection-preflight-cache.json`，条目以 `asset_id` 索引，并绑定源 SHA-256、图片策略 SHA-256 和算法版本；算法版本 2 表示单次读取/解码后完成双比例编码探测。前端队列另维护 `queued|running|completed|cancelled|failed`、当前采用意图、意图版本和任务权重；该瞬时状态不进入 handoff，只有预裁剪通过且仍为最新采用意图时才写入正式图片决定。该文件是可失效重建的任务内缓存，不是 handoff。最终选图提交只接受当前任务且当前采用文件夹为已准备范围子集的画廊；重新采用未准备文件夹会使画廊失效。最终校验成功后写入 `selected-asset-preflight.json`、`final-material-package.json` 和唯一的 `handoff_kind=final_material_selection` 交接。

gallery Worker 枚举受支持扩展名后，先对每条唯一路径执行一次只读 `stat`；只有 200KiB–20MiB（含边界）的图片进入硬合规池，超限或元数据不可读的路径不读取、不哈希、不解码。每商品目标数为硬合规图片数与 100 的较小值。分配先覆盖非空采用文件夹，文件夹内依次选择 500KiB–15MiB 优先层、200KiB–不足 500KiB 小图补充层、大于 15MiB–20MiB 大图补充层；其余名额先从优先层按各文件夹剩余数量比例分配，只对缺口依次使用小图和大图补充层。合规候选使用最多 8 个线程的固定并发队列，每张统一占用一个工作槽；同一原图只读取、哈希和解码一次，预览直接复用该解码结果。异步完成项按原抽样序号合并，保证重复内容的保留归属不受线程完成顺序影响。每累计 10 张有序结果把累计只读结果写入 `partial-gallery.json`，并通过 `gallery-job.json.progress.available_candidate_count/published_batch_count` 触发前端渐进刷新；页面仍按每批最多 30 张分页。此时仍为 `gallery_preparing`，不能采用、预裁剪或提交。全部候选完成后才发布 `confirmed-gallery.json` 并切换为 `image_selection`。

`gallery-checkpoint.json` 是当前时间戳任务专用的可重建断点，绑定 gallery identity。每条完成项保存源路径、大小、mtime、完整 SHA-256、检查结果和任务预览路径；同任务重试只在身份、大小、mtime 与预览文件均匹配时复用。身份或源事实变化会重新读取，损坏文件会被安全重建。该文件、`partial-gallery.json` 和 `preview-cache/` 均不得提升为用户级或跨任务缓存。

素材匹配阶段的 `result.json.data` 包含：

- `requirements`：逐商品 `product_id`、`product_title`、后台 `missing_materials`、本阶段实际 `candidate_count`、`slot_image_min=3`、`slot_image_max=9` 和 `slot_planning_stage=slots_copy`。第三阶段不得生成 `required_images`，因为后台缺失篇数不等于本次必须创建的篇数。
- `asset_candidates`：逐候选 `asset_id`、商品、稳定 `folder_id`、可审计 `folder_path`、来源、绝对只读路径、SHA-256、尺寸、匹配类型、匹配状态、授权状态、校验状态与远端重复标记。
- `candidate_strategy`：默认覆盖优先、500KiB–15MiB 优先、仅按缺口依次补充小图和大图、层内按文件夹剩余图片数比例抽样时为 `proportional_task_sample`；`candidate_strategy_version=4` 表示该分层策略，只有显式离线审计流程才从全量图片索引生成。
- `candidate_limit` 默认按商品独立限制为 100，`page_size` 默认 30；`sampling_seed` 使用当前任务 ID，`sampling_identity_sha256` 记录策略 ID、版本与任务身份的稳定摘要。
- `candidate_size_preference`：记录硬门槛与优先层的字节边界、`fallback_order=[below_preferred, above_preferred]` 和文件夹覆盖开关，供任务恢复和审计解释分层选择。
- `scan_summary`：记录采用文件夹总数，以及明确区分的 `discovered_path_count`、`size_eligible_count`、`size_filtered_count`、`size_below_minimum_count`、`size_exceeded_count`、`source_stat_failure_count`、`preferred_size_count`、`fallback_below_preferred_count`、`fallback_above_preferred_count`、三层实际抽样数、`fallback_sampled_count`、`planned_inspection_count`、`inspected_count`、`inspection_failure_count`、`content_duplicate_count`、`final_candidate_count` 和 `pending_count`。不变量为 `planned=inspected+failures`、`inspected=final+duplicates`、`asset_candidates.length=final`；逐商品保存同口径字段。兼容字段 `discovered_count`、`prepared_count` 仍可读取，但新页面不能用它们表达最终候选数。逐商品还包含 `nonempty_folders`、`represented_folders`、`uncovered_folders`、`complete_folder_coverage` 和 `reason_codes`。每个采用文件夹都必须保留分配行，包含 `folder_id`、路径、来源、原始发现数、唯一发现数、硬合规数、三层图片数、三类预筛排除数、`base_allocation`、`proportional_allocation`、三层实际分配数、`fallback_allocation`、`sampled_images`、`final_candidate_count` 和 `zero_allocation_reason`。任务级按需候选不得把“发现路径数”表述成“已完成全量图片索引”；父子文件夹的原始递归计数允许重叠。
- `remote_dedupe_status`：仅在提供可信远端内容指纹并完成比对时为 `checked`；后台只有素材 ID 时为 `not_available`。
- `reason_codes`：包含远端指纹不可用等批次级原因。

用户决定写入同一时间戳会话素材匹配阶段的 `input.json.values`：

- `license_decisions`：`asset_id` 与 `status=confirmed`；由同一批 `asset_decisions` 中的采用项自动生成，不要求用户逐图重复勾选授权。
- `asset_decisions`：`product_id`、`asset_id`、`sha256`、来源、`decision=selected`、`selection_order`。第三阶段不写 `group_index` 或坑位内 `position`；第五阶段完成编排后再生成。

候选选择在大小合规池中执行任务内稳定伪随机抽样：路径先规范化排序，抽样身份绑定策略版本、任务、商品和稳定文件夹；相同任务输入不变时必须得到相同候选，新任务可以得到不同候选。默认只对当前任务抽中的合规候选计算 SHA-256；本地与当前批次重复以 SHA-256 排除。远端内容指纹不可用时不得把远端素材 ID 当作图片去重证据。图片预览必须同时满足“出现在当前结果中”及“位于配置根目录或当前任务已确认文件夹下”，从而兼容映射盘与 UNC 路径差异但不扩大文件读取范围。

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

`material_item` 保存 `task_id`、商品 ID、媒体类型、slot index、媒体清单、标题、描述、内容哈希、状态、尝试次数、远端素材 ID 和原因码。千牛搜推列表可能在发布后把新素材插入现有素材之前，因此远端证据必须同时保存发布前 ID 集合、发布后集合差异、当前显示位置和 `method=remote_id_set_delta`；不得仅凭批准时的 1-based 坑位位置写入 ID。只有旧集合完整保留且新增集合恰好包含一个 ID 时，任务才能进入 `submitted`、`under_review` 或 `success`。

交互授权转换出的发布批次保存到 `07-approval/publish-runs/r<revision>-<input_sha前缀>/`，其中包含 `run.json`、`product-tasks.json`、`material-items.json`、`run.sqlite3`、`approval-manifest.json` 和 `upload-results.json`。同一 revision 与 input SHA 的重复调用只能幂等恢复；新 revision 必须使用新目录，禁止覆盖旧批次。

坑位 task ID 由 `run_id + product_id + material_type + slot_index` 确定性生成。

## Approval Manifest

必需字段：schema version、run ID、目标店铺、批次输入 SHA-256、精确 task ID、商品 ID、媒体 SHA-256、标题、描述、动作、批准人、批准时间和 manifest SHA-256。`valid_until` 仅为旧清单兼容字段，新流程不生成。

manifest SHA-256 覆盖除自身之外的完整规范化批准信封，不能只覆盖 entries。发布前重算批次输入文件和每个媒体文件的 SHA-256；批准后的内容变化不得沿用旧批准。旧清单包含 `valid_until` 时仍执行兼容校验。

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

第四阶段 `input.json.values.decisions[]` 使用 `suitability_decision`：`asset_id`、`product_id`、`decision=candidate|excluded`、`candidate_ratios[]`、`crop_candidates.<ratio>`、`requires_compression` 和 `asset_matching_revision`。第四阶段输入不得包含正式 `output`；兼容读取的旧 `action/target_ratio/crop_box` 只用于迁移审计。

第五阶段 `review-context.json.data` 使用 `slot_plan` schema，包含 `image_review_revision`、`policy_sha256`、`products[].assets[]`（一张原图一条规范记录）、`products[].outputs[]`（比例能力候选，不是正式输出）、`blocked_outputs[]`、`slot_image_min=3`、`slot_image_max=9`。规范素材以 `asset_id + source_sha256` 为身份，保存宽高、原始比例、`size_bytes`、显示大小、格式和嵌套的 `ratio_options.1:1/3:4`。新任务不生成 `rule_drafts[]`；历史规则字段仅供只读审计。`slot_assignments[]` 包含 `slot_id`、`product_id`、有序 `asset_ids`、单一 `target_ratio`、来源和 revision 绑定。

用户确认计划后，`processed-outputs.json` 记录 `plan_sha256`、逐坑位实际输出、源/输出 SHA-256、处理参数和 `workflow_state=outputs_ready`；正式文件只位于 `05-slots-copy/derived/`。`copy_edits[]` 必须逐 `slot_id` 记录标题、描述、人工确认、计划 SHA 和最终输出 SHA 列表。

每个决策模式保存于 `<stage>/decision-modes/<decision_id>.json`，必含
`schema_version`、`session_id`、`stage_id`、`decision_id`、`mode`、
`selected_by`、`selected_at`、`bound_revision` 和 `persisted`。模式必须来自
阶段注册表允许集合；revision 不一致时返回 `DECISION_REVISION_STALE`。

Codex 辅助请求位于 `05-slots-copy/agent-requests/<request_id>/`：

- `request.json` 状态为 `pending_agent|processing|completed|failed|superseded|cancelled`，每次迁移记录 actor、时间、context revision 和 reason code。
- 候选记录商品身份、asset ID、源 SHA-256、比例候选、裁剪框、压缩需求，以及请求目录缩略图路径、尺寸、大小、来源和缩略图 SHA-256；不得暴露共享盘源路径给响应消费者。
- `response.json` 绑定 request ID、provider、模型和分析 schema。坑位方案必须包含同商品、单一比例、3–9 张唯一有序图片、理由、`ai_confidence`、预计裁剪/压缩数、裁剪风险、多样性和重复摘要。
- `adoption.json` 绑定 response SHA-256、proposal index 和采用后的草稿 revision；采用只更新 `slot_assignments`，不生成 `processed-outputs.json`。
- `rejection.json` 记录拒绝方案及说明，不改动当前草稿。

规则建议使用 `rule_score` 和评分组成，AI 建议使用 `ai_confidence`，两者不得混用。

## 第五阶段 AI 默认工作流（Schema v2）

- `slot_plan_with_analysis` 请求绑定 session、`slots_copy` revision、第四阶段 revision、策略 SHA、剩余坑位和受控缩略图预算。
- 响应固定分为 `asset_analysis`、`clusters`、`slot_plan`。图片分析包含场景、主体、拍摄类型、角度、姿态、商品可见度、风格、质量、比例风险和重复组。
- `current-slot-plan.json` 是唯一活动草稿，包含 `plan_revision`、`workflow_state`、`decision_source`、`confirmed`、原 Agent request/response 身份以及审计事件。人工修改将来源改为 `manual_override`。
- 新草稿来源只允许 `agent_assisted`、`manual`、`manual_override`。历史 `rules` 与 `agent_assisted_with_rules_fallback` 可读取；一旦编辑即写为人工来源，不再生成规则草稿。
- 保存草稿允许空坑位和少于 3 张的中间状态；确认时严格校验每坑 3–9 张、坑位 ID、商品归属及 `asset_id + source_sha256` 跨坑唯一。
- 历史素材缺少宽高、大小或格式时，只按该素材的 `source_path` 增量补采；已有快照不重复读取，也不触发共享盘全量扫描。
- 坑位必须包含主题、数量理由、3–9 张唯一有序图片、逐图角色/理由/新增信息、未采用原因和预计裁剪压缩数。
- `crop-preflight.json` 绑定已确认的 `plan_revision`、`plan_sha256`、完整裁剪/压缩参数、逐图输出身份、检查数量、`minimum_size_bytes=204800` 和通过时间。只有该绑定与当前计划及页面参数完全一致时，图片完成按钮才可用；任何坑位、顺序、比例、裁剪框或压缩参数变化都会使其失效。
- `processed-outputs.json` 同时保存计划 SHA 和包含裁剪参数的 processing SHA；输出逐图保存源/输出 SHA、宽高、大小、比例和顺序。它只可由仍然有效的 `crop-preflight.json` 转换得到，完成时复核任务内实际文件，不重复读取共享盘原图或重新裁剪。
- `copy_draft` 请求绑定 `slot_plan_revision`、最终输出集合 SHA 和逐坑有序输出；`request_context.authorization` 固定记录 `status=granted`、授权来源、session/stage/revision、最终输出 SHA、各坑位首图身份、允许动作清单、`requires_chat_confirmation=false`、`publish_allowed=false` 及整个授权信封的 SHA-256。请求创建后状态为 `pending_agent`（保留该 schema 枚举以兼容历史数据），由工作台后台调度器通过唯一 `process-copy-request` 入口领取；处理器必须在打开浏览器前复核授权信封与当前不可变输出，旧版请求仅在最终输出指纹仍精确一致时补齐限定授权。`progress.json` 保存 `pending|processing|completed|failed`、总坑位数、已完成数、当前 `slot_id`、逐坑已完成草稿及最后错误；正式 `response.json` 只在全部坑位完成后写入，并逐坑包含标题、描述、依据、风险、校验原因和未确认状态。
图片分析缓存键由源 SHA-256、`codex-agent-handoff`、模型标识和 schema 版本共同确定。
# Agent wait envelope

`session.json.agent_wait` 可为空；存在时为：

```json
{
  "schema_version": 1,
  "wait_id": "opaque-id",
  "claimant_id": "codex-agent",
  "session_id": "20260729_171047",
  "stage_id": "setup",
  "expected_revision": 12,
  "started_at": "ISO-8601",
  "heartbeat_at": "ISO-8601",
  "expires_at": "ISO-8601",
  "budget_expires_at": "ISO-8601"
}
```

它只用于历史兼容 UI 和诊断，不属于新任务正常调度。兼容 `listen` 每次最多等待 15 秒并续租同一 wait ID，`expires_at` 最长为当前心跳后 30 秒；`budget_expires_at` 是整个兼容监听窗口，续租不得重置。处理排他权仍只来自 `processing_claim`。handoff 独立持久化，不随 wait 超时或清理而删除；新任务由工作台后台队列与恢复扫描取得该 claim。

## Agent-only 异常信封

`<session>/agent-diagnostics/current.json` 是 Codex 读取技术异常的唯一当前入口，业务前端
不得渲染其中的 reason code、堆栈、文件路径或重试命令。必需字段为：

- `session_id/stage_id/revision/input_sha256`：绑定原 handoff 身份；
- `status=open|resolved`、`phase`、`reason_code/reason_codes[]`；
- `owner=codex|user_via_codex` 与 `user_action_required`；
- `message/evidence[]`：仅供 Codex 诊断；
- `processor/handoff_kind/phase/error_type/traceback`：指出唯一失败入口、交接类型、失败阶段和完整技术堆栈；
- `retry.idempotent=true` 与 `retry.command`：只能重跑同一正式处理入口；
- `created_at/updated_at/resolved_at`。

每次写入同时在 `agent-diagnostics/history/` 留下不可变快照。阶段按同一 revision 成功完成
后把当前信封标记为 `resolved`，不得删除历史证据或创建新的业务 revision。
