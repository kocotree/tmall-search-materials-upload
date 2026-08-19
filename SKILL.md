---
name: upload-search-materials
description: Use when starting, configuring, testing, preparing, validating, reviewing, resuming, auditing, dry-running, approving, publishing, or uploading Tmall search-recommendation materials. Always start or resume the managed interaction UI before requesting structured business inputs such as store, image-source roots, product choices, media decisions, slots, copy, approval, or production confirmation in chat.
---
# Upload Search Materials

## 维护信息

- 部门：运营中心-天猫部
- 作者：虾米

## 正常业务流与宿主审批边界（每次触发均适用）

1. 必须区分两类确认：工作台中的业务确认用于记录用户的业务决定与生产授权；Codex 宿主的命令审批卡用于越出既有技术权限边界。二者不得互相替代，也不得因为后台准备执行正常步骤而在聊天中重复询问用户“是否允许”。
2. 用户启动本 Skill、提交当前工作台阶段或点击对应业务按钮后，下列动作属于已授权的确定性正常流程，由托管工作台后台调度器通过正式处理器执行；Codex 不得为它们生成命令审批卡、Approval request、confirmation boundary 或自然语言二次授权：
   - 启动后读取或刷新精确 session 的 URL、健康状态、当前阶段、revision、处理进度和结果；
   - 原子保存 handoff、唤醒工作台后台队列、刷新 `ui-status`，以及当前 session 的幂等恢复；
   - 处理已验证的 setup handoff，采集“搜推高价值”全部分页，保存 checkpoint，并生成商品选择数据；
   - 同步和读取已配置素材源的现有文件夹索引；缺少有效共享快照时发布第一份不可变快照；读取目录元数据并生成文件夹候选；
   - 用户在工作台确认文件夹并点击加载或重试后，排队并启动一次性素材执行器，读取被采用文件夹的任务所需原图、生成预览和预检结果；
   - 根据已提交的工作台决定执行确定性坑位编排、裁剪预校验、图片输出、dry-run 和状态回写；
   - 处理已绑定 revision、请求 ID 和 SHA-256 的 `copy_draft`；
   - 用户在“上传任务确认”页提交精确任务后，运行唯一的 `process-publish-authorization` 入口完成复核和发布。该页面提交已经是生产授权，不得再追加聊天确认或命令确认。
3. 正常流程只能调用本 Skill 已声明的固定脚本、localhost 工作台 API、正式处理器和一次性桌面执行器。工作台后台按持久化 handoff 的 `session_id + stage_id + revision + input_sha256` 领取 `process-setup`、`process-product-selection`、`process-final-material-handoff`、`process-copy-request` 和 `process-publish-authorization`，服务重启后扫描当前精确 session 的未完成交接并恢复。Codex 不参与阶段轮询和领取，不得为了同一动作改用临时 PowerShell、拼接 shell、手工改 JSON、访问无关目录或扩大命令前缀。
4. Windows 桌面身份只在运行环境准备阶段建立。日常任务应复用已经允许的固定 `scripts/start-managed-workbench.ps1` 入口及其托管服务，不得对每次状态读取、页面监听、NAS 元数据读取或候选生成再次申请桌面权限。当前电脑尚未具备固定启动权限时，只能把它作为一次性 `SYSTEM_PERMISSION_REQUIRED` 环境准备处理，并把申请范围限定到该固定入口；完成后立即返回工作台流程。
5. 只有正常业务路径无法继续且确实需要新增技术权限时，才允许触发宿主审批：首次安装 `uv` 或修复 Plugin 运行环境；访问未配置位置、连接新 NAS、认证或映射网络盘；刷新或再次发布已有共享索引；写入、移动、删除源素材；修改选择器、依赖或代码；使用临时诊断/恢复命令；以及 `publish_uncertain` 后创建新任务并重新发布。登录、验证码和工作台中的商品、文件夹、图片、坑位、文案及上传清单选择仍由对应页面承载，不转成命令审批卡。
6. 宿主沙箱和管理员策略高于本 Skill，Agent 不得规避或伪装宿主强制审批；但也不得主动把正常工作流包装成需要提权的异常命令。若宿主仍阻止固定入口，应报告一次稳定原因码并停止技术旁路，不得围绕同一业务步骤连续弹卡。

### 正常流程禁止源码研究

1. 工作台健康、后台调度器在线且正式处理器没有返回异常时，Codex 只负责启动或恢复精确工作台、提供当前链接和解释页面业务状态。不得用 CodeGraph、`rg`、`Get-Content` 或类似方式搜索/阅读项目源码，不得扫描 Plugin 目录、版本缓存或 runs 目录来重新发现入口，也不得直接读取 handoff、input、锁文件、进程文件或调度状态来手工接管正常步骤。
2. 用户提交后，工作台提交事务先原子保存 input、revision 快照与 handoff，再唤醒单 session 后台队列；调度器先检查持久化积压，然后由唯一正式处理器领取 processing claim。Codex 不调用 `agent-wait`、`listen-handoff`、`wait-agent-request` 或阶段处理器，不需要在聊天中等待、轮询或接收“已提交”。旧监听和受控 Agent API 只保留为开发诊断与旧版本兼容入口，不属于新任务正常路径。
3. 页面每 2 秒读取工作台状态与后台调度状态，显示排队、处理中、完成或异常。Codex 浏览器宿主是否具备同源 JSON 请求能力不再影响业务流程；不得试探 `fetch`、XHR、页面脚本、终端轮询或版本缓存来代替工作台后台。
4. 只有正式处理器返回稳定异常原因码，并且与当前 revision 绑定的 `agent-diagnostics/current.json` 已写入 `open` 诊断后，才进入源码研究。必须先运行唯一的 `diagnose-session` 读取 phase、processor、证据和幂等重试入口，再把研究范围限制到该处理器及其直接调用链；修复后仍重跑原固定入口。登录、人机验证和业务校验退回不属于源码异常，不得触发源码扫描。

## 前端优先启动与交互路由（每次触发首先执行）

1. 完整读取本文件后先启动或恢复交互页面。Windows 上必须通过已在运行环境准备阶段允许的固定 `scripts/start-managed-workbench.ps1` 入口，以普通桌面身份运行，不能从 Codex 沙箱身份直接启动；不得在日常任务中临时扩大该入口的授权范围。启动结果只核对 `launcher_runtime_identity` 与 `runtime_identity` 的 SID 和登录会话相同。`remote_drive_letters` 仅作诊断展示：本机已保存图片源只是配置页历史预填项，不得把其盘符可见性作为工作台启动条件。用户提交配置页时，才检测页面中本次最终填写的图片源。服务显示 `healthy` 但桌面身份不符时，立即停止该精确 session 的服务并用同一固定桌面入口恢复，不得让用户反复点击图片重试。托管服务同时启动当前精确 session 的后台调度器；页面提交自动唤醒它。真正读取图片时使用项目自带的 Windows 一次性素材执行器：用户点击“确认文件夹并加载图片”或“重试加载图片”后，页面自动请求 Windows 桌面会话启动它；用户不运行终端命令，也不接受读取素材的二次命令审批。执行器必须通过同一普通桌面身份启动，以继承本次所选路径需要的映射；只按 `source_id + relative_path` 读取本机绑定，处理当前任务后退出，不安装系统服务、不保存 NAS 凭据。
2. 启动成功后，优先用 Codex 内置浏览器打开 JSON 中的精确 `url`。内置浏览器不可用时才传 `-OpenSystemBrowser` 或把 URL 交给用户。浏览器失败不等于服务失败。
   每次在聊天中提醒用户执行任何交互操作前，必须先从本次启动结果或
   `tmall-materials ui-status --runs-root <精确 runs-root> --session <精确 session-id>`
   取得当前 session 的精确 `url`，并在同一条提醒中提供可点击的工作台链接。该要求适用于
   登录/验证码协助、任务配置、商品选择、文件夹审查、选图、坑位与裁剪、文案确认、上传任务确认和恢复操作。
   不得硬编码端口、复用其他 session 的 URL、根据最新目录猜测 URL，或只说“回到工作台”而不给链接。
   若服务不可达，先恢复同一 session 并使用恢复结果中的新 URL；无法恢复时报告服务故障，
   不得在没有有效 URL 时要求用户进行页面操作。
3. 新任务没有店铺、图片源或 NAS 映射时仍须启动工作台；这些是后续业务字段，不是启动阻断。工作台同时自动打开千牛原生窗口并检查登录：未登录时只显示登录等待页，不展示阶段一业务表单；用户在千牛窗口完成登录后自动进入配置，不要求点击技术验证按钮。
4. 每一阶段都在当前页面完成结构化配置和人工决定，不得先在聊天中索取。正式提交后，页面显示“工作台后台已接收/处理中”，并通过同源 stage status 与 `workflow_dispatch` 状态自动刷新；后台只按持久化 `handoff_identity` 调用允许的固定处理器。Codex 不建立监听租约，不运行终端监听命令，也不需要保持当前对话回合存活。
5. 只有启动器/API 返回允许的稳定原因码后，才能对声明为 `frontend_preferred` 的字段使用 `tmall-materials chat-fallback`；写入仍绑定同一 session、stage、revision、schema 和审计历史。页面恢复后立即回到页面。
6. 安装 uv 或系统运行权限可在 UI 前通过聊天申请；店铺/NAS 等业务值不可以。上传任务确认即使降级也必须使用精确清单与哈希，模糊的“全部继续”无效。

“先提交、后启动后台”和“后台已启动、后提交”都由持久化 handoff 与启动恢复扫描覆盖。Codex 回合中断、用户关闭聊天或页面刷新不会丢失交接；托管工作台仍运行时会继续处理。只有工作台服务本身停止时才暂停，恢复同一精确 session 后自动扫描未领取或可恢复的任务，无需用户在聊天输入“已提交”。

新任务启动时省略 `-RunsRoot`，让工作台使用用户数据目录下的稳定 `runs/`；恢复任务时必须从启动结果或恢复指令沿用该任务的精确 `runs_root`。不得把新任务写入 Plugin 安装目录或版本缓存。日常状态与恢复：

```powershell
scripts\start-managed-workbench.ps1
scripts\start-managed-workbench.ps1 -RunsRoot "<精确 runs-root>" -Session "<session-id>"
tmall-materials ui-status --runs-root <runs-root> --session <session-id>
tmall-materials ui-restart --runs-root <runs-root> --session <session-id>
tmall-materials ui-stop --runs-root <runs-root> --session <session-id>
```

固定脚本必须从当前 Plugin 版本目录执行，以把真实 Plugin 根目录显式传给用户级运行环境和托管子进程。

工作台健康时，Codex 只读取当前页面或 `ui-status` 提供的公开状态，不读取 `%LOCALAPPDATA%` 中的锁文件，也不启动等价终端命令。以下旧接口仅供兼容测试和稳定异常诊断，不属于正常业务流：

```text
GET  /api/sessions/<session_id>/stages/<stage_id>/status
POST /api/sessions/<session_id>/stages/<stage_id>/agent-wait
POST /api/sessions/<session_id>/agent-actions/process-setup
POST /api/sessions/<session_id>/agent-actions/process-product-selection
POST /api/sessions/<session_id>/agent-actions/process-final-material-handoff
```

后台调度器是新任务的唯一正常领取者，claimant 固定为 `workbench-dispatcher`。它在服务进程内读取经过验证的 handoff 或 `copy_draft` 请求，处理失败时写入统一诊断并停止自动技术旁路。兼容 `agent-wait`、`listen-handoff` 与 `resume-session --ack 已提交` 不得出现在正常用户步骤、聊天提示或审批说明中。

`tmall-materials interact` 只用于前台调试，不是日常入口。完整原因码、字段路由、聊天降级信封和跨电脑约束见 [frontend-interaction-contract.md](references/frontend-interaction-contract.md)；长命令见 [operations-guide.md](references/operations-guide.md)。

## 执行模式契约（每一阶段必读）

本 Skill 在不同阶段使用 `manual`、`rules`、`deterministic`、`agent_assisted`、`manual_only` 执行模式。
Agent 在处理任何 handoff 前，必须先读取该阶段当前 revision 的持久化
decision-mode；不存在时使用 Skill 的阶段默认值。第五阶段新任务的坑位编排只允许
`deterministic` 与 `manual` 两个入口；历史规则/AI 草稿只读展示，不得重新生成、
自动采用或覆盖。`agent_assisted` 只用于最终图片确定后的标题和描述；新任务默认
调用千牛商品坑位内置的“AI生成文案”，不由 Codex 自行编写商品文案。
模式选择决定 Agent 是等待用户、运行确定性规则，还是领取受控 AI 请求。

素材选择提交后，同步运行确定性编排器并生成唯一未确认
`current_slot_plan`。用户必须审核或人工调整草稿，再点击“确认坑位并进入图片裁剪”。
AI 不参与选图、坑位数量、分组、顺序、比例、裁剪或压缩，也不能触发 dry-run、
批准或上传。
`approval` 永远是 `manual_only`，任何 Agent、规则或页面轮询都不能代替当前用户的
精确授权。用户在“上传任务确认”页勾选精确任务后点击一次“提交并自动上传所选任务”，
该次提交同时构成批准清单授权与正式发布授权；不得再要求独立“生产确认”。内部仍先生成
不可变批准清单、再执行发布复核，这两个技术动作不增加用户按钮。完整阶段边界、回退和恢复协议见
[decision-boundaries.md](references/decision-boundaries.md)；字段见
[data-schema.md](references/data-schema.md)，稳定错误见
[error-handling.md](references/error-handling.md)。

## 第三至第五阶段：图片合规、裁剪与坑位编排

1. 第三阶段候选卡片先执行原图硬性检查：文件可读，格式属于 JPG/JPEG/BMP/GIF/HEIC/PNG/WebP，文件大小在 200KiB–20MiB（含边界），且至少可按 1:1 或 3:4 最大内接裁出宽高均不少于 720px 的结果。任一硬性条件不满足时卡片显示红字并禁用“采用”。原图已是 1:1/3:4 时才继续比较对应推荐分辨率；未达到 3:4 的 1440×1920 或 1:1 的 1440×1440 只显示黄色提醒。原图不是目标比例但可裁剪时只显示“需要裁剪”的黄色提醒，不重复判定推荐分辨率。
2. 用户勾选“采用”时立即只对这一张图片执行 1:1、3:4 最大内接预裁剪：保留原文件格式，检查裁后宽高均不少于 720px、大小仍在 200KiB–20MiB，并以源 SHA、图片策略和算法版本缓存结果。勾选先记录当前采用意图，只有预裁剪通过后才写入正式 `asset_decisions/license_decisions`；排队中和检查中的复选框必须始终允许取消。尚未开始的取消任务从队列移除；已开始的取消任务可继续完成任务内缓存，但迟到结果不得重新选中图片。再次勾选必须复用仍在运行或已经完成的同一结果。处理普通图片最多同时运行 6 个任务，总权重预算为 8，同一素材源不单独限流；不超过 1200 万像素的普通 JPEG/WebP 权重 1，PNG/GIF/HEIC/HEIF 或 1200–3000 万像素权重 2，超过 3000 万像素权重 3，达到 6000 万像素权重 4。每个任务必须只读取、解码和 EXIF 转正原图一次，再从同一解码结果依次生成两个比例的精确编码探测；页面只更新当前素材卡片、已选列表和数量摘要，不得因单张完成而重建整批候选卡片。两个比例均失败时不保存勾选并在卡片标红；只有一个比例可行时橙色提醒但允许采用。保存 1–2 张草稿合法，最终提交只汇总仍有效的单图缓存、重复 SHA 和每商品共同可行比例，不应再次逐图读取共享盘；历史草稿缺少缓存时可在首次提交中补检一次。每个商品必须至少有 3 张预检通过、源 SHA-256 唯一且共同支持同一比例的图片。校验结果写入 `03-asset-matching/selected-asset-preflight.json`；不得遍历未采用共享盘图片，不得在第三阶段生成派生图片。
   最终选图预检发现阻塞素材时不得静默丢弃或只显示汇总错误：页面必须保留全部选择，在对应商品的“已选素材”卡片逐图标红、显示可理解的更换原因并定位首个问题；只剩一个可行比例时用橙色提示并允许继续。页面不得向业务用户暴露 SHA、reason code 或本机路径，技术细节仍交给 Codex 诊断。
3. 确定性坑位数为 `K=min(后台缺失坑位, floor(可用唯一图片数/3))`，最多使用 `min(N,K*9)` 张并均衡分配。示例：9 张且缺 3/2/1 个坑位时分别为 `3+3+3`、`5+4`、`9`。图片先按用户选择顺序，再按来源文件夹稳定轮询交错。
4. 每坑比例必须是全部成员共同可行的 3:4 或 1:1，并依次按原生比例数量、推荐分辨率通过数、画面保留率、较少压缩和最终 3:4 同分优先进行确定性评分。策略 ID、版本和 SHA-256 与输入 revision 一起写入草稿；相同输入必须产生相同结果。
5. 新任务不显示或提交独立 `image_review` 阶段；内部 stage ID 和旧 `04-image-review/` 文件仅用于历史恢复。新任务也不得创建 `slot_plan` / `slot_plan_with_analysis` Agent request。
6. 第五阶段只使用两个递进子页面：`图片裁剪和压缩 → AI生成标题和描述`。第一页顶部先显示自动坑位摘要、依据、未使用候选和人工编辑器；计划未按精确 revision 确认前隐藏裁剪区。人工可增删坑位、增删/调序图片及修改比例，一张图片只能属于一个坑位。确认后才在同页展开可视化 3:4/1:1 裁剪和压缩。页面提供独立的“裁剪预校验”：只对当前已编排进坑位的图片按实际比例、裁剪框和正式编码参数生成任务内输出，逐张复核尺寸、格式、SHA-256 与不少于 204,800 字节；该步骤是本地确定性处理，不创建 handoff。预校验未通过时“完成图片处理并进入文案生成”保持禁用；图片、顺序、比例、裁剪框或压缩参数变化后立即使旧预校验失效。完成按钮只复核预校验指纹和实际输出，不重复执行完整试裁。全部标题和描述核对后使用一个整批确认勾选框提交，由本地确定性流程自动执行 dry-run；无阻塞时直接进入“上传任务确认”，有阻塞时自动返回当前编辑页，并在页面顶部显示具体问题和修改要求。独立 `dry_run` 阶段只保留为内部安全门禁和历史恢复状态，不显示在新任务左侧流程中。
7. “完成图片处理并进入文案生成”把已复核输出锁定为 `outputs_ready`，同时自动创建独立 `copy_draft` 请求；该按钮提交本身就是对本次文案种子图操作的限定授权，授权只覆盖“逐坑上传顺序第一的已验证成品图、调用千牛内置 AI、读取生成文案、退出未发布表单”，并绑定 session、计划 revision、最终输出 SHA-256、坑位和首图清单。工作台后台调度器收到有效 `copy_draft` 后直接调用唯一 `process-copy-request` 处理器，不得在聊天中再次要求用户回复“同意”、授权或确认；图片、顺序、比例、裁剪结果、输出 SHA 或请求身份变化时应由处理器拒绝旧授权，而不是扩大授权范围。此授权明确不包含“填充文案”、表单确认、正式上传或发布。
   新任务不再要求用户首次点击“生成标题与描述”，只在已有请求后提供“重新生成新版本”。调度器在请求创建时被立即唤醒，并在服务启动或恢复同一精确 session 时扫描 `pending` / 可恢复的 `processing` 请求；不依赖 Codex 运行 `wait-agent-request`、聊天回复“已提交”或保持对话存活。正式处理器必须复用项目已有 `browser/qianniu_copy.py` Playwright 流程，不得复制或另写第二套千牛文案抓取脚本。处理器按商品逐坑进入千牛“发图文”表单，验证商品已锁定后，使用该坑位顺序第一的最终输出图片唤起千牛内置“AI生成文案”：对任务目录中的本地最终图片复核 SHA-256 后，直接通过千牛网页的文件输入控件执行“本地上传”；为避免千牛截断长文件名和历史重名，上传事务使用包含源 SHA 前缀和随机后缀的短唯一素材名，图片字节不得改变，并按该唯一名称精确选中。不得先搜索或复用云端素材库中的同名图片，也不得使用与当前坑位无关的任意已有图片。读取标题和正文后立即退出未确认表单。进入商品列表时不得固定只查顶层页面或盲目选择 iframe；必须在限定时间内轮询顶层与全部 frame，并且仅采用“唯一商品名称/ID搜索框 + 已加载商品表格”同时成立的页面作用域。顶部帮助搜索框（例如“如何设置电子发票”）不得作为商品搜索框。该过程不得点击“填充文案”、表单最终“确认”或任何发布按钮；种子图片上传不等于批准或正式发布，正式上传仍受“上传任务确认”页一次精确提交约束。请求仍绑定计划 revision、最终输出 SHA-256 和图片顺序；每完成一个坑位立即写入同一请求的 `progress.json`，页面轮询并回填已完成文案，后续坑位失败不得丢失已完成结果。响应逐坑记录标题、描述、绑定商品、触发图片、依据和风险。技术异常写入统一 Agent 诊断并由页面显示稳定业务提示；只有异常持续存在时，Codex 才读取诊断、修复后重跑同一幂等入口。千牛文案必须逐坑人工核对和编辑，再通过页面唯一的整批勾选框统一确认；图片、比例、顺序、裁剪结果或输出 SHA 改变时对应文案过期，并自动取消整批确认。千牛页面不可用时保留可重试失败状态，允许人工填写，不得自动改用 Codex 生成商品事实。

详细字段、判定规则和恢复方式见 [asset-requirements.md](references/asset-requirements.md)、[data-schema.md](references/data-schema.md) 与 [operations-guide.md](references/operations-guide.md)。

将商品、月度规则、天猫后台状态和已授权素材转换成可恢复、可审计的商品级与坑位级任务。默认只运行 `dry-run`；正式发布仅接受当前批次不可变批准清单，并使用用户已登录会话中的 Playwright DOM 操作。

## 启动契约（兼容说明）

把“运行环境准备”和“阶段 1 业务配置”严格分开：

1. 启动前只检查运行条件：当前 Plugin 目录、用户级可写运行环境，或用于创建环境的 `uv`。不得要求只读 Plugin 安装缓存中预先存在 `.venv`。Windows 使用 `scripts/bootstrap.cmd` 在用户数据目录准备 Python 3.11、锁定依赖、虚拟环境和缓存。若必须安装 `uv`，只请求安装权限；安装完成后继续启动配置页。
2. 不得在配置页启动前通过聊天索取店铺名、图片源名称或图片根目录，也不得把缺少这些值报告为启动阻断。
3. 日常素材任务先通过已经允许的固定 `scripts/start-managed-workbench.ps1` 在实际桌面用户会话创建或恢复时间戳会话；Agent 直接调用该入口，不得先在聊天中请求批准，也不得为后续 `ui-status` 或工作台后台处理重复请求宿主桌面权限。只有当前电脑尚未完成运行环境准备、宿主明确阻止固定入口时，才以 `SYSTEM_PERMISSION_REQUIRED` 发起一次范围精确的环境权限申请。启动 JSON 必须验证 SID 和登录会话，不能把沙箱用户的同会话进程误当成 Explorer 桌面身份。可见网络盘只用于诊断，不得根据历史本机配置阻止配置页启动。只有已经明确不使用图片源、NAS、映射盘和原生目录窗口时才可使用 `scripts/start-ui.cmd`。`tmall-materials interact` 只保留为前台调试入口。即使尚未配置店铺或图片源，交互页面也必须正常打开。
4. 让用户在阶段 1 配置页填写并确认店铺和一个或多个图片源；只从当前会话经过校验的 setup `input.json`/`handoff.json` 读取这些值。
5. setup handoff 尚未提交时，只等待页面提交或提供恢复指令；不得自行采集、索引、dry-run、上传或发布。
6. 技术前置检查不得作为阶段一的用户操作。工作台启动后自动启动或恢复 CDP Chrome，并在显示业务配置表单前轮询登录状态；未登录、扫码、短信、验证码或风控时只显示简洁等待页并把官方千牛窗口置前，登录成功后自动展示配置页。用户不点击“验证当前页面”“验证选择器”“启动浏览器”或“检测路径”。setup 提交只保存店铺、图片源等业务决定并创建 handoff；生产选择器、当前 DOM、CDP、官方素材中心、目标店铺和路径均由处理器自动复核。缺少或失效时写入 `agent-diagnostics/current.json`，Codex 使用 `diagnose-session` 读取完整异常并重跑同一幂等入口；不得把 reason code、DOM、CDP、SHA 或修复命令暴露给业务用户。缺少本机生产选择器时只能创建 `production=false` 候选并基于真实 DOM 修复验证，禁止复制示例后直接标为生产。

聊天中用户主动提供的值可以用于解释或预填建议，但不能代替配置页提交，也不能跳过 setup handoff。

## 阶段输入（不是启动前置条件）

- 用户在配置页确认页面可见的准确店铺名。第一阶段不再配置目标月份、商品范围或搜推采集页数。
- 商品总表和月度规则从项目目录自动发现。团队索引文件夹和图片源都在任务配置页维护：团队索引文件夹保存为当前电脑的候选匹配来源，图片源可配置 1–50 个“来源名称 + 根路径”并保存为本机配置。创建任务后把商品表与规则表复制到时间戳任务目录并记录 SHA-256；不复制 NAS 原图。
- 本轮上传范围仅为搜推素材，不导出、不审查也不补传基础素材。搜推页的“导出数据”仅是经营指标；第二阶段商品范围必须来自正确店铺实时 DOM 中“商品分类 → 搜推高价值”的全量采集结果。
- 生产选择器运行时配置；示例文件不能直接用于生产。
- 人工图片素材清单、历史基础素材表和历史推广素材状态都是高级可选导入，不是日常任务必填项。
- AI 文案响应、禁用词政策；视频任务还需要完整视频规格。

缺少信息时保留明确 blocked/needs_manual_review 结果，不猜测、不静默跳过。

## Workflow

### 搜推高价值采集的唯一生产路径

处理阶段一 handoff 时，运行：

```powershell
tmall-materials process-setup --runs-root "<runs-root>" --session "<session-id>"
```

该入口必须校验精确 session、stage、revision 和 `input_sha256`，然后复用现有
`supplement --scan-mode high-value` 维护链路。正常任务禁止由 Agent 临时输出或
保留另一份 Playwright 采集脚本，也禁止使用 `--max-pages` 截断生产采集。

`process-setup` 正常模式只负责校验、创建或复用当前 `attempt_id` 并启动受管后台
Worker，随后立即返回；不得等待全量分页完成。只有显式本地诊断才使用
`--foreground`。使用以下命令读取唯一权威状态：

```powershell
tmall-materials collection-status --runs-root "<runs-root>" --session "<session-id>"
```

状态必须按“当前绑定完成结果 → 活跃且归属可证的 Worker → 已确认死亡可恢复 →
归属不确定/有效租约 → 当前阻断 → 草稿”解析。历史错误只显示在 superseded 历史，
不能与新 Worker 同时成为当前状态。已有完整页时，工作台主提示只显示“已完成第 X 页 ·
Y 行；分页起点 A · 当前页 B/C”；phase、heartbeat、checkpoint 时间、日志和恢复信息继续
保留在 `task_status` / `ui-status` 技术状态中。首个 checkpoint 前显示阶段与心跳，不得
显示为“已采集 0 行”。

checkpoint 与当前 revision、input SHA-256、选择器 SHA-256、店铺和 `attempt_id`
绑定。每页写入前先验证当前 claim，CSV 原子写入后记录 SHA-256、唯一商品 ID、页码
和行数。旧 Worker 的迟到写入必须被拒绝；恢复只从最后完整页之后继续。PID 只有同时
匹配私有 ownership token、session/attempt 和进程创建身份时才可判定归属；无法证明
时不得结束或抢占进程。

每次完整页 checkpoint 成功后，采集器按随机 1–2 页间隔执行一次只读随机动作：先在
当前采集页实际存在的“已填坑位”和“空坑位”类型中等概率随机选择一种，再在该类型内
等概率随机选择一个具体坑位；当前页只有一种类型时直接从该类型选择。已填坑位直接查看后退出，
空坑位直接点击并进入“发图文”表单，再点击表单框外的遮罩退出。该动作不得输入或搜索商品 ID，不得填写字段、
上传素材、点击确认或发布。退出后必须在最多 30 秒内持续核验动作前精确 URL、同一组有序商品 ID
和发布 iframe 已关闭，三项连续稳定至少 3 次后才能翻到下一页；轮询期间可重复执行既有关闭动作，
不得因千牛慢加载在约 5 秒时提前判定失败。超时仍无法恢复时必须停止分页，并分别记录 URL、商品顺序
和发布 iframe 的最终状态作为失败证据。没有可用商品或坑位时
记录安全跳过并继续采集，不能把随机动作失败解释为业务空值。

每次新的“搜推高价值”全量采集在复用素材中心标签页后，必须先刷新一次页面并等待
列表恢复稳定，以清除上一轮商品 ID 等 SPA 筛选状态。随后必须从“搜推高价值 N”
选择框读取页面声明的总商品数；只有无 `--max-pages` 的完整采集得到的唯一商品数与
该总数一致时才可完成。无法读取总数、刷新失败或总数不一致时必须 fail closed，并
把 `HIGH_VALUE_REFRESH_FAILED`、`HIGH_VALUE_TOTAL_UNVERIFIED` 或
`HIGH_VALUE_TOTAL_MISMATCH` 写入 Agent 可读取的采集证据。

每次新的“搜推高价值”采集在读取第一行或写入首个 checkpoint 前，必须从页面可见
分页控件确认真实当前页，并在必要时返回、稳定验证为第 1 页。点击已经激活的搜推
标签或已经选中的高价值筛选项不能视为分页重置。恢复任务同样先回到第 1 页，再按
checkpoint 中已验证的逐页商品身份重放导航。完成采集必须同时满足：当前页等于可见
末页、下一页稳定不可用、当前页商品身份稳定；不得只凭 `is_enabled() == false`
宣告完成。无法证明起点、翻页或末页时必须 fail closed，并保存分页证据。

每个 attempt 的 Worker、日志、CSV、checkpoint、分页起点/翻页/末页证据、选择器证据和结果只写入
`collected/promotion/attempts/a-<attempt-prefix>/`。只有通过 revision、输入、选择器、
店铺、CSV/checkpoint SHA、行数和商品 ID 唯一性校验的成功 attempt，才原子发布到
`collected/promotion/current/`，并投影到旧兼容路径。恢复只能复用同一 attempt 的
checkpoint；失败 attempt 保持为不可变历史。

生产选择器配置遵循：显式 `--selectors`、`TMALL_SELECTORS_FILE`、用户数据目录
`config/runtime.json` 中保存的路径、用户数据目录 `config/selectors.local.yaml`。优先在
阶段一前端的“采集运行环境”组件验证并保存；仓库示例、占位选择器和用途不匹配
的配置不得用于真实页面。生产 profile 通过真实 DOM 校验后，必须原子复制到用户数据
目录 `config/selectors.local.yaml` 并让 `config/runtime.json` 指向该稳定副本；不得只保存
开发仓库、Plugin 安装目录或版本缓存中的原路径。Plugin 更新与跨版本恢复必须复用这个
稳定副本。显式 `process-setup --selectors` 同样先校验并安装稳定副本，再把该路径传给
受管工作台服务；不得在跨进程派发时丢失该参数。

启动器取得 setup claim 后，选择器缺失、无效或离线运行环境预检失败时，必须写入与当前
revision、input SHA-256 和 attempt 绑定的 `needs_user_input` 结果及
`agent-diagnostics/current.json`，并由结果事务释放精确 processing claim；不得仅返回
HTTP 409 或留下无结果的过期租约。修复后只恢复同一幂等入口。`--project-root` 属于启动器
内部兼容参数，即使未显示在 `--help` 中也不能据此判断本机 CLI 版本过旧；应通过实际参数
解析或 `environment-status` 的项目/运行时身份验证能力。

业务页和 CDP Chrome 是两个窗口：业务页只保存配置、选择、编辑与确认；CDP Chrome
只用于用户自行登录、扫码、短信、验证码和系统自动化。进入阶段一业务配置前，系统
自动打开官方素材中心并确认页面已显示登录店铺。处理过程中出现
`LOGIN_INTERACTION_REQUIRED` 或 `HUMAN_CHECK` 时，处理器保持原 session、revision、
handoff 与 processing claim。分页采集在每页读取前、完整页 checkpoint 后、随机动作后
和翻页前检查人机验证；命中时写入与当前 attempt 绑定的 `human-checkpoint.json`，Worker
持续心跳，页面提示用户在 CDP Chrome 完成验证，验证元素消失后自动从同一 Worker 和
最后完整页继续。不得自动操作或绕过滑块，也不得索取或保存登录凭据；仅当 Worker 已
确认退出时才使用原有恢复入口，不要求用户重新填写业务数据。

千牛素材中心的 `DOMContentLoaded` 不代表 SPA 已可操作。采集器必须先等待已验证的素材页
主体控件挂载，再处理受限安全弹窗；表格水合、筛选和分页等待期间仍须持续检查迟到弹窗，
并确认其关闭后再继续读取或点击。不得依赖导航后的单次短窗口，也不得因网络加载较慢把
尚未渲染的弹窗当作不存在。

日常命令必须通过当前 Plugin 目录中的 `scripts\run-plugin.cmd` 执行；本文中的 `tmall-materials ...` 仅是
CLI 语义简写，不代表用户级环境中安装了一份业务包。启动器使用用户级 `.venv` 的
Python 和第三方依赖，但始终从当前 Plugin 的 `src/` 加载业务代码，且不得通过
`uv run` 触发隐式同步。默认运行根位于 Windows `%LOCALAPPDATA%\tmall-search-materials\runtime`
，可用 `TMALL_RUNTIME_ROOT` 覆盖。依赖缓存和环境指纹只由 bootstrap 脚本准备；恢复采集
不得下载或解析依赖，也不得向 Plugin 安装缓存写入环境或运行数据。

只有现有维护链路持久化了可复现的选择器、导航、弹窗、解析、分页或页面状态错误
后，才允许用 Playwright 检查真实 DOM。诊断结果必须用于修复现有生产选择器配置
或现有 collector，并新增/更新回归测试；随后必须重新运行原
`supplement --scan-mode high-value` 路径。临时诊断代码不能成为第二条生产采集
路径。

批量采集只记录 `target_capacity/current_count/missing_count`，并标记
`exact_slot_status=not_collected`；不得把缺失数量解释成具体空坑位编号。仅在正式
批准和发布规划前，对选中商品执行逐商品精确坑位复核。

Windows 工作台首次启动且本机尚无选择器文件时，必须把 Plugin 随包提供的生产基线初始化到 `%LOCALAPPDATA%\tmall-search-materials\config\selectors.local.yaml`，并在本机运行时配置中引用该稳定副本；已有文件不得被首次初始化或 Plugin 升级覆盖。基线同时声明 `high_value_collection` 与 `exact_material_status`，包含空坑位结构选择器，但不代替当前页面验证。每次真实采集必须按用途加载生产 profile，并在当前登录店铺 DOM 上失败关闭；任何商品身份、目标坑位、素材表、空坑位或审核状态证据缺失都返回 `SELECTOR_INVALID`，不得解释为零素材或零空坑位。

素材阶段按以下顺序执行：

1. 先创建或复用精确时间戳会话并启动配置页；用户提交 setup handoff 后，才把自动发现的商品表、规则表复制为本次任务输入快照。本分支不导出或审查基础素材。搜推素材必须使用 `supplement --scan-mode high-value` 选择“商品分类 → 搜推高价值”，不传 `--max-pages`，串行遍历全部分页并把结果写入当前任务目录。完成环境与商品表预检。只有商品表读取失败、缺少/重复必需表头等 schema 或批次级错误、以及空表才停止 raw indexing。`MISSING_PRODUCT_ID`、`INVALID_PRODUCT_ID`、`DUPLICATE_PRODUCT_ID` 是行级 blocked：在 `scan-summary.json` 留下 source row 与 reason codes，只排除对应行的 ID/SKU/名称匹配，其余有效行继续；非空但全部行为行级 blocked 时仍完成纯 metadata 索引。
2. 文件夹索引由同一 Plugin 内的 `$maintain-team-folder-index` 管理。商品选择处理器在生成候选前必须使用配置页已校验并保存的 `team_folder_index_root` 执行共享快照同步；固定团队 UNC 仅是首次默认建议，当前电脑保存的可访问目录优先于该默认值。路径不可访问、需要 Windows 认证或没有有效来源时，把完整度阶段保持为 blocked，页面明确引导用户返回任务配置选择团队索引文件夹；保存后工作台刷新同一处理器运行时并幂等重试原 `process-product-selection`，不得要求重新提交商品选择。Plugin 不得传递、请求、记录或保存 SMB 凭据，也不得用低层命令静默连接共享。校验最新有效不可变快照后，把可移植的 `folders.csv` 缓存到 `folder_index_root/team-cache`。当前配置的路径派生 `source_id` 没有快照、但存在 canonical 路径身份键相同的旧 ID 快照时，必须把最新有效旧快照复制发布为新 ID 的不可变快照并保留全部旧快照；只有完全没有等价快照时，才自动只读遍历该素材源的目录元数据、建立本机目录索引并发布第一份不可变快照，无需再次索取授权。已有有效共享快照时不得启动 `index-folders`、生成新快照或刷新 NAS；只有用户明确要求更新某个已有索引的素材源时，才可按 `$maintain-team-folder-index` 契约运行 `--refresh-source SOURCE` 或 `--refresh-prefix SOURCE=RELATIVE_PATH`，确认完整后显式 `publish`。共享目录暂时不可访问时允许使用最后一次校验通过的本机 `team-cache`，并在任务扫描摘要中明确记录 `origin=local_cache`；快照哈希、从 canonical source 派生的路径身份键或本机绑定路径身份键不一致时必须阻断。不得扩大刷新范围、修改其他机器快照或自动删除历史版本。商品表、名称、货号或匹配器变化时只从缓存的文件夹元数据重新匹配，不重扫 NAS。
3. 第二阶段商品选择正式提交后，工作台后台调度器只调用一次 `process-product-selection` 正式处理器。该唯一入口必须校验并领取精确 completeness handoff，使用当前任务商品快照、当前匹配器版本和已校验的 `team-cache/**/folders.csv` 即时匹配所选商品，原子生成任务级 `folder-candidates.csv`、扫描摘要和 `folder-review.json`，写回 completeness 完成结果与 asset-matching review context，并推进到素材匹配。全局 `folder_index_root/folder-candidates.csv` 即使存在也属于历史派生文件，禁止读取或作为回退。禁止再由 Codex 手工串联 `snapshot-folder-candidates`、`prepare-folder-review`、`result.json` 或阶段状态。任务目录不得包含 `folder-index.sqlite3`。任务摘要必须绑定商品快照 SHA-256、匹配器版本和来源 snapshot ID；入口必须幂等，已完成的同一 revision/SHA 只补齐或返回既有转换，不重复生成 revision。异常时必须在 `02-completeness/product-selection-diagnostic.json` 写入失败 phase、稳定 reason code、异常类型与消息、session/stage/revision/input SHA、相关文件存在性/大小/SHA、traceback 和同一入口恢复动作；能安全绑定当前 handoff 时把 completeness 写成 `blocked` 并清除 processing claim。Codex 读取该诊断、修复索引或处理器后，只能重跑同一入口，不得手工拼接半成品。该入口只读文件夹索引，不枚举或读取 NAS 图片。文件夹匹配优先使用商品 ID、完整货号和规范化商品基础名称；匹配基础名称时忽略末尾的“（主）/（副）”。除此之外，可将与基础名称具有至少 50% 最长公共连续字符、且公共连续部分不少于 5 个字符的文件夹作为粗略候选，但不得据此自动确认归属。若用户明确给出完整文件夹名，可用 `prepare-folder-review --exact-folder PRODUCT_ID=FOLDER_NAME` 做当前任务的一次性精确查询；不得把该查询写入别名表或自动复用于其他任务。页面必须先展示商品 ID、货号、来源、命中类型、文件夹名和完整路径；ID、货号和完整基础名称候选默认“采用”，粗略候选默认“排除”，用户筛选后再读取采用文件夹中的图片；决定写入当前时间戳会话的 `folder_decisions`。
   基础名称包含 `/`、`／`、`、` 或 `|` 时，只拆成去重后的字面片段并分别检查文件夹名是否完整包含该片段，禁止拼接公共前缀、补词或推导组合名称。少于 3 个字符的片段忽略；5 个字符及以上的完整片段候选默认采用，3–4 个字符的完整短片段候选默认排除。原有 50% 粗略候选仍按未拆分基础名称计算，且继续默认排除。
专用 handoff 必须只有一个领取者：正常流程只允许当前精确 session 的 `workbench-dispatcher` 领取；旧兼容 `listen-handoff`、`resume-session`、`wait-handoff` 和页面恢复接口只返回经过验证的身份，不得提前领取或与调度器竞争。completeness 只路由到 `process-product-selection`，`handoff_kind=final_material_selection` 只路由到 `process-final-material-handoff`。首次完整度审查尚无正式提交记录时按钮显示“提交给工作台”；只有已提交结果被退回补充时才显示“补充后重新提交”。

`source_types`、`folder_decisions`、`license_decisions` 和 `asset_decisions` 是素材匹配页
内部结构化状态，只能由文件夹与选图业务控件自动维护；不得向用户展示原始字段或 JSON
文本框。隐藏这些技术控件不得改变草稿恢复、自动保存、校验或 handoff 内容。

4. “素材匹配”是同一阶段内的两步流程。`folder_review` 页面展示目录元数据；文件夹列表下方、候选图片上方的“确认文件夹并加载图片”保存决定、创建 `queued` gallery job，并自动启动一次性素材执行器，不经过通用阶段提交处理器、不创建 handoff、不领取 Agent 租约，也不需要在聊天回复“已提交”。启动必须委托给现有 Explorer shell，不能直接从 UI/Codex 创建继承其令牌的子进程。执行器认领任务后，使用当前电脑 `config/local-paths.json` 中相同 `source_id` 的本机根目录拼接已校验的 `relative_path`，再枚举图片、回填递归素材数、生成预览与候选；业务数据不得依赖固定盘符或 RaiDrive 虚拟 UNC 绝对路径。去除盘符或 UNC 服务器部分后具有相同路径身份键的本机路径可以绑定同一 source ID；旧任务中的历史 source ID 可通过本机历史绑定解析为当前路径派生 ID。旧索引可在执行器中用绝对路径做一次兼容识别，新索引和新决定必须使用稳定标识。`folder_review` 和 `gallery_preparing` 时页面底部不得再显示阶段提交按钮。选图页原位置按钮为“确认选图并提交给工作台”；单图勾选已完成保留原格式的预裁剪并缓存能力，提交按钮只复核缓存身份、重复项、每商品至少 3 张以及共同可行比例，然后创建 `final_material_selection` handoff。工作台后台调用 `process-final-material-handoff` 校验最终素材包并继续确定性坑位编排，不重新扫描文件夹。采用文件夹不等于采用图片，刷新页面不得自动保存或增加 revision。候选上限按商品独立计算为 100 张；枚举扩展名后必须先对全部唯一图片路径执行一次只读 `stat`，只有 200KiB–20MiB（含边界）的图片进入硬合规池，小于下限、大于上限或元数据不可读的图片不得读取、哈希、解码或生成预览。硬合规池内以 500KiB–15MiB（含边界）为优先层，200KiB–不足 500KiB 为小图补充层，大于 15MiB–20MiB 为大图补充层；每商品目标数为 `min(100, 硬合规图片总数)`。先为每个非空采用文件夹分配 1 个覆盖名额，文件夹内依次优先使用优先层、小图补充层和大图补充层；优先层数量足够时，无优先层图片的文件夹仍可使用补充层保留覆盖。其余名额先在优先层按各文件夹剩余数量比例分配，仅对不足部分依次使用小图补充层和大图补充层，不得因优先层不足而把整批退回旧式混合抽样。若单商品合规非空文件夹超过 100 个，仍保持 100 张上限，按稳定文件夹顺序确定覆盖范围并明确报告无法完整覆盖的文件夹。每个采用文件夹都保留发现数、硬合规数、三层图片数、预筛排除数、覆盖名额、比例余量、各层抽样数、总抽样数和零名额原因。系统以当前任务、商品、稳定文件夹身份和策略版本执行任务内稳定伪随机抽样；同一任务输入不变时结果不变，新任务可重新抽样。页面每批最多显示 30 张，超过 30 张时启用“换一批”，最后一批按实际余数显示。只对抽中的最多 100 张读取、校验、计算 SHA-256 和生成预览；运行中分别显示发现路径、大小预筛合规与跳过、计划检查、已检查、检查失败、内容重复、最终候选和待处理数量，完成后最终候选必须等于已检查减内容重复。结果写入当前任务的 `confirmed-gallery.json`，不得建立全量图片数据库。旧会话缺少新计数字段时只显示“历史进度口径”，不得从旧 `prepared_count` 猜测最终候选数。
   同一原图必须在一次源读取和一次解码中完成哈希、尺寸检查与任务预览；每个商品使用一个固定并发队列，所有合规候选统一占用一个工作槽，最多同时使用 8 个工作线程，不再按文件大小或格式降低并发，完成结果仍按抽样序号确定性合并。每累计 10 张有序结果立即以 `gallery_preparing` 渐进发布，页面仍按每批最多 30 张分页；后一批可以在前一批慢文件完成前进入受控调度，不得为每个发布批次重建线程池或形成读取屏障。全部候选完成前只允许浏览，不开放采用、预裁剪或最终提交。检查结果和预览只可在当前时间戳任务内通过 `gallery-checkpoint.json`、`partial-gallery.json` 按 gallery identity、源文件大小和 mtime 断点复用；身份或源事实变化时重算，不得建立跨任务本机缓存。checkpoint 最多累计 10 张或 1 秒批量落盘，并在每个渐进批次及任务结束时强制保存；工作台进度最多每 0.75 秒持久化一次，但批次和最终状态不得丢失。目录枚举必须先按扩展名过滤并使用 `os.scandir` 单次遍历目录项元数据；同一商品采用的父子或相同物理文件夹只递归遍历其最上层覆盖根一次，再按既有文件夹排序投影递归数与图片归属；大小预筛只对每条唯一路径执行一次必要的 `stat`，不能再执行不必要的 `resolve` 或重复 `stat`。技术结果必须记录目录枚举、源文件元数据、NAS 读取、哈希、解码、预览、checkpoint 与进度持久化耗时，供稳定异常诊断使用，不得直接暴露给业务用户。
   页面计数必须使用三个固定名称：点击前为“文件夹内素材（递归统计）”，点击后为“本轮进入候选检查”和“最终可选素材”；不得再用含义不明的“已准备”混合原始路径数、计划检查数和最终候选数。检查成功数不包含检查失败，最终可选素材等于检查成功减内容重复。
5. 人工审查并排除错误的完整名称候选、同货号不同名称文件夹，再逐文件选择本次采用素材；采用图片时同步生成本次授权。文件夹自身名称中的完整 SKU 可为 `matched_unlicensed`；历史 `pending` 文件夹按默认采用读取，新提交不得继续保存 `pending`。
6. 搜推素材实时采集完成后，运行 `tmall-materials inspect-completeness --products <商品表> --promotion-status <promotion-material-status.csv> --output <任务目录>/02-completeness/completeness-matrix.json`。只有“搜推高价值”采集结果中的商品进入第二阶段，商品表只补充名称、货号和负责人（“运营”列），不得扩展商品范围。把 JSON 写入当前 revision 的 `result.json.data`，页面展示搜推素材目标/已有/缺失篇数、候选素材状态、负责人和后台证据。用户可搜索、按状态或负责人筛选、逐项或批量选择商品；提交后从 `02-completeness/input.json.values.selected_product_ids` 读取下阶段商品范围，禁止要求用户直接编辑 JSON。
7. 完成素材完整性可视化审查后，再进入生产选择器、全量 dry-run、1–3 商品生产验收，以及文档/发布状态更新。

可复制的文件夹索引、`--refresh`、`--rematch-only` 与可选全量图片索引命令见 [operations-guide.md](references/operations-guide.md)。raw folder indexing 不要求月份、店铺或坑位；这些值只在后续 eligible、完整性和生产阶段使用。原始 NAS 文件保持只读，视频继续延期。索引器只生成审查候选和扫描状态：不生成批准清单，不执行 dry-run、上传或发布。

### 第二阶段唯一处理入口

`completeness` 阶段拥有专用处理器后，正常流程只由 `workbench-dispatcher` 领取并调用
`process-product-selection`。`resume-session`、`listen-handoff`、旧兼容 `wait-handoff` 和页面
`recover-processing` 只能返回经过验证的恢复身份，不得领取或替换该阶段 processing claim。
启动恢复扫描发现过期租约或可恢复状态时仍调用同一处理器，禁止先运行通用恢复命令再
运行专用处理器。

### 素材可视化选择

推广素材状态采集和文件夹归属审查完成后，默认使用 `tmall-materials prepare-confirmed-gallery` 将当前 `input.json` 的采用文件夹与 `promotion-material-status.csv` 合并为任务级候选 JSON。采用文件夹只授权生成候选，不自动采用其中图片。第三阶段不计算“必须选择的图片总数”，用户可从每商品最多 100 张候选中人工采用任意数量；提交时每个商品必须至少有 3 张预检通过且源 SHA-256 唯一的图片，后台缺失篇数只作为上下文。第五阶段按 `K=min(后台缺失坑位, floor(有效唯一采用图片数/3))` 创建坑位，最多使用 `min(有效唯一采用图片数,K*9)` 张图片，并为每个坑位安排 3–9 张、统一为 3:4 或 1:1 的图片；超出容量的采用图片进入未使用候选池，本次任务不要求填满后台全部空坑位。硬合规图片超过 100 张时执行文件夹覆盖优先、500KiB–15MiB 优先、仅按缺口补充小图和大图、层内按剩余数量比例分配的任务内稳定伪随机抽样。只有显式离线审计场景才使用 `prepare-gallery` 从 `asset-index.sqlite3` 生成候选。

首次进入“素材匹配”阶段时沿用任务配置的图片根目录，由本地页面服务从本机共享文件夹索引生成当前商品候选。`source_types` 固定规范化为 `["image"]`，不要求用户填写。页面先执行文件夹归属审查，只提供“采用 / 排除该文件夹”二态决定并保存到 `folder_decisions`：确定性候选默认采用，50% 连续名称粗略候选默认排除，用户确认后才能采用；全部排除时不得加载图片。排除文件夹必须立即从同商品画廊移除其候选，并同步取消来自该文件夹的已选素材与本次授权；重新采用未被本轮画廊准备的文件夹时必须重新加载图片，不恢复旧选择。过滤后必须重新计算候选数、30 张分页、页码和换批按钮。保存文件夹决定时只校验 `image_roots` 为非空路径列表；随后由页面服务启动的本地 Worker 在相同 Windows 身份下检查实时可访问性。不可访问或身份变化时写入稳定 gallery-job 错误并提供“重试加载图片”，不得创建 Codex handoff。画廊必须绑定 `session_id/stage_id/prepared_from_revision/prepared_from_input_sha256/folder_decisions_sha256/prepared_folder_keys`；陈旧或跨任务画廊不得用于最终提交。`needs_user_input`/`blocked` 的素材匹配结果必须保存为只读 `review-context.json`。页面展示缩略图、来源、匹配方式和单一“采用”选择；勾选“采用”表示当前采用意图，预裁剪通过后才确认该图片可用于本次发布并同时写入 `asset_decisions` 和对应的 `license_decisions`。取消采用必须立即撤销当前意图和既有决定，不能等待后台图片处理结束；后端必须按最终文件夹决定再次过滤并规范化授权记录。新图片候选必须携带稳定 `folder_id` 和 `folder_path`；历史候选缺少 `folder_id` 时按最长规范化父路径关联，无法关联时保留并标记。候选准备时把最长边不超过 640 像素的 JPEG 预览写入当前任务 `03-asset-matching/preview-cache/`，页面不得直接传输共享盘原图；旧任务缺少预览时按需生成。预览使用短时私有缓存，选择图片时不得重建整组图片卡片。

组合标题的完整字面片段属于确定性名称候选：不少于 5 字时默认采用；3–4 字时标记为短名称片段候选并默认排除。文件夹名完整包含商品基础名称时仍按名称候选处理；不得从拆分片段生成任何新的组合名称。

本地重复使用 SHA-256 排除，同一任务内同一图片不得跨商品重复选择。只有提供后台已有图片指纹时才可声称远端去重完成；后台仅提供素材 ID 而没有图片指纹时，页面必须显示“远端去重未完成”，最终上传前继续保持人工核对门禁。完整名称候选在文件夹归属确认前不可选择，逐文件授权未确认的候选也不可选择。

完成素材阶段后：

1. 阅读 [business-rules.md](references/business-rules.md)、[data-schema.md](references/data-schema.md) 和 [asset-requirements.md](references/asset-requirements.md)。
2. 通过 `tmall-materials supplement --scan-mode high-value` 扫描“搜推高价值”全部分页并读取实时 DOM；目标容量、当前篇数、远端素材 ID 和可见状态必须来自同一页面证据。搜推页“导出数据”若保留，只能标记为经营指标。
3. 用户在第二阶段选择商品后，以 `selected_product_ids` 作为后续唯一商品边界；月度规则可用于后续文案或业务校验，不得重新扩展或替换该边界。
4. 默认运行 `tmall-materials supplement` 扫描“搜推高价值”的全部分页，每页增量写入 CSV 和 checkpoint；只对目标容量不明确、解析失败或状态异常的商品，再带 `--scan-mode exact --candidates supplement-candidates.csv` 按精确商品 ID 补采。`--scan-mode recommended` 仅为旧证据和恢复命令保留。
5. 带 `--backend-status`、已人工确认的素材配置和 AI 文案响应再次运行 dry-run，生成两级任务和 `review.html`。
6. 用户在“上传任务确认”页选择精确 task ID 并提交后，该提交即为所选任务的正式发布授权。工作台后台调度器用本 Plugin 的用户级运行环境调用唯一 `process-publish-authorization` 处理器；该入口自行校验并领取 approval handoff，确定性生成或复用发布批次，生成不可变 `approval-manifest.json`，执行发布并把逐任务结果写回原阶段。不得借用其他项目或 Conda base 的环境运行，不得再手工拼接 bridge/approve/publish 命令，也不得等待第二次生产确认。

在交互页面中，上述第 5 步由第四阶段提交自动触发，不再要求用户单独点击 dry-run。上传任务确认页必须同时展示商品、目标坑位、图片数量、批准文案和非阻塞警告；所有可授权任务首次进入时默认勾选，用户取消勾选即表示本次不上传该任务，刷新或状态更新不得重新勾选已取消项。页面只允许当前仍勾选的精确 task ID 进入授权，并只提供一次“提交并自动上传所选任务”；提交后不再显示独立生产确认页。正式上传前仍须在千牛当前页面实时复查目标坑位为空，自动 dry-run 不得把历史页面状态当作发布时事实。
7. 正式图片上传必须使用项目内 `browser/qianniu_upload.py` 中从 PlaywrightAuto 迁移的千牛搜推流程：精确搜索商品 ID、定位批准的 1-based 空坑位、进入“发图文”跨域表单、把批准的最终图片以 `publish-<SHA前缀>-<随机后缀>.<扩展名>` 短唯一名称上传到素材库、按该名称唯一选中、核对选择数量，再填写已批准标题和正文。正文编辑器须兼容普通 textarea/contenteditable 与 `textarea[data-cangjie-dockey]`。最终按钮优先按千牛发布专用语义 `data-autolog*=publisher_ok_clk` 唯一定位；只有明确“提交发布/发布”文字才可作为兼容入口，禁止按通用“确认/确定”文字点击。发布前保存该商品全部既有 `CopyId_value` 集合；点击一次后重新读取同商品，只有旧集合完整保留且恰好新增一个 ID 才记录成功。千牛会把新素材前插，禁止按原 1-based 位置推断新远端 ID。出现二次确认、点击超时、基线变化或新增 ID 不唯一时进入 `publish_uncertain` 并暂停批次。PlaywrightAuto 目录只作为迁移来源，生产运行不得依赖该外部目录。
8. 上传中断后运行 `tmall-materials resume`；已有远端证据的任务不会重复上传。使用 `tmall-materials report` 重新生成中文报告。

运行当前 Plugin 的 `scripts\run-plugin.cmd --help` 查看参数。用户级 `.venv` 只安装
Python 与 `uv.lock` 锁定的第三方依赖，不安装或复制 `upload_search_materials`
业务包；Plugin 更新后的业务代码和页面资源因此无需再次 bootstrap 即可生效。
所有命令从本 Plugin 目录执行；首次使用运行 `scripts/bootstrap.cmd`。启动器支持官方源及显式选择的 HTTPS 镜像、用户级缓存、Python 3.11 和锁文件一致性检查；只有开发测试才传 `-WithTests`，只有明确切换锁文件来源才传 `-UpdateLock`。

首次使用先按 [operations-guide.md](references/operations-guide.md) 完成安装、CDP 浏览器启动和六阶段命令。所有时间参数必须是带时区的 ISO 8601。

## 交互式任务执行

1. 先解析用户指定的精确 `session_id`；不得默认选择 `runs` 中最新的会话。
2. 只有新任务才创建以时间戳命名的隔离会话目录；恢复时显式复用原 `session_id`。
3. 任务配置页要求店铺确认、一个团队索引文件夹和图片源配置；不提供目标月份、商品范围或搜推采集页数，也不提供账号密码或“连接共享盘”模块。商品表、规则表和运行目录只读展示；没有可用的已保存团队索引路径时，页面只枚举 Windows 当前可见盘符并精确检查 `盘符:\浙江酷趣\天猫部\搜推素材索引-虾米`，不得递归扫描盘符。唯一命中只自动回填，提交时才保存；多处命中或未命中仍由用户选择、检测并保存。用户在“本次图片源”中新增、删除、检测并保存任意 1–50 个本机目录、映射盘或 UNC 来源，人工素材清单与历史文件只放在高级设置。
4. 启动仅监听 `localhost` 的交互页面。
5. 第二阶段先将“搜推高价值”全量结果与商品表合并，并自动排除标题或等级命中 `uvno`、`积分`、`清仓`、`好物体验`、`会员日` 的商品。排除项保留在页面和结果中供审计，但不可选择；用户提交其余商品后直接进入第三阶段“素材匹配”，不再设置独立的“维护范围确认”阶段。
6. 托管工作台为当前精确 session 启动单一后台调度器。每次正式提交完成原子持久化后立即唤醒队列；服务启动时先扫描已存在的 `ready_for_agent` handoff、租约已过期的 `processing` handoff，以及 `pending` / 可恢复的 `processing` 文案请求。调度器空闲时每 2 秒执行一次轻量恢复扫描，不建立 `agent_wait`，不依赖 Codex 长轮询或聊天在线状态。
7. 验证 `session_id`、`stage_id`、`revision` 和 `input_sha256` 与当前 `input.json` 全部一致。
8. 将该阶段标记为 `processing`。
9. 只执行该 `stage_id` 允许的动作。
10. 只通过项目的 Python `SessionStore.write_result`/正式 CLI 以 UTF-8 写入与同一组 `session_id`、`stage_id`、`revision` 和 `input_sha256` 绑定的 `result.json`；不得用 PowerShell 管道、重定向或命令行字符串拼接直接生成含中文的 JSON。`summary` 或 `next_action` 出现连续三个问号时必须以 `RESULT_TEXT_ENCODING_INVALID` 拒绝发布。
11. 根据结果停止，或明确进入下一阶段。

提交前允许编辑并在停止输入约 1 秒后自动保存草稿；草稿不得生成 handoff 或唤醒工作台后台。每次草稿或正式提交携带在重试期间保持不变的 request ID，并通过版本化 stage transaction 原子推进 input、revision snapshot、handoff、session state 与审计事件。中途失败后读取状态或重试原请求必须幂等修复；相同 request ID 但内容不同返回 `REVISION_CONTENT_CONFLICT`，不得删除或覆盖既有证据。正式提交后冻结该阶段的全部配置。只有状态仍为 `ready_for_agent`、尚未被后台调度器认领时，用户才能显式撤回并继续修改；`processing` 和 `completed` 禁止覆盖。`needs_user_input` 或 `blocked` 才重新开放输入。每次草稿和正式提交都保存到阶段目录的 `revisions/<revision>/`，活动 `handoff.json` 只代表当前可认领提交。

正式提交前，页面必须按当前阶段 schema 检查全部必填业务字段；缺失时不得发送提交请求，必须显示字段级业务提示、平滑滚动到首个错误位置并把焦点移入对应控件。后端仍须保留相同校验；后端返回 `field_errors` 时前端必须执行同样的错误展示、滚动和焦点恢复，不得只在固定底栏显示 `validation failed`。自定义列表或卡片字段应定位到其可见业务控件，错误节点保持 `aria-live`，不得暴露底层字段名或英文校验文本。

恢复已有 `session_id` 时，页面必须先读取 `session.json` 状态并直接进入有效的 `current_stage`，同时一次性显示全部阶段的真实状态。只打开、刷新、水合图片决定或构建默认坑位不得标记 dirty、自动保存或增加 revision。草稿保存进行中收到“提交给工作台”时必须明确显示已排队，并在保存成功后使用最新 revision 继续提交；失败或阶段切换时必须明确暂停或取消，禁止静默丢弃点击。查看已完成阶段时显示锁定原因和“进入当前阶段”，不得重新启用写入。

图片源行和团队索引设置都提供“选择文件夹”，仅由用户点击后通过已验证 Windows 桌面身份打开本机原生目录窗口并回填完整路径。选择器使用单实例 STA 助手及 request ID、ownership token、PID 身份和私有结果通道，必须区分选择、取消、启动失败、GUI 不可用、超时和无效返回，并保留原输入及手工填写兜底。窗口无法证明可见时返回 `FOLDER_PICKER_NOT_VISIBLE`，只结束本次 helper。手工输入继续用于 UNC、远程或无界面环境。选择目录和“检测路径”都只能读取目录元数据，不得枚举或读取图片。页面必须区分“保存为本机配置”“保存草稿”和“提交给工作台”。

页面必须以统一的 `task_status` 持续显示整个任务的当前阶段、业务摘要、下一步动作、阶段进度，以及后台调度器的“已就绪、排队、处理中、完成、异常”状态。页面每 2 秒读取同一状态；`ui-status` 同步返回该 `task_status`，供 Codex 在启动、恢复、用户追问或稳定异常时说明任务状态。Codex 不通过命令轮询模拟实时监听，也不得为了状态汇报重新读取源码、运行目录或 handoff。工作台服务运行时，页面提交可直接唤醒后台；服务停止后，恢复同一精确 session 会自动扫描未完成任务。不得要求用户在聊天输入“已提交”，也不得把旧兼容 `agent_wait` 租约显示成后台在线状态。

只有“上传任务确认”阶段完成且已经持久化逐任务上传结果，才是整个工作流的终态。终态出现后，页面显示结果与关闭倒计时，受管工作台继续运行 10 分钟后自动关闭 Python 服务；任务目录、阶段结果和审计记录不得删除。搜推高价值全量采集完成、商品选择完成、候选生成、选图或 dry-run 完成都只是中间状态，绝不能触发自动关闭。恢复一个已完成的精确 session 时，可以重新启动工作台并获得新的 10 分钟结果查看窗口。

恢复指令必须包含 `runs_root`、精确 `session_id`、`stage_id` 和 revision，并要求校验 `handoff.json.input_sha256`；禁止按目录新旧猜测 session。前一阶段未写入 `completed` 结果时，不得提交下一阶段。

“上传任务确认”页只产生一次 `publish_authorization` handoff，不直接在 HTTP 请求线程中执行外部写入。该 handoff 中的精确 task ID、批准人和 `final_confirmation=true` 同时授权工作台后台调度器调用唯一 `process-publish-authorization` 入口；入口内部顺序执行批准与发布，当前简化流程不设置授权有效期。旧 `production_confirmation` 仅供历史会话读取，不得用于新任务，也不得再次向用户索要确认。真正授权来自页面这次提交，聊天文字或调度器恢复扫描都不是授权来源；素材改变后，旧批准永远不得授权发布。可复制的启动和恢复命令见 [operations-guide.md](references/operations-guide.md)。

## Safety Contract

### 跨电脑路径解析

- 不得把用户名、桌面绝对路径或某台电脑的盘符写入 Skill 逻辑。启动时按 `--config`、`TMALL_CONFIG_FILE`、用户数据目录 `config/runtime.json`、旧版项目内 `config/local-paths.json` 的顺序读取本机配置；本机配置不得提交到仓库。
- 未显式配置商品表或规则表时，从程序包的 `src/upload_search_materials/docs/` 分别按 `天猫商品信息表*产品数据表*数据总表.csv` 和 `天猫商品信息表*每月推品规则*Grid View.csv` 查找。仅唯一命中时自动采用；零命中标记 `missing`，多命中标记 `ambiguous`，不得猜测最新文件。
- 共享图片目录从前端配置页读取，并以稳定 `source_id + label` 引用；实际盘符或 UNC 只保存到用户数据目录的每机 `config/runtime.json`。`source_id` 必须由图片源路径身份键确定性生成：路径先执行 Unicode NFC、大小写归一和分隔符归一；盘符路径去掉 `Y:` 等盘符，UNC 路径去掉 `\\服务器` 部分，保留其余层级，再取 SHA-256 的前 12 位并加 `source-` 前缀。来源名称不参与 ID，因而“图片源 3”“视觉部模特图”“公司模特图”只要根目录相同就得到同一 ID。旧配置中的任意 ID 必须保存在不含凭据的历史绑定中，只作为旧任务兼容别名；配置页删除后再次添加相同路径身份键时直接得到同一新 ID，不再依赖历史身份数量。映射层级不同仍会得到不同路径身份键，不得猜测或自动改写层级。不得扫描盘符或假设所有电脑都映射为 `Y:`、`Z:`。`canonical_unc` 只作为可选建议与团队快照的二次校验来源，不是生成本机 ID 的必填项；可选保存最后验证身份/时间，但不得保存凭据、目录清单或图片内容。必须至少配置 1 个名称与路径均非空且不重复的来源。
- NAS 原图只能由素材执行器读取。页面通过 `gallery-job.json` 排队并自动请求桌面启动；执行器必须在能访问本机挂载的用户会话中运行，校验 source ID 和相对路径，拒绝绝对路径、盘符、`..` 与目录逃逸，只把任务所需预览和校验元数据写回会话目录。每个按钮任务启动一个一次性进程，任务结束即退出，不注册系统服务。启动失败记录 `MATERIAL_EXECUTOR_LAUNCH_FAILED`；挂载不可用返回 `SOURCE_BINDING_MISSING`、`SOURCE_ACCESS_DENIED` 或 `SOURCE_PATH_INVALID` 并保留用户决定。手工 PowerShell/shell 启动脚本只用于开发诊断，不得作为日常用户步骤。Skill 不得自动建立网络盘映射、挂载共享、获取或保存 NAS 凭据，也不得绕过共享权限。
- 配置页不得展示或自动检测 `config/nas-sources.yaml` 中的公司共享目录，也不得提供“连接 NAS”按钮。用户应先在 Windows/Explorer 中获得可用的 Y 盘、Z 盘或 UNC 路径，再直接从“本次图片源”选择或粘贴；工作台只对最终填写路径执行只读元数据检测，不自动认证、映射或挂载。共享定义和诊断命令只供开发排障，见 [nas-sources.md](references/nas-sources.md)。
- `--runs-root` 优先；否则使用 `TMALL_RUNS_ROOT` 或本机配置；均未提供时使用用户数据目录下的 `runs/`。所有任务继续按时间戳目录隔离，不写入 Plugin 安装缓存。
- 团队快照根路径可由 `TMALL_TEAM_FOLDER_INDEX_ROOT` 显式覆盖；否则使用配置页保存到本机配置的 `team_folder_index_root`，未保存时才使用固定 UNC 默认建议。自动发现仅对当前可见盘符拼接固定相对路径做一次有总时限的目录元数据检查，不得枚举盘内目录、触发认证、建立映射或直接写入本机配置。NAS 身份使用 `team_folder_index_nas_source_id`。本机索引缓存路径优先使用 `TMALL_FOLDER_INDEX_ROOT`，其次使用 `folder_index_root`，默认使用用户数据目录下的 `cache/folder-index/`。该长期缓存只保存已校验的团队 `folders.csv` 快照及同步元数据，不保存可复用商品候选；每个时间戳任务单独即时生成并保存当前商品候选快照。
- 本机配置格式和环境变量见 [operations-guide.md](references/operations-guide.md)。

- 不保存或输出密码、Cookie、Token、短信码、二维码登录数据。
- 同一个 `asset-index.sqlite3` 同一时刻只能由一个 Agent 或进程运行 new、`--resume` 或 `--refresh`；禁止并发索引同一数据库。
- 同一素材源发布共享快照时必须取得短时跨机器发布租约；每次发布只创建新快照并原子更新 current pointer，不覆盖其他机器已发布的快照。普通上传任务可自动发布缺失的第一份有效快照，无需再次索取口头授权；已有有效快照时只同步和读取，不获得刷新或再次发布权限。
- 文件夹索引是默认入口：只保存目录元数据，不读取、哈希或统计所有图片。确定性候选默认采用，粗略候选默认排除；用户完成文件夹筛选后，才按需读取采用文件夹中的图片。
- 组合商品标题只按 `/`、`／`、`、` 或 `|` 拆分字面片段；不少于 5 字的完整片段候选默认采用，3–4 字的完整短片段候选默认排除，少于 3 字忽略。禁止拼接或补全片段。
- 文件夹决定必须绑定 `folder_id + product_id + source_system + folder_path`；排除文件夹时必须同步移除其候选、采用和授权决定，后端提交边界再次检查一致性。
- 索引时只读声明的原始图片 roots，不修改、移动或删除源文件；视频不进入本阶段。
- 不调用未公开的天猫内部 API，不绕过登录、验证码、扫码、短信、风控或权限。
- 当前店铺与目标店铺不一致：整批进入 blocked，任何任务都不得搜索或发布。
- 批次 blocked 是运行门禁：保留各 item 原状态并标记 held，不把坑位任务强改成不存在于其状态机的 blocked；店铺恢复正确并重新核验后再决定是否解除门禁。
- 批准清单总 SHA-256 必须覆盖 run ID、店铺、输入哈希、批准人和全部 item；发布前重算输入及媒体文件哈希。口头“全部发”不能替代批次清单。
- 单个 item 的媒体、标题、描述、动作或坑位变化：只撤销该 item 的批准。店铺、schema、manifest 总哈希或批次输入哈希变化：阻断整批。
- 单项批准撤销时从 `approved` 退回 `ready_for_review`，记录 `APPROVED_CONTENT_CHANGED`，重新生成该项内容哈希并重新批准；其他未变化 item 保留批准但可被批次门禁暂时 held。
- 发布按钮每个 item 最多点击一次。点击后结果不可信时，该 item 进入 `publish_uncertain`，同时暂停批次并先回查。
- 远端“不存在”只有在正确店铺、精确商品 ID、目标坑位、批准指纹和提交时间窗口均完成可信查询后成立；重新提交仍需显式执行决定。
- `publish_uncertain` 确认远端不存在后，原 task 仍不得再次点击；如决定重提，创建新 task ID、新批准清单并再次显式执行。
- 页面选择器失效时返回 `SELECTOR_INVALID`，不得把缺失元素解释为 0 个素材或空坑。商品 eligibility 保持原判定，受影响坑位进入 `needs_manual_review`。

## State Contract

商品任务：`discovered → excluded|eligible|blocked → ready_for_review → approved → uploading → partially_completed|completed|failed`。

坑位任务：`pending_validation → needs_manual_review|ready_for_review → approved → uploading → submitted|publish_uncertain|failed → under_review|success|failed`。

`publish_uncertain`、`submitted`、`under_review`、`success` 或带远端素材 ID/证据的任务不得回到自动上传队列。

## Completion Output

必须报告输入与 SHA-256、月份、店铺、eligible/excluded/blocked/approved/submitted/success/failed 数量、人工任务及原因、远端素材 ID/审核状态、输出目录，以及本次是否停在 dry-run 或执行了已批准发布。
