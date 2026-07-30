## Purpose

保证阶段草稿与正式提交在 Windows 文件占用、进程中断和重复请求下仍保持可恢复的一致状态，不会因半完成 revision 永久阻塞当前会话。

## ADDED Requirements

### Requirement: Stage persistence is recoverable and idempotent
系统 MUST 将一次草稿或正式提交绑定到 session、stage、基准 revision、目标 revision、规范化输入哈希和幂等请求 ID，并允许相同请求安全重试。

#### Scenario: Retry after session-state replacement failure
- **WHEN** revision 输入和快照已经持久化，但会话状态更新因短暂文件占用失败
- **THEN** 相同请求的重试 SHALL 验证已有内容并完成会话状态更新，而不是返回 revision 快照冲突

#### Scenario: Duplicate completed request
- **WHEN** 客户端重复发送一个已经完整提交的相同幂等请求
- **THEN** 系统 SHALL 返回原有成功结果且不得创建新 revision、handoff 或副作用

### Requirement: Genuine revision conflicts fail closed
系统 MUST 仅在已有目标 revision 与当前请求的身份或规范化内容不一致时返回稳定冲突，并保留所有现有证据。

#### Scenario: Different content targets an existing revision
- **WHEN** 请求试图使用已被不同输入占用的目标 revision
- **THEN** 系统 SHALL 返回 `REVISION_CONTENT_CONFLICT` 并不得覆盖快照、输入、handoff 或会话状态

### Requirement: Incomplete transactions are observable and repairable
系统 SHALL 在读取、提交或恢复会话时识别半完成事务，并返回当前事务阶段、可自动修复状态和明确恢复动作。

#### Scenario: Page loads an inconsistent session
- **WHEN** 会话 revision 落后于内容一致的 input 与 revision 快照
- **THEN** 系统 SHALL 自动完成安全的状态修复或展示可执行恢复动作，不得只显示通用 409 错误

### Requirement: Windows replacement retries distinguish transient occupancy
系统 SHALL 对已证明目标可写且非只读的短暂 Windows 替换占用执行有限重试，并对持续权限不足返回稳定权限错误。

#### Scenario: Existing session file is briefly held by a reader
- **WHEN** 原子替换在短暂读取窗口内返回 Windows access-denied 或 sharing-violation
- **THEN** 系统 SHALL 在有界时间内重试并在占用释放后完成写入

#### Scenario: Target is genuinely not writable
- **WHEN** 目标或父目录不具备所需写入权限
- **THEN** 系统 SHALL 快速返回 `PERSISTENCE_ACCESS_DENIED` 且不得循环重试或留下未归属临时文件
