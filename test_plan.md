# Upload Search Materials 测试计划

**目标：** 按“测试环境 → 交互页面与任务隔离 → 素材清单与一个或多个图片来源 → 图片策略 → 生产选择器 → 全量 dry-run → 1–3 商品生产验收 → 更新文档和发布状态”逐阶段验证 `upload-search-materials`。

**执行原则：** 一次只执行一个阶段。每个阶段必须留下命令、退出码、实际结果和证据；涉及代码修复时先写失败测试；真实发布必须再次获得用户明确授权。视频测试本轮延期，不阻断图片测试。

## 全局规则

### 状态

- `未开始`：尚未执行。
- `进行中`：正在执行。
- `通过`：命令、预期结果和证据全部满足。
- `失败`：实际结果与预期不符，需要修复并重测。
- `阻断`：缺少配置、权限、用户输入或生产授权，禁止继续。

### 安全门禁

- [ ] 默认只运行 `dry-run`。
- [ ] 阶段 7 开始前，用户必须在当前对话中再次明确授权真实上传。
- [ ] 交互页面只采集输入、保存 JSON 和交接 Agent；页面本身不执行上传或发布。
- [ ] 阶段 08“生产确认”和阶段 09“结果”只用于交接与展示，不直接触发生产动作。
- [ ] 不保存或输出密码、Cookie、Token、验证码、短信码或二维码登录数据。
- [ ] 不调用未公开接口，不绕过登录、验证码、风控或权限。
- [ ] 店铺、商品、坑位、素材指纹或批准清单不一致时立即停止。
- [ ] 每个任务最多点击一次发布；结果不确定时先回查，不盲目重试。
- [ ] `docs` 和原始媒体只读，不覆盖、不删除。
- [ ] 每次完整任务必须创建独立的时间戳目录；同秒创建时必须追加稳定后缀，禁止任务间共用状态文件。

### 证据目录

```text
test_evidence/
  01-environment/
  02-interaction/
  02-assets-video/       # 历史图片子阶段证据，保留不移动
  03-assets-video/       # 三处图片来源的后续证据
  04-image-policy/
  05-selectors/
  06-dry-run/
  07-production-pilot/
  08-release/
```

每个阶段记录：

```text
状态：
执行时间：
执行命令：
退出码：
预期结果：
实际结果：
证据文件：
问题与后续动作：
```

## 运行时变量

生产值由用户提供，并保存在版本库之外：

| 变量 | 用途 | 首次需要阶段 |
| --- | --- | --- |
| `TMALL_INTERACTION_RUNS` | 时间戳隔离的交互任务根目录 | 2 |
| `TMALL_ASSET_ROOT` | NAS/本地素材根目录 | 3 |
| `TMALL_ASSET_MANIFEST` | 逐文件授权和媒体元数据清单 | 3 |
| `TMALL_MEDIA_POLICY` | 已确认的媒体策略 YAML | 4 |
| `TMALL_STORE` | 页面可见的准确店铺名 | 5 |
| `TMALL_SELECTOR_CONFIG` | 生产选择器 YAML 绝对路径 | 5 |
| `TMALL_CDP_URL` | 用户启动的本地 CDP 地址 | 5 |
| `TMALL_COPY_RESPONSES` | AI 文案响应 JSON | 6 |
| `TMALL_PILOT_RUN` | 生产小批量批次目录 | 7 |
| `TMALL_APPROVER` | 批准人可审计身份 | 7 |

变量为空、路径不存在或内容未确认时，相应阶段标记为 `阻断`。

---

## 阶段 1：测试环境

**状态：** 通过（2026-07-20）

**目标：** 使用 `uv` 建立可复现的 Python 3.11 环境和锁文件，证明 CLI、完整测试和 Skill 校验可以运行。

### 执行清单

- [x] 确认 `uv` 和项目固定的 Python 版本。
- [x] 按 `pyproject.toml` 和 `uv.lock` 同步 `.venv` 与测试依赖。
- [x] 验证 CLI、完整 pytest 和 Codex Skill 校验器。

```powershell
uv --version
Get-Content .\upload-search-materials\.python-version
uv sync --project .\upload-search-materials --extra test
uv lock --project .\upload-search-materials --check
uv run --project .\upload-search-materials --locked tmall-materials --help
uv run --directory .\upload-search-materials --locked python -m pytest -q tests --basetemp ..\test_evidence\01-environment\pytest-temp -o cache_dir=..\test_evidence\01-environment\pytest-cache
$quickValidate = Join-Path $env:USERPROFILE ".codex\skills\.system\skill-creator\scripts\quick_validate.py"
uv run --project .\upload-search-materials --locked python -X utf8 $quickValidate .\upload-search-materials
```

**实际结果：** `uv 0.11.29` 使用 CPython 3.11.15，同步 17 个包；`uv lock --check`、CLI 帮助和 Skill 校验通过，Skill 校验输出 `Skill is valid!`；当时完整测试为 `95 passed, 1 warning`。唯一警告来自真实基础素材 XLSX 缺少默认样式，openpyxl 使用默认样式读取；只读测试通过。

**证据：** `test_evidence/01-environment/uv-version.txt`、`uv-tree.txt`、`cli-help.txt`、`pytest-full.txt`、`skill-validation.txt`。

---

## 阶段 2：交互页面与任务隔离

**状态：** 通过（2026-07-24；任务配置精简、自动输入状态、任务隔离、真实浏览器与非生产 handoff 已验收）

**目标：** 验证十阶段交互向导、时间戳任务隔离、JSON 交接、CLI 等待和恢复提示；任何页面动作都不得直接上传或发布。

### 自动化与 CLI 检查

- [x] 交互阶段、会话、API/UI 自动化测试已完成一轮实现级验证。
- [x] CLI `interact` / `wait-handoff` 已完成聚焦验证。
- [x] 控制器对全部评审修复运行最终回归，并保存退出码和完整日志。

```powershell
New-Item -ItemType Directory -Force test_evidence\02-interaction | Out-Null
uv run --directory .\upload-search-materials --locked python -m pytest -q tests\test_interaction_stages.py tests\test_interaction_session.py tests\test_interaction_web.py tests\test_interaction_ui_state.py --basetemp ..\test_evidence\02-interaction\pytest-temp -o cache_dir=..\test_evidence\02-interaction\pytest-cache 2>&1 | Tee-Object test_evidence\02-interaction\pytest-focused.txt
node --test .\upload-search-materials\tests\ui_state.test.cjs 2>&1 | Tee-Object test_evidence\02-interaction\node-ui-state.txt
uv run --project .\upload-search-materials --locked tmall-materials --help | Select-String "interact|wait-handoff"
uv run --project .\upload-search-materials --locked tmall-materials interact --help
uv run --project .\upload-search-materials --locked tmall-materials wait-handoff --help
```

**已确认自动化证据：** 最终交互协议/API/UI/编排聚焦测试 `112 passed`，Node UI-state 测试 `9 passed`，全量回归 `203 passed, 1 warning`；唯一 warning 仍为只读源 XLSX 缺少默认样式。CLI 聚焦测试 `6 passed`、Skill validator 通过，Skill 压力场景 `5/5 GREEN`；`uv lock --check` 通过，wheel 构建成功并包含页面模板与全部静态资源。

### 会话、交接与一致性检查

- [x] 每次完整任务创建 `YYYYMMDD_HHMMSS` 时间戳目录；同一秒创建两个任务时，第二个目录带碰撞后缀且两者状态互不影响。
- [x] 页面提交后，当前阶段生成 `input.json` 和 `handoff.json`；`wait-handoff` 输出绑定当前 session、stage、revision 和 `input_sha256` 的 JSON。
- [x] “最近一次提交时间”来自当前阶段有效 `handoff.created_at`，刷新页面和服务重启后仍存在；切换阶段不得显示上一阶段时间。
- [x] 过期 revision、错误 input hash、旧阶段或旧 revision 的 `result.json` 均被拒绝或不展示。
- [x] 服务重启后能恢复任务；无活跃 Agent 时页面显示明确的恢复指令，不伪装成已经唤醒 Agent。
- [x] 保存/提交请求返回前切换阶段，响应不得污染新阶段的表单、提交时间、状态或结果。
- [x] 结果区只从当前绑定且校验通过的 `result.json` 展示摘要、证据、阻断原因和下一步；未扫描时显示“尚未扫描”，不得显示硬编码数量。
- [x] `session.json`、`input.json`、`handoff.json`、`result.json` 使用版本化协议；缺失或不支持的 `schema_version` 在 SessionStore 和 Web 读取路径均拒绝处理。
- [x] handoff 在会话锁内独占领取；同一 revision 的顺序或并发 waiter 不会重复启动 Agent 工作。
- [x] 草稿保存使用 revision compare-and-swap；旧页面不能覆盖较新的提交或结果，前端以服务端返回 revision 为准。
- [x] 页面不包含上传/发布调用；阶段 08/09 仅交接 Agent 和展示结果。
- [x] 任务配置主界面只保留店铺确认、目标月份和商品范围；月份默认当前月，商品范围默认全部符合当月规则的商品。
- [x] 商品表和规则表自动发现并只读展示；图片源可在页面新增、删除、检测及保存为本机配置；基础素材显示自动导出，推广素材显示自动采集，运行目录由系统创建且不可编辑。
- [x] 人工图片素材清单、历史基础素材表和历史推广素材状态只在高级设置中作为可选导入；视频延期、别名在文件夹审查中积累。
- [x] 任务目录只保存输入快照、自动导出/采集、当前候选、用户决定、dry-run、批准和结果；NAS 原图及共享文件夹索引不重复复制。

### 本地服务与浏览器视觉验收

- [x] 启动仅监听 `127.0.0.1` 的本地服务，保存启动日志和实际 URL。
- [x] 在真实浏览器检查十阶段左侧导航、字段、帮助文案、空状态、提交状态、恢复提示，以及 1440×900 / 1024×768 布局；1024 宽度无横向溢出，滚到底部后输入和恢复区域不被固定交接栏遮挡。
- [x] 确认三处图片来源可编辑，视频入口禁用并显示“本轮测试延期”，阶段输入均有保存/提交接口。

```powershell
uv run --project .\upload-search-materials --locked tmall-materials interact --runs-root test_evidence\02-interaction\runs --port 8765
```

**浏览器与 handoff 证据：** 最终本地会话 `20260722_114534` 在阶段 04 提交三处图片源；页面显示 revision `1` 和持久化提交时间。`wait-handoff` 返回 SHA-256 `868fdb9fd2e9af15dfca298c246dd4877655de36a10addaf5f2f85d65ef88cb2`，与 `input.json` 实算一致。通过 `SessionStore.write_result` 写入非生产结果后，页面正确展示摘要、证据、阻塞原因和“进入图片策略人工审查，不执行上传”；全程未调用上传或发布。

**2026-07-24 任务配置精简验收：** 页面主配置只保留店铺确认、默认当前月份和默认“全部符合当月规则的商品”；商品表、规则表、三处图片源与运行目录只读展示，基础素材为自动导出、推广素材为自动采集。人工素材清单和两类历史文件位于高级设置。隔离会话 `20260724_135211` 保存草稿后只持久化新契约的 11 个键、3 个共享图片根目录和 `product_scope=all_eligible`，旧 `basic_xlsx/search_xlsx/asset_root/runs_root` 均未进入 `input.json`。证据见 `test_evidence/04-interaction-ui/setup-config/20260724_135007/acceptance.json`、`setup-default.png`、`setup-advanced.png` 和对应 `runs/20260724_135211/01-setup/input.json`。

**2026-07-24 跨电脑路径验收：** 商品表和规则表改为从项目根目录 `docs/` 按受控模式唯一匹配；共享图片目录只从 Git 忽略的 `config/local-paths.json` 或显式配置读取；运行目录默认使用项目 `runs/`。无共享盘配置、表格缺失或盘符不同均不得阻止交互页面启动，歧义文件不得自动选择。源码防回归测试禁止写入个人用户名和 `Y:`/`Z:` 固定盘符。

**2026-07-24 动态图片源配置验收：** 任务配置页改为可复用的动态图片源组件，允许配置 1–50 个来源，不再限定三处；每项保存来源名称和根路径，支持新增、删除、只读可访问性检测及保存到 Git 忽略的本机配置。setup handoff 新增 `image_source_labels` 并与 `image_roots` 按顺序绑定；零项、空值、重复名称或重复路径必须拒绝。

**2026-07-24 交互提交协议验收：** 图片源已增加用户触发的系统文件夹选择入口；编辑期防抖自动保存草稿，草稿不触发 Agent；正式提交后前后端同时冻结，只有未认领 handoff 可撤回，processing/completed 禁止覆盖；每个 revision 保存历史快照；恢复指令绑定精确 session/stage/revision；前序阶段未完成时拒绝提交下一阶段；交互服务启动前检测端口占用并报告 PID。隔离浏览器会话 `20260724_163755` 验证自动保存、提交冻结、恢复入口启用和撤回解锁均成功；系统目录窗口保留为用户点击验收，不在无头浏览器中代替用户选择。

**通过标准：** 最终自动化回归、JSON 绑定、重启恢复、跨阶段竞态和真实浏览器视觉检查全部通过；页面始终不执行上传或发布。

---

## 阶段 3：素材清单与一个或多个图片来源

**状态：** 阻断（2026-07-24 文件夹索引、真实候选归属审查页面和素材候选画廊 MVP 已完成；用户尚未完成 11 个真实候选的业务确认，按需图片候选、授权和完整性审查待执行；视频延期）

**目标：** 验证目录型或逐文件清单型素材能够进入 dry-run，并按商品安全匹配；本轮只测试图片。

### 本轮可编辑/测试图片根目录

1. `Y:\视觉部\1-模特图`
2. `Z:\浙江酷趣\运营中心\营销板块\小红书koc置换&淘宝买家秀\优质买家秀`
3. `Z:\浙江酷趣\运营中心\营销板块\小红书koc置换&买家秀\优质买家秀`

匹配可以依据精确商品 ID、SKU、已确认别名，或需要人工确认的商品名称候选。图片文件名不要求是订单 ID；目录名、清单字段和人工确认结果均可参与匹配，但候选名称不得自动冒充精确命中。

### 执行清单

- [x] 目录型和 `--asset-manifest` 入口进入 CLI，逐文件授权不被批次级参数覆盖。
- [x] 含 `confirmed` / `unknown` 的清单返回稳定授权结果。
- [x] 有商品 ID 的清单行必须精确匹配商品 ID，避免共享 SKU 跨链接取错素材。
- [x] 分片增量索引器完成本地隔离 new、持久 checkpoint 中断恢复和增删改 refresh 验收；索引候选不自动生成批准清单。
- [x] 默认改为文件夹一级索引：三处真实根目录共记录 `5,279` 个文件夹，耗时约 `225.7` 秒，三源完成且错误为 0；不读取或哈希图片内容。
- [x] 文件夹候选只按自身名称匹配，不把父目录命中传播到 `KV/合成/1/2/3/4` 等普通子目录；真实候选由 22 条降为 11 条。
- [x] 文件夹索引支持 `--refresh` 更新增删改，支持 `--rematch-only` 在不重新遍历 NAS 的情况下重算名称、货号和别名匹配。
- [x] `prepare-folder-review` 将真实候选转换为 `folders_only` 审查数据；页面显示商品 ID、货号、来源、命中类型、文件夹名和完整路径，且明确确认前不读取图片。
- [x] 页面预留“确认归属 / 确认归属并记录别名 / 排除”及备注输入，决定绑定文件夹与商品并保存到当前时间戳会话的 `folder_decisions`。
- [x] `prepare-gallery` 可将只读索引与推广素材状态合并，按“缺失篇数 × 每篇图片数”生成逐商品候选和应选图片数。
- [x] 页面可展示候选缩略图、三处来源、匹配方式、授权状态和采用状态；图片预览仅允许当前结果中且位于当前 `image_roots` 下的文件。
- [x] 候选使用稳定排序和固定分片，“换一批”不随机；支持先取消一张再选择另一张，并将结果保存到当前时间戳任务 JSON。
- [x] 当前任务内使用 SHA-256 排除本地/跨商品重复；缺少后台图片指纹时明确显示“远端去重未完成”，不得声称远端已去重。
- [ ] 对上述三处根目录分别生成只读扫描清单，再按商品合并候选；记录来源、匹配方式、命中数量、待确认项和 SHA-256。
- [ ] 由用户在页面完成全部 11 个真实候选的业务确认；交互与保存机制已验收，但自动验收中的 3 条示例决定不视为用户批准，也不用于读取真实图片。
- [x] 在页面逐商品展示搜推素材完整性审查表，至少包含商品 ID、商品名称、应有篇数、已有篇数、缺失数量、缺失类型、候选素材数量、后台证据和人工处理结论；用户确认结果保存到当前时间戳任务的 JSON。基础素材仅用于筛选售卖中范围，本轮不审查、不补传。
- [ ] 对 `uvno`、清仓、好物体验类、会员日和积分商品显示“排除维护”及具体原因；不得仅从页面隐藏，也不得进入后续补素材候选。
- [ ] 本轮图片扫描范围仅为上述三处 NAS 根目录；飞书表格/文档图片入口标记为“后续接入，等待路径、导出格式与读取权限确认”，不得视为已验收。
- [ ] 视频元数据与视频策略检查：`延期`，不得阻断本轮图片测试。
- [ ] 光合短视频下载入口标记为“视频阶段待确认”，后续需向业务负责人确认入口、授权和下载方式；本轮不得视为已验收。
- [ ] 比较测试前后原始图片的大小、mtime 和 SHA-256。

```powershell
New-Item -ItemType Directory -Force test_evidence\03-assets-video | Out-Null
uv run --directory .\upload-search-materials --locked python -m pytest -q tests\test_assets.py --basetemp ..\test_evidence\03-assets-video\pytest-temp -o cache_dir=..\test_evidence\03-assets-video\pytest-cache 2>&1 | Tee-Object test_evidence\03-assets-video\test-assets.txt
uv run --project .\upload-search-materials --locked tmall-materials run --help | Select-String "asset-root|asset-manifest"
```

**已完成图片子阶段实际结果：** 完整测试当时为 `99 passed, 1 warning`。真实样本使用商品 `887508274682 / KQ25024 / 小芭蕉卷卷帽` 的同一达人 3 张图片，均成功读取为 `854×1280`；授权未知返回 `LICENSE_UNKNOWN`，当前比例策略返回 `ASPECT_RATIO_INVALID`；测试前后大小、mtime 和 SHA-256 一致。

**2026-07-24 搜推完整度页面验收：** 隔离会话 `20260724_172019` 共生成 `629` 个售卖中商品；其中 `30` 个已有实时搜推证据并判定待补充，`599` 个标记为“需后台补采”，不得视为已完成扫描。浏览器实测筛选后为 `30` 行，搜索商品 `1065650301510` 后为 `1` 行；填写人工结论并自动保存后，筛选、搜索词与当前矩阵均保持不变。页面未出现基础素材审查字段。证据见 `test_evidence/02-completeness/20260724-stage2/visual-acceptance.json` 与 `stage2-promotion-only.png`。

**2026-07-24 可配置采集页数：** 任务配置页增加可选 `promotion_max_pages`。用户填写 `1–10000` 的整数时，Agent 必须调用 Skill 的 `supplement --scan-mode recommended --max-pages <值>`；留空时不传 `--max-pages` 并采集全部分页。该字段保存到当前时间戳任务的 setup `input.json`，限制页数时未覆盖商品不得标记为完整。

**2026-07-24 五页真实采集验收：** 隔离会话 `20260724_215033` 在配置页填写 `promotion_max_pages=5`，自动保存后由测试脚本读取 setup `input.json` 并调用 Skill 的 `supplement --scan-mode recommended --max-pages 5`。真实店铺 `kocotree旗舰店` 完成 5 页，checkpoint 为 `last_completed_page=5`、`row_count=50`，CSV 为 50 行、50 个唯一商品 ID；未点击搜推页的经营数据导出按钮。与当前基础素材售卖中范围合并后为 627 个商品：50 个待补充、577 个需后台补采。该范围比上一版 629 少商品 `1027968776704`、`908285760995`，进入全量采集前需核对两次基础素材范围差异。

**2026-07-22 三源预检结果：** 隔离会话 `20260722_143048` 已创建。三个根目录均可只读访问；使用 `rg --files` 统计图片共 `413,309` 张，其中 Y 盘模特图 `396,683` 张（2026：`116,262`，2025：`165,937`，2024：`82,031`，2023：`32,453`），小红书 KOC 与淘宝买家秀 `14,042` 张，小红书 KOC 与买家秀 `2,584` 张。三处来源各抽取 1 张图片完成可读性、尺寸和 SHA-256 检查，读取前后大小与 mtime 一致；三张均因尚未确认逐文件授权返回 `LICENSE_UNKNOWN`。仓库已有商品总表 `611` 行、基础素材 `2,158` 行和搜推数据 `808` 行；商品表中 `33` 行缺商品 ID、`11` 行重复商品 ID，必须保持 blocked。样本“小鸟岛针织帽”和“小萌宠滑雪服”命中清仓排除；“花仙子翻翻帽-椰蓉”仅作为 `1009488728622 / KQ26021 / 花仙子翻翻帽` 的待确认别名。`tests/test_assets.py` 为 `13 passed`。

**2026-07-23 索引器本地隔离验收：** 在 `test_evidence/03-assets-video/indexer-acceptance/20260723_101741/` 创建最小商品表和三个本地图片 roots，覆盖 ID 精确候选、SKU 精确候选、名称人工候选和未匹配图片。completed new 生成数据库、候选 CSV 与摘要，结果为 3 roots、4 active files、3 matched files、3 candidate groups；在 1 个 partition 的 checkpoint 已持久化后制造 `KeyboardInterrupt`，中断退出码 `1` 且生成等价恢复命令，实际 `--resume` 退出码 `0` 并完成；图片增、改、删后 `--refresh` 验证 4 active / 1 inactive、修改文件重新哈希、新文件 active、删除文件 inactive，以及 SKU 候选消失、ID 候选图片数变为 2。三源、UTF-8/CSV BOM、`license_status=unknown`、名称 `needs_manual_confirmation`、不生成 `confirmed-assets.csv`、索引前后源文件不被索引器修改均通过；未访问 NAS、未测试视频、未 dry-run、上传或发布。自动验证为 pytest `265 passed, 1 skipped, 1 warning`、Node `9 passed`、`uv lock --check` 与 Skill validator 通过；warning 仍为只读源 XLSX 缺少默认样式。

**2026-07-23 索引器最终修复验收：** 在 `test_evidence/03-assets-video/indexer-acceptance/20260723_165014/` 重新运行三本地 roots 的实际 CLI/API new、持久 checkpoint 中断后 `--resume`、以及 `--refresh`。商品表为 46 行：2 行有效、33 行缺商品 ID、11 行重复商品 ID；new 退出码 `0`，行级 blocked 44 行均以 source row + reason codes 留证并排除匹配，有效行继续产生 ID/SKU/名称候选。new 本轮为 discovered/indexed/matched/failed `7/7/5/1`，单文件 `FILE_INSPECTION_ERROR` 未使 partition 或整批失败；3 个 root 及逐 partition 的 current scan 状态和计数完整。中断时已观测前一 partition 在扫描后一 partition 前持久化为 completed，后一 partition 的 processed count 为 `1`；在排序游标前删除、重命名和新增文件后，`--resume` 退出码 `0`，从分片头部重扫并正确 inactive 旧路径。refresh 退出码 `0`，新增文件 active、删除文件 inactive、修改文件重新哈希，本轮计数仍为 `7/7/5/1`。候选授权保持 `unknown`，名称候选保持 `needs_manual_confirmation`，new 与 refresh 前后源文件 snapshot 不变；未访问 NAS、浏览器、dry-run、上传或发布。证据见同目录 `acceptance-summary.json`、`commands-and-results.json`、`new-output/` 与 `interrupt-resume-output/`。

**最终修复自动验证：** Python 全量回归 `289 passed, 2 skipped, 1 warning`；聚焦索引器回归 `100 passed, 2 skipped`；Node UI 状态测试 `9 passed`；`uv lock --check` 与 Skill validator 均通过。最终故障注入补充验证了“未见文件失活 + 分片完成”使用同一 SQLite 事务，以及 root/partition 在同步窗口中断时仍生成一致的本轮统计；独立完整分支复审为 `READY`，Critical/Important 均为 0。唯一 warning 仍为只读源 XLSX 缺少默认样式。Skill 文档使用两个独立 fresh-context Agent 完成 RED/GREEN：旧文档无法确认 33/11 行级错误可继续，修复后明确只有 schema/batch/空表阻断整批，行级 blocked 留证并排除。

**2026-07-24 素材候选画廊 MVP：** 新增 `prepare-gallery`，从 `asset-index.sqlite3` 与 `promotion-material-status.csv` 生成确定性候选数据；页面实现逐商品缺失量、候选缩略图、来源、匹配方式、授权确认、采用、单张替换和“换一批”，决定保存到当前时间戳会话 JSON。隔离视觉验收会话 `20260724_040238` 从 `ASSET-01/02/03` 稳定切换到 `ASSET-07/08/04` 并保存草稿；本地 SHA-256 去重生效，后台图片指纹不可用时页面明确显示“远端去重未完成”。该验收使用本地模拟三源图片，不代表真实 NAS 授权、真实名称候选或远端内容去重已完成。

**2026-07-24 真实文件夹归属审查页面：** 新增 `prepare-folder-review` 和素材匹配阶段 `folder_decisions`。真实三源的 11 个候选按 3 个商品分组展示，页面包含文件夹名、完整路径、来源、货号/名称命中、处理进度、确认、别名确认、排除和备注接口，并显示“确认归属前不会读取、统计或哈希图片”。隔离验收会话 `20260724_111144` 验证 11 张文件夹卡片全部显示，并以 1 条确认、1 条别名确认、1 条排除验证 JSON 落盘；这些决定仅为 UI 自动验收样例，不代表用户业务批准。

**本次自动验证：** Python 全量回归 `318 passed, 2 skipped, 1 warning`；文件夹审查相关定向回归 `79 passed`；Node UI 状态测试 `9 passed`，`app.js` 语法检查、`uv lock --check` 与 Skill validator 均通过。唯一 warning 仍为只读真实 XLSX 缺少默认样式。真实文件夹索引与重新匹配证据位于 `test_evidence/03-assets-video/folder-index-real/20260724_092758/`。

**当前阻断：** 真实三源文件夹索引和归属审查页面已完成；下一步由用户在页面确认 11 个真实候选文件夹的归属，特别是 `KQ23002-呼吸冰袖` 的同货号异名，以及“椰椰小岛两栖泳衣三件套”与副链接的别名关系。确认后才按需读取这些文件夹中的图片、计算 SHA-256、确认逐文件授权并进入真实素材完整性审查。后台当前仅采集到远端素材 ID，缺少可用于内容去重的图片指纹，因此生产上传前仍须人工核对重复。完整逐图片索引改为可选离线审计，不再阻断 1–3 商品试跑。

**本次证据：** 真实文件夹归属页面验收为 `test_evidence/03-assets-video/folder-review/20260724_110859/visual-acceptance.json`、`folder-review-before.png`、`folder-review-after.png` 和 `runs/20260724_111144/04-asset-matching/input.json`；真实候选源数据为 `test_evidence/03-assets-video/folder-index-real/20260724_092758/folder-review.json`。画廊视觉验收为 `test_evidence/06-asset-gallery/visual-acceptance.json`、`gallery-before.png`、`gallery-after-change.png` 和 `runs/20260724_040238/04-asset-matching/input.json`。最终索引器修复 acceptance 为 `test_evidence/03-assets-video/indexer-acceptance/20260723_165014/acceptance-summary.json`、同目录 `commands-and-results.json`、`new-output/` 和 `interrupt-resume-output/`；历史索引器 acceptance 为 `test_evidence/03-assets-video/indexer-acceptance/20260723_101741/`；既有三源预检证据为 `test_evidence/03-assets-video/runs/20260722_143048/04-asset-matching/preflight.json`、同目录 `input.json`，以及会话根目录的 `session.json`。

**历史证据（保留）：** `test_evidence/02-assets-video/run-help-after-manifest.txt`、`pytest-final-images.txt`、`mixed-license.txt`、`xiaobajiao-assets.csv`、`xiaobajiao-inspection.json`、`xiaobajiao-before.csv`、`xiaobajiao-after.csv`。

**通过标准：** 三处图片来源均可只读扫描和审查，匹配原因可追溯，人工候选未被自动确认，原始图片未修改。视频项保持延期。

---

## 阶段 4：图片策略

**状态：** 未开始

**目标：** 证明图片格式、数量、比例和容差来自 YAML，而不是代码硬编码。

### 执行清单

- [ ] 运行图片格式、比例、重复、损坏和零尺寸回归测试。
- [ ] 策略 A：只允许 JPG、1:1、固定 3 张。
- [ ] 策略 B：只允许 PNG、3:4、数量 3–5 张；只改 YAML 即改变结果。
- [ ] 混合比例返回 `MIXED_ASPECT_RATIO`；损坏图片、零高度和超容差不会异常退出或静默通过。
- [ ] 结合阶段 3 的三处来源，确认小红书常见 `2:3` 图片应直接上传、裁切还是仅作人工候选。
- [ ] 验证业务坑位规则：三坑商品最终三坑传满，九坑商品最终九坑传满；缺少可用素材时标记 `blocked` 或 `needs_manual_review`，不得以空坑通过。
- [ ] 本轮纯图片商品优先生成图文任务；视频恢复测试后，再验证三坑为 `1 视频 + 2 图文`、九坑为 `3 视频 + 6 图文`。视频配比在恢复测试前保持“延期”，不得标记通过。
- [ ] 对需要裁剪的图片展示原图比例、目标比例、裁剪预览和裁剪后尺寸，并由用户确认“直传 / 裁剪 / 仅作候选”；裁剪只能生成派生文件或使用平台裁剪，不得覆盖原图。

```powershell
New-Item -ItemType Directory -Force test_evidence\04-image-policy | Out-Null
uv run --directory .\upload-search-materials --locked python -m pytest -q tests\test_assets.py -k "image or ratio or duplicate or zero" --basetemp ..\test_evidence\04-image-policy\pytest-temp -o cache_dir=..\test_evidence\04-image-policy\pytest-cache 2>&1 | Tee-Object test_evidence\04-image-policy\image-tests.txt
```

**通过标准：** 格式、数量、比例和容差均由运行时策略控制，原因码稳定可审计，原始图片未修改。

---

## 阶段 5：后台导出与生产选择器

**状态：** 进行中（2026-07-24 完成基础/推广导出契约和本地模拟下载测试；真实浏览器导出与推广字段确认待执行）

**目标：** 在用户控制的已登录 Chromium 中验证基础/推广素材导出和生产选择器，全阶段只读，不上传、不发布。

### 前置门禁与执行清单

- [ ] 用户确认 `TMALL_STORE`、仓库外的 `TMALL_SELECTOR_CONFIG` 和仅监听 `127.0.0.1` 的 CDP 地址。
- [ ] 未把 `selectors.example.yaml` 当作生产配置。
- [x] CLI 支持 `--report basic|promotion|both`，每类报表保存到独立子目录并生成 `source-files.json`、`export-manifest.json`。
- [x] 下载后校验 XLSX 表头、数据行数和 SHA-256；非空输出目录在浏览器动作前停止。
- [x] 新选择器键 `export_promotion` 与旧键 `export_search` 兼容；真实页面确认该入口导出的是经营数据，契约改为 `metrics_only`，不得作为坑位真相源。
- [x] 当前真实基础素材 `68075059.xlsx` 只读校验为 627 行、627 个唯一商品 ID；空素材单元格由项目读取器识别为空，SHA-256 为 `4099523b1ff75411d26ef5364c82edaaec198d30e3047de323156d916105d387`。
- [x] 2026-07-24 自动化验收：导出相关聚焦测试 `145 passed, 1 skipped`，全量回归复跑 `294 passed, 2 skipped, 1 warning`，Node UI-state 测试 `9 passed`；Skill validator 与 `uv lock --check` 通过。唯一 warning 仍为真实基础素材 XLSX 缺少默认样式，不影响只读解析结果。
- [x] 2026-07-24 真实浏览器只读门禁通过：登录有效、无验证码或风控页，页面可见准确店铺为 `kocotree旗舰店`，基础素材和导入/导出入口可见。
- [x] 2026-07-24 基础素材真实单文件导出成功：复用 `C:\Users\ZhangQuanLong\Desktop\playwright\export-basic.js` 的通用弹窗关闭、坐标点击和异步任务轮询逻辑；隔离批次 `20260724_012240-basic-browser` 创建 1 个导出任务并下载 `68077261.xlsx`，上传和发布动作均为 0。
- [x] 新版页面已不存在旧“商品状态”筛选器；默认导出的原始文件为全部状态，共 2,158 行、2,158 个唯一商品 ID，其中 `售卖中 627`、`已下架 1,477`、`从未上架 54`。原始文件 SHA-256 为 `8c5d17482efa6649b6e85bbe2bb12a2127cf7ae1dd7ff54cfae18441de6e12b5`。
- [x] 新文件按 `商品状态=售卖中` 得到的 627 个商品 ID 与历史已筛选导出 `68075059.xlsx` 的 627 个商品 ID 完全一致；后续应改为“全量原始导出留证 → 按工作簿商品状态过滤”，不得继续依赖已失效的页面状态选择器。
- [ ] 将基础素材生产导出器改为异步任务流，并实现导出后 `商品状态=售卖中` 的确定性过滤；完成前不得进入全量 dry-run。
- [x] 2026-07-24 确认搜推页“导出数据”为经营指标，不是商品素材与坑位真相源；推广素材改为按精确商品 ID 从实时 DOM 只读采集。
- [x] 延迟弹窗策略验收：关键动作前后等待连续 3 秒无新安全弹窗，仅关闭“稍后再看/关闭/取消/暂不”和关闭图标；真实采集中累计关闭 3 次延迟弹窗，采纳、上传和发布动作均为 0。
- [x] 9 坑样本 `565628742471` 为 9/9，读取 9 个远端素材 ID，但存在“重复或图片有删除”；样本 `903588197784` 为 3/9，读取 3 个远端素材 ID，缺失 6。
- [x] 非高价值“店铺优选”样本 `1037160921729` 当前读取 1 个远端素材 ID，但页面行未明确目标容量；保持 `TARGET_CAPACITY_NOT_EXPLICIT / needs_manual_review`，不得仅凭分类名称写成 3 坑。
- [x] 当前页面的“坑位”是发布容量/篇数，不是固定编号槽位；页面没有编号时只记录目标容量、现有素材数和缺失数量，空坑位编号保持未知。
- [x] `supplement` 已改为默认扫描“推荐补充素材”分页，支持跨页商品 ID 去重、每页原子 CSV/checkpoint、`--max-pages` 小规模验收，以及 `--scan-mode exact --candidates` 异常商品兜底；相关聚焦回归 `72 passed, 1 skipped`，全量回归 `296 passed, 2 skipped, 1 warning`，Node UI-state `9 passed`，Skill validator 与 `uv lock --check` 通过。
- [ ] 使用仓库外生产 selectors 和已登录 CDP 对真实前 2–3 页运行新版 CLI，核对页面商品数、CSV 行数、检查点页码及 3 个已知样本。
- [x] 精确商品 ID 自动筛选已验证：输入后失焦并等待表格稳定，`565628742471`、`903588197784` 均只返回 1 行且页面商品 ID 完全一致。
- [ ] 继续确认普通商品目标 3 篇的页面证据，并补采每篇素材的完整审核状态、内容指纹和页面时间；当前只能确认高价值目标 9 篇、远端素材 ID、缺失数量及部分可见异常。
- [ ] 错误店铺、验证码或失效选择器安全停止；失效选择器返回 `SELECTOR_INVALID`。
- [ ] 全过程上传和发布动作均为 0。

```powershell
New-Item -ItemType Directory -Force test_evidence\05-selectors | Out-Null
uv run --project .\upload-search-materials --locked python -c "from pathlib import Path; import os; from upload_search_materials.browser.config import load_selectors; print(sorted(load_selectors(Path(os.environ['TMALL_SELECTOR_CONFIG']))))" 2>&1 | Tee-Object test_evidence\05-selectors\selector-keys.txt
```

---

## 阶段 6：全量 dry-run

**状态：** 未开始

**目标：** 使用真实商品表、规则表和后台导出完成全量只读演练。

### 执行清单

- [ ] 记录全部输入的大小、mtime 和 SHA-256。
- [ ] 完成第一轮 dry-run、只补采候选商品，再用后台状态、逐文件素材清单、媒体策略和文案响应运行最终 dry-run。
- [ ] 每个输入商品恰有一条资格审计结果，多原因商品保留全部原因。
- [ ] 资格审计明确覆盖 `uvno`、清仓、好物体验类、会员日和积分五类排除规则；每类至少准备一个命中样本并输出稳定原因码，命中商品不得生成上传任务。
- [ ] 标题按“KK树 + 年龄或性别 + 品类词 + 卖点词”生成并逐字段校验；缺少必要字段时进入人工审查，不得猜测补全。
- [ ] AI 卖点描述必须来自可审计的 `TMALL_COPY_RESPONSES`，页面同时展示原始输入、生成描述和人工确认结果；未确认文案不得进入批准清单。视频文案验收随视频测试延期。
- [ ] `product-tasks.json`、`material-items.json`、`review.html`、`run.sqlite3` 和报告完整。
- [ ] 不生成 `approval-manifest.json` 或真实发布结果；原始输入 SHA-256 不变。

```powershell
New-Item -ItemType Directory -Force test_evidence\06-dry-run | Out-Null
uv run --project .\upload-search-materials --locked tmall-materials run --mode dry-run --month $env:TMALL_MONTH --store $env:TMALL_STORE --products $env:TMALL_PRODUCTS_CSV --rules $env:TMALL_RULES_CSV --basic $env:TMALL_BASIC_XLSX --search $env:TMALL_SEARCH_XLSX --output test_evidence\06-dry-run\initial --started-at $env:TMALL_STARTED_AT
uv run --project .\upload-search-materials --locked tmall-materials supplement --store $env:TMALL_STORE --selectors $env:TMALL_SELECTOR_CONFIG --candidates test_evidence\06-dry-run\initial\supplement-candidates.csv --output test_evidence\06-dry-run\backend-material-status.csv --collected-at $env:TMALL_COLLECTED_AT --cdp-url $env:TMALL_CDP_URL
```

**通过标准：** 缺失配置转为 `blocked` 或 `needs_manual_review`，不猜值；两级任务、审核页、状态库和报告完整；没有真实发布。

---

## 阶段 7：1–3 商品生产验收

**状态：** 阻断——等待用户在本阶段开始前再次明确授权

**目标：** 对用户批准的 1–3 个低风险商品验证审批、单次发布、远端回查和恢复。

### 授权门禁

- [ ] 用户在当前对话中明确同意真实上传，并确认店铺、商品 ID、目标坑位和最大商品数。
- [ ] 用户确认选择器、逐文件授权、媒体策略、文案政策和批准人身份。
- [ ] 用户查看 `review.html`，只选择 `ready_for_review` 的精确 task ID。
- [ ] 任一条件不满足即停止，不运行 `approve` 或 `publish`。

### 执行清单

- [ ] 一次性批准全部选中 task ID，并核对 manifest 的店铺、run ID、输入哈希、全部 item、批准人和有效期。
- [ ] 修改测试副本中的已批准 item，确认返回 `APPROVED_CONTENT_CHANGED`。
- [ ] 发布前再次核对店铺、商品、坑位、输入和素材 SHA-256，并获得本次点击发布的最终确认。
- [ ] 保存每个任务的发布次数、远端素材 ID、坑位、内容指纹、提交时间和审核状态。
- [ ] 结果不确定时进入 `publish_uncertain` 并暂停，不重复点击；恢复后不重复上传。

```powershell
New-Item -ItemType Directory -Force test_evidence\07-production-pilot | Out-Null
uv run --project .\upload-search-materials --locked tmall-materials publish --run-dir $env:TMALL_PILOT_RUN --store $env:TMALL_STORE --selectors $env:TMALL_SELECTOR_CONFIG --cdp-url $env:TMALL_CDP_URL
uv run --project .\upload-search-materials --locked tmall-materials resume --run-dir $env:TMALL_PILOT_RUN --store $env:TMALL_STORE --selectors $env:TMALL_SELECTOR_CONFIG --cdp-url $env:TMALL_CDP_URL
uv run --project .\upload-search-materials --locked tmall-materials report --run-dir $env:TMALL_PILOT_RUN
```

**通过标准：** 只处理用户批准的 1–3 个商品；每个任务最多点击一次；远端回查证据完整；所有异常安全停止。

---

## 阶段 8：更新文档和发布状态

**状态：** 进行中（Skill validator 与 5/5 Skill GREEN 场景已有证据；最终状态待前序阶段）

**目标：** 让文档准确反映测试证据，不提前宣称生产可用。

### 执行清单

- [x] Skill validator 通过。
- [x] Skill 压力场景 `5/5 GREEN`。
- [ ] 更新 `plan.md`、`pressure-scenarios.md`、`production-acceptance.md`、`operations-guide.md` 和 `SKILL.md` 的最终状态与命令。
- [ ] 阶段 7 未通过时，状态保持“实现与只读验收完成，待生产小批量验收”。
- [ ] 只有阶段 1–8 全部通过后，才允许标记“生产可用”。
- [ ] 运行最终完整测试、Skill 校验、Git 差异和敏感数据检查。

```powershell
New-Item -ItemType Directory -Force test_evidence\08-release | Out-Null
uv run --directory .\upload-search-materials --locked python -m pytest -q tests --basetemp ..\test_evidence\08-release\pytest-temp -o cache_dir=..\test_evidence\08-release\pytest-cache 2>&1 | Tee-Object test_evidence\08-release\pytest-final.txt
git status --short
git diff --check
git diff -- test_plan.md upload-search-materials plan.md
```

---

## 总体验收表

| 阶段 | 状态 | 关键证据/待办 |
| --- | --- | --- |
| 1. 测试环境 | 通过（2026-07-20） | `test_evidence/01-environment/`；历史完整测试 `95 passed`，Skill 校验通过 |
| 2. 交互页面与任务隔离 | 通过（2026-07-22） | 聚焦 `112 passed`、Node `9 passed`、全量 `203 passed`；版本协议、独占领取、草稿 CAS、wheel 资源及 1440/1024 浏览器与非生产 handoff 验收通过 |
| 3. 素材清单与三处图片来源 | 阻断（2026-07-24） | 真实三源文件夹索引完成：`5,279` 文件夹、11 个去层级重复候选、错误 0；真实归属审查页面与候选画廊 MVP 已验收。待用户完成 11 个归属决定，再生成按需图片候选、确认逐文件授权和完整性，飞书与视频入口延期 |
| 4. 图片策略 | 未开始 | 三/九坑填满、纯图片优先、视频配比延期及裁剪预览待验收；`test_evidence/04-image-policy/` |
| 5. 生产选择器 | 未开始 | `test_evidence/05-selectors/` |
| 6. 全量 dry-run | 未开始 | `test_evidence/06-dry-run/` |
| 7. 1–3 商品生产验收 | 阻断：等待再次授权 | `test_evidence/07-production-pilot/` |
| 8. 文档和发布状态 | 进行中 | Skill validator 与 `5/5 GREEN` 已确认；最终状态待前序阶段 |

### 最终结论规则

- 阶段 1–6 通过、阶段 7 未执行：`实现与只读验收完成，待生产小批量验收`。
- 阶段 7 被配置、权限或授权阻断：记录具体条件，不扩大商品范围。
- 阶段 1–8 全部通过且证据完整：`生产可用`。
- 任一安全门禁失败：不得标记生产可用。

## 下一步

解除阶段 3 阻断：打开已验收的文件夹归属页面，由用户完成真实 11 个候选的“确认 / 别名确认 / 排除”；只对确认文件夹按需读取图片、计算 SHA-256 并确认授权，再结合后台缺失状态生成逐商品应有/已有/缺失坑位、候选素材和五类排除原因。完整逐图片索引保留为可选离线审计。飞书图片入口与光合视频入口保持延期。完成素材来源验收后，再进入阶段 4 图片策略，确认三/九坑填满规则、纯图片优先，以及小红书常见 `2:3` 图片是允许直传、生成派生裁剪文件还是仅作为人工候选。
