# Upload Search Materials 测试计划

## 前端优先交互与受管启动器增量测试（2026-07-29）

- [ ] fresh repository context 能发现 `$upload-search-materials`，并在业务动作前完整读取 canonical Skill。
- [ ] “开始上传素材”“继续旧 session”“页面打不开”三类 fresh-context 任务的首个业务交互是页面或精确 UI 恢复动作，不询问店铺/NAS 路径。
- [x] `scripts/start-ui.cmd` 后台启动、健康轮询并及时返回 JSON；重复启动同一健康 session 不产生第二服务。
- [x] 默认端口占用时选择受控范围下一端口，不结束未知进程；提前退出、迟启动和超时返回稳定原因与日志。
- [x] `ui-status/ui-stop/ui-restart` 绑定精确 session；PID 或 ownership token 不匹配时拒绝结束进程。
- [x] 服务健康但浏览器失败时返回 `BROWSER_OPEN_FAILED` 和精确 URL，不误报服务失败。
- [x] 每个阶段和字段 API 返回 component、interaction policy 和允许原因码；未声明结构化字段默认 `frontend_required`。
- [x] 有效聊天保底写入相同 `input.json`、revision 和 handoff 管道；无原因、错误字段、frontend-required 字段和陈旧 revision 均拒绝。
- [x] 页面恢复后显示聊天保底来源；`SCHEMA_GAP` 只生成补充任务，不把未知自由文本写入业务值。
- [x] 普通屏与窄屏检查全部阶段字段可见、可输入、可恢复，不存在只能在聊天完成的结构化决定。
- [x] 批准与生产确认保持精确 task、店铺、坑位、有效期和哈希门禁；“全部继续”无效。
- [x] 测试全过程不调用真实 `approve`、`publish` 或素材上传。

建议命令：

```powershell
uv run --directory .\upload-search-materials --locked pytest -q --basetemp ..\test_evidence\frontend-first\pytest-temp
node --test .\upload-search-materials\tests\ui_state.test.cjs
uv run --project .\upload-search-materials --locked python .\upload-search-materials\scripts\validate_skill_entry.py --repository-root .
$env:PYTHONUTF8='1'; uv run --project .\upload-search-materials --locked python "$env:USERPROFILE\.codex\skills\.system\skill-creator\scripts\quick_validate.py" .\upload-search-materials
openspec validate make-skill-interactions-frontend-first --strict
```

## 确定性坑位编排增量测试（2026-07-28）

- [ ] 新任务显示 8 个阶段，不出现独立图片适用性阶段；旧任务仍可读取该阶段。
- [ ] 选 1/2 张分别显示还差 2/1 张；可保存草稿但不能提交。
- [ ] 选 3 张提交后直接生成 1 个完整坑位，不等待坑位 Agent。
- [ ] 选 9 张且后台缺 3/2/1 坑时分别生成 3+3+3、5+4、9。
- [ ] 重复输入得到相同顺序、比例和策略 SHA-256。
- [ ] 多来源文件夹轮询交错；重复 SHA 不计数；超容量图片进入未使用池。
- [ ] 第五阶段只显示“图片裁剪和压缩 / AI生成标题和描述”两个子页。
- [ ] 坑位未确认时裁剪区隐藏；人工修改后按当前 revision 再确认。
- [ ] 输出复核通过后才解锁文案页，AI 文案不得改变坑位和图片。
- [ ] 全流程只执行 dry-run，不执行真实上传。

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
- [ ] 阶段 07“批准”和阶段 08“生产确认”只生成交接，阶段 09“结果”只展示状态；三者都不直接触发生产动作。
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
| `TMALL_ASSET_ROOT` | 在阶段 1 配置页维护、阶段 3 使用的 NAS/本地素材根目录 | 1 |
| `TMALL_ASSET_MANIFEST` | 逐文件授权和媒体元数据清单 | 3 |
| `TMALL_MEDIA_POLICY` | 已确认的媒体策略 YAML | 4 |
| `TMALL_STORE` | 在阶段 1 配置页确认、后续采集使用的准确店铺名 | 1 |
| `TMALL_SELECTOR_CONFIG` | 生产选择器 YAML 绝对路径 | 5 |
| `TMALL_CDP_URL` | 用户启动的本地 CDP 地址 | 5 |
| `TMALL_COPY_RESPONSES` | AI 文案响应 JSON | 6 |
| `TMALL_PILOT_RUN` | 生产小批量批次目录 | 7 |
| `TMALL_APPROVER` | 批准人可审计身份 | 7 |

业务变量为空、路径不存在或内容未确认时，只阻断实际依赖它的后续阶段，不阻止阶段 1 配置页启动。除运行环境准备外，不得在启动配置页前通过聊天索取店铺名、月份或图片根目录。

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

**目标：** 验证九阶段交互向导、时间戳任务隔离、JSON 交接、CLI 等待和恢复提示；任何页面动作都不得直接上传或发布。

### 自动化与 CLI 检查

- [x] 交互阶段、会话、API/UI 自动化测试已完成一轮实现级验证。
- [x] CLI `interact` / `wait-handoff` 已完成聚焦验证。
- [x] 控制器对全部评审修复运行最终回归，并保存退出码和完整日志。
- [x] 仅准备运行环境后即可创建会话并启动配置页；店铺、月份和图片源由用户在页面填写，缺失时不得要求先在聊天中提供。

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
- [x] 页面不包含上传/发布调用；当前九阶段流程的阶段 07/08 仅交接 Agent 和展示结果。
- [x] 任务配置主界面只保留店铺确认、目标月份和图片源配置；不提供商品范围或搜推素材采集页数。
- [x] 商品表和规则表自动发现并只读展示；图片源可在页面新增、删除、检测及保存为本机配置；搜推素材显示“搜推高价值”全量自动采集，基础素材不进入本分支，运行目录由系统创建且不可编辑。
- [x] 人工图片素材清单、历史基础素材表和历史推广素材状态只在高级设置中作为可选导入；视频延期；不维护商品别名。
- [x] 任务目录只保存输入快照、自动导出/采集、当前候选、用户决定、dry-run、批准和结果；NAS 原图及共享文件夹索引不重复复制。
- [ ] 验证每台电脑只维护一个由 `folder_index_root` 指定的共享文件夹索引；新任务使用 `snapshot-folder-candidates` 生成当前商品快照，任务目录不出现 `folder-index.sqlite3`。

### 本地服务与浏览器视觉验收

- [x] 启动仅监听 `127.0.0.1` 的本地服务，保存启动日志和实际 URL。
- [x] 在真实浏览器检查九阶段左侧导航、字段、帮助文案、空状态、提交状态、恢复提示，以及 1440×900 / 1024×768 布局；1024 宽度无横向溢出，滚到底部后输入和恢复区域不被固定交接栏遮挡。
- [x] 确认三处图片来源可编辑，视频入口禁用并显示“本轮测试延期”，阶段输入均有保存/提交接口。

```powershell
uv run --project .\upload-search-materials --locked tmall-materials interact --runs-root test_evidence\02-interaction\runs --port 8765
```

**浏览器与 handoff 证据：** 最终本地会话 `20260722_114534` 在阶段 04 提交三处图片源；页面显示 revision `1` 和持久化提交时间。`wait-handoff` 返回 SHA-256 `868fdb9fd2e9af15dfca298c246dd4877655de36a10addaf5f2f85d65ef88cb2`，与 `input.json` 实算一致。通过 `SessionStore.write_result` 写入非生产结果后，页面正确展示摘要、证据、阻塞原因和“进入图片策略人工审查，不执行上传”；全程未调用上传或发布。

**2026-07-24 历史任务配置验收（已被第二阶段范围调整取代）：** 当时页面曾默认“全部符合当月规则的商品”并保存 `product_scope=all_eligible`；该字段现已删除，不再作为当前验收标准。历史证据仍保留在 `test_evidence/04-interaction-ui/setup-config/20260724_135007/`。

**2026-07-24 跨电脑路径验收：** 商品表和规则表改为从项目根目录 `docs/` 按受控模式唯一匹配；共享图片目录只从 Git 忽略的 `config/local-paths.json` 或显式配置读取；运行目录默认使用项目 `runs/`。无共享盘配置、表格缺失或盘符不同均不得阻止交互页面启动，歧义文件不得自动选择。源码防回归测试禁止写入个人用户名和 `Y:`/`Z:` 固定盘符。

**2026-07-24 动态图片源配置验收：** 任务配置页改为可复用的动态图片源组件，允许配置 1–50 个来源，不再限定三处；每项保存来源名称和根路径，支持新增、删除、只读可访问性检测及保存到 Git 忽略的本机配置。setup handoff 新增 `image_source_labels` 并与 `image_roots` 按顺序绑定；零项、空值、重复名称或重复路径必须拒绝。

**2026-07-24 交互提交协议验收：** 图片源已增加用户触发的系统文件夹选择入口；编辑期防抖自动保存草稿，草稿不触发 Agent；正式提交后前后端同时冻结，只有未认领 handoff 可撤回，processing/completed 禁止覆盖；每个 revision 保存历史快照；恢复指令绑定精确 session/stage/revision；前序阶段未完成时拒绝提交下一阶段；交互服务启动前检测端口占用并报告 PID。隔离浏览器会话 `20260724_163755` 验证自动保存、提交冻结、恢复入口启用和撤回解锁均成功；系统目录窗口保留为用户点击验收，不在无头浏览器中代替用户选择。

### 历史任务当前阶段与保存/提交竞态

- [x] 使用仅含 `session_id=20260725_095720` 的链接刷新后，页面直接进入 `image_review`，不再固定进入阶段 01。
- [x] 首屏一次性显示 setup、completeness、asset_matching 为已完成，当前 image_review 为编辑中。
- [x] 只打开并等待图片加载、默认决定和自动保存周期后，image_review revision 保持 `41`，input SHA-256 保持 `1376830f63e7adc8c8926ccd2c7413712431c6a01d62f9bc75183b5e7e4b276a`，且没有新增 handoff。
- [x] 查看已完成阶段时，保存和提交保持禁用，页面显示锁定原因并提供“进入当前阶段”。
- [x] 隔离会话快速执行保存后提交，最终仅生成一个有效 handoff；前端状态层验证保存进行中提交优先排队、重复保存合并、跨阶段请求失效。

**通过标准：** 最终自动化回归、JSON 绑定、重启恢复、跨阶段竞态和真实浏览器视觉检查全部通过；页面始终不执行上传或发布。

---

## 阶段 3：素材匹配（素材清单与一个或多个图片来源）

**状态：** 进行中（2026-07-25 已完成 2 个商品的按需图片候选与浏览器预览验收；图片卡片已合并为单一“采用”操作，坑位编排待继续测试；视频延期）

**目标：** 验证目录型或逐文件清单型素材能够进入 dry-run，并按商品安全匹配；本轮只测试图片。

### 本轮可编辑/测试图片根目录

1. `Y:\视觉部\1-模特图`
2. `Z:\浙江酷趣\运营中心\营销板块\小红书koc置换&淘宝买家秀\优质买家秀`
3. `Z:\浙江酷趣\运营中心\营销板块\小红书koc置换&买家秀\优质买家秀`

匹配默认只依据精确商品 ID、完整 SKU，或文件夹名中出现的完整商品名称。仅共享部分词语不得匹配，不维护别名。用户明确提供完整文件夹名时，允许在当前任务使用一次性的 `PRODUCT_ID=FOLDER_NAME` 精确查询，但不得写入共享别名或自动复用于其他任务。图片文件名不要求是订单 ID；目录名、清单字段和人工确认结果均可参与匹配，但名称候选不得自动冒充精确命中。

### 执行清单

- [x] 目录型和 `--asset-manifest` 入口进入 CLI，逐文件授权不被批次级参数覆盖。
- [x] 含 `confirmed` / `unknown` 的清单返回稳定授权结果。
- [x] 有商品 ID 的清单行必须精确匹配商品 ID，避免共享 SKU 跨链接取错素材。
- [x] 分片增量索引器完成本地隔离 new、持久 checkpoint 中断恢复和增删改 refresh 验收；索引候选不自动生成批准清单。
- [x] 默认改为文件夹一级索引：三处真实根目录共记录 `5,279` 个文件夹，耗时约 `225.7` 秒，三源完成且错误为 0；不读取或哈希图片内容。
- [x] 文件夹候选只按自身名称匹配，不把父目录命中传播到 `KV/合成/1/2/3/4` 等普通子目录；真实候选由 22 条降为 11 条。
- [x] 文件夹索引支持 `--refresh` 更新增删改，支持 `--rematch-only` 在不重新遍历 NAS 的情况下重算名称和货号匹配。
- [x] 验证素材匹配中文件夹默认采用；排除后卡片保留并变灰，对应候选图片立即隐藏，来自该文件夹的已选素材和授权同步取消，不自动提交、不进入下一阶段。
- [x] 验证文件夹决定提交不因 Web 服务进程暂时看不到 Y/Z 映射盘而报 `validation failed`；确认文件夹后由 Agent 在读取图片前单独检查实时可访问性。
- [x] `prepare-folder-review` 将真实候选转换为 `folders_only` 审查数据；页面显示商品 ID、货号、来源、命中类型、文件夹名和完整路径，且明确确认前不读取图片。
- [x] `prepare-folder-review --exact-folder PRODUCT_ID=FOLDER_NAME` 可把用户明确指定的完整文件夹名加入当前任务候选，匹配类型为 `exact_folder_query`，不保存为别名。
- [x] 页面只保留“采用 / 排除该文件夹”及备注输入，不提供“待确认”或别名确认；无决定和历史 `pending` 默认采用，二态决定绑定文件夹与商品并保存到当前时间戳会话的 `folder_decisions`。
- [x] `prepare-gallery` 可将只读索引与推广素材状态合并；后台缺失篇数只作上下文，不再换算第三阶段必须选择的图片数。
- [x] `prepare-confirmed-gallery` 可直接读取本任务使用中的文件夹；每个商品最多按比例稳定随机抽取 100 张，发现不足 100 张时全部显示，只检查抽样候选，不建立全量图片数据库。
- [x] 页面可展示候选缩略图、三处来源和匹配方式；图片卡片只保留“采用”，勾选时同步确认本次发布授权。图片预览仅允许当前结果中且位于当前 `image_roots` 或已确认文件夹下的文件，并使用短时私有缓存。
- [x] 候选准备阶段在当前任务生成最长边不超过 640 像素的 JPEG 预览；旧任务按需补建。页面不直接传输共享盘大原图，选择图片时不重建整个图片网格。
- [x] 图片预览白名单兼容映射盘与 UNC 路径差异：候选必须出现在当前结果中，并位于配置根目录或本任务已确认文件夹下。
- [x] 页面每批最多显示 30 张；候选池超过 30 张时启用“换一批”，最后一批显示实际余数。已选素材跨批次保留，并将结果保存到当前时间戳任务 JSON。
- [ ] 验证文件夹决定与图片画廊实时同步：排除后立即更新候选数、分页、换批按钮及“已选素材”，准确提示自动取消数量；重新采用后恢复候选但不恢复旧选择。
- [x] 当前任务内使用 SHA-256 排除本地/跨商品重复；缺少后台图片指纹时明确显示“远端去重未完成”，不得声称远端已去重。
- [ ] 对上述三处根目录分别生成只读扫描清单，再按商品合并候选；记录来源、匹配方式、命中数量、待确认项和 SHA-256。
- [ ] 使用新二态模型复核当前所选商品：历史无决定和 `pending` 文件夹按采用显示，用户只排除错误来源；提交前确认不存在来自已排除文件夹的已选素材。
- [x] 在页面逐商品展示“搜推高价值”全量候选，至少包含商品 ID、商品名称、应有篇数、已有篇数、缺失数量、候选素材数量和后台证据；用户逐项或批量选择后将 `selected_product_ids` 保存到当前时间戳任务的 JSON。基础素材不参与范围计算。
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

**2026-07-24 历史搜推完整度页面验收（已被全量候选逻辑取代）：** 隔离会话 `20260724_172019` 曾把基础素材范围与 30 条实时搜推证据合并为 629 个商品；该合并逻辑现已删除，不再作为当前验收标准。历史证据仍保留在 `test_evidence/02-completeness/20260724-stage2/`。

**2026-07-24 历史可配置采集页数（已废止）：** `promotion_max_pages` 已从任务配置和 setup 契约删除。`--max-pages` 只保留给显式 CLI 诊断测试，正式第二阶段必须全量遍历“推荐补充素材”分页。

**2026-07-24 五页真实采集验收：** 隔离会话 `20260724_215033` 在配置页填写 `promotion_max_pages=5`，自动保存后由测试脚本读取 setup `input.json` 并调用 Skill 的 `supplement --scan-mode recommended --max-pages 5`。真实店铺 `kocotree旗舰店` 完成 5 页，checkpoint 为 `last_completed_page=5`、`row_count=50`，CSV 为 50 行、50 个唯一商品 ID；未点击搜推页的经营数据导出按钮。与当前基础素材售卖中范围合并后为 627 个商品：50 个待补充、577 个需后台补采。该范围比上一版 629 少商品 `1027968776704`、`908285760995`，进入全量采集前需核对两次基础素材范围差异。

**2026-07-24 第二阶段范围调整：** 上述“五页真实采集”仅保留为采集器限页诊断证据，不再代表正式第二阶段范围。正式流程删除任务配置中的“商品范围”和“搜推素材采集页数”，直接选择“商品分类 → 搜推高价值”并遍历全部分页。采集到的商品全部进入第二阶段候选列表，用户可搜索、筛选、逐项选择、选择/取消当前筛选结果；提交内容为非空 `selected_product_ids`，只有所选商品进入下一阶段。验收时确认商品总表只补充名称和货号，不把列表外商品扩入候选集。

**2026-07-25 第二、三阶段合并调整：** 删除独立的“维护范围确认”页面，交互流程由十阶段缩为九阶段。第二阶段矩阵对标题或等级命中 `uvno`、`积分`、`清仓`、`好物体验`、`会员日` 的商品自动写入 `excluded / selectable=false`，保留商品及原因供审计但禁止选择；批量选择自动跳过排除项，服务端再次拒绝排除或陈旧 ID。提交其余商品后直接进入新的第三阶段“素材匹配”。核心回归为 `135 passed`，全量回归为 `347 passed, 2 skipped, 1 warning`，Node UI 状态测试 `9 passed`，Skill validator 通过；下一步以真实 262 条“搜推高价值”数据重新生成矩阵，核对五类排除数量后进入真实素材匹配。

**2026-07-24 第二阶段新版页面验收：** 隔离会话 `20260724_223809` 使用 2 条推荐补充素材样本完成浏览器验收。页面显示全量候选、目标/已有/缺失、候选图片数、证据、搜索和状态筛选；点击“选择当前筛选结果”后已选择计数从 1 变为 2，隐藏交接字段写入 `1001`、`1002`。第一阶段页面已不再显示商品范围和采集页数。

**2026-07-24 第二阶段真实全量验收：** 会话 `20260724_221347` 的 setup revision 3 完成店铺确认后，Skill 选择“商品分类 → 推荐补充素材”并在不传 `--max-pages` 的情况下遍历 20 页，采集 196 行、196 个唯一商品 ID、0 个空 ID。第二阶段矩阵显示 59 个“待补充”和 137 个“需人工确认”；搜索商品 `931630978007` 返回 1 行，状态筛选分别返回 59/137 行。修复了搜索与筛选误触发草稿 revision、以及浏览器缓存旧版 `app.js` 的问题；静态资源现使用内容哈希版本号，复测搜索和筛选后 revision 保持 9 不变。

**2026-07-24 分类策略修订与真实验收：** 上述“推荐补充素材”验收保留为历史证据。生产默认分类改为“商品分类 → 搜推高价值”，因为该分类逐商品显示“发布坑位到 N 篇、当前发布 N 篇”等明确文本，可直接计算缺失篇数；CLI 默认 `--scan-mode high-value`，旧 `recommended` 模式只用于历史兼容。首次扫描得到 252 条，与页面分类总数 262 不符，确认旧翻页逻辑可能在页面尚未刷新时重复读取上一页。修复为点击下一页后必须等待商品 ID 集合变化，否则返回 `SELECTOR_INVALID`。复扫完成 27 页、262 行、262 个唯一商品 ID，商品 ID、目标容量、当前发布数和缺失数均为 0 空值；目标容量全部为 9。第二阶段 revision 11 显示 260 个“待补充”、2 个“已完整”、0 个因坑位不明而“需人工确认”。

**2026-07-22 三源预检结果：** 隔离会话 `20260722_143048` 已创建。三个根目录均可只读访问；使用 `rg --files` 统计图片共 `413,309` 张，其中 Y 盘模特图 `396,683` 张（2026：`116,262`，2025：`165,937`，2024：`82,031`，2023：`32,453`），小红书 KOC 与淘宝买家秀 `14,042` 张，小红书 KOC 与买家秀 `2,584` 张。三处来源各抽取 1 张图片完成可读性、尺寸和 SHA-256 检查，读取前后大小与 mtime 一致；三张均因尚未确认逐文件授权返回 `LICENSE_UNKNOWN`。仓库已有商品总表 `611` 行、基础素材 `2,158` 行和搜推数据 `808` 行；商品表中 `33` 行缺商品 ID、`11` 行重复商品 ID，必须保持 blocked。样本“小鸟岛针织帽”和“小萌宠滑雪服”命中清仓排除；“花仙子翻翻帽-椰蓉”仅作为 `1009488728622 / KQ26021 / 花仙子翻翻帽` 的待确认别名。`tests/test_assets.py` 为 `13 passed`。

**2026-07-23 索引器本地隔离验收：** 在 `test_evidence/03-assets-video/indexer-acceptance/20260723_101741/` 创建最小商品表和三个本地图片 roots，覆盖 ID 精确候选、SKU 精确候选、名称人工候选和未匹配图片。completed new 生成数据库、候选 CSV 与摘要，结果为 3 roots、4 active files、3 matched files、3 candidate groups；在 1 个 partition 的 checkpoint 已持久化后制造 `KeyboardInterrupt`，中断退出码 `1` 且生成等价恢复命令，实际 `--resume` 退出码 `0` 并完成；图片增、改、删后 `--refresh` 验证 4 active / 1 inactive、修改文件重新哈希、新文件 active、删除文件 inactive，以及 SKU 候选消失、ID 候选图片数变为 2。三源、UTF-8/CSV BOM、`license_status=unknown`、名称 `needs_manual_confirmation`、不生成 `confirmed-assets.csv`、索引前后源文件不被索引器修改均通过；未访问 NAS、未测试视频、未 dry-run、上传或发布。自动验证为 pytest `265 passed, 1 skipped, 1 warning`、Node `9 passed`、`uv lock --check` 与 Skill validator 通过；warning 仍为只读源 XLSX 缺少默认样式。

**2026-07-23 索引器最终修复验收：** 在 `test_evidence/03-assets-video/indexer-acceptance/20260723_165014/` 重新运行三本地 roots 的实际 CLI/API new、持久 checkpoint 中断后 `--resume`、以及 `--refresh`。商品表为 46 行：2 行有效、33 行缺商品 ID、11 行重复商品 ID；new 退出码 `0`，行级 blocked 44 行均以 source row + reason codes 留证并排除匹配，有效行继续产生 ID/SKU/名称候选。new 本轮为 discovered/indexed/matched/failed `7/7/5/1`，单文件 `FILE_INSPECTION_ERROR` 未使 partition 或整批失败；3 个 root 及逐 partition 的 current scan 状态和计数完整。中断时已观测前一 partition 在扫描后一 partition 前持久化为 completed，后一 partition 的 processed count 为 `1`；在排序游标前删除、重命名和新增文件后，`--resume` 退出码 `0`，从分片头部重扫并正确 inactive 旧路径。refresh 退出码 `0`，新增文件 active、删除文件 inactive、修改文件重新哈希，本轮计数仍为 `7/7/5/1`。候选授权保持 `unknown`，名称候选保持 `needs_manual_confirmation`，new 与 refresh 前后源文件 snapshot 不变；未访问 NAS、浏览器、dry-run、上传或发布。证据见同目录 `acceptance-summary.json`、`commands-and-results.json`、`new-output/` 与 `interrupt-resume-output/`。

**最终修复自动验证：** Python 全量回归 `289 passed, 2 skipped, 1 warning`；聚焦索引器回归 `100 passed, 2 skipped`；Node UI 状态测试 `9 passed`；`uv lock --check` 与 Skill validator 均通过。最终故障注入补充验证了“未见文件失活 + 分片完成”使用同一 SQLite 事务，以及 root/partition 在同步窗口中断时仍生成一致的本轮统计；独立完整分支复审为 `READY`，Critical/Important 均为 0。唯一 warning 仍为只读源 XLSX 缺少默认样式。Skill 文档使用两个独立 fresh-context Agent 完成 RED/GREEN：旧文档无法确认 33/11 行级错误可继续，修复后明确只有 schema/batch/空表阻断整批，行级 blocked 留证并排除。

**2026-07-24 素材候选画廊 MVP：** 新增 `prepare-gallery`，从 `asset-index.sqlite3` 与 `promotion-material-status.csv` 生成确定性候选数据；页面实现逐商品缺失量、候选缩略图、来源、匹配方式、授权确认、采用、单张替换和“换一批”，决定保存到当前时间戳会话 JSON。隔离视觉验收会话 `20260724_040238` 从 `ASSET-01/02/03` 稳定切换到 `ASSET-07/08/04` 并保存草稿；本地 SHA-256 去重生效，后台图片指纹不可用时页面明确显示“远端去重未完成”。该验收使用本地模拟三源图片，不代表真实 NAS 授权、真实名称候选或远端内容去重已完成。

**2026-07-24 真实文件夹归属审查页面（历史）：** 新增 `prepare-folder-review` 和素材匹配阶段 `folder_decisions`。旧版曾提供别名确认；2026-07-25 已按业务决定删除，当前只保留确认归属和排除。历史自动验收决定不代表用户业务批准。

**2026-07-25 真实按需候选验收（旧版窗口策略）：** 会话 `20260725_095720` 曾按 18×3 窗口准备 68 张候选。该策略现已替换为每商品最多 100 张、按文件夹大小比例稳定随机抽样、页面每批 30 张；历史数据仅保留为旧版测试证据，不再作为当前验收标准。

**2026-07-25 自动验证：** 按需候选、可选图片索引、Web 预览和编排定向回归 `168 passed, 2 skipped`；Python 全量回归 `359 passed, 2 skipped, 1 warning`；Node UI 状态测试 `9 passed`，`app.js` 语法检查和 Skill validator 通过。唯一 warning 为只读真实 XLSX 缺少默认样式，与本次变更无关。

**2026-07-25 删除别名流程：** 第三阶段删除“确认归属并记录别名”和显式别名输入。最新二态模型进一步删除“待确认”，只保留“采用 / 排除该文件夹”；索引默认仅接受商品 ID、完整货号或完整商品名称命中。`分龄成长太阳镜` 不会仅因为共享“分龄成长”而自动匹配到 `分龄成长软软镜/稳稳镜/酷酷镜`。用户明确指定 `886506466908=分龄成长太阳镜` 后，可把完整同名文件夹作为本任务 `exact_folder_query` 候选，不记录别名。

**本次自动验证：** Python 全量回归 `318 passed, 2 skipped, 1 warning`；文件夹审查相关定向回归 `79 passed`；Node UI 状态测试 `9 passed`，`app.js` 语法检查、`uv lock --check` 与 Skill validator 均通过。唯一 warning 仍为只读真实 XLSX 缺少默认样式。真实文件夹索引与重新匹配证据位于 `test_evidence/03-assets-video/folder-index-real/20260724_092758/`。

**当前阻断：** 真实三源文件夹索引和归属审查页面已完成；候选文件夹默认采用，下一步由用户排除不属于当前商品的文件夹。最终采用目录才按需读取图片、计算 SHA-256 并进入逐图选择；后台当前仅采集到远端素材 ID，缺少可用于内容去重的图片指纹，因此生产上传前仍须人工核对重复。完整逐图片索引改为可选离线审计，不再阻断 1–3 商品试跑。

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
- [ ] 验证业务坑位规则：第五阶段允许本次只选择部分空坑位；每个图文坑位使用 3–9 张且统一为 3:4 或 1:1，未处理空坑继续保留为缺失，不得误报完整。
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
- [x] 当前搜推专用分支不再依赖基础素材生产导出器或 `商品状态=售卖中` 过滤；后续范围以第二阶段 `selected_product_ids` 为准。
- [x] 2026-07-24 确认搜推页“导出数据”为经营指标，不是商品素材与坑位真相源；推广素材改为按精确商品 ID 从实时 DOM 只读采集。
- [x] 延迟弹窗策略验收：关键动作前后等待连续 3 秒无新安全弹窗，仅关闭“稍后再看/关闭/取消/暂不”和关闭图标；真实采集中累计关闭 3 次延迟弹窗，采纳、上传和发布动作均为 0。
- [x] 9 坑样本 `565628742471` 为 9/9，读取 9 个远端素材 ID，但存在“重复或图片有删除”；样本 `903588197784` 为 3/9，读取 3 个远端素材 ID，缺失 6。
- [x] 非高价值“店铺优选”样本 `1037160921729` 当前读取 1 个远端素材 ID，但页面行未明确目标容量；保持 `TARGET_CAPACITY_NOT_EXPLICIT / needs_manual_review`，不得仅凭分类名称写成 3 坑。
- [x] 当前页面的“坑位”是发布容量/篇数，不是固定编号槽位；页面没有编号时只记录目标容量、现有素材数和缺失数量，空坑位编号保持未知。
- [x] `supplement` 已改为默认扫描“搜推高价值”分页，支持跨页商品 ID 去重、每页原子 CSV/checkpoint、`--max-pages` 小规模验收，以及 `--scan-mode exact --candidates` 异常商品兜底；`--scan-mode recommended` 仅保留兼容。
- [x] “搜推高价值”分类切换与翻页等待修复完成；真实全量为 27 页、262 个唯一商品，关键坑位字段 0 空值。聚焦回归 `76 passed, 1 skipped`，全量回归 `343 passed, 2 skipped`；Skill validator 与 `uv lock --check` 通过。
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

**状态：** 进行中（2026-07-29 已完成前端优先交互、受管 UI 启动器、聊天降级审计和普通/窄屏自动验收；全量 `526 passed, 2 skipped`、Node `17 passed`、Skill validator、OpenSpec strict、`uv lock --check` 和 `git diff --check` 通过。真实生产小批量仍待用户另行授权。）

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
| 3. 素材清单与三处图片来源 | 验收中（2026-07-25） | 真实三源文件夹索引完成：`5,279` 文件夹、错误 0；第三阶段改为文件夹默认采用、用户只排除，并实时同步候选画廊和已选素材。待完成历史任务与新任务浏览器验收，飞书与视频入口延期 |
| 4. 图片策略 | 未开始 | 三/九坑填满、纯图片优先、视频配比延期及裁剪预览待验收；`test_evidence/04-image-policy/` |
| 5. 生产选择器 | 未开始 | `test_evidence/05-selectors/` |
| 6. 全量 dry-run | 未开始 | `test_evidence/06-dry-run/` |
| 7. 1–3 商品生产验收 | 阻断：等待再次授权 | `test_evidence/07-production-pilot/` |
| 8. 文档和发布状态 | 进行中 | 前端优先变更的实现与非生产验收完成；真实生产小批量仍待用户授权 |

### 最终结论规则

- 阶段 1–6 通过、阶段 7 未执行：`实现与只读验收完成，待生产小批量验收`。
- 阶段 7 被配置、权限或授权阻断：记录具体条件，不扩大商品范围。
- 阶段 1–8 全部通过且证据完整：`生产可用`。
- 任一安全门禁失败：不得标记生产可用。

## 下一步

完成阶段 3 验收：打开文件夹归属页面，确认候选默认采用并只排除错误文件夹；核对候选、分页、已选素材和授权实时同步。只对最终采用文件夹按需读取图片、计算 SHA-256，再结合后台缺失状态进入后续图片策略。完整逐图片索引保留为可选离线审计；飞书图片入口与光合视频入口保持延期。
# 图片完整预检与压缩补充测试（阶段 3–5）

- [x] 第三阶段显示原图宽高、原始比例、格式、原始文件大小、200KiB–20MiB 范围及 1:1/3:4 最大裁剪尺寸。
- [x] 验证 200KiB、20MiB 和 720px 边界包含；小于 200KiB、无法形成 720×720 输出、损坏和不支持格式不可采用。
- [x] 超过 20MiB 时只有压缩提供方可用才允许采用；不可用返回 `COMPRESSION_UNAVAILABLE`。
- [x] Pillow 支持仅压缩、人工裁剪、裁剪并压缩、透明背景合成、质量搜索和受控缩放。
- [x] 第四阶段同时保存 1:1/3:4 适用性与候选裁剪框，提交不产生正式派生文件。
- [x] 第五阶段先确认每坑 3–9 张、有序图片和唯一比例，再在 `05-slots-copy/derived/` 处理并重新校验实际文件。
- [x] 自动端到端测试贯通第三阶段选择、第四阶段适用性、第五阶段两组坑位处理和文案确认；源图大小、mtime、SHA-256 不变。
- [x] Codex 方案一交接已覆盖显式创建、任务内缩略图、领取、原子回写、缓存键、预算拒绝和规则降级的自动测试。
- [x] 自动校验：Python `415 passed, 2 skipped, 1 warning`；Node `14/14`；前端语法、Skill validator、OpenSpec strict、`uv lock --check` 和 `git diff --check` 通过。
- [ ] 浏览器真实任务验收仍需单独执行，不并入上述自动校验结论。
- [x] 使用历史会话重新生成第三阶段结果，页面显示真实大小与比例；114 张候选均含完整预检，普通候选不再出现“大小未知 / 比例未知”。
- [ ] 使用真实历史任务重新验收新的第五阶段子流程：规则方案 → 可选 Codex 请求 → 计划确认 → 图片处理 → 文案确认；并核对源文件不变。

## 人工 / 规则 / Codex AI 决策边界补充验收

### AI 默认坑位编排与后处理（2026-07-28）

- [x] 第五阶段页面收敛为三个递进子页面：候选素材与 AI 坑位编排、图片裁剪与压缩、AI 标题与描述；确认坑位与生成派生图片为两个独立动作。
- [x] 原始 `slot_assignments` / `copy_edits` JSON 不再作为普通用户输入框显示；request、revision、SHA 和恢复信息收纳到折叠技术详情。
- [x] 候选图片按商品折叠并每批最多展示 30 张；每个子页面只保留一个当前高强调主动作，规则和人工入口作为次要兜底。
- [x] 重复确认保持同一计划 revision；相同计划和裁剪参数的重复处理复用已有输出，不重复写派生文件。
- [x] 自动验证：Python 全量 `457 passed, 2 skipped`，Node `15 passed`，`uv lock --check`、Skill validator 与 OpenSpec strict 均通过；唯一 warning 为只读 XLSX 缺少默认样式。
- [x] 浏览器验证：1024×768 与 1440×900 均无横向溢出，每页仅一个主动作；刷新后历史任务 revision 保持 48。历史卷卷帽部分图片缺少旧版 `size_bytes`，页面明确显示“大小读取失败”并阻断处理，需重跑第四阶段检测。
- [ ] 用历史任务 `20260725_095720` 完成三子页浏览器验收：太阳镜 3:4 三张无需裁剪；卷卷帽覆盖换图、调序、裁剪、压缩、最终预览和文案确认。
- [ ] 首次进入第五阶段只创建一个 `slot_plan_with_analysis`；刷新、轮询和重复初始化不新增同 revision 请求。
- [ ] 核对每坑主题、数量理由、逐图角色/选择理由、未采用原因；3 张已经完整表达时停止，4–9 张仅因新增场景/角度/动作/细节而扩展。
- [ ] 验证单商品非法方案只回退该商品规则草稿，其他合法 AI 商品保留；跨商品、跨比例、重复图和超过剩余坑位均被拒绝。
- [ ] AI 草稿自动进入唯一 `plan_review`，但不自动确认；人工换图、调序、增删坑位或改比例后标记 `manual_override`，迟到响应不得覆盖。
- [ ] 计划确认后才开放人工裁剪；分别验证接受默认框、调整 1:1/3:4 框、确认压缩、原生比例不裁剪和源 SHA 变化阻断。
- [ ] 输出通过后才创建 `copy_draft`；核对请求绑定计划 revision、最终输出 SHA 和顺序，逐坑展示标题、描述、依据、风险及版本。
- [ ] 人工修改/重新生成文案后逐坑确认；任一未确认、无依据声明或禁用词阻止进入 dry-run。
- [ ] 浏览器走通 AI 成功、完全人工、AI 失败后重试、AI 等待中人工接管四条路径，并确认阶段 07/08 仍为 `manual_only`。

- [x] 九阶段均注册 decision ID、允许模式、默认模式、输入输出、人工确认点、Agent 禁止动作、失败回退和继续条件；历史任务缺少模式文件时不得默认为 AI。
- [x] 第五阶段只显示“AI 编排坑位 / 人工添加坑位”两个入口；二者共同编辑唯一草稿，刷新页面只恢复草稿，不创建请求、不采用方案、不增加无意义 revision。
- [x] Agent 请求优先使用当前任务第三/第四阶段预览缓存；共享盘离线但缓存有效时仍可创建。缓存与原图均不可用返回 `AGENT_THUMBNAIL_UNAVAILABLE`，且不留下半成品 request。
- [x] 请求状态覆盖 `pending_agent / processing / completed / failed / superseded / cancelled`；领取幂等，revision 改变使活动请求过期，离开 AI 模式取消请求，迟到响应被拒绝。
- [x] AI 坑位响应校验同商品、单一 1:1/3:4、3–9 张有序唯一图片，并显示理由、`ai_confidence`、预计裁剪/压缩数、裁剪风险、多样性与重复摘要；失败时不生成规则草稿。
- [x] 合法 AI 结果只更新 `slot_assignments` 草稿并记录 request/response 身份；不得生成 `processed-outputs.json` 或派生图片。用户仍须点击“确认坑位并进入图片裁剪”。
- [x] 使用历史会话 `20260725_095720` 验证共享盘不可读而 9 张任务缓存可用时仍可创建并完成请求：request `20260727-084459-ea9a9bdcc6` 的 9 张候选全部来自 `image_review_cache`，两个商品分别返回 3:4 的 3 张和 6 张方案。采用/拒绝、比例切换和已选图片保持仍待浏览器人工确认。
- [ ] 分别完成两条浏览器入口：人工添加坑位、AI 编排坑位；验证 AI 超时/无效响应/取消后当前合法草稿保持不变，无草稿时保持空状态。
- [ ] 验证任何 AI 成功、轮询、刷新、采用或“换一套”均不会触发 dry-run、批准、生产确认或上传。
- [ ] 阶段 07 精确授权与阶段 08 生产写入始终保持 `manual_only`；生产测试继续等待当前对话对精确店铺、商品、坑位和任务清单的再次授权。

**当前自动化证据：** 新增 decision-mode、任务缓存缩略图、请求状态机、响应硬规则、
Web 采用/拒绝/回退和 Skill 文档契约测试；全量回归 `437 passed, 2 skipped`，
Node `14/14`，OpenSpec strict 与 Skill validator 通过。历史 session 的缓存请求和
Codex 响应已成功；内置浏览器控制暂时无法接管现有标签页，因此采用/拒绝、比例切换
及两商品选择保持列为待用户页面确认。本轮未执行图片处理、dry-run、批准或上传。
