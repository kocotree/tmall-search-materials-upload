# 首次运行指南

## 1. 安装与检查

在本 skill 目录使用 Python 3.11 或更高版本：

```powershell
py -3.11 -m pip install -e ".[test]"
py -3.11 -m upload_search_materials.cli --help
```

如果 `py -3.11` 不存在，先安装受信任的 Python 3.11+，不要使用本机旧版 `python`。安装后可以使用 `tmall-materials`，也可始终使用 `py -3.11 -m upload_search_materials.cli`。

## 2. 启动用户控制的 CDP 浏览器

关闭正在使用同一 profile 的 Chromium 后，以独立 profile 和仅本机监听的调试端口启动 Chrome 或 Edge。例如将实际可执行文件路径替换进下列命令：

```powershell
& "<chrome-or-edge.exe>" --remote-debugging-port=9222 --remote-debugging-address=127.0.0.1 --user-data-dir="<独立profile目录>"
```

CDP URL 为 `http://127.0.0.1:9222`。用户必须在该窗口自行登录、处理验证码/短信/扫码/风控，并确认页面可见店铺名。不要把 profile、Cookie 或登录信息放入版本库。

## 3. 导出与只读检查

所有时间使用带时区 ISO 8601，例如 `2026-07-17T10:00:00+08:00`。导出 `run-id` 使用不可重复的可读值，例如 `20260717T100000+0800-kktree-export`。

```powershell
tmall-materials export --store "<精确店铺名>" --selectors "<生产selectors.yaml>" --output "<导出目录>" --run-id "<导出run-id>" --downloaded-at "<ISO时间>" --cdp-url "http://127.0.0.1:9222"
tmall-materials inspect-xlsx --basic "<基础素材XLSX>" --search "<搜推经营XLSX>"
```

## 4. 首次 dry-run 与 Playwright 补采

```powershell
tmall-materials run --mode dry-run --month <1-12> --store "<店铺名>" --products "<商品总表.csv>" --rules "<月度规则.csv>" --basic "<基础素材.xlsx>" --search "<搜推经营.xlsx>" --output "<首次批次目录>" --started-at "<ISO时间>"
tmall-materials supplement --store "<店铺名>" --selectors "<生产selectors.yaml>" --candidates "<首次批次目录>\supplement-candidates.csv" --output "<backend-material-status.csv>" --collected-at "<ISO时间>" --cdp-url "http://127.0.0.1:9222"
```

## 5. 素材、授权、文案与最终 dry-run

目录型素材只搜索 `<asset-root>/<商品ID>/`，其次 `<asset-root>/<货号>/`。`--license-status confirmed` 表示用户确认该批目录中的每个文件均已授权；若授权状态不统一，当前 CLI 不能正式发布，应保持 blocked，待接入逐文件素材清单。当前视频元数据入口也未闭合，视频任务应保持 `VIDEO_METADATA_UNAVAILABLE`。

AI 文案 JSON 使用 `<商品ID>:<坑位号>` 作为键，值包含 `title` 和 `description`；详细结构见 [data-schema.md](data-schema.md)。

```powershell
tmall-materials run --mode dry-run --month <1-12> --store "<店铺名>" --products "<商品总表.csv>" --rules "<月度规则.csv>" --basic "<基础素材.xlsx>" --search "<搜推经营.xlsx>" --backend-status "<backend-material-status.csv>" --asset-root "<素材根目录>" --license-status confirmed --media-policy "<生产media-policy.yaml>" --copy-responses "<AI文案.json>" --prohibited-term "<禁用词>" --output "<最终审核批次目录>" --started-at "<ISO时间>"
```

打开 `review.html`，只选择 `ready_for_review` 的精确 task ID。

## 6. 精确批准、发布与恢复

```powershell
tmall-materials approve --run-dir "<最终审核批次目录>" --task-id "<task-id-1>" --confirmed-by "<批准人>" --confirmed-at "<ISO时间>" --valid-until "<ISO时间>"
tmall-materials publish --run-dir "<最终审核批次目录>" --store "<店铺名>" --selectors "<生产selectors.yaml>" --cdp-url "http://127.0.0.1:9222" --now "<ISO时间>"
```

`approve` 生成并持久化 `approval-manifest.json` 及 approved 状态；发布命令只接受该目录中的不可变清单。发布结果不确定时立即暂停，执行：

```powershell
tmall-materials resume --run-dir "<最终审核批次目录>" --store "<店铺名>" --selectors "<生产selectors.yaml>" --cdp-url "http://127.0.0.1:9222" --now "<ISO时间>"
tmall-materials report --run-dir "<最终审核批次目录>"
```

生产前还必须满足 [production-acceptance.md](production-acceptance.md)。示例选择器和示例媒体策略不能直接用于生产。
