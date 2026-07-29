## Why

新 Codex 会话可能没有发现或没有加载 `upload-search-materials`，即使加载了 Skill，也缺少一个可后台启动、等待就绪并打开浏览器的统一入口，导致 Agent 跳过配置页，直接在聊天中索要店铺名、NAS 根目录等结构化输入。需要把“前端优先、对话保底”提升为可发现、可执行、可审计的 Skill 契约，而不是只在文档中保留一句提示。

## What Changes

- 让项目内的 `upload-search-materials` 能被新 Codex 会话稳定发现，并确保触发元数据、界面说明和默认提示词与规范保持一致。
- 增加单一 UI 启动入口：准备或复用环境、创建隔离会话、后台启动服务、等待健康检查、选择可用端口并打开精确会话 URL；启动命令必须及时返回，不能因前台 Flask 进程表现为卡住。
- 将所有可结构化配置和人工决定默认路由到对应阶段页面，包括任务配置、商品选择、文件夹归属、图片选择、坑位调整、裁剪压缩、文案确认、dry-run 审查、精确批准和生产确认。
- 明确 Codex 对话只作为有原因码的降级通道：前端服务确实无法启动、当前客户端不能打开页面、原生系统授权只能由聊天批准，或页面 schema 尚不支持该输入时才能使用。
- 对话降级收集的值仍写入同一时间戳会话、阶段、revision 和 JSON schema，记录来源与原因；不得形成第二套旁路状态，也不得以聊天回答静默代替批准或生产确认。
- 页面恢复后优先回到前端继续；错误信息必须包含可操作的恢复入口，不得直接重新询问全部业务配置。
- 增加 fresh-context Skill 触发、UI 自动启动、无前置业务提问、降级边界、恢复和跨电脑行为验收。

## Capabilities

### New Capabilities

- `discoverable-skill-entrypoint`: 新 Codex 会话能够发现正确版本的 Skill，并从简洁、无冲突的启动契约进入工作流。
- `managed-interaction-ui-launcher`: 提供非阻塞、可恢复、自动打开精确会话页面的统一前端启动与服务生命周期。
- `frontend-first-interaction-routing`: 将用户配置与人工决定优先路由到前端，并为不可前端化的情况提供受控、可审计的 Codex 对话保底。

### Modified Capabilities

<!-- 当前 `openspec/specs/` 没有已发布能力规格；本变更只新增能力。 -->

## Impact

- Skill 元数据与入口：`upload-search-materials/SKILL.md`、`agents/openai.yaml`、项目级 Skill 发现/注册方式。
- 启动与生命周期：`scripts/`、`tmall-materials interact`、健康检查、端口选择、后台进程与会话恢复。
- 交互协议：阶段 schema、`input.json`、`handoff.json`、fallback 审计字段、错误和恢复协议。
- 前端：各阶段用户输入入口、降级提示、恢复入口和页面可用性检测。
- 测试与文档：fresh-context Skill 验收、浏览器测试、跨电脑测试、Skill validator、操作指南和 `test_plan.md`。
- 不改变真实上传、精确批准、店铺校验和生产确认的安全门禁；对话降级不得扩大授权范围。
