## Context

`upload-search-materials/SKILL.md` 已声明启动配置页并禁止在聊天中提前索要业务字段，但新 Codex 会话的实际行为仍不稳定：

- canonical Skill 不在项目 `.codex/skills/` 或用户 Skill 目录中，fresh context 可能根本没有触发它；
- `tmall-materials interact` 是前台阻塞服务，只打印 URL，不负责等待就绪或打开浏览器；
- `bootstrap.cmd` 只同步环境和执行 CLI 冒烟，不启动交互页；
- “字段存在于页面”与“Agent 必须优先使用页面”之间缺少机器可验证的路由协议；
- 当前测试主要验证文档中存在指定句子，没有验证 fresh-context Agent 的首个交互动作。

本变更跨越 Skill 发现、进程生命周期、会话协议、前端和安全门禁。默认平台仍为 Windows，任务数据继续使用时间戳目录、stage ID、revision 和 SHA-256 隔离。

## Goals / Non-Goals

**Goals:**

- 让从项目启动的新 Codex 会话稳定发现并触发正确版本的 Skill。
- 用一个低自由度入口完成环境检查、会话创建、后台服务、就绪探测和精确 URL 打开。
- 让结构化业务输入和人工决定默认只在前端收集。
- 允许有边界、有原因、有审计的 Codex 对话降级，并复用同一阶段 schema。
- 保持 dry-run、精确批准、店铺校验和生产确认门禁不变。
- 用 fresh-context 和浏览器测试证明行为，而不只检查文档文本。

**Non-Goals:**

- 不把 Agent 业务执行嵌入 Flask 页面进程。
- 不让页面自动执行索引、采集、dry-run、批准或上传。
- 不创建第二套聊天专用数据模型。
- 不在本变更中重写现有各阶段业务算法或前端视觉风格。
- 不让对话降级绕过验证码、登录、文件系统权限或生产授权。

## Decisions

### 1. 使用 canonical Skill 加可生成的发现入口

`upload-search-materials/SKILL.md` 继续作为唯一完整规范。项目增加 Codex 可发现的入口，入口只保留高质量 frontmatter、前端优先启动规则以及读取 canonical Skill 的明确指令；同时提供用户级注册命令，供从仓库外启动的会话使用。

入口、`agents/openai.yaml` 和 canonical Skill 的关键启动契约由校验脚本统一检查，禁止人工维护两份完整工作流。Skill 描述必须同时写明触发场景和“先打开交互页”的默认动作，因为 frontmatter 是 Skill 是否触发的唯一依据。

备选方案是把完整 Skill 复制到 `.codex/skills/`。该方案容易产生版本漂移，因此不采用。仅依赖用户手工安装也不能保证克隆仓库后的 fresh context，故也不采用。

### 2. 新增非阻塞的受管 UI 启动器

保留 `interact` 作为前台调试命令，新增受管启动入口。启动器：

1. 解析项目、运行目录和现有 `.venv`；
2. 必要时调用 bootstrap，但只为安装 uv 或系统权限请求聊天授权；
3. 创建新时间戳 session，或显式恢复指定 session；
4. 在受控端口范围选择空闲端口，不终止未知监听进程；
5. 后台启动服务，把 PID、端口、日志、session ID 和启动时间写入会话服务状态；
6. 轮询健康端点和精确 session API；
7. 返回机器可读 JSON 与 URL，并优先由 Agent 的内置浏览器打开；内置浏览器不可用时才调用系统默认浏览器；
8. 在限定时间内失败则停止自身创建的进程，返回稳定原因码和恢复命令。

这避免 Agent 直接运行长期阻塞的 Flask 命令，也避免通过固定 sleep 猜测服务是否就绪。

### 3. 以阶段 schema 声明交互通道

阶段及字段增加交互策略元数据，默认值是 `frontend_required` 或 `frontend_preferred`，只有明确列出的字段允许 `chat_fallback`。Agent 在准备提问前必须读取当前阶段 schema：

- `frontend_required`：必须在页面完成；聊天只能解释如何恢复页面。
- `frontend_preferred`：先展示页面；页面确实不可用后可降级。
- `chat_fallback`：系统安装授权、浏览器/登录协助、无法由页面发起的系统级批准等可以在聊天处理。

各阶段现有字段、图片决定、裁剪框、批准清单继续由同一 schema 驱动，不把聊天回答存到独立文件。

### 4. 对话降级使用同一 revision 与审计信封

业务字段在聊天降级时通过受控 CLI/API 写入当前 stage 的 draft，并附加：

- `interaction_channel=chat_fallback`
- `fallback_reason_code`
- `fallback_detail`
- `recorded_at`
- `recorded_by`
- 当前 `session_id/stage_id/revision/input_sha256`

写入后仍执行与页面完全相同的字段校验、revision 冲突和 handoff 规则。页面恢复时展示这些值及来源，用户可以继续修改。批准和生产确认若使用聊天降级，必须展示精确不可变清单及哈希，并保存用户在当前对话中的明确确认；“全部继续”等模糊回答无效。

备选方案是让 Agent 在内存中记住聊天答案并直接执行业务。该方案不可恢复、不可审计且会绕过阶段门禁，因此不采用。

### 5. 用路由状态机限制 Agent 提问

每个需要用户输入的动作按以下顺序执行：

```text
发现 Skill
  → 启动/恢复 UI
  → 等待页面 handoff
  → Agent 执行当前阶段
  → 下一阶段 UI
```

只有收到启动失败原因或前端能力检查明确失败时，才进入：

```text
记录 fallback 原因
  → 在聊天中只询问当前阶段缺失字段
  → 写入同一 draft/revision
  → 页面恢复后回到 UI
```

Agent 不得因为值尚未填写、共享盘当前不可访问或用户尚未登录，就把配置页启动判定为失败。

### 6. 测试以行为验收为准

除单元测试外，增加：

- fresh-context Skill 发现与触发测试；
- 首个用户可见动作不得是询问店铺/NAS 路径的压力测试；
- 后台启动、动态端口、健康检查、URL 打开和停止/恢复测试；
- 每个阶段的 frontend/fallback 路由矩阵测试；
- 聊天降级写回同一 JSON、revision 冲突和恢复页面测试；
- 窄屏/普通屏浏览器完整导航测试；
- Skill validator、OpenSpec strict 和跨电脑无绝对路径检查。

forward-test 必须只提供原始 Skill 和真实用户式请求，不向测试 Agent泄露预期行为。

## Risks / Trade-offs

- [项目级入口与 canonical Skill 发生漂移] → 入口保持最小化并由生成/校验脚本比较契约 SHA。
- [后台服务残留] → 只管理带会话服务状态且由本启动器创建的 PID；提供幂等 stop/status，不杀未知进程。
- [自动打开浏览器在无 GUI 环境失败] → URL 返回视为服务成功，浏览器失败单独记录并进入受控降级。
- [前端 schema 不能覆盖新问题] → 使用 `SCHEMA_GAP` 原因码降级，并要求创建后续 schema 任务，不能永久依赖自由文本。
- [聊天降级削弱生产安全] → 批准/生产确认继续要求精确清单、哈希和显式当前对话确认。
- [Skill 过长导致关键规则被忽略] → 按 skill-creator 原则把详细参考移出 canonical Skill，保留低自由度启动与路由规则在最前面，并保持入口简洁。
- [首次 bootstrap 需要网络或安装权限] → 仅这些运行条件可以在启动页前通过聊天请求权限，业务配置仍不得提前索取。

## Migration Plan

1. 建立发现入口和契约校验，但保留现有 canonical Skill。
2. 实现受管启动器和健康端点；`interact` 暂时保留兼容。
3. 为阶段 schema 增加默认交互通道，不改变已有 JSON 字段含义。
4. 增加 fallback 审计信封及写入入口，迁移现有页面恢复逻辑。
5. 更新 Skill、metadata 和操作指南，把受管启动器设为唯一推荐入口。
6. 在新工作目录和 fresh Codex 会话进行前向验收，再合入默认测试分支。

回滚时可以停用新发现入口和受管启动器，继续使用 `interact`；新增审计字段必须保持向后兼容并可被旧读取器忽略。

## Open Questions

- Codex 内置浏览器不可用但系统默认浏览器成功打开时，是否需要在页面上额外显示“返回 Codex”恢复提示？
- 用户级 Skill 注册采用目录链接还是带来源 SHA 的复制包，需在 Windows 权限与跨电脑验收后最终确定。
