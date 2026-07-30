## Purpose

确保天猫搜推素材工作流在真实 Windows、CDP 页面、长时间分页和中断恢复条件下可诊断、可恢复且不重复执行，并在进入昂贵采集前暴露运行环境与输入数据问题。

## ADDED Requirements

### Requirement: Browser actions are bounded and outcome-verified
维护中的生产采集器 SHALL 对安全弹窗和关键只读点击使用有界状态机，并在每次关键动作后验证可观察的业务状态变化。

#### Scenario: Recognized onboarding dialogs are present
- **WHEN** 白名单内的新手引导或安全提示遮挡推广页、筛选器或翻页控件
- **THEN** 采集器 SHALL 在有限次数内关闭或推进弹窗，并在无状态变化时提前停止而不是重复等待到全局超时

#### Scenario: Ordinary and forced clicks report success without changing state
- **WHEN** 点击调用没有抛错但目标的选中状态、当前页或列表身份没有变化
- **THEN** 采集器 SHALL 把动作判为失败，并仅对声明为只读的白名单目标尝试一次受控 DOM 回退

#### Scenario: Unknown overlay blocks an action
- **WHEN** 遮挡元素不属于已识别安全弹窗
- **THEN** 采集器 MUST 停止并保存目标、重试、耗时、overlay 摘要和选择器身份，不得广泛 force click

### Requirement: Collection artifacts are isolated by attempt
每次采集 attempt MUST 拥有独立的 worker、日志、checkpoint、CSV 临时结果和终态证据；共享正式路径只 SHALL 发布当前成功 attempt 的完整结果。

#### Scenario: A new attempt follows a failed empty checkpoint
- **WHEN** 旧 attempt 留下零完成页或失败 checkpoint
- **THEN** 系统 SHALL 原样归档旧证据并为新 attempt 创建干净输出，不得因 `attempt_id` 冲突阻断恢复

#### Scenario: A resumable attempt restarts
- **WHEN** 同一 attempt 的 checkpoint 身份和输出哈希有效
- **THEN** 系统 SHALL 从最后完整页之后继续，并保持唯一商品 ID 和已完成页不重复

#### Scenario: A stale worker writes late
- **WHEN** 旧 attempt 或旧 claim 在新 attempt 启动后尝试写入进度、checkpoint 或结果
- **THEN** 写入 MUST 被拒绝且不得改变当前正式输出

### Requirement: One authoritative resolver governs recovery
系统 SHALL 使用单一优先级解析 completed result、活跃 Worker、processing lease、checkpoint、历史 attempt、blocked/needs-user-input 结果和草稿状态。

#### Scenario: Historical failure and current worker coexist
- **WHEN** 较早 attempt 有失败结果而当前 attempt 的归属可证 Worker 仍活跃
- **THEN** 页面 SHALL 把当前 Worker 显示为权威状态，并把旧错误标为 superseded 历史

#### Scenario: Blocked or needs-user-input condition was repaired
- **WHEN** 当前 handoff 身份未变且导致阻断的 collector/runtime 问题已经修复
- **THEN** 系统 SHALL 允许显式恢复同一 handoff 或创建隔离的新 attempt，而无需用户重新填写业务值

#### Scenario: Worker ownership is indeterminate
- **WHEN** 系统无法证明 Worker 所属进程身份或死亡状态
- **THEN** 系统 MUST 保护现有 lease，禁止结束、抢占或重用该 PID

### Requirement: Windows worker ownership survives launcher indirection
Windows 上的 Worker 归属 SHALL 同时绑定 session、attempt、私有 ownership token、启动器进程身份和实际工作进程身份，不得仅依赖 `Popen` 返回的 PID。

#### Scenario: Virtual-environment launcher spawns a different Python PID
- **WHEN** `.venv\Scripts\python.exe` 启动器 PID 与实际 Worker PID 不同
- **THEN** Worker SHALL 在验证启动器仍为预期进程后安全认领实际 PID，并保留启动器身份作为审计证据

#### Scenario: PID is reused
- **WHEN** manifest PID 存在但创建时间或进程身份与原 Worker 不匹配
- **THEN** 系统 MUST 把归属视为不确定，不得杀死或恢复该进程

### Requirement: Routine execution selects one prepared project environment
日常启动、恢复和测试 SHALL 通过唯一项目入口解析正确模块环境，并在执行前验证解释器、依赖、环境指纹和本机配置。

#### Scenario: Repository root and module both contain virtual environments
- **WHEN** 顶层轻量环境和子模块完整环境同时存在
- **THEN** 唯一入口 SHALL 选择声明的项目运行环境，并避免因工作目录不同误用缺少依赖的解释器

#### Scenario: Prepared environment is offline
- **WHEN** 本地环境和锁指纹有效但网络不可用
- **THEN** 日常运行 SHALL 使用既有环境，不得隐式下载或解析依赖

#### Scenario: Environment is incomplete
- **WHEN** 必需依赖、解释器版本或环境指纹不满足要求
- **THEN** 系统 SHALL 在认领业务 handoff 前返回原因码和单一准备动作

### Requirement: Durable files use one atomic persistence contract
JSON、CSV、manifest、checkpoint 和阶段结果 MUST 使用统一的原子写入行为，包括父目录创建、唯一临时文件、刷新、Windows 替换和失败清理。

#### Scenario: First historical result is archived
- **WHEN** 首次写入尚不存在的 `results` 或 attempt 目录
- **THEN** 写入器 SHALL 创建父目录并原子发布文件，不得因临时文件路径不存在失败

#### Scenario: Concurrent writers target one document
- **WHEN** 两个合法调用竞争同一输出
- **THEN** 每个调用 SHALL 使用唯一临时文件，最终文件 SHALL 是完整 JSON/CSV 而不是部分内容

#### Scenario: Input JSON contains a UTF-8 BOM
- **WHEN** Windows 工具写出 UTF-8-SIG JSON
- **THEN** 读取器 SHALL 在不改变哈希身份规则的前提下正确解析或返回明确编码错误

### Requirement: Progress and failure evidence are actionable
长时间动作 SHALL 报告细粒度 phase、具体动作、目标、重试次数、已用时间、心跳、页码、行数、checkpoint 时间和精确恢复建议。

#### Scenario: Popup cleanup takes longer than expected
- **WHEN** 采集器在 `opening_promotion` 或翻页前处理多个弹窗
- **THEN** 状态 SHALL 指明当前弹窗动作和重试，而不是长时间仅显示模糊 phase

#### Scenario: First checkpoint has not been written
- **WHEN** Worker 存活但尚未完成第 1 页
- **THEN** 页面 SHALL 显示心跳和当前动作，不得把状态描述为已采集 0 行

#### Scenario: A selector fails reproducibly
- **WHEN** 选择器、点击结果或分页状态验证失败
- **THEN** 证据 SHALL 足以在同一维护链路中复现和添加回归测试，同时排除凭据和敏感页面内容

### Requirement: Input data quality is visible before collection
阶段一 SHALL 在正式提交前展示商品表总行数、有效行数、重复商品 ID、缺失或非法 ID、被排除行和可继续处理数量。

#### Scenario: Some rows are invalid
- **WHEN** 商品表同时包含有效行和重复、缺失或非法 ID 行
- **THEN** 页面 SHALL 展示原因码和源行摘要，排除异常行并允许其余有效行继续

#### Scenario: Every row is invalid
- **WHEN** 非空商品表的全部行都是行级异常
- **THEN** 元数据索引 SHALL 完成并生成可审计摘要，但依赖有效商品的后续阶段 SHALL 停止并给出恢复动作

#### Scenario: Required table schema is invalid
- **WHEN** 商品表为空、不可读或缺少必需表头
- **THEN** 系统 SHALL 在启动昂贵的浏览器采集前阻断并显示批次级错误

### Requirement: Windows and live acceptance close the reliability change
该能力 MUST 通过隔离的 Windows 回归测试和不包含上传/发布动作的真实会话验收后才可视为完成。

#### Scenario: Automated regression runs on Windows paths
- **WHEN** 测试覆盖长路径、短临时目录、UTF-8-SIG、launcher PID、父目录首次创建和重复恢复
- **THEN** 所有测试 SHALL 通过且 pytest 清理权限警告不得掩盖测试结果或污染项目状态

#### Scenario: Fresh live session completes high-value collection
- **WHEN** 用户通过当前 DOM、真实安全弹窗和全部可见分页完成一次新会话
- **THEN** 维护中的唯一生产链路 SHALL 生成 1 份成功 attempt、完整 checkpoint、唯一商品 CSV、完整性矩阵和可审计进度

#### Scenario: No production authority is exercised
- **WHEN** 执行本变更的真实验收
- **THEN** 流程 MUST 在 dry-run/完整性审查边界前停止，不得批准、上传、发布或保存登录凭据
