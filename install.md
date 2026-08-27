# Windows 11 手动安装说明

本文说明如何在另一台 Windows 11 电脑上，通过 Gitee 的 `win` 分支手动安装“天猫搜推素材” Codex Plugin。

## 当前发布状态

在把本说明交给安装人员前，维护者必须先完成 Windows 分支发布：

1. `.agents/plugins/marketplace.json` 中插件源码的 `ref` 必须为 `win`。
2. `.codex-plugin/plugin.json` 必须使用一个从未发布过的 Windows Plugin 新版本号。
3. 上述文件和目标代码必须已经提交并推送到远程 `win` 分支。

安装人员只应在维护者确认远程 `win` 分支和全新版本均已发布后继续。添加 Marketplace 时的 `--ref win` 与 Marketplace 清单中的 Plugin `source.ref` 是两层独立配置；安装后必须再次检查 `source.ref`，不是 `win` 就立即停止，不要继续测试。

## 一、目标电脑准备

目标电脑需要满足：

- Windows 11，使用日常登录的普通桌面用户操作，不要切换到另一个管理员账号安装或运行工作台。
- 已安装并登录 Codex 桌面端。
- 已安装 Git for Windows，并且当前用户能够通过 HTTPS 读取项目的 Gitee 公开仓库；不需要配置 Gitee SSH 公钥。
- 当前电脑能够连接公司网络中的两个固定 NAS。尚未建立访问会话时，可以在首次配置页面中由用户点击“连接共享盘”并通过 Windows 完成认证。

打开 PowerShell，依次检查：

```powershell
git --version
codex.cmd --version
```

说明：

- 安装使用公开仓库的 HTTPS 只读地址，不要求登录 Gitee，也不需要添加 SSH 主机密钥。
- 不要把 NAS 密码、Cookie、Token 或其他凭据复制到项目目录或 Codex 对话中。

## 二、添加 Windows Marketplace

以下命令会让 Codex 从远程 `win` 分支读取 Marketplace 清单，不需要手工复制项目目录：

```powershell
codex.cmd plugin marketplace add https://gitee.com/QuanLongZhang/tmall-search-materials-upload.git --ref win --json
```

然后检查 Marketplace：

```powershell
codex.cmd plugin marketplace list --json
```

输出中应存在：

- `name` 为 `tmall-materials-team`；
- `marketplaceSource.source` 为本项目的 Gitee HTTPS 地址。

如果这台电脑已经配置过同名 Marketplace，不要直接覆盖或猜测当前来源。先执行“六、已有旧版本时的处理”。

## 三、安装 Plugin

执行：

```powershell
codex.cmd plugin add tmall-search-materials@tmall-materials-team --json
```

安装后检查：

```powershell
codex.cmd plugin list --json
```

必须同时满足：

- `pluginId` 为 `tmall-search-materials@tmall-materials-team`；
- `installed` 为 `true`；
- `enabled` 为 `true`；
- `version` 与本次 Windows 发布版本一致；
- `source.ref` 为 `win`，不能是 `main`；
- `marketplaceSource.source` 为本项目的 Gitee HTTPS 地址。

任何一项不满足都不要继续。尤其当 `source.ref` 为 `main` 时，说明远程 Windows Marketplace 清单尚未正确发布。

## 四、首次运行时自动准备环境

普通用户不需要打开 Plugin 缓存目录，也不需要手动运行 `bootstrap.cmd`、安装 Python 或同步依赖。

首次在 Codex 中启动本 Plugin 时，Agent 会自动检查用户级运行环境。环境缺失时，Agent 使用当前 Plugin 自带的 `scripts\bootstrap.cmd`，在 `%LOCALAPPDATA%\tmall-search-materials\runtime` 下准备 Python 3.11、锁定依赖、虚拟环境和缓存，然后继续打开配置页。第三方依赖默认优先从清华 TUNA 镜像下载，镜像不可用时自动回退 Python 官方源；版本和 SHA-256 仍严格取自随 Plugin 发布的 `uv.lock`，不会因切换下载源而改变依赖。下载中断留下的半成品虚拟环境不会被视为可用，下一次启动会继续修复。

工作台首次启动还会把随 Plugin 发布的生产选择器基线初始化到 `%LOCALAPPDATA%\tmall-search-materials\config\selectors.local.yaml`，其中包含“搜推高价值”全量采集和空坑位精确复核所需选择器。该文件已经存在时绝不覆盖，Plugin 升级也不会替换本机维护版本。基线只用于免除首次手工配置；每次真实采集仍校验当前千牛页面 DOM，校验失败时停止并提示选择器失效，不会把未匹配到的元素当作零素材或零空坑位。

如果电脑尚未安装 `uv`，Agent 只会请求用户批准安装；批准后继续自动准备。用户不应把依赖手工安装到系统 Python、Conda 基础环境或 Plugin 安装缓存中。

`bootstrap.cmd` 和 `run-plugin.cmd --help` 只作为维护者故障诊断命令，不是普通用户的安装步骤。

## 五、首次启动与 Windows/UNC 验收

完成 Plugin 安装后，完全退出并重新打开 Codex。新建任务并输入：

```text
开始上传搜推素材
```

首次启动时按 Codex 提示允许项目的固定 Windows 工作台启动脚本在当前桌面用户身份下运行。不要用另一个管理员账号启动，否则该进程可能看不到当前用户的 UNC 会话或原生文件夹窗口。

配置页允许用户选择当前电脑可访问的团队索引文件夹和本次图片源。以下共享位置可以作为团队环境中的默认参考，但页面不会自动认证或强制使用固定路径：

- 浙江酷趣：`\\192.168.110.20\浙江酷趣`
- 视觉部：`\\192.168.124.85\视觉部`

需要访问共享目录时，先由用户在 Windows 资源管理器中完成认证，再通过页面的“选择文件夹”选取目录。Plugin 不会静默连接、绕过权限或保存 NAS 密码。

配置页的“飞书多维表格”模块已经预设团队商品信息表和上传记录表，不需要填写链接或表 ID。首次使用时点击“授权飞书”，在新打开的飞书官方页面完成授权；工作台会自动检测并启用负责人同步和成功上传记录。授权后工作台会把负责人和成功上传原图指纹一起同步到本机；后续加载素材时使用本机快照排除已经成功上传的原图，不会逐张请求飞书。授权完成后后续任务会复用当前 Windows 用户的飞书登录状态，不需要每次重新授权。上传记录表需保留文本字段“原图 SHA-256”。

工作台会自动绑定团队飞书应用；普通用户不需要在终端运行飞书命令。如果页面提示授权组件缺失或应用权限未开放，请联系 Plugin 维护者处理环境或应用权限。未授权或飞书暂时不可用时，Plugin 会继续使用本地商品表，不会阻断主上传流程。

第一轮手工验收只做到“NAS 自动检测、业务目录选择和选图”：

1. 配置页能够正常打开。
2. “团队索引文件夹”可选择、检测并保存一个当前电脑可访问的索引目录。
3. “本次图片源”可通过“选择文件夹”逐项添加本机、映射盘或 UNC 目录。
4. 提交配置时系统自动复核团队索引和图片源路径可用性。
5. 若第二阶段提示需要团队索引，返回任务配置保存正确目录后，系统自动继续原任务。
6. 采用素材文件夹后，点击“确认文件夹并加载图片”。
7. 页面显示候选加载进度并进入选图页，预览可见，NAS 原图保持只读。
8. 可以采用或取消采用图片，刷新页面后当前决定仍然正确。

本轮验收到此停止，不进入审批、真实上传或生产发布。

## 六、已有旧版本时的处理

先停止旧 Plugin 启动的工作台，再查看现状：

```powershell
codex.cmd plugin marketplace list --json
codex.cmd plugin list --json
```

如果 `tmall-materials-team` 或 `tmall-search-materials@tmall-materials-team` 指向 `main`，按以下顺序移除旧配置，再重新执行本文第二至第五节：

```powershell
codex.cmd plugin remove tmall-search-materials@tmall-materials-team --json
codex.cmd plugin marketplace remove tmall-materials-team --json
codex.cmd plugin marketplace add https://gitee.com/QuanLongZhang/tmall-search-materials-upload.git --ref win --json
codex.cmd plugin add tmall-search-materials@tmall-materials-team --json
```

移除和重新安装不会修复“相同版本号、不同代码内容”的发布错误。如果 Windows 分支修改过随 Plugin 分发的文件，维护者必须先发布全新的版本号；不要反复移除和添加同一个版本来碰运气。

## 七、常见问题

### Gitee HTTPS 下载失败

先确认目标电脑能够用浏览器访问 Gitee，并检查公司网络、代理或 TLS 证书策略。公开仓库的只读安装不需要 Gitee 账号、SSH 公钥或私钥。

### `codex` 在 PowerShell 中被执行策略阻止

本说明统一使用 `codex.cmd`，避免 PowerShell 优先调用 `codex.ps1`。不要为了安装 Plugin 随意放宽整台电脑的脚本执行策略。

### `UV_NOT_FOUND`

正常首次运行中，Agent 应请求用户批准安装 `uv`，安装完成后自动继续，不应要求普通用户执行终端命令。只有自动准备失败并进入维护者诊断时，才手工检查 `uv --version` 和 `scripts\bootstrap.cmd`。

### UNC 返回不可访问或拒绝访问

先在配置页对应的“浙江酷趣”或“视觉部”卡片点击“连接共享盘”，并由用户在 Windows 中完成 NAS 认证。确认资源管理器和 Plugin 工作台使用同一个普通 Windows 登录用户。Plugin 不会自动映射共享、绕过权限或保存凭据。

### 已安装但仍然是旧代码

检查 `codex.cmd plugin list --json` 中的 `version` 和 `source.ref`。若版本号未变化或不是 `win`，停止测试，由维护者修正远程 `win` 清单并发布新的不可变版本；不要继续复用旧缓存。
