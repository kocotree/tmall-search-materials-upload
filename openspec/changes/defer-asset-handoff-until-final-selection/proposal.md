## Why

第三阶段的文件夹扫描、确定性抽样、图片检查和缩略图生成都是本地固定程序可以完成的动作，却被设计成先创建 Codex handoff、等待 Codex 领取后才执行。这增加了会话唤醒、租约、恢复和“已提交”沟通成本，也让用户点击“确认文件夹并加载图片”后无法直接得到页面内反馈。

## What Changes

- 将“确认文件夹并加载图片”改为页面直接启动本地画廊准备任务，不创建 Codex/Agent handoff。
- 本地任务保存文件夹决定，检查采用路径，按现有规则抽样、校验并生成任务内预览；页面轮询或订阅进度并自动进入选图状态。
- 文件夹决定和图片选择草稿只在本地阶段状态中保存，不唤醒 Codex。
- 将第三阶段唯一的 Codex handoff 延后到用户点击“确认选图并提交给 Codex”之后。
- 最终 handoff 携带完整商品边界、文件夹决定摘要、最终图片决定、图片来源/SHA-256、合规结果和当前 session/revision 身份。
- 本地任务失败时由页面直接显示稳定原因、重试和恢复操作；Codex 仅作为用户主动请求的异常诊断或恢复兜底。
- 纠正 `fix-third-stage-folder-gallery-flow` 中“第一轮提交创建 Agent handoff”的职责划分；不改变文件夹匹配、每商品 100 张候选、30 张分页、图片合规或坑位编排规则。

## Capabilities

### New Capabilities

- `local-gallery-preparation`: 定义页面直接启动、观察、重试和恢复本地画廊准备任务，以及最终选图后才生成唯一 Codex handoff 的行为边界。

### Modified Capabilities

无。当前仓库主规格尚未包含未归档变更中的 `two-step-asset-matching` 能力，本变更以独立增量规格明确纠偏后的最终契约。

## Impact

- 受影响代码：第三阶段提交路由、SessionStore 状态、画廊准备 Worker、前端按钮和进度轮询、最终选图 handoff、恢复逻辑。
- API 将增加或明确一个本地画廊准备入口及状态查询；第一轮操作不再复用 Agent handoff 接口。
- canonical Skill、数据结构、操作指南和错误说明需要同步更新。
- 不增加外部依赖，不修改 NAS 原图，不触发 dry-run、批准、上传或发布。
