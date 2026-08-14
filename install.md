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
- 已安装 Git for Windows，并且当前用户有权通过 SSH 读取项目的 Gitee 仓库。
- 当前电脑能够连接公司网络中的两个固定 NAS。尚未建立访问会话时，可以在首次配置页面中由用户点击“连接共享盘”并通过 Windows 完成认证。

打开 PowerShell，依次检查：

```powershell
git --version
codex.cmd --version
ssh -T git@gitee.com
```

说明：

- `ssh -T` 只用于确认 Gitee SSH 身份。首次连接时按公司安全要求核对主机指纹。
- 不要把 NAS 密码、Cookie、Token 或 SSH 私钥复制到项目目录或 Codex 对话中。

## 二、添加 Windows Marketplace

以下命令会让 Codex 从远程 `win` 分支读取 Marketplace 清单，不需要手工复制项目目录：

```powershell
codex.cmd plugin marketplace add git@gitee.com:QuanLongZhang/tmall-search-materials-upload.git --ref win --json
```

然后检查 Marketplace：

```powershell
codex.cmd plugin marketplace list --json
```

输出中应存在：

- `name` 为 `tmall-materials-team`；
- `marketplaceSource.source` 为本项目的 Gitee SSH 地址。

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
- `marketplaceSource.source` 为本项目的 Gitee SSH 地址。

任何一项不满足都不要继续。尤其当 `source.ref` 为 `main` 时，说明远程 Windows Marketplace 清单尚未正确发布。

## 四、首次运行时自动准备环境

普通用户不需要打开 Plugin 缓存目录，也不需要手动运行 `bootstrap.cmd`、安装 Python 或同步依赖。

首次在 Codex 中启动本 Plugin 时，Agent 会自动检查用户级运行环境。环境缺失时，Agent 使用当前 Plugin 自带的 `scripts\bootstrap.cmd`，在 `%LOCALAPPDATA%\tmall-search-materials\runtime` 下准备 Python 3.11、锁定依赖、虚拟环境和缓存，然后继续打开配置页。

如果电脑尚未安装 `uv`，Agent 只会请求用户批准安装；批准后继续自动准备。用户不应把依赖手工安装到系统 Python、Conda 基础环境或 Plugin 安装缓存中。

`bootstrap.cmd` 和 `run-plugin.cmd --help` 只作为维护者故障诊断命令，不是普通用户的安装步骤。

## 五、首次启动与 Windows/UNC 验收

完成 Plugin 安装后，完全退出并重新打开 Codex。新建任务并输入：

```text
开始上传搜推素材
```

首次启动时按 Codex 提示允许项目的固定 Windows 工作台启动脚本在当前桌面用户身份下运行。不要用另一个管理员账号启动，否则该进程可能看不到当前用户的 UNC 会话或原生文件夹窗口。

配置页会自动从 Plugin 的 `config\nas-sources.yaml` 加载并检测以下两个固定 NAS：

- 浙江酷趣：`\\192.168.110.20\浙江酷趣`
- 视觉部：`\\192.168.124.85\视觉部`

用户不需要手工输入这两个 UNC 根路径。NAS 已可访问时，页面直接提供“添加整个共享盘”和“选择业务目录”；尚未连接时，页面显示“连接共享盘”，由用户点击后在 Windows 资源管理器中完成认证。Plugin 不会静默连接、绕过权限或保存 NAS 密码。

第一轮手工验收只做到“NAS 自动检测、业务目录选择和选图”：

1. 配置页能够正常打开。
2. “公司共享盘”区域自动展示“浙江酷趣”和“视觉部”，并自动显示各自访问状态。
3. 对尚未连接的 NAS 点击“连接共享盘”，在 Windows 中完成认证后等待页面重新检测。
4. 在任一可用 NAS 中点击“选择业务目录”，逐级浏览并选择本次素材源文件夹。
5. 选中的目录自动加入“本次图片源”，提交配置时系统自动复核路径可用性。
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
codex.cmd plugin marketplace add git@gitee.com:QuanLongZhang/tmall-search-materials-upload.git --ref win --json
codex.cmd plugin add tmall-search-materials@tmall-materials-team --json
```

移除和重新安装不会修复“相同版本号、不同代码内容”的发布错误。如果 Windows 分支修改过随 Plugin 分发的文件，维护者必须先发布全新的版本号；不要反复移除和添加同一个版本来碰运气。

## 七、常见问题

### Gitee 报权限不足或 Host key verification failed

先由目标电脑使用者修复当前 Windows 用户的 Git/SSH 访问。不要把私钥复制到项目或 Plugin 缓存中。

### `codex` 在 PowerShell 中被执行策略阻止

本说明统一使用 `codex.cmd`，避免 PowerShell 优先调用 `codex.ps1`。不要为了安装 Plugin 随意放宽整台电脑的脚本执行策略。

### `UV_NOT_FOUND`

正常首次运行中，Agent 应请求用户批准安装 `uv`，安装完成后自动继续，不应要求普通用户执行终端命令。只有自动准备失败并进入维护者诊断时，才手工检查 `uv --version` 和 `scripts\bootstrap.cmd`。

### UNC 返回不可访问或拒绝访问

先在配置页对应的“浙江酷趣”或“视觉部”卡片点击“连接共享盘”，并由用户在 Windows 中完成 NAS 认证。确认资源管理器和 Plugin 工作台使用同一个普通 Windows 登录用户。Plugin 不会自动映射共享、绕过权限或保存凭据。

### 已安装但仍然是旧代码

检查 `codex.cmd plugin list --json` 中的 `version` 和 `source.ref`。若版本号未变化或仍指向 `main`，停止测试，由维护者修正远程 `win` 清单并发布新的不可变版本；不要继续复用旧缓存。
