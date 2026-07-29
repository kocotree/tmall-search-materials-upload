# 人工、规则与 Codex Agent 决策边界

## 执行总则

每次处理阶段 handoff 前，Agent 必须先读取当前 revision 绑定的
`<stage>/decision-modes/<decision_id>.json`。文件不存在时，只能使用下表的
Skill 默认模式；历史任务不得推断成 AI 模式。

- `manual`：等待用户在页面作出决定；Agent 只校验并执行已提交结果。
- `rules`：执行确定性规则并展示可解释草案；用户仍可修改或确认。
- `deterministic`：新任务第五阶段默认模式；相同输入和策略版本必须产生相同坑位草稿。
- `agent_assisted`：仅用于 AI 文案和读取历史坑位 AI 草稿；新任务不得创建坑位 AI 请求。
- `manual_only`：不可委托。Agent 必须等待用户的页面确认或当前对话精确授权。

模式选择、人工覆盖、AI 草稿载入、取消和规则回退都写入审计事件。模式或上游
revision 改变时，旧 AI 请求必须取消或标记 `superseded`；合法人工草稿和图片
选择必须保留。合法 AI 响应可写入唯一未确认草稿，但不能自动确认、裁剪、压缩、
dry-run、批准或上传。

## 九阶段边界表

| 阶段 / decision ID | 允许模式（默认） | Agent 可执行 | 必须等待用户 | 禁止动作 | 失败回退与继续条件 |
| --- | --- | --- | --- | --- | --- |
| `setup/task_configuration` | `manual_only`（默认） | 启动页面、自动探测只读路径、校验提交 | 店铺、月份、图片源及店铺一致性确认 | 猜测配置、提前采集或上传 | 保留页面并给恢复指令；setup handoff 校验通过后继续 |
| `completeness/product_selection` | `rules`（默认）、`manual` | 全量采集搜推高价值、规则排除、生成矩阵 | 选择进入后续阶段的商品 | 扩大实时采集商品范围、替用户选商品 | 采集异常保持人工审查；用户提交非空合法商品集合后继续 |
| `asset_matching/asset_selection` | `manual`（默认）、`rules` | 规则匹配文件夹、生成预览；提交采用图片时增量预检并同步生成确定性坑位草稿 | 排除错误文件夹、选择及授权图片 | 用别名猜测归属、检查未采用共享盘图片、生成派生图片 | 1–2 张可保存草稿但不得提交；每个商品至少 3 张可用唯一图后继续 |
| `image_review/suitability_review` | 历史兼容 | 只读恢复旧适用性快照 | 仅旧任务保留原决定 | 新任务显示或提交独立适用性页面 | 迁移到素材选择内的 selected-asset preflight |
| `slots_copy/slot_plan` | `deterministic`（默认）、`manual`；历史 `agent_assisted` 只读 | 按缺失坑位、选择顺序、来源轮询和比例评分生成唯一初始草稿 | 审核或人工修改坑位、确认计划、可视化裁剪压缩、确认文案 | 创建新坑位 AI 请求、自动确认、自动处理图片、进入 dry-run 或上传 | 自动编排失败保留当前草稿或空状态；用户人工编排，确认合法 3–9 张同一比例坑位后处理 |
| `dry_run/dry_run_review` | `rules`（默认）、`manual_only` | 生成只读 dry-run 和差异报告 | 审查全部商品与阻断项 | 把 dry-run 当发布授权 | 阻断项回到对应阶段；人工通过后才进入授权 |
| `approval/exact_authorization` | `manual_only`（默认） | 生成精确不可变批准清单供查看 | 当前批次 task ID、店铺、内容哈希和有效期的显式批准 | 推断批准、沿用内容变化前批准 | 未批准保持等待；批准清单身份完整后继续 |
| `production_confirmation/production_write` | `manual_only`（默认） | 再校验店铺、登录、批准和远端状态 | 当前对话对 1–3 商品生产写入的显式确认 | AI/规则自动点击发布、跨店铺发布 | 未确认或身份不一致立即停止；明确确认后仅执行批准项 |
| `results/result_recovery` | `rules`（默认）、`manual` | 远端回查、对账、报告和可证明的断点恢复 | 不确定发布、审核失败或是否重提的裁决 | 对不确定发布自动重发 | 保持 `publish_uncertain`；可信远端证据或新人工决定后继续 |

## 活动 Agent 请求

新任务不创建、领取、重试或完成坑位编排 Agent request。下列坑位请求命令只用于
恢复历史任务；新任务唯一允许创建的第五阶段 Agent request 是在实际图片输出全部
通过复核后生成标题和描述的 `copy_draft`。

用户点击第五阶段“提交给 Agent 生成建议”后，Agent 可执行：

```powershell
uv run tmall-materials list-agent-requests --runs-root <runs目录> --session <session_id>
uv run tmall-materials wait-agent-request --runs-root <runs目录> --session <session_id> --timeout 30
uv run tmall-materials claim-agent-request --runs-root <runs目录> --session <session_id> --request <request_id>
uv run tmall-materials complete-agent-request --runs-root <runs目录> --session <session_id> --request <request_id> --response <response.json>
```

等待必须有界；超时后保留页面当前草稿或空状态，允许重试 AI 或转为人工编排，
不声称 Agent 仍在后台运行。请求
只能读取其目录中的缩略图。响应必须给出同商品、单一比例、3–9 张有序唯一图片，
并包含理由、`ai_confidence`、裁剪风险、多样性、重复判断和预计处理成本。
分析每张图前先按“源 SHA-256 + provider + 模型 + analysis schema”读取
`agent-analysis-cache`，未命中才分析并写回；“换一套”可以调整组合和顺序，但
不得重复消耗未变化图片的视觉分析。

用户开始人工编辑可运行 `cancel-agent-request`；上游 revision 改变时请求
标记 `superseded`，迟到响应必须拒绝。Codex 任务已结束时，用户使用页面给出的
精确 `runs_root + session_id + stage_id + revision + request_id` 恢复提示词继续，
不得按目录新旧猜测任务。
