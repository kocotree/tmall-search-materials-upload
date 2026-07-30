## Purpose

使 Windows 工作台在用户明确授权后以能够访问本机与 NAS 资源的桌面用户身份运行，并在不同电脑上通过本机绑定可靠选择和读取素材目录。

## ADDED Requirements

### Requirement: File-access components share one verified desktop identity
交互服务、路径诊断、文件夹选择器、文件索引和素材读取 Worker MUST 在同一经用户授权且可验证的桌面用户会话中运行。

#### Scenario: Mapped drive is visible only to the desktop user
- **WHEN** 沙箱身份无法访问映射盘但授权桌面用户能够访问
- **THEN** 系统 SHALL 使用授权桌面用户上下文启动整条文件访问链路并将路径报告为可访问

#### Scenario: Runtime identity changes unexpectedly
- **WHEN** 子进程的 Windows SID 或登录会话与受管服务身份不一致
- **THEN** 系统 SHALL 在读取素材前停止并返回 `LOCAL_RESOURCE_IDENTITY_MISMATCH`

### Requirement: Desktop launch is narrow and locally contained
用户上下文启动 MUST 使用固定项目入口、精确参数、本机回环地址和不可猜测所有权令牌，不得接受任意命令或扩大上传与发布权限。

#### Scenario: User authorizes the managed workbench launcher
- **WHEN** Codex 使用固定启动入口请求一次用户许可
- **THEN** 启动器 SHALL 只启动项目准备好的工作台及其受管子进程，并仅监听 `127.0.0.1`

### Requirement: Machine-local bindings make NAS sources portable
系统 SHALL 以稳定素材源名称引用图片来源，并将实际盘符或 UNC 路径保存在不进入版本库的每机配置中。

#### Scenario: Another computer maps the same NAS to a different drive
- **WHEN** 用户在新电脑上将同一素材源重新绑定到不同盘符或 UNC
- **THEN** 工作流 SHALL 使用新电脑的本机绑定而无需修改项目文件或业务阶段数据

### Requirement: Folder selection reliably owns an interactive window
文件夹选择 MUST 由显式用户操作触发，并由桌面用户上下文中的单实例交互 helper 创建、前置和拥有窗口。

#### Scenario: Picker opens successfully
- **WHEN** 用户点击选择文件夹且桌面会话可用
- **THEN** 系统 SHALL 在有界时间内显示前台选择窗口，并在选择后返回可访问的绝对目录

#### Scenario: Existing picker is already open
- **WHEN** 用户重复点击而同一服务已有活动选择窗口
- **THEN** 系统 SHALL 激活已有窗口或返回 `FOLDER_PICKER_BUSY`，不得静默创建不可见窗口

#### Scenario: Picker cannot become visible
- **WHEN** helper 已启动但无法在期限内证明窗口可见
- **THEN** 系统 SHALL 结束仅属于该请求的 helper，保留原路径并返回 `FOLDER_PICKER_NOT_VISIBLE` 与手工输入回退

### Requirement: NAS access never stores or bypasses credentials
系统 MUST 使用 Windows 当前用户已有访问权，不得收集、记录、传播 NAS 凭据或绕过文件系统权限。

#### Scenario: Desktop user lacks NAS permission
- **WHEN** 授权桌面身份仍无法读取配置目录
- **THEN** 系统 SHALL 返回 `ACCESS_DENIED` 并要求用户在 Windows 中解决权限，不得提示在工作台输入密码
