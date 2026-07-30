## Purpose

定义第三阶段由页面直接驱动本地画廊准备、且只在用户最终确认图片后才向 Codex 交接的行为边界，避免确定性中间步骤依赖聊天唤醒。

## ADDED Requirements

### Requirement: 文件夹确认直接启动本地画廊准备
系统 SHALL 将“确认文件夹并加载图片”实现为页面服务的本地操作，并 MUST NOT 为该操作创建 Codex/Agent handoff、Agent wait 或 processing claim。

#### Scenario: 用户确认文件夹
- **WHEN** 用户为每个商品保留至少一个采用文件夹并点击“确认文件夹并加载图片”
- **THEN** 系统 SHALL 保存规范化文件夹决定、启动本地画廊准备任务并立即向页面返回已启动状态
- **THEN** 当前 Codex 任务 SHALL NOT 因该操作被唤醒

#### Scenario: 用户重复点击相同决定
- **WHEN** 同一 session、stage 和文件夹决定身份已经具有运行中或成功的本地任务
- **THEN** 系统 SHALL 返回同一任务或已有画廊，不得重复扫描、重复增加 revision 或创建 handoff

#### Scenario: 用户改变文件夹决定
- **WHEN** 文件夹决定摘要不同于当前本地任务或画廊
- **THEN** 系统 SHALL 使旧画廊失效并为新决定启动新的本地准备任务

### Requirement: 本地准备任务可观察且可恢复
系统 SHALL 持久化本地画廊任务的身份、状态、进度、心跳、结果和稳定错误，并 SHALL 允许页面在不经过 Codex 的情况下查询、重试或恢复。

#### Scenario: 本地任务正在运行
- **WHEN** 本地任务尚未完成
- **THEN** 页面 SHALL 显示当前商品或文件夹、发现数量、准备数量、心跳和本地恢复操作

#### Scenario: 页面刷新
- **WHEN** 用户刷新存在运行中或已完成本地任务的页面
- **THEN** 页面 SHALL 通过当前任务身份恢复进度或图片选择状态，不得创建新 handoff

#### Scenario: 本地任务中断
- **WHEN** 进程退出、租约过期或任务未留下成功结果
- **THEN** 页面 SHALL 提供“重试加载图片”或等价的本地恢复操作，并 SHALL 从当前 session 的安全检查点恢复或重新执行

#### Scenario: 采用路径不可访问
- **WHEN** 本地任务无法读取一个采用文件夹
- **THEN** 系统 SHALL 保留用户文件夹决定，显示完整路径、稳定原因码和本地重试操作，不得发布空成功画廊或自动唤醒 Codex

### Requirement: 本地画廊任务保持确定性和源素材只读
系统 MUST 只读取当前采用文件夹，沿用每商品 100 张候选、覆盖优先抽样、30 张页面批次和任务内缩略图规则，并 MUST NOT 修改 NAS 原图。

#### Scenario: 画廊成功准备
- **WHEN** 所有采用路径可访问且准备完成
- **THEN** 系统 SHALL 写入与 session、stage、文件夹决定摘要和所选商品边界绑定的画廊及预览
- **THEN** 页面 SHALL 自动进入图片选择且所有图片初始为未采用

#### Scenario: 文件夹为空或图片重叠
- **WHEN** 采用文件夹没有受支持图片或其图片已被其他采用文件夹覆盖
- **THEN** 系统 SHALL 保留零分配审计并继续处理其他可用文件夹

#### Scenario: 旧任务结果迟到
- **WHEN** 本地任务结果的任务身份或文件夹决定摘要不再是当前权威身份
- **THEN** 系统 MUST 拒绝该结果成为当前画廊

### Requirement: 中间编辑不产生 Codex handoff
系统 SHALL 将文件夹决定、图片加载、图片采用草稿、授权同步和备注保存视为第三阶段本地状态，以上动作 MUST NOT 创建或更新 Codex handoff。

#### Scenario: 保存文件夹草稿
- **WHEN** 用户保存或自动保存文件夹决定但未最终确认图片
- **THEN** 系统 SHALL 只更新本地阶段草稿

#### Scenario: 保存图片选择草稿
- **WHEN** 用户选择图片、取消选择或自动保存选图草稿
- **THEN** 系统 SHALL 保留有效画廊并只更新本地阶段草稿

#### Scenario: 用户请求异常诊断
- **WHEN** 用户主动在当前 Codex 会话请求分析本地任务错误
- **THEN** Codex MAY 读取当前任务证据进行诊断，但该行为不得成为正常画廊准备的前置条件

### Requirement: 最终确认图片创建第三阶段唯一 handoff
系统 SHALL 只在用户点击“确认选图并提交给 Codex”且所有商品通过最终校验后创建第三阶段唯一 Codex handoff。

#### Scenario: 最终选图满足要求
- **WHEN** 每个进入第三阶段的商品至少具有 3 张合法、可读、已授权且源 SHA-256 唯一的采用图片
- **THEN** 系统 SHALL 创建一个绑定当前 session、stage、revision 和 input SHA-256 的 Codex handoff
- **THEN** handoff SHALL 包含完整商品边界、规范化文件夹决定摘要、最终图片决定、来源路径、文件夹身份、SHA-256 和合规结果

#### Scenario: 任一商品图片不足
- **WHEN** 任一商品少于 3 张有效唯一图片
- **THEN** 系统 SHALL 留在图片选择页面并显示逐商品差额，且 MUST NOT 创建 handoff

#### Scenario: 最终确认被重试
- **WHEN** 相同最终选择和相同请求身份被重复提交
- **THEN** 系统 SHALL 返回同一最终 handoff，不得生成重复 Codex 交接

#### Scenario: Codex 接收最终 handoff
- **WHEN** Codex 领取最终素材 handoff
- **THEN** Codex SHALL 从已经确认的素材包继续坑位编排，不得重新执行文件夹扫描或要求用户再次确认同一批图片

### Requirement: 历史中间 handoff 安全迁移
系统 SHALL 兼容在本变更前已经为文件夹确认创建的第三阶段 handoff，同时 SHALL 防止新页面继续生成该类中间 handoff。

#### Scenario: 历史中间 handoff 尚未领取
- **WHEN** 旧任务存在只包含文件夹决定且尚未领取的 handoff
- **THEN** 系统 SHALL 将其迁移或转换为本地画廊任务，并 SHALL 标记该 handoff 不再用于唤醒 Codex

#### Scenario: 历史中间 handoff 正在处理
- **WHEN** 旧任务的文件夹扫描已被 Agent 领取
- **THEN** 系统 SHALL 允许该次处理安全完成或恢复，但后续选图前不得再创建第二个中间 handoff

#### Scenario: 新任务执行第三阶段
- **WHEN** 任务由新版本页面开始文件夹确认
- **THEN** 系统 MUST NOT 写入历史中间 handoff 格式
