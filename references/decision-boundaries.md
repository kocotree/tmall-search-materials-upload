# 人工、规则与 Codex Agent 决策边界

## 执行总则

所有结构化配置和人工决定先进入对应前端页面。Agent 的第一动作仅是启动或恢复受管 UI 并提供精确链接；页面健康时由工作台后台等待和处理 handoff，禁止在聊天中询问店铺、NAS 根目录或阶段决定。只有真实 UI 失败并取得允许的稳定原因码后，才可把 fallback-eligible 字段写回同一阶段 draft。完整路由见 [frontend-interaction-contract.md](frontend-interaction-contract.md)。

每次处理阶段 handoff 前，工作台正式处理器必须先读取当前 revision 绑定的
`<stage>/decision-modes/<decision_id>.json`。文件不存在时，只能使用下表的
Skill 默认模式；历史任务不得推断成 AI 模式。

- `manual`：等待用户在页面作出决定；工作台后台只校验并执行已提交结果。
- `rules`：执行确定性规则并展示可解释草案；用户仍可修改或确认。
- `deterministic`：新任务第五阶段默认模式；相同输入和策略版本必须产生相同坑位草稿。
- `agent_assisted`：仅用于 AI 文案和读取历史坑位 AI 草稿；新任务不得创建坑位 AI 请求。
- `manual_only`：不可委托。工作台后台必须等待用户的页面确认；聊天不能代替页面授权。

模式选择、人工覆盖、AI 草稿载入、取消和规则回退都写入审计事件。模式或上游
revision 改变时，旧 AI 请求必须取消或标记 `superseded`；合法人工草稿和图片
选择必须保留。合法 AI 响应可写入唯一未确认草稿，但不能自动确认、裁剪、压缩、
dry-run、批准或上传。

## 九阶段边界表

| 阶段 / decision ID | 允许模式（默认） | Agent 可执行 | 必须等待用户 | 禁止动作 | 失败回退与继续条件 |
| --- | --- | --- | --- | --- | --- |
| `setup/task_configuration` | `manual_only`（默认） | 启动页面、自动探测只读路径、校验提交 | 店铺、图片源及店铺一致性确认 | 猜测配置、提前采集或上传 | 保留页面并给恢复指令；setup handoff 校验通过后继续 |
| `completeness/product_selection` | `rules`（默认）、`manual` | 全量采集搜推高价值、规则排除、生成矩阵 | 选择进入后续阶段的商品 | 扩大实时采集商品范围、替用户选商品 | 采集异常保持人工审查；用户提交非空合法商品集合后继续 |
| `asset_matching/asset_selection` | `manual`（默认）、`rules` | 规则匹配文件夹、生成预览；提交采用图片时增量预检并同步生成确定性坑位草稿 | 排除错误文件夹、选择及授权图片，或显式去掉当前商品 | 用别名猜测归属、检查未采用共享盘图片、生成派生图片、自动去掉商品 | 1–2 张可保存草稿但不得提交；已去掉商品同步清理决定且任务至少保留一个商品；其余每个商品至少 3 张可用唯一图后继续 |
| `image_review/suitability_review` | 历史兼容 | 只读恢复旧适用性快照 | 仅旧任务保留原决定 | 新任务显示或提交独立适用性页面 | 迁移到素材选择内的 selected-asset preflight |
| `slots_copy/slot_plan` | `deterministic`（默认）、`manual`；历史 `agent_assisted` 只读 | 按缺失坑位、选择顺序、来源轮询和比例评分生成唯一初始草稿 | 审核、人工修改或去掉当前坑位、确认计划、可视化裁剪压缩、确认文案 | 创建新坑位 AI 请求、自动确认、自动处理图片、进入 dry-run 或上传 | 自动编排失败保留当前草稿或空状态；去掉坑位使旧输出失效；用户确认剩余合法 3–9 张同一比例坑位后处理 |
| `dry_run/dry_run_review` | `rules`（默认）、`manual_only` | 生成只读 dry-run 和差异报告 | 审查全部商品与阻断项 | 把 dry-run 当发布授权 | 阻断项回到对应阶段；人工通过后才进入授权 |
| `approval/exact_authorization` | `manual_only`（默认） | 生成精确不可变批准清单并通过唯一入口执行发布 | 当前批次 task ID、店铺和内容哈希的一次显式批准兼发布授权 | 推断批准、沿用内容变化前批准、要求第二次生产确认 | 未批准保持等待；授权身份完整后运行 `process-publish-authorization` |
| `production_confirmation/production_write` | 历史只读 | 不执行新任务动作 | 无 | 新任务进入此阶段或再次索要授权 | 返回当前 approval/results；不得发布 |
| `results/result_recovery` | `rules`（默认）、`manual` | 远端回查、对账、报告和可证明的断点恢复 | 不确定发布、审核失败或是否重提的裁决 | 对不确定发布自动重发 | 保持 `publish_uncertain`；可信远端证据或新人工决定后继续 |

## 活动 Agent 请求

新任务不创建、领取、重试或完成坑位编排 Agent request。下列坑位请求命令只用于
恢复历史任务；新任务唯一允许创建的第五阶段 Agent request 是在实际图片输出全部
通过复核后生成标题和描述的 `copy_draft`。

新任务创建 `copy_draft` 后由工作台后台调度器自动调用固定处理器。下列命令只供历史会话兼容和开发诊断：

```powershell
uv run tmall-materials list-agent-requests --runs-root <runs目录> --session <session_id>
uv run tmall-materials wait-agent-request --runs-root <runs目录> --session <session_id> --timeout 30
uv run tmall-materials claim-agent-request --runs-root <runs目录> --session <session_id> --request <request_id>
uv run tmall-materials complete-agent-request --runs-root <runs目录> --session <session_id> --request <request_id> --response <response.json>
uv run tmall-materials cancel-agent-request --runs-root <runs目录> --session <session_id> --request <request_id>
```

历史兼容等待必须有界；新任务页面读取 `workflow_dispatch` 与请求进度，允许重试 AI 或转为人工编排，
不得把旧 Agent 等待租约显示成工作台后台状态。请求
只能读取其目录中的缩略图。响应必须给出同商品、单一比例、3–9 张有序唯一图片，
并包含理由、`ai_confidence`、裁剪风险、多样性、重复判断和预计处理成本。
分析每张图前先按“源 SHA-256 + provider + 模型 + analysis schema”读取
`agent-analysis-cache`，未命中才分析并写回；“换一套”可以调整组合和顺序，但
不得重复消耗未变化图片的视觉分析。

用户开始人工编辑时工作台可取消请求；上游 revision 改变时请求
标记 `superseded`，迟到响应必须拒绝。Codex 任务结束不影响仍在运行的工作台后台；
工作台服务停止后使用精确 `runs_root + session_id` 恢复，调度器自动扫描该 session，
不得按目录新旧猜测任务。
