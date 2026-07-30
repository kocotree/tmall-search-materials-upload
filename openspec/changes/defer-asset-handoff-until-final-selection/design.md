## Context

参见 [proposal.md](proposal.md) 的动机和
[local-gallery-preparation spec](specs/local-gallery-preparation/spec.md) 的行为契约。

当前实现复用了 `asset_matching` 的 `save_input → handoff.json → processing_claim`
链路来启动已确认文件夹的图片枚举。该链路适合需要 Codex 继续推理的阶段，却不适合固定的
本地扫描：页面必须等待 Codex 领取，扫描失败也被包装成 Agent 恢复问题。与此同时，当前
最终选图提交会在 Web 请求内同步预检并直接生成确定性坑位草稿，没有形成用户要求的唯一
最终素材 handoff。

本地图片读取必须继续运行在拥有 NAS 映射或 UNC 访问权限的 Windows 用户身份下。扫描和
预览可能耗时，不能阻塞单个 HTTP 请求，也不能依赖浏览器页面保持打开。

## Goals / Non-Goals

**Goals:**

- 为第三阶段增加独立于 Codex 的持久化本地任务生命周期。
- 让相同文件夹决定的重复点击、刷新和进程恢复保持幂等。
- 保持本地 Worker 与页面服务的 Windows 资源身份一致。
- 只在最终图片集合通过校验后生成一次 Codex handoff。
- 清晰迁移当前已经生成的中间 handoff，不破坏历史证据。

**Non-Goals:**

- 不把 NAS 扫描放进浏览器进程或同步 HTTP 请求。
- 不引入消息队列、远程服务或新的 AI 调用。
- 不改变文件夹匹配、抽样、缩略图、合规和图片数量规则。
- 不让 Codex 自动选择图片，也不在本变更中执行发布。

## Decisions

### 1. 第一轮使用专用本地动作，不再复用 stage submit

新增语义明确的本地入口，例如：

- `POST /api/sessions/<session>/stages/asset_matching/prepare-gallery`
- `GET /api/sessions/<session>/stages/asset_matching/gallery-job`
- `POST /api/sessions/<session>/stages/asset_matching/gallery-job/retry`

第一次请求完成三件事：服务端规范化并持久化文件夹决定、计算任务身份、启动或复用本地
Worker。响应使用 `202 Accepted` 返回任务状态。它不得调用 `save_input` 的 handoff 分支，
不得写 `handoff.json`，也不得改变 Codex agent-wait/processing-claim。

不继续复用通用 `/submit`，因为相同路由同时表达“启动本地任务”和“交接 Codex”容易再次
造成职责混淆。保留“保存草稿”用于普通本地编辑。

### 2. 为不产生 handoff 的权威本地 revision 增加事务

SessionStore 增加本地动作提交能力，原子完成：

1. 校验当前 revision、状态和 request ID；
2. 写入新的 `input.json` revision 和 revision snapshot；
3. 不生成 `handoff.json`；
4. 把 stage 状态设置为 `local_processing` 或继续使用可明确投影的本地子状态；
5. 写入本地动作审计事件。

本地任务身份包含：

- session、stage；
- 提交后的 input revision 和 SHA-256；
- 规范化 `folder_decisions_sha256`；
- 完整商品边界；
- 候选策略与图片策略版本；
- 本地资源身份摘要。

不伪造 ready-for-agent 状态，也不把本地任务塞进现有 `processing_claim`，否则 Codex 恢复
逻辑仍会错误认为自己拥有该任务。

### 3. 本地 Worker 使用独立租约和尝试记录

页面服务以同一项目环境启动受控子进程。Worker 只接收 runs root、session 和 gallery job
ID，从任务文件读取其余输入，避免命令行携带路径和大型决定。启动前记录页面服务的
SID/login session；子进程启动后验证身份一致，再读取采用文件夹。

任务文件建议为：

```text
03-asset-matching/
  gallery-job.json
  gallery-attempts/<attempt-id>/
    progress.json
    result.json
    error.json
  confirmed-gallery.json
  preview-cache/
```

`gallery-job.json` 保存当前 job ID、身份摘要、attempt ID、状态、PID、租约、心跳、进度、
恢复动作和最终结果摘要。attempt 目录保留迟到或失败证据。发布当前画廊前，在 SessionStore
锁内重新验证 job ID、revision、input SHA 和文件夹摘要。

不使用内存线程作为唯一状态，因为页面服务重启后无法判断任务是否仍在运行，也无法安全
区分迟到结果。

### 4. 重试由页面直接驱动，并保持相同身份幂等

相同任务身份：

- 运行中：返回现有 job；
- 已成功：返回现有画廊；
- 已失败或租约过期：创建新 attempt，但保持同一 job 身份；
- 用户修改文件夹决定：创建新 job，旧 attempt 只能进入历史证据。

页面轮询 stage 状态或 gallery-job 状态；运行完成后自动重新加载 stage 并进入
`image_selection`。失败时显示“重试加载图片”。用户可以主动把错误交给 Codex 分析，但
正常恢复不依赖聊天回复“已提交”。

### 5. 最终选图提交才使用现有 handoff 协议

第二轮按钮改为“确认选图并提交给 Codex”。服务端先在不创建 handoff 的情况下完成：

- 画廊身份和文件夹范围校验；
- 完整商品边界的至少 3 张校验；
- 路径、授权、合规和 SHA-256 唯一性预检；
- 写入 `selected-asset-preflight.json`。

全部通过后，调用标准 handoff 事务写入最终 `input.json` 和 `handoff.json`，stage 进入
`ready_for_agent`。handoff 的 `values` 或绑定结果包含最终素材包身份，Codex 领取后继续
坑位编排。

不再由该 HTTP 请求直接完成 `asset_matching` 或生成最终坑位草稿；否则 Codex 收不到用户
要求的最终素材交接。确定性坑位算法仍可由 Codex 领取后调用现有维护入口执行。

### 6. 历史中间 handoff 使用显式迁移分类

启动或读取第三阶段时检查现有 handoff：

- 若输入只有文件夹决定、没有最终有效图片集合，分类为 `legacy_gallery_handoff`；
- 尚未领取：原子撤销其 Codex 可领取性，并据同一输入创建本地 job；
- 已领取且存活：允许旧处理完成，结果按新 gallery identity 发布；
- 已过期：释放旧 claim，迁移为本地 job；
- 包含最终有效图片集合：保留为真正的最终 handoff。

迁移必须写审计事件，不删除历史 revision/handoff/claim 文件。新版本禁止生成新的
`legacy_gallery_handoff`。

### 7. 原变更与本变更的实施顺序

本变更是对 `fix-third-stage-folder-gallery-flow` 的纠偏层。应用时优先：

1. 保留其中已经完成的水合幂等、文件夹默认值、画廊身份和完整商品校验；
2. 替换其中第一轮 `save_input + process-confirmed-gallery claim`；
3. 把维护命令改为只领取 gallery job，而不是领取 handoff；
4. 把最终同步完成改为最终 handoff；
5. 更新原变更剩余任务，避免两个变更对同一路由提出相反要求。

不要求先归档原变更；但两个变更全部完成前，不应把旧的“第一轮 Agent handoff”文档同步
到主规格。

## Risks / Trade-offs

- [新增本地任务状态与 stage 状态并存，可能出现双重事实来源] → gallery job 只负责候选准备，SessionStore revision 和最终 handoff 仍是业务事实；所有发布在同一锁内校验身份。
- [页面服务和 Worker 的 Windows 身份不同导致 NAS 再次不可访问] → 启动时记录并比较 SID/login session，身份不一致直接返回稳定错误，不尝试猜测路径。
- [用户快速修改文件夹时旧 Worker 仍在扫描] → 新 job 使旧 attempt 失去发布资格；可请求旧 Worker 优雅退出，但不依赖成功终止保证正确性。
- [最终提交不再立即进入坑位页面，用户会看到等待 Codex] → 页面明确显示“最终素材已交接 Codex”，并复用现有当前会话自动唤醒与“已提交”兜底。
- [两个未归档 OpenSpec 变更存在相反描述] → 在任务中显式更新前一变更的设计、规格和未完成任务，严格校验时同时检查两者。

## Migration Plan

1. 增加 gallery job 数据结构、只读状态查询和不产生 handoff 的本地 revision 事务。
2. 将现有 confirmed-gallery 处理器改为领取 gallery job，并加入身份、租约和 attempt 发布门禁。
3. 接入页面的 prepare/retry/poll 流程，移除第一轮 Codex 等待文案。
4. 将最终选图路由改为预检后创建唯一 handoff，Codex 领取后沿用确定性坑位逻辑。
5. 增加历史中间 handoff 分类和迁移。
6. 更新两个相关 OpenSpec 变更、Skill 和操作文档。
7. 用新会话及历史中间 handoff 脱敏夹具验证零中间唤醒和单一最终交接。

回滚时可以恢复第一轮 Agent 处理入口，但必须保留 gallery job、attempt 和最终素材证据；
不得把已经分类为最终 handoff 的输入重新解释为文件夹扫描授权。
