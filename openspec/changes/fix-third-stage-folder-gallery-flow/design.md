## Context

> **Superseded orchestration boundary:** `defer-asset-handoff-until-final-selection`
> replaces this change's first-round Agent handoff. Folder confirmation now starts
> a local gallery job; the only Codex handoff is created after final image
> selection. The normalization, hydration, gallery-identity, preview, and
> complete-product validation decisions below remain applicable.

参见 [proposal.md](proposal.md) 的问题背景与
[two-step-asset-matching spec](specs/two-step-asset-matching/spec.md) 的行为契约。

当前第三阶段使用一个 `asset_matching` stage 同时承载文件夹决定和图片决定。后端已能：

- 将候选文件夹规范化为 `folder_decisions`；
- 从已提交文件夹生成 `confirmed-gallery.json`；
- 渲染图片候选并同步文件夹排除；
- 在最终选图提交时同步预检并生成确定性坑位草稿。

缺口主要位于阶段编排和状态表达。现有结果只有“包含 `folder_candidates`”或“包含
`asset_candidates`”的隐式区别，前端仍展示同一套字段和通用提交按钮。默认文件夹
决定通过控件初始化写入隐藏字段时可能派发 `input`，从而触发 autosave。图片来源类型
仍作为必填用户字段，但日常任务实际上只有 `image`。此外，画廊必须与文件夹决定绑定，
又不能因为用户只保存图片选择草稿而被无意义地判为过期。

## Goals / Non-Goals

**Goals:**

- 在不拆分现有 stage ID 的前提下建立可恢复的两个子步骤。
- 让第一轮提交成为唯一的图片枚举授权边界，并让第二轮提交成为唯一的图片采用完成边界。
- 将“页面 revision”和“画廊所依据的文件夹决定身份”分开，既阻止旧结果覆盖，又允许选图草稿保存。
- 确保页面水合完全只读，真实用户编辑才进入 autosave。
- 保留历史任务和既有 `asset_matching` 输入/结果的可读兼容。

**Non-Goals:**

- 不改变共享文件夹索引、50% 连续名称匹配、每商品 100 张候选或 30 张分页策略。
- 不改变图片合规阈值、确定性坑位算法或第四阶段行为。
- 不引入 AI 选图、自动图片采用或新的外部依赖。
- 不重写历史 revision，不修改 NAS 原图，也不触及批准或上传。

## Decisions

### 1. 保留一个 stage，增加显式 `workflow_step`

第三阶段继续使用 `asset_matching`，当前结果的 `data.workflow_step` 取值：

- `folder_review`
- `gallery_preparing`
- `image_selection`

`folder_review` 结果只包含文件夹审查数据；第一轮 handoff 被认领后，处理状态由现有
processing claim/worker 进度表达为 `gallery_preparing`；成功结果写为
`image_selection`。

历史结果没有该字段时采用只读推导：

- 有 `asset_candidates` 或有效 `confirmed-gallery.json` → `image_selection`
- 否则有 `folder_candidates` → `folder_review`

不采用新增 stage ID，因为这会改变导航、阶段顺序、历史会话迁移和大量现有 API。
不继续只通过数组是否为空推断，因为合法的零候选失败/空文件夹状态无法与尚未准备区分。

### 2. 用文件夹决定身份绑定画廊，而不是绑定每次选图草稿 revision

第一轮提交生成：

- `prepared_from_revision`
- `prepared_from_input_sha256`
- `folder_decisions_sha256`
- session、stage 和所选商品边界

画廊结果保存这些字段。页面只在当前规范化文件夹决定 SHA-256 与画廊一致时允许选图。
图片选择、授权和备注的草稿 revision 可以增加，但只要文件夹决定摘要不变，画廊仍有效。

如果排除已准备文件夹，前端和后端都可对当前画廊做安全子集过滤；如果重新采用一个当前
画廊未准备的文件夹，则标记 `gallery_stale` 并返回第一轮重新准备。

不把画廊只绑定最新 stage revision，因为选图 autosave 会使画廊立即过期。不只绑定
文件路径列表，因为还需要防止跨 session、跨商品或跨输入重用。

### 3. 服务器规范化完整默认决定和图片来源类型

前端渲染默认采用/排除只用于展示。保存或提交时，服务端以当前权威
`folder_candidates` 为全集，把缺失决定补成：

- 精确 ID、SKU、完整名称候选 → `confirmed`
- 50% 连续名称候选或已有明确排除 → `rejected`

新任务在 `source_types` 缺失或为空时规范化为 `["image"]`；历史非空列表原样保留。
前端隐藏该内部字段或只读显示“图片”，不再让用户填写。

服务端规范化是最终事实来源，可覆盖旧前端、重试和手工 API。只在前端预填无法保证
提交完整性，也无法修复空来源类型。

### 4. 水合使用显式静默事务

前端增加水合边界：

1. 开始加载 stage 时进入 `isHydrating=true`。
2. 写入服务端值、规范化默认控件和渲染组件时禁止 dirty、autosave 和 request ID 创建。
3. 完成全部组件渲染并保存规范化快照后退出水合。
4. 用户事件只有在退出水合且新旧规范化值不同的情况下才能标记 dirty。

`writeJsonListControl` 在初始化路径必须使用静默写入，不派发 `input`。文件夹卡片初始
渲染不得调用等价于用户 change 的同步函数。提交时可直接从可见卡片和权威候选构建
完整决定，不依赖初始化期间把默认值写入隐藏控件。

不采用延长 autosave 防抖，因为初始化事件最终仍会写入；也不完全关闭第三阶段
autosave，因为真实的长时间文件夹和选图操作仍需防止丢失。

### 5. 两个提交动作使用同一路由但不同服务端门禁

前端根据 `workflow_step` 设置按钮：

- `folder_review` → “确认文件夹并加载图片”
- `image_selection` → “确认选图并进入坑位编排”

服务端不信任按钮文案，而根据当前权威结果和提交内容判断：

- `folder_review`：规范化文件夹决定，验证每个商品至少一个采用文件夹，保存 input 并创建 Agent handoff。
- `image_selection`：要求有效且未过期的画廊，重新过滤决定，执行选图预检，完成 stage 并生成坑位草稿。

在 `gallery_preparing` 时重复提交相同请求保持幂等，不创建第二个 handoff。已过期或内容
不同的请求返回现有稳定 revision/request 冲突，而不是覆盖当前处理。

不增加两个公开 API 路由，避免破坏现有前端和恢复指令；明确的 `workflow_step` 仍能使
同一路由保持可测试。

### 6. 为图片准备提供维护入口和可观察进度

实现或收敛一个维护的第三阶段处理入口，职责为：

1. 精确领取 asset-matching handoff。
2. 校验 session、revision、input SHA-256、所选商品和文件夹决定摘要。
3. 在实际读取前校验本机资源身份和采用路径。
4. 调用现有 confirmed-gallery 准备逻辑。
5. 原子写入候选、预览审计和 `image_selection` review context。

进度至少包含当前子步骤、商品、文件夹、已发现/已准备数量、心跳和稳定原因码。源路径
错误保持 `needs_user_input`，保留 input 和已有审计，不发布空成功画廊。

复用现有候选生成逻辑而不是从 Web 请求内同步遍历 NAS，避免长请求、身份漂移和页面
超时。

### 7. 最终提交按完整商品边界校验

最低三张校验使用第二阶段进入第三阶段的完整商品集合，而不是仅使用
`asset_decisions` 中已经出现的商品集合。每个商品分别计算合法、非重复、仍属于采用
文件夹的选择数并给出差额。

预检成功后沿用现有同步完成路径：写入 `selected-asset-preflight.json`、完成
`asset_matching`、创建确定性坑位草稿并进入 `slots_copy`。

这会关闭“只为部分商品选图即可完成阶段”的隐性缺口，同时不改变至少三张和坑位公式。

### 8. 文档以两轮流程为唯一正常路径

canonical Skill、发现入口、数据结构、操作指南和错误处理统一使用两个子步骤和两个按钮。
文档明确：

- 采用文件夹不等于采用图片；
- 第一轮提交后才读取图片；
- 页面显示 Agent 离线时，在绑定会话中用“已提交”恢复；
- 第二轮提交通过后才进入坑位编排。

生成 Skill 发现入口并校验 canonical SHA-256，避免仓库入口继续描述旧行为。

## Risks / Trade-offs

- [一个 stage 内有两个提交语义，状态判断错误可能走错路径] → 使用显式
  `workflow_step`、服务端权威门禁和覆盖全部状态转移的 API 测试。
- [选图 autosave 增加 revision，但画廊仍来自较早 revision] → 保存独立
  `folder_decisions_sha256` 和 preparation identity；最终提交同时校验 session、商品边界
  和文件夹摘要。
- [排除文件夹可以安全做子集过滤，重新采用却需要重新扫描] → 页面明确区分并在新增采用
  时使画廊失效，不尝试从未读取目录伪造候选。
- [NAS 检查和预览生成耗时] → 保持 Agent/Worker 后台处理，提供进度和同 session 恢复，
  不在 Web 请求中阻塞。
- [历史结果缺少 workflow 和绑定字段] → 只读推断旧子步骤；已有历史画廊继续可审查，
  但修改文件夹后要求使用新流程重新准备。
- [严格要求全部商品至少三张可能暴露既有任务数据不足] → 按商品显示精确差额并留在图片
  选择，不静默跳过商品。

## Migration Plan

1. 先加入后端兼容读取和 `workflow_step` 推导，不改变现有页面。
2. 加入服务端默认决定、`source_types` 规范化和画廊绑定字段。
3. 更新前端水合事务、子步骤渲染和双按钮文案。
4. 加入维护的图片准备处理入口、进度与恢复。
5. 更新最终提交的完整商品边界校验。
6. 更新 Skill 与参考文档，重新生成发现入口。
7. 使用合成测试和真实会话 `20260730_055346` 的脱敏夹具验证：只打开不写 revision、
   第一轮生成画廊、第二轮完成阶段。

回滚时保留新增字段，旧代码会忽略它们；恢复旧前端和路由判定即可。不得删除已生成
revision、候选、预览或审计证据。
