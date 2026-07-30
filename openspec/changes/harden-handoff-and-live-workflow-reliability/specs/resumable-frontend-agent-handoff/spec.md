## Purpose

让前端人工阶段在 Codex 当前回合存活时自动交接，并在监听超时或中断后通过同一会话中的简短恢复信号安全、幂等地继续，而不引入外部唤醒服务或削弱批准门禁。

## ADDED Requirements

### Requirement: Manual stages use bounded handoff waiting
当 Agent 打开一个需要人工输入的阶段时，系统 SHALL 允许 Agent 为精确 session、stage 和预期 revision 建立有时限的等待租约，并以分段等待方式监听权威 handoff。

#### Scenario: User submits during an active wait
- **WHEN** 用户在有效等待租约期间正式提交当前阶段
- **THEN** Agent SHALL 在不要求额外聊天回复的情况下发现并尝试认领该 handoff

#### Scenario: User does not submit within the watch window
- **WHEN** 有界等待窗口结束且没有有效 handoff
- **THEN** 系统 SHALL 让等待租约自然过期并保持阶段草稿或已提交状态不变，不得写入失败结果

#### Scenario: Waiting is reported without blocking feedback
- **WHEN** Agent 分段等待 handoff
- **THEN** 用户 SHALL 至少每分钟看到一次仍在等待、剩余窗口或恢复方式，而不是无期限无反馈

### Requirement: Waiting and processing ownership are distinct
系统 MUST 将表示“Agent 正在等待”的 `agent_wait` 与表示“Agent 已认领并正在执行”的 processing claim 分离，等待租约不得授予任何业务执行权限。

#### Scenario: Waiter is active before submission
- **WHEN** 当前阶段尚未生成 handoff 且 `agent_wait` 有效
- **THEN** 页面 SHALL 显示 Codex 正在监听，但阶段 SHALL 保持可编辑且不得显示为 processing

#### Scenario: Waiter disappears
- **WHEN** Agent 回合中断且 `agent_wait` 超过到期时间
- **THEN** 页面 SHALL 显示 Codex 当前未监听以及“在当前聊天输入已提交”的恢复提示

#### Scenario: Handoff is claimed
- **WHEN** 一个有效 handoff 被原子认领
- **THEN** 系统 SHALL 清除或覆盖等待提示并以 processing claim 作为唯一执行归属

### Requirement: Handoff identity is authoritative
Agent MUST 在执行任何阶段动作前重新读取权威 session、input 和 handoff，并验证 `session_id`、`current_stage`、`stage_id`、`revision` 和 `input_sha256`。

#### Scenario: Submitted identity is valid
- **WHEN** handoff 身份与当前 session 和 input 内容完全一致且阶段为 `ready_for_agent`
- **THEN** 系统 SHALL 在同一会话锁内创建唯一 processing claim

#### Scenario: Input changed after submission
- **WHEN** 当前 `input.json` 的重新计算哈希与 handoff 不一致
- **THEN** Agent MUST 拒绝认领并返回稳定的身份冲突原因，不得执行业务动作

#### Scenario: Two agents observe the same handoff
- **WHEN** 两个 Agent 同时发现同一 revision 的 handoff
- **THEN** 只有一个 Agent SHALL 获得 processing claim，另一个 SHALL 显示权威处理中状态

### Requirement: Current-chat submission acknowledgement is a recovery signal
在当前聊天已唯一绑定精确 session 时，用户输入“已提交” SHALL 只触发权威状态解析与恢复，不得被解释为结构化业务输入、批准或生产发布授权。

#### Scenario: Ready handoff exists
- **WHEN** 用户在当前绑定聊天输入“已提交”且当前阶段为 `ready_for_agent`
- **THEN** Agent SHALL 校验并认领该 handoff，然后继续工作流

#### Scenario: No formal submission exists
- **WHEN** 用户输入“已提交”但当前阶段仍为 draft 且没有有效 handoff
- **THEN** Agent SHALL 告知尚未检测到正式提交并继续等待，不得自行构造 handoff

#### Scenario: Work is already processing
- **WHEN** 用户重复输入“已提交”且存在有效 processing claim
- **THEN** Agent SHALL 返回当前进度而不得启动重复执行

#### Scenario: Chat has no unique session binding
- **WHEN** 当前聊天是新会话、上下文丢失或存在多个候选 session
- **THEN** Agent MUST 要求页面生成的完整恢复指令，禁止按目录时间猜测

### Requirement: Handoff recovery is idempotent across interruption points
系统 SHALL 依据阶段状态、processing claim、result 和 checkpoint 从提交前、认领前、执行中或完成后中断恢复，并保证同一 revision 的副作用最多执行一次。

#### Scenario: Agent stops after submission but before claim
- **WHEN** handoff 已原子写入而 Agent 在认领前中断
- **THEN** 阶段 SHALL 保持 `ready_for_agent`，后续“已提交”恢复 SHALL 能认领同一 handoff

#### Scenario: Agent stops after claim
- **WHEN** Agent 已认领但处理进程中断
- **THEN** 系统 SHALL 根据 processing lease、进程归属和 durable checkpoint 决定等待、恢复或人工处理

#### Scenario: Result was completed before chat ended
- **WHEN** 绑定同一身份的 completed result 已存在
- **THEN** 后续恢复 SHALL 复用结果并进入下一阶段，不得重复执行已完成动作

### Requirement: Safety-critical authorization remains exact
等待租约、自动发现 handoff 和“已提交”恢复 MUST NOT 替代批准清单、生产确认、店铺校验或发布授权。

#### Scenario: User says submitted at approval stage
- **WHEN** 用户只输入“已提交”而没有与精确清单和哈希绑定的有效批准 handoff
- **THEN** 系统 MUST NOT 生成批准或执行发布

#### Scenario: Exact approval handoff is valid
- **WHEN** 页面提交的批准 handoff 与不可变任务清单、内容哈希、店铺、有效期和动作完全绑定
- **THEN** Agent MAY 在重新校验全部安全门禁后执行该阶段允许的批准动作
