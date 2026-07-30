## Why

当前前端提交与 Codex 处理之间缺少稳定的短期监听和中断恢复契约，导致用户经常必须返回聊天重复发送“已提交”。同时，真实会话 `20260729_171047` 暴露了弹窗处理、checkpoint 隔离、Windows Worker 身份、恢复状态机、运行环境、原子写入、编码、可观测性和输入数据质量等十类问题；部分修复已经在工作树中验证，但尚未被统一规格、实现边界和端到端验收闭合。

## What Changes

- 增加“有界监听 + 等待租约 + 持久化 handoff + 聊天幂等恢复”的前端交接协议：Codex 在人工阶段默认分段等待，监听中断后用户可在同一会话仅输入“已提交”恢复。
- 将 `agent_wait` 与现有 `processing_claim` 分离：前者只表示 Codex 当前正在等待 handoff，后者继续作为唯一排他执行认领；页面准确显示正在监听、等待恢复、已认领和处理中状态。
- 规定“已提交”只触发当前绑定 session 的权威状态解析，不构成业务输入、批准或生产授权；新会话或多 session 歧义时仍要求完整恢复指令。
- 收敛采集与阶段恢复状态机，统一处理 Worker 启动器 PID、实际进程身份、尝试历史、空/失败 checkpoint、`blocked`/`needs_user_input` 重试和迟到写入。
- 把 checkpoint 从共享可冲突状态改为按 attempt 隔离、成功后发布的模型；旧尝试的 checkpoint、CSV、日志和结果原样归档。
- 强化真实 DOM 交互：关键点击后必须验证业务状态变化；安全弹窗使用白名单、有限重试和受控 DOM 回退，无变化时提前失败并留下可复现证据。
- 提供唯一项目运行入口，自动选择正确的项目虚拟环境并预检依赖、CDP、选择器和本机配置，避免误用仓库顶层或不完整环境。
- 统一原子 JSON/CSV 写入、父目录创建、Windows 替换重试、UTF-8/UTF-8-SIG 兼容和测试临时目录策略。
- 扩展进度与诊断信息，记录阶段、动作、目标、重试次数、耗时、页面、行数、checkpoint 和恢复建议，避免长时间停留在模糊 phase。
- 在阶段一提交前展示商品表总行数、有效行、重复 ID、缺失/非法 ID 和可继续处理数量；行级异常不得阻断其余有效商品。
- 以真实会话重放和 Windows 回归测试闭合上述十类问题，并明确复用 `make-skill-interactions-frontend-first`、`stabilize-search-material-collection-workflow` 与 `close-high-value-collection-runtime-gaps`，不得创建第二套生产采集器或旁路状态。

## Capabilities

### New Capabilities

- `resumable-frontend-agent-handoff`: 定义人工阶段的有界等待、等待租约、权威 handoff 认领、同会话“已提交”恢复、页面反馈、幂等和安全授权边界。
- `live-workflow-reliability`: 定义真实采集工作流的弹窗与点击验证、attempt/checkpoint 隔离、Windows Worker 身份、统一恢复状态、运行环境、原子持久化、编码、可观测性和输入数据质量要求。

### Modified Capabilities

<!-- 当前 openspec/specs/ 尚无已发布能力；相关进行中 change 作为实现与验收依赖被协调，不在此声明为已发布能力修改。 -->

## Impact

- 交互协议与前端：阶段提交、session 状态、`agent_wait`、页面监听/恢复提示、“已提交”恢复路由和批准/生产确认门禁。
- 运行与恢复：`wait-handoff`、processing claim、attempt/worker/checkpoint/result 状态解析、Windows 进程身份和后台 Worker 生命周期。
- 浏览器采集：白名单弹窗处理、点击后状态验证、选择器证据和 maintained `supplement --scan-mode high-value` 路径。
- 持久化与环境：JSON/CSV 原子写入、目录创建、编码、项目虚拟环境选择、依赖预检和测试临时目录。
- 数据质量与诊断：商品表预检摘要、行级异常、结构化进度、日志和恢复建议。
- 测试与文档：Windows 单元/集成测试、真实会话重放、前端浏览器验收、Skill/操作指南和现有 OpenSpec 变更协调。
- 不增加上传、批准或生产发布权限；不自动登录、不处理验证码、不引入 `thread_id` 唤醒服务或外部消息队列。
