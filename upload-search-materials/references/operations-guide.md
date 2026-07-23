# 首次运行指南

## 1. 安装与检查

在本 skill 目录使用 `uv` 管理 Python 3.11、锁文件和虚拟环境：

```powershell
uv sync --extra test
uv run tmall-materials --help
$quickValidate = Join-Path $env:USERPROFILE ".codex\skills\.system\skill-creator\scripts\quick_validate.py"
uv run python -X utf8 $quickValidate .
```

`uv` 根据 `.python-version` 使用 Python 3.11，并依据 `uv.lock` 创建或同步 `.venv`。首次同步需要访问 Python 包索引；后续验收使用 `uv lock --check` 检查锁文件是否与 `pyproject.toml` 一致。不要向系统 Python 或 Conda 基础环境直接安装本项目依赖。

## 2. 分片增量图片索引

先校验商品表，再对三个声明的图片来源建立只读索引。只有读取失败、缺少/重复必需表头等 schema 或批次级错误、以及空表会阻断整批。缺商品 ID、非法商品 ID、重复商品 ID 是行级 blocked：`scan-summary.json` 保留对应 source row 和 reason codes，这些行不参与 ID、SKU 或名称匹配，其余有效行继续。非空但全部行为行级 blocked 时仍可完成纯 metadata 索引。以下命令使用占位商品表与隔离输出目录，仅供复制后替换；它们不表示已经扫描真实 NAS。raw indexing 不需要月份、店铺或坑位。

首次 new：

```powershell
uv run tmall-materials index-assets `
  --products "<商品总表.csv>" `
  --root "model_nas=Y:\视觉部\1-模特图" `
  --root "xhs_taobao=Z:\浙江酷趣\运营中心\营销板块\小红书koc置换&淘宝买家秀\优质买家秀" `
  --root "xhs_buyer=Z:\浙江酷趣\运营中心\营销板块\小红书koc置换&买家秀\优质买家秀" `
  --output "<隔离输出目录>\asset-index" `
  --partition-depth 2 `
  --checkpoint-size 1000
```

中断、已有持久 checkpoint 或部分失败后，在同一商品表、roots、output 和参数上恢复：

```powershell
uv run tmall-materials index-assets `
  --products "<商品总表.csv>" `
  --root "model_nas=Y:\视觉部\1-模特图" `
  --root "xhs_taobao=Z:\浙江酷趣\运营中心\营销板块\小红书koc置换&淘宝买家秀\优质买家秀" `
  --root "xhs_buyer=Z:\浙江酷趣\运营中心\营销板块\小红书koc置换&买家秀\优质买家秀" `
  --output "<隔离输出目录>\asset-index" `
  --partition-depth 2 `
  --checkpoint-size 1000 `
  --resume
```

图片新增、修改或删除后，在同一索引身份上刷新：

```powershell
uv run tmall-materials index-assets `
  --products "<商品总表.csv>" `
  --root "model_nas=Y:\视觉部\1-模特图" `
  --root "xhs_taobao=Z:\浙江酷趣\运营中心\营销板块\小红书koc置换&淘宝买家秀\优质买家秀" `
  --root "xhs_buyer=Z:\浙江酷趣\运营中心\营销板块\小红书koc置换&买家秀\优质买家秀" `
  --output "<隔离输出目录>\asset-index" `
  --partition-depth 2 `
  --checkpoint-size 1000 `
  --refresh
```

每次运行后检查 `<隔离输出目录>\asset-index\scan-summary.json` 和 `match-candidates.csv`；`asset-index.sqlite3` 保存 checkpoint 与 active/inactive 状态。`scan-summary.json` 也包含等价的恢复命令、最小商品校验证据、本轮逐 root/partition 统计，以及 `PATH_OUTSIDE_ROOT`、`FILE_STAT_ERROR`、`FILE_INSPECTION_ERROR` 等稳定错误码。ID/SKU 精确候选仍是 `matched_unlicensed`，名称候选是 `needs_manual_confirmation`，所有候选的 `license_status` 都从 `unknown` 开始。索引器不会生成 `confirmed-assets.csv`、批准清单或生产任务，也不会 dry-run、上传或发布。

原始 NAS 图片只读，视频继续延期。同一个 `asset-index.sqlite3` 同一时刻只能有一个 Agent 或进程执行 new、`--resume`、`--refresh`，禁止并发。人工检查候选并确认逐文件授权后，才能生成或接受 `confirmed-assets.csv`，然后进入素材完整性审查、生产选择器、全量 dry-run 和 1–3 商品生产验收。

## 3. 启动用户控制的 CDP 浏览器

关闭正在使用同一 profile 的 Chromium 后，以独立 profile 和仅本机监听的调试端口启动 Chrome 或 Edge。例如将实际可执行文件路径替换进下列命令：

```powershell
& "<chrome-or-edge.exe>" --remote-debugging-port=9222 --remote-debugging-address=127.0.0.1 --user-data-dir="<独立profile目录>"
```

CDP URL 为 `http://127.0.0.1:9222`。用户必须在该窗口自行登录、处理验证码/短信/扫码/风控，并确认页面可见店铺名。不要把 profile、Cookie 或登录信息放入版本库。

## 4. 导出与只读检查

所有时间使用带时区 ISO 8601，例如 `2026-07-17T10:00:00+08:00`。导出 `run-id` 使用不可重复的可读值，例如 `20260717T100000+0800-kktree-export`。

```powershell
uv run tmall-materials export --store "<精确店铺名>" --selectors "<生产selectors.yaml>" --output "<导出目录>" --run-id "<导出run-id>" --downloaded-at "<ISO时间>" --cdp-url "http://127.0.0.1:9222"
uv run tmall-materials inspect-xlsx --basic "<基础素材XLSX>" --search "<搜推经营XLSX>"
```

## 5. 首次 dry-run 与 Playwright 补采

```powershell
uv run tmall-materials run --mode dry-run --month <1-12> --store "<店铺名>" --products "<商品总表.csv>" --rules "<月度规则.csv>" --basic "<基础素材.xlsx>" --search "<搜推经营.xlsx>" --output "<首次批次目录>" --started-at "<ISO时间>"
uv run tmall-materials supplement --store "<店铺名>" --selectors "<生产selectors.yaml>" --candidates "<首次批次目录>\supplement-candidates.csv" --output "<backend-material-status.csv>" --collected-at "<ISO时间>" --cdp-url "http://127.0.0.1:9222"
```

## 6. 素材、授权、文案与最终 dry-run

目录型素材只搜索 `<asset-root>/<商品ID>/`，其次 `<asset-root>/<货号>/`。`--license-status confirmed` 表示用户确认该批目录中的每个文件均已授权；若授权状态不统一，当前 CLI 不能正式发布，应保持 blocked，待接入逐文件素材清单。当前视频元数据入口也未闭合，视频任务应保持 `VIDEO_METADATA_UNAVAILABLE`。

AI 文案 JSON 使用 `<商品ID>:<坑位号>` 作为键，值包含 `title` 和 `description`；详细结构见 [data-schema.md](data-schema.md)。

```powershell
uv run tmall-materials run --mode dry-run --month <1-12> --store "<店铺名>" --products "<商品总表.csv>" --rules "<月度规则.csv>" --basic "<基础素材.xlsx>" --search "<搜推经营.xlsx>" --backend-status "<backend-material-status.csv>" --asset-root "<素材根目录>" --license-status confirmed --media-policy "<生产media-policy.yaml>" --copy-responses "<AI文案.json>" --prohibited-term "<禁用词>" --output "<最终审核批次目录>" --started-at "<ISO时间>"
```

打开 `review.html`，只选择 `ready_for_review` 的精确 task ID。

## 7. 精确批准、发布与恢复

```powershell
uv run tmall-materials approve --run-dir "<最终审核批次目录>" --task-id "<task-id-1>" --confirmed-by "<批准人>" --confirmed-at "<ISO时间>" --valid-until "<ISO时间>"
uv run tmall-materials publish --run-dir "<最终审核批次目录>" --store "<店铺名>" --selectors "<生产selectors.yaml>" --cdp-url "http://127.0.0.1:9222"
```

`approve` 生成并持久化 `approval-manifest.json` 及 approved 状态；发布命令只接受该目录中的不可变清单。发布结果不确定时立即暂停，执行：

```powershell
uv run tmall-materials resume --run-dir "<最终审核批次目录>" --store "<店铺名>" --selectors "<生产selectors.yaml>" --cdp-url "http://127.0.0.1:9222"
uv run tmall-materials report --run-dir "<最终审核批次目录>"
```

生产前还必须满足 [production-acceptance.md](production-acceptance.md)。示例选择器和示例媒体策略不能直接用于生产。

## 8. 交互式任务执行

新任务使用以时间戳命名的独立会话目录；恢复旧任务时必须指定原 `session_id`，不得默认选择最新目录。在项目根目录运行：

```powershell
uv run --project .\upload-search-materials --locked tmall-materials interact --runs-root .\runs
uv run --project .\upload-search-materials --locked tmall-materials wait-handoff --runs-root .\runs --session 20260721_143025 --stage asset_matching
```

Agent 按以下顺序处理每一阶段：

1. 解析精确 `session_id`。
2. 仅对新任务创建时间戳隔离目录。
3. 启动仅监听 `localhost` 的 UI。
4. 等待精确当前阶段的 handoff。
5. 对照 `input.json` 验证 `session_id`、`stage_id`、`revision` 和 `input_sha256`。
6. 将该阶段标记为 `processing`。
7. 只运行当前阶段允许的动作。
8. 写入与同一组四个值绑定的 `result.json`。
9. 停止，或明确进入下一阶段。

`wait-handoff` 只返回已验证的当前 handoff，并领取该阶段为 `processing`。页面阶段 08/09 只保存输入并生成 handoff；它们不直接运行 `approve` 或 `publish`。这两个命令必须由 Agent 分开调用，且 1–3 个商品的生产测试需在当前对话再次获得用户显式授权。素材变化会使旧批准失效，页面上的旧提交不授权发布新内容。

若页面没有 Agent 心跳，或原 Codex 任务已结束，请用户把页面显示的恢复指令完整粘贴到新建或当前 Codex 任务。页面不会在后台继续执行 Agent，也无法唤醒已结束的任务。
