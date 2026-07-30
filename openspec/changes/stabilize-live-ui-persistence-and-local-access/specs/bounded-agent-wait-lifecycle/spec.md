## Purpose

让页面只在 Agent 实际等待时显示短而准确的监听状态，并在提交、状态变化或 Agent 正常退出后及时清理，不制造无限延长的租约错觉。

## ADDED Requirements

### Requirement: One waiter renews one short lease
同一 claimant 对同一 session、stage 和 expected revision MUST 复用同一个 `wait_id`，单次等待租约不得超过 30 秒。

#### Scenario: Agent continues segmented waiting
- **WHEN** 同一 Agent 在总等待预算内开始下一个等待片段
- **THEN** 系统 SHALL 续租原有 `wait_id`，不得创建新的等待身份或重置开始时间

### Requirement: Wait leases have explicit terminal cleanup
Agent 在正常超时、取消、服务错误、stage/session 变化或成功认领时 MUST 主动清除匹配等待租约；异常退出时才允许自然过期。

#### Scenario: Total wait budget ends normally
- **WHEN** Agent 完成最后一个等待片段且没有收到 handoff
- **THEN** 系统 SHALL 清除等待租约并让页面立即显示聊天恢复提示

#### Scenario: Submission fails while Agent is waiting
- **WHEN** 页面提交返回持久化或验证错误
- **THEN** 页面 SHALL 优先显示提交失败及恢复动作，同时准确说明 Agent 是否仍在等待

### Requirement: Heartbeat lease and total budget are distinct
页面 MUST 分别呈现 Agent 在线心跳状态与总等待预算，不得把反复续租后的剩余秒数描述成任务总剩余时间。

#### Scenario: Lease is renewed during a two-minute setup wait
- **WHEN** 等待心跳续租但总等待预算持续消耗
- **THEN** 页面 SHALL 保持总预算单调递减且不得让用户看到倒计时反复跳回初始值

### Requirement: Stage budgets remain safe and configurable
普通配置阶段默认总等待预算 SHALL 为两分钟；复杂人工阶段可以使用更长配置值，批准或生产确认超时不得形成任何授权。

#### Scenario: Setup is not submitted within default budget
- **WHEN** 两分钟配置等待预算结束
- **THEN** Agent SHALL 正常结束等待、清理租约并提示用户使用精确会话恢复
