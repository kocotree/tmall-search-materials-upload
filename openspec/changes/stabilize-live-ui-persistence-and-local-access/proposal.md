## Why

真实会话 `20260730_004051` 证明当前交互工作台仍可能在 Windows 文件替换失败后留下 revision 快照与 `session.json` 不一致的半完成提交，随后所有重试都报 `revision snapshot already exists`；与此同时，等待器反复创建 90 秒租约，使页面看起来长期处于监听状态。当前文件夹选择器和 NAS 检测还运行在 Codex 沙箱身份中，即使桌面用户能够访问映射盘，选择窗口也可能无法出现，路径也会被误报为不可访问。

## What Changes

- 将阶段草稿与正式提交改为可恢复、幂等的事务：相同请求重试能够完成半完成状态，内容冲突才返回 revision 冲突。
- 为 Windows 原子替换增加可判定的短暂占用恢复，并记录事务阶段和稳定原因码，避免把一次文件占用永久放大成会话损坏。
- 将等待租约缩短并改为复用同一 `wait_id` 续租；Agent 正常结束、提交失败或状态变化时主动清理，页面分别展示心跳有效期和总等待预算。
- 让交互服务、文件夹选择器、路径检测、文件索引和素材读取 Worker 在用户授权后统一运行于可访问本机资源的桌面用户上下文。
- 保留每台电脑独立的素材源路径配置，支持映射盘与 UNC；不同电脑只需重新绑定稳定素材源名称，不把盘符、用户名或凭据写入仓库。
- 用一个受限的 Windows 用户上下文启动入口替代后台 Flask 线程直接弹窗；确保单实例、前台激活、超时、取消和手工输入回退均有稳定结果。
- 增加真实故障夹具和 Windows 验收，覆盖 revision 半完成写入、短租约、退出清理、映射盘身份差异、选择窗口未出现及并发选择。
- 与 `harden-handoff-and-live-workflow-reliability` 和 `fix-nas-path-detection-and-folder-picker` 协调：复用已有状态协议与诊断，不引入第二套 handoff、路径探测或文件夹选择实现。

## Capabilities

### New Capabilities

- `crash-consistent-stage-submission`: 定义草稿、revision 快照、正式 handoff 与会话状态的幂等提交、半完成事务恢复和冲突判定。
- `bounded-agent-wait-lifecycle`: 定义短期等待租约的创建、续租、主动清理、总等待预算和准确页面反馈。
- `interactive-local-resource-access`: 定义受限桌面用户上下文启动、本机/NAS 路径访问、跨电脑本机绑定和可靠 Windows 文件夹选择。

### Modified Capabilities

<!-- `openspec/specs/` 当前没有已发布能力；相关进行中变更作为协调依赖，不声明为已发布能力修改。 -->

## Impact

- 会话与持久化：`interaction/session.py`、共享原子写入、revision 目录、事务恢复、提交/草稿 API 和错误响应。
- Agent 交接：`wait-handoff`、`agent_wait` 生命周期、前端状态投影、倒计时与恢复提示。
- Windows 运行身份：受管 UI 启动、Worker 派生、SID/会话身份证据、映射盘与 UNC 预检。
- 文件夹选择：用户触发的桌面 helper、窗口前置、单实例、超时、取消和手工路径回退。
- 本机配置：素材源稳定名称与每台电脑独立路径绑定；不持久化 NAS 凭据。
- 测试与文档：真实会话回放、Windows 集成测试、前端状态测试、Skill 和操作指南。
- 不扩大批准、上传或生产发布权限，不自动登录 NAS，不绕过 Windows 权限，不读取或修改源图片内容。
