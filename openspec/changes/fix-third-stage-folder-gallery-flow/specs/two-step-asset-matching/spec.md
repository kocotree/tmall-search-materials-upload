## Purpose

> The handoff timing in this delta is superseded by
> `defer-asset-handoff-until-final-selection`: gallery preparation is local and
> only final image selection creates a Codex handoff.

定义第三阶段从候选文件夹归属审核、受控图片准备到人工选图完成的可观察流程，使用户始终知道当前子步骤，并确保只读取已明确采用的文件夹且只在用户真实编辑或提交时持久化状态。

## ADDED Requirements

### Requirement: 第三阶段具有明确且持久化的两个子步骤
系统 SHALL 将素材匹配阶段区分为 `folder_review` 和 `image_selection` 两个可观察子步骤，并 SHALL 根据当前权威结果而不是页面临时状态确定所显示的子步骤。

#### Scenario: 首次进入文件夹审核
- **WHEN** 第二阶段完成并为所选商品生成候选文件夹
- **THEN** 第三阶段 SHALL 显示 `folder_review`，展示文件夹候选且不展示空图片画廊

#### Scenario: 图片候选准备完成
- **WHEN** 当前文件夹决定 revision 的候选图片、预览和审计结果已经成功生成
- **THEN** 第三阶段 SHALL 显示 `image_selection` 并展示与该 revision 绑定的图片候选

#### Scenario: 刷新已进入图片选择的页面
- **WHEN** 用户刷新或重新打开一个已具有有效图片准备结果的第三阶段
- **THEN** 页面 SHALL 恢复 `image_selection`，不得退回未扫描状态或要求重复提交相同文件夹决定

### Requirement: 文件夹采用不等于图片采用
系统 MUST 将文件夹归属决定与逐图片采用决定分开；采用文件夹只授权系统在图片准备动作中读取该目录，不得自动采用其中任何图片。

#### Scenario: 用户把文件夹设为采用
- **WHEN** 用户在文件夹审核中将一个候选文件夹选择为采用
- **THEN** 页面 SHALL 只更新该文件夹决定，并 SHALL 明确提示图片将在确认文件夹后加载

#### Scenario: 图片候选首次展示
- **WHEN** 已采用文件夹的候选图片首次显示
- **THEN** 所有图片 SHALL 保持未采用，直到用户逐图片作出采用决定

### Requirement: 第一轮提交完整保存文件夹决定
系统 SHALL 为全部可见候选生成规范化的 `confirmed` 或 `rejected` 文件夹决定，并 SHALL 使用“确认文件夹并加载图片”作为第一轮提交动作。

#### Scenario: 用户接受默认文件夹决定
- **WHEN** 用户未逐项修改默认值并点击“确认文件夹并加载图片”
- **THEN** 提交 SHALL 包含每个候选文件夹的显式规范化决定，不得因隐藏控件为空而遗漏默认决定

#### Scenario: 用户排除粗略或错误候选
- **WHEN** 用户将一个候选文件夹改为排除后提交
- **THEN** 当前 revision SHALL 保存该排除决定，且图片准备 SHALL 不读取该文件夹

#### Scenario: 没有采用文件夹
- **WHEN** 某个本轮商品的全部候选文件夹均被排除
- **THEN** 系统 SHALL 阻止图片准备并明确指出该商品没有采用文件夹

### Requirement: 日常图片来源类型不要求用户配置
系统 SHALL 将第三阶段日常图片来源确定性规范化为 `image`，并 SHALL 隐藏或只读展示内部来源类型字段；历史非空来源类型输入 MUST 保持可读取。

#### Scenario: 新任务提交文件夹决定
- **WHEN** 新任务的页面没有显式 `source_types` 用户输入
- **THEN** 后端 SHALL 使用 `["image"]` 进行规范化且不得以来源类型为空阻断提交

#### Scenario: 恢复历史任务
- **WHEN** 历史任务具有已保存的非空来源类型
- **THEN** 系统 SHALL 保留其兼容语义且不得因新默认值改写历史 revision

### Requirement: 页面水合和默认值计算不产生持久化副作用
加载服务端输入、渲染文件夹默认决定、恢复图片候选和计算规范化默认值 MUST NOT 标记页面为用户编辑、触发自动保存、增加 revision 或创建 handoff。

#### Scenario: 只打开文件夹审核页面
- **WHEN** 用户打开第三阶段但未执行任何编辑
- **THEN** session revision、input SHA-256、handoff、提交时间和审计事件 SHALL 保持不变

#### Scenario: 默认决定被渲染
- **WHEN** 页面根据匹配类型把精确候选显示为采用并把粗略候选显示为排除
- **THEN** 默认值 SHALL 只存在于水合状态，直到用户保存或提交，不得派发用户输入事件

#### Scenario: 用户真实修改决定
- **WHEN** 用户改变文件夹采用状态或备注且规范化后的值与原值不同
- **THEN** 页面 SHALL 标记为已编辑并允许按既有草稿防抖规则保存

### Requirement: 图片准备由当前提交身份严格绑定
第一轮提交 SHALL 创建绑定 session、stage、revision、input SHA-256 和文件夹决定摘要的 Agent handoff；图片准备结果 MUST 与同一身份匹配后才能成为当前画廊。

#### Scenario: Agent 成功准备候选
- **WHEN** Agent 对当前提交枚举已采用文件夹并成功生成候选、预览和审计结果
- **THEN** 系统 SHALL 写入与当前提交身份绑定的 `confirmed-gallery.json` 并将阶段返回 `needs_user_input` 的图片选择子步骤

#### Scenario: 旧 Agent 迟到写入
- **WHEN** Agent 结果的 revision 或 input SHA-256 不再等于当前提交
- **THEN** 系统 MUST 拒绝其成为当前画廊并保留为不可授权当前选图的历史证据

#### Scenario: 页面显示准备进度
- **WHEN** 第一轮 handoff 已被认领但图片准备尚未完成
- **THEN** 页面 SHALL 显示正在检查采用文件夹、当前商品或文件夹进度、心跳和恢复动作，不得显示空画廊为完成状态

### Requirement: 图片准备只读取采用文件夹并保持源文件只读
系统 MUST 只枚举当前商品已采用且当前可访问的文件夹， SHALL 为每商品准备不超过 100 张候选并生成任务内预览，且 MUST NOT 修改、移动、删除或复制 NAS 原图到任务目录。

#### Scenario: 采用文件夹可访问
- **WHEN** 当前 revision 的采用文件夹均可读取
- **THEN** 系统 SHALL 按既有覆盖优先和确定性抽样规则生成候选及最长边不超过 640 像素的预览

#### Scenario: 采用文件夹不可访问
- **WHEN** 一个采用文件夹在 Agent 实际枚举时不可访问
- **THEN** 系统 SHALL 保留文件夹决定并返回稳定原因码、完整路径和恢复动作，不得用零图片假装成功

#### Scenario: 文件夹为空
- **WHEN** 一个可访问的采用文件夹不包含受支持的唯一图片
- **THEN** 系统 SHALL 保留零分配审计记录并在画廊摘要中说明该文件夹没有贡献候选

### Requirement: 图片选择页面提供可解释的候选状态
图片选择子步骤 SHALL 按商品展示候选图片、来源文件夹、宽高、格式、大小、合规状态和不可用原因，并 SHALL 区分发现数、已准备数、有效数、100 张上限和 30 张页面批次。

#### Scenario: 候选超过单批容量
- **WHEN** 一个商品准备了超过 30 张图片
- **THEN** 页面 SHALL 每批最多显示 30 张并提供确定性的上一批或下一批导航

#### Scenario: 候选被合规检查阻断
- **WHEN** 图片格式、大小、尺寸、重复状态或来源归属不满足采用条件
- **THEN** 页面 SHALL 显示具体原因并禁用该图片的采用动作

#### Scenario: 远端指纹不可用
- **WHEN** 后台仅有远端素材 ID 而没有图片指纹
- **THEN** 页面 SHALL 显示远端去重未完成，不得声称候选已完成远端去重

### Requirement: 修改文件夹决定会使相关画廊状态失效
在图片选择子步骤修改文件夹归属时，系统 MUST 清除被排除文件夹绑定的候选、图片决定和授权决定；新增采用但尚未准备的文件夹 MUST 重新经过第一轮提交和图片准备。

#### Scenario: 排除已经准备的文件夹
- **WHEN** 用户在图片选择期间排除一个已准备文件夹
- **THEN** 页面 SHALL 立即移除其候选并清除相关采用和授权决定

#### Scenario: 重新采用未准备的文件夹
- **WHEN** 用户重新采用一个当前画廊未枚举的文件夹
- **THEN** 页面 SHALL 返回待确认文件夹状态并要求重新执行“确认文件夹并加载图片”，不得伪造或恢复不存在的候选

### Requirement: 最终选图提交同步完成预检和阶段推进
第二轮提交 SHALL 使用“确认选图并进入坑位编排”，并 MUST 在完成前重新校验文件归属、授权、可读性、合规性和源 SHA-256 唯一性。

#### Scenario: 每个本轮商品均满足最低图片数
- **WHEN** 每个进入第三阶段的商品至少采用 3 张合法且源 SHA-256 唯一的图片
- **THEN** 系统 SHALL 保存 `selected-asset-preflight.json`、完成第三阶段并生成确定性坑位草稿

#### Scenario: 任一商品图片不足
- **WHEN** 任一进入第三阶段的商品少于 3 张合法且唯一的采用图片
- **THEN** 系统 SHALL 保持在图片选择子步骤，并按商品显示还差多少张

#### Scenario: 最终提交包含已排除文件夹图片
- **WHEN** 提交的图片决定引用当前已排除文件夹中的候选
- **THEN** 后端 MUST 删除或拒绝该决定，且不得把它写入授权或坑位草稿

### Requirement: 第三阶段保持安全边界
第三阶段 MUST 保持源素材只读，并 SHALL NOT 创建 dry-run、批准、生产确认、上传或发布授权。

#### Scenario: 完成图片选择
- **WHEN** 第三阶段成功完成并进入坑位编排
- **THEN** 系统 SHALL 只产生任务内候选、预览、预检和坑位草稿，不得访问发布动作或产生批准清单
