# Upload Search Materials 测试计划

**目标：** 按“测试环境 → 交互页面与任务隔离 → 素材清单与三处图片来源 → 图片策略 → 生产选择器 → 全量 dry-run → 1–3 商品生产验收 → 更新文档和发布状态”逐阶段验证 `upload-search-materials`。

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

**状态：** 通过（2026-07-22；自动化、真实浏览器与非生产 handoff 均已验收）

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

### 本地服务与浏览器视觉验收

- [x] 启动仅监听 `127.0.0.1` 的本地服务，保存启动日志和实际 URL。
- [x] 在真实浏览器检查十阶段左侧导航、字段、帮助文案、空状态、提交状态、恢复提示，以及 1440×900 / 1024×768 布局；1024 宽度无横向溢出，滚到底部后输入和恢复区域不被固定交接栏遮挡。
- [x] 确认三处图片来源可编辑，视频入口禁用并显示“本轮测试延期”，阶段输入均有保存/提交接口。

```powershell
uv run --project .\upload-search-materials --locked tmall-materials interact --runs-root test_evidence\02-interaction\runs --port 8765
```

**浏览器与 handoff 证据：** 最终本地会话 `20260722_114534` 在阶段 04 提交三处图片源；页面显示 revision `1` 和持久化提交时间。`wait-handoff` 返回 SHA-256 `868fdb9fd2e9af15dfca298c246dd4877655de36a10addaf5f2f85d65ef88cb2`，与 `input.json` 实算一致。通过 `SessionStore.write_result` 写入非生产结果后，页面正确展示摘要、证据、阻塞原因和“进入图片策略人工审查，不执行上传”；全程未调用上传或发布。

**通过标准：** 最终自动化回归、JSON 绑定、重启恢复、跨阶段竞态和真实浏览器视觉检查全部通过；页面始终不执行上传或发布。

---

## 阶段 3：素材清单与三处图片来源

**状态：** 进行中（2026-07-21 图片子阶段通过；三处来源综合回归待执行；视频延期）

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
- [ ] 对上述三处根目录分别生成只读扫描清单，再按商品合并候选；记录来源、匹配方式、命中数量、待确认项和 SHA-256。
- [ ] 在页面逐商品人工确认名称候选和别名，确认结果保存到当前时间戳任务的 JSON，不修改原图。
- [ ] 视频元数据与视频策略检查：`延期`，不得阻断本轮图片测试。
- [ ] 比较测试前后原始图片的大小、mtime 和 SHA-256。

```powershell
New-Item -ItemType Directory -Force test_evidence\03-assets-video | Out-Null
uv run --directory .\upload-search-materials --locked python -m pytest -q tests\test_assets.py --basetemp ..\test_evidence\03-assets-video\pytest-temp -o cache_dir=..\test_evidence\03-assets-video\pytest-cache 2>&1 | Tee-Object test_evidence\03-assets-video\test-assets.txt
uv run --project .\upload-search-materials --locked tmall-materials run --help | Select-String "asset-root|asset-manifest"
```

**已完成图片子阶段实际结果：** 完整测试当时为 `99 passed, 1 warning`。真实样本使用商品 `887508274682 / KQ25024 / 小芭蕉卷卷帽` 的同一达人 3 张图片，均成功读取为 `854×1280`；授权未知返回 `LICENSE_UNKNOWN`，当前比例策略返回 `ASPECT_RATIO_INVALID`；测试前后大小、mtime 和 SHA-256 一致。

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

```powershell
New-Item -ItemType Directory -Force test_evidence\04-image-policy | Out-Null
uv run --directory .\upload-search-materials --locked python -m pytest -q tests\test_assets.py -k "image or ratio or duplicate or zero" --basetemp ..\test_evidence\04-image-policy\pytest-temp -o cache_dir=..\test_evidence\04-image-policy\pytest-cache 2>&1 | Tee-Object test_evidence\04-image-policy\image-tests.txt
```

**通过标准：** 格式、数量、比例和容差均由运行时策略控制，原因码稳定可审计，原始图片未修改。

---

## 阶段 5：生产选择器

**状态：** 未开始

**目标：** 在用户控制的已登录 Chromium 中验证生产选择器，全阶段只读，不上传、不发布。

### 前置门禁与执行清单

- [ ] 用户确认 `TMALL_STORE`、仓库外的 `TMALL_SELECTOR_CONFIG` 和仅监听 `127.0.0.1` 的 CDP 地址。
- [ ] 未把 `selectors.example.yaml` 当作生产配置。
- [ ] 校验所有必需选择器键、准确店铺名、人工验证页面检测、两份 XLSX 导出入口和精确商品 ID 搜索。
- [ ] 只读检查目标坑位、素材表、空坑、审核状态、远端素材 ID、指纹和时间。
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
| 3. 素材清单与三处图片来源 | 进行中 | 历史“小芭蕉卷卷帽”图片证据已保留；三处来源综合扫描待执行；视频延期 |
| 4. 图片策略 | 未开始 | `test_evidence/04-image-policy/` |
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

继续阶段 3：对三处图片来源执行只读综合扫描与人工确认，记录来源、匹配方式、待确认项和 SHA-256；视频测试保持延期。完成素材来源验收后，再进入阶段 4 图片策略，确认小红书常见 `2:3` 图片是允许直传、裁切还是仅作为人工候选。
