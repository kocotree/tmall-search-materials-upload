## 1. 基线与交互清单

- [x] 1.1 记录当前分支、fresh-context Skill 可见性、canonical Skill、Agent metadata、`interact` 前台行为和另一个窗口直接询问店铺/NAS 路径的复现证据
- [x] 1.2 枚举全部阶段字段、按钮、原生选择器、登录/授权动作和现有聊天提问，建立 `frontend_required / frontend_preferred / chat_fallback` 路由矩阵
- [x] 1.3 明确系统安装授权、登录验证码、前端启动失败、客户端无浏览器和 schema gap 的稳定降级原因码
- [x] 1.4 记录现有端口、会话目录、PID、日志、健康检查和浏览器打开能力的跨电脑约束

## 2. Skill 发现与触发入口

- [x] 2.1 增加项目级 Codex 可发现入口，使 fresh repository session 能发现 `upload-search-materials`
- [x] 2.2 让发现入口在任何任务动作前解析并完整读取 canonical `upload-search-materials/SKILL.md`，解析失败时返回稳定安装错误
- [x] 2.3 提供用户级注册、更新和状态检查入口，支持从仓库外启动且不产生不可检测的规范副本漂移
- [x] 2.4 重写 Skill frontmatter description，覆盖启动、配置、测试、恢复、审查、dry-run 和上传场景，并明确 UI 是结构化输入默认入口
- [x] 2.5 按 `skill-creator` 规范重新生成并校验 `agents/openai.yaml` 的 display name、short description 和 default prompt
- [x] 2.6 增加发现入口、canonical Skill 与 Agent metadata 的名称、触发范围、来源 SHA 和启动契约一致性检查
- [x] 2.7 增加 fresh-context Skill 目录发现、缺失 canonical 文件和陈旧 metadata 的自动化测试

## 3. 受管前端启动器

- [x] 3.1 定义服务状态 schema，记录 session ID、runs root、PID、ownership token、端口、URL、日志、启动时间和健康状态
- [x] 3.2 增加专用健康端点，区分 Web 服务健康、精确 session 可读和浏览器是否成功打开
- [x] 3.3 实现新时间戳 session 创建与显式 session 恢复，禁止默认猜测最新任务
- [x] 3.4 实现受控端口范围探测；端口被未知进程占用时选择下一端口，不结束未知进程
- [x] 3.5 实现后台服务启动、stdout/stderr 会话日志、就绪轮询、超时清理和机器可读启动结果
- [x] 3.6 实现幂等 `status`、`start`、`stop` 和 `restart`，仅管理 ownership token 与监听端点同时匹配的进程
- [x] 3.7 优先让 Agent 使用 Codex 内置浏览器打开精确 URL；无内置浏览器时支持系统默认浏览器并单独报告打开失败
- [x] 3.8 为 Windows 提供单一 `.cmd/.ps1` 入口，组合既有 uv bootstrap 与 UI 启动但不修改系统执行策略
- [x] 3.9 保留 `interact` 前台调试兼容入口，并把受管启动器设为 Skill 和操作指南的唯一日常推荐命令
- [x] 3.10 覆盖默认端口占用、迟启动、子进程提前退出、重复启动、PID 复用、浏览器失败和恢复测试

## 4. 前端优先路由协议

- [x] 4.1 为每个阶段和字段增加交互策略元数据，未声明的结构化业务字段默认使用 `frontend_required`
- [x] 4.2 在阶段 API 中返回当前字段的 frontend component、fallback eligibility 和允许原因码
- [x] 4.3 在 Agent handoff/恢复协议中要求先打开当前阶段页面并等待 handoff，再决定是否允许聊天提问
- [x] 4.4 禁止把空店铺、空月份、空图片源、NAS 当前不可访问或未登录状态解释为配置页启动失败
- [x] 4.5 定义 fallback 审计信封，绑定 channel、reason、detail、actor、时间、session、stage、revision 和 input SHA
- [x] 4.6 实现受控 chat-fallback draft 写入 CLI/API，复用现有字段校验、revision 冲突、自动保存和 handoff 生成
- [x] 4.7 页面恢复时水合 fallback 值并显示来源，允许用户按正常 revision 规则修改且不丢失审计
- [x] 4.8 schema 不支持字段时记录 `SCHEMA_GAP` 并生成前端补充任务，不允许将自由文本旁路永久化
- [x] 4.9 对 UI 启动失败、不可达、无浏览器、系统权限和 schema gap 分别实现可操作恢复说明
- [x] 4.10 覆盖有效 fallback、无原因 fallback 拒绝、陈旧 revision、字段错误、重开 Codex 会话和前端恢复测试

## 5. 各阶段前端覆盖与安全门禁

- [x] 5.1 核对阶段一任务配置页完整承载店铺、月份、1–50 个图片源、原生文件夹选择、路径检测和高级可选输入
- [x] 5.2 核对搜推高价值采集后的商品批量选择、搜索、筛选和排除审计全部通过第二阶段页面完成
- [x] 5.3 核对文件夹采用/排除、图片换批与采用、原图信息、预检状态和重复提示全部通过素材页面完成
- [x] 5.4 核对确定性坑位摘要、人工增删调序、比例决定、可视化裁剪和压缩确认全部通过第五阶段第一页完成
- [x] 5.5 核对 AI 文案请求状态、标题描述编辑、风险说明和逐坑确认全部通过第五阶段第二页完成
- [x] 5.6 核对 dry-run 审查、精确批准清单、生产确认和结果恢复均有清晰前端入口
- [x] 5.7 对批准页不可用的聊天降级继续要求店铺、task ID、内容哈希、有效期和动作的精确清单确认
- [x] 5.8 对“全部继续”等模糊回答、聊天中直接提供店铺/NAS 路径和未绑定 revision 的决定保持拒绝或只作预填建议
- [x] 5.9 增加普通屏与窄屏全阶段导航测试，证明页面字段可见、可输入、可恢复且没有只能靠聊天完成的结构化决定

## 6. Skill 精简、文档与前向验证

- [x] 6.1 将低自由度启动顺序和 frontend-first 路由放到 canonical Skill 最前部，并删除重复或冲突的 Required Inputs 表述
- [x] 6.2 按渐进披露原则把详细字段、长命令、schema 和阶段示例保留在直接链接 references 中，控制 Skill 长度与重复
- [x] 6.3 更新操作指南、错误处理、数据 schema、Agent 决策边界和 `test_plan.md`
- [x] 6.4 更新 uv bootstrap 说明，明确只有运行环境安装/权限可以在 UI 前通过聊天处理
- [ ] 6.5 使用不泄露预期答案的 fresh-context 任务验证“开始上传素材”“继续旧 session”“页面打不开”三类真实触发
- [ ] 6.6 验证 fresh-context 的首个业务交互是前端页面或明确 UI 恢复动作，而不是询问店铺名/NAS 根目录
- [x] 6.7 运行 Python 全量、Node、浏览器、前端语法、Skill validator、OpenSpec strict、`uv lock --check` 和 `git diff --check`
- [x] 6.8 保存 Skill 发现、启动器、全阶段前端、聊天降级、跨电脑和恢复验收证据
- [x] 6.9 确认测试全过程不执行真实批准、生产确认或素材上传，并更新发布状态
