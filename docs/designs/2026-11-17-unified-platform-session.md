# 统一平台登录态获取设计

> 2026-11-21 更新：模型可见入口统一为 `execute(target="workspace", ...)`；
> 文中 `workspace_shell` 仅指历史工具名。

状态：已按当前代码实施，部署与真实平台验收仍需在目标环境完成。
日期：2026-11-17。
当前核对源码：主工作区 ff950a79；本地 Runtime 容器也保留了按 platforms 条件注入的实现。工作区存在其他未提交改动，本设计不修改这些文件。

## 1. 要交付的用户效果

用户已在平台配置凭据后，无论在普通对话中临时写代码、执行工作区命令、运行已有 Skill、创建并试跑草稿、还是由 Worker 执行后台任务，都能通过同一入口发现并获取当前身份可用的平台登录态。

用户无需选择“Skill 沙箱”“草稿沙箱”，无需提前给 Skill 添加 platforms 才能获取登录态，也无需安装 Python 包、提供 Redis 地址或手工拷贝 Cookie。

统一的是平台访问能力和调用契约。已有 Skill、草稿副本、用户工作区继续保持各自的文件、版本和回写语义；不会为了统一登录态而把它们合并成一个目录。

普通对话的模型通过现有 execute 执行命令；模型本身不直接读取 Cookie。脚本、宿主平台适配器、原生 MCP 连接按各自协议消费同一 Session 服务。任意外部浏览器、SSH 机器、用户自行启动的进程不自动获得身份，不能宣称“任何环境无条件可用”。

## 2. 当前事实与缺口

| 当前实现位置 | 当前行为 | 必须改变的行为 |
| --- | --- | --- |
| Runtime skill_tools.workspace_shell | 未传 platforms 时走无 Session 分支 | 始终装配 Session 入口，缺省范围由 Console 解析 |
| agent-core draft_shell | 只有草稿声明 platforms 才创建平台上下文 | 草稿不声明也可发现和获取登录态 |
| Skill 沙箱执行器与 skill_bridge | context 为空或 platform_keys 为空时不注入命令 | 已绑定执行身份即可注入；空平台集合返回业务结果 |
| Runtime executor._skill_platform_context | 初始 platform_keys 为空 | 不用空元组同时表示“自动发现”和“禁止访问” |
| Worker executor / agentic_skills | 平台候选依赖 Skill platforms | Worker 与 Runtime 使用相同的范围与服务契约 |
| session_cli | 必须提供匹配本次执行的 skill-name | Skill 名不再承担身份认证与平台选择职责 |

此前测试传入了平台声明或 platforms 参数，证明了这些特定路径能获取 Session，没有覆盖“不声明平台、自然对话中途引入平台”的核心路径。统一 execute 工具名不等于统一 Session 能力。

## 3. 唯一架构与责任划分

```text
对话 / Skill / 草稿 / 后台任务
        ↓ 可信执行装配（一次绑定身份与生命周期）
ExecutionPlatformContext
        ↓
ExecutionSessionBroker ── Console PlatformAccessService
        ↓                     平台目录 / 授权 / 凭据选择
PlatformSessionService
        ↓
Redis Session Manager ── Platform Adapter（验证、登录、刷新）
        ↓
脚本临时 Session 文件 / 宿主适配器内存 Session / MCP 认证配置
```

- Console 是平台可见性、可访问性和凭据选择的唯一权威，复用现有 USER_ONLY、SHARED_ONLY、USER_THEN_SHARED、NONE 规则。
- PlatformSessionService 是获取、校验、复用、刷新 Session 的唯一业务实现。Runtime 与 Worker 不各自实现登录流程。
- ExecutionSessionBroker 负责把当前执行的可信身份绑定到一次调用，并将服务结果转换为本次执行可消费的引用。
- CLI 只做协议客户端，不连接 Redis、不解析用户凭据、不自行登录。
- 现有 Redis Session 键隔离、凭据版本和刷新锁继续复用。不得按 Skill 或草稿另建第二套登录缓存。

实现时把现有 SkillPlatformContext、SkillPlatformAccess、skill_platform_environment 重构为执行级名称和职责；所有生产调用点一次性迁移。旧 Session 登录实现被替换，不保留两套后端或根据执行类型切换登录实现。

## 4. 身份与平台范围

ExecutionPlatformContext 包含 tenant、actor_user、agent、可信 execution_ref（RUN 或 TASK）、invocation 标识、截止时间和 scope。身份来自持久化 Run/Task 与宿主装配，脚本不能通过命令参数切换用户、租户或任务。

平台 scope 显式区分：

- AUTO：不指定平台，按当前执行身份发现全部可用平台。
- SELECTED：显式指定平台集合，仅收窄当前执行范围。
- NONE：宿主明确禁止平台访问，入口仍在，调用返回明确拒绝。

空集合不再兼任上述三种语义。SELECTED 的空集合为参数错误；AUTO 在当前无平台时返回空目录。

平台声明语义统一：

| 输入 | 含义 |
| --- | --- |
| 未声明 platforms | AUTO，可发现并按需使用当前身份的平台 |
| Skill / 草稿 / execute 显式声明非空 platforms | SELECTED，在授权范围内收窄 |
| 宿主或管理策略明确禁用 | NONE，不能由模型参数绕过 |

显式声明不授予权限。多层范围同时存在时取交集；模型参数不能扩大宿主、Agent 或任务已有的限制。声明不存在或无权的平台时返回具体业务错误，不悄悄切换到其他账号。

AUTO 是惰性解析策略，不在每次普通 Shell 命令开始时认证所有平台。平台目录只提供名称、稳定 key、认证能力与当前配置可用状态，不返回 Cookie、AK/SK、凭据引用、Redis key 或未经脱敏的 resolver 配置。缺凭据标记为不可用，不在“可用”列表中冒充可登录。

Run/Task 冻结身份、执行策略和显式限制；每次 get/refresh 由 Console 重验当前授权与凭据版本。执行中撤销权限立即对后续获取生效。AUTO 能发现后续新增的已授权配置；SELECTED 不因目录变化扩大范围。

## 5. 对外唯一命令契约

选择沿用 session-manager 作为所有脚本环境的唯一命令名，不再另加 platform_workspace_shell 或按沙箱类型命名的获取工具。

```sh
session-manager list-platforms
session-manager get-state --platform mssp
session-manager refresh --platform mssp
session-manager --help
```

- list-platforms：仅列目录，不触发平台登录，也不把登录失败伪装成“目录为空”。
  > 2026-11-20 修订：`list-platforms` 已增强为按本次范围实际检查登录态，`available` 不再只表示“有凭据配置”；实现与验收见 `.code-flow/tasks/2026-11-19/platform-session-reporting/`。`get-state` / `refresh` 的契约保持不变。
- get-state：获取一个指定平台可用 Session；若缓存有效则复用，否则受控验证、刷新或登录。
- refresh：明确请求刷新，不得忽略参数后仍返回复用成功。使用平台适配器声明的刷新能力；不支持时明确返回不支持，不能猜测 flag 或声称已经强制刷新。
- 单次 get-state/refresh 必须指定稳定 platform key。仅有一个可用平台也不暗中猜选；模型通过目录确定 key。
- 一个平台失败不阻止另一个平台被单独获取；不再一次性对所有候选平台登录。
- --help、list-platforms 和命令定位在没有平台时也必须能工作。

统一成功响应（示例值仅说明结构）：

```json
{
  "version": 2,
  "platform": "mssp",
  "authenticated": true,
  "sessionStateFile": "/run/platform-session/state/current.json",
  "expiresAt": "<server timestamp or null>",
  "resolution": "reused",
  "refresh": {"requested": false, "performed": false}
}
```

resolution 使用稳定枚举 reused / authenticated / refreshed。刷新竞争中若复用另一调用刚完成的刷新结果，返回 refreshed，并明确 performed=false 与满足刷新请求的依据；不得通过伪造 authenticated=true 掩盖过期。

临时状态文件使用唯一规范结构：version + platforms（以稳定平台 key 为索引的对象）。每个平台包含 expiresAt、cookies、requestProfile，必要时包含 storageState。CLI stdout 只返回引用和状态元数据，不输出 Cookie 正文。SDK 提供唯一解析器，业务 Skill 不各自兼容数组和字典两种结构。

无认证平台（credential_mode=NONE）也可出现在目录中，但应标记 authentication=none；获取结果明确返回 authenticated=false、认证材料为空及可用请求配置，不把“无需登录”伪装为“已登录成功”。状态文件中无有效 Cookie 不必然意味着失败，需结合平台认证模式判定。

历史 CLI 的 get-state --skill-name 用法全部盘点：仓库代码、文档、样例及已安装制品。产品第一方调用全部迁移到明确平台 key；需要保留导入旧制品能力时，只允许在导入步骤做可审查转换，不能在运行时静默走另一套登录入口。未转换的旧制品报明确迁移错误，不能重新出现 command not found。

## 6. 惰性获取、校验与刷新

get-state：重验身份和平台授权 → 解析当前凭据版本 → 查共享 Session → 判断可用性 → 必要时在分布式锁下再次检查并登录 → 生成本执行临时文件。

不能只凭“有 expiresAt 字段”或“文件存在”判断成功。已过期、凭据版本不匹配、适配器验证失败均不能返回有效 Session。没有可靠过期时间的平台使用适配器的验证策略，不能推断为永久有效。

refresh：记录请求开始时观察到的 Session 版本 → 在现有分布式刷新锁下重验 → 若其他调用已产生更新且有效的 Session，则返回该刷新结果；否则执行适配器支持的刷新/重新认证 → 更新缓存并验证 → 返回结果。刷新失败不得回落到旧失效 Session 报成功。具体 Session 代际字段由缓存契约所有者定义，实现前锁定并纳入并发测试。

业务请求的鉴权失败：通过宿主平台客户端执行的请求按明确鉴权失败信号刷新一次并重试一次；第二次失败明确结束。原始脚本自行使用 requests/httpx 发出的请求不会被后台神奇重放，脚本调用 refresh 后重新获取文件，再自行重试；统一文档和 SDK 明确这条边界。普通业务错误、限流与权限拒绝不得当成密码失效反复登录。

登录/校验/刷新全部使用同一宿主出站执行身份、目标解析、预算和审计链路。关闭出站限制不应决定入口是否注入；开启时返回实际策略错误，不允许回退到不受管控的登录请求。

## 7. 所有执行入口的装配

| 场景 | 身份来源 | 必须提供的能力 |
| --- | --- | --- |
| 日常对话临时编写、运行脚本 | 当前 Run | execute workspace 默认 AUTO，命令始终存在 |
| 已有 Skill 同步执行 | 当前 Run / invocation | 相同 CLI、文件契约和错误语义 |
| 创建并试跑未发布草稿 | 当前 Run 与草稿所有者 | 无 platforms、草稿未完整时仍可查询目录和获取；不依赖成功解析 SKILL.md 才注入 |
| 异步 Skill、定时任务、批量子任务 | 持久化 Task 的 actor 与执行引用 | Worker 重建相同服务与 CLI，不能沿用父进程临时路径 |
| bwrap 等正式沙箱 | 宿主绑定身份 | 挂载 launcher、CLI、Unix socket 与临时状态目录，设置 PATH 和 socket 变量 |
| dev-unsafe 调试执行 | 同一可信上下文 | 使用本次临时路径，行为与正式沙箱一致，不依赖宿主全局安装 |
| 宿主平台适配器 / Agent 平台工具 | 同一执行上下文 | 直接调用 PlatformSessionService，复用授权与缓存实现 |
| 原生 MCP 服务器 | 宿主 MCP 连接配置绑定的平台与认证能力 | 显式认证适配器调用同一服务；不假设每个 MCP 都能消费 Cookie |

所有脚本入口使用同一个 ExecutionEnvironment 工厂装配 Session 能力。目标执行器只负责文件挂载、cwd、版本回写和超时，不判断是否允许注入 Session CLI。Runtime 与 Worker 都调用该工厂。

草稿未写完或无 platforms 时使用 AUTO；合法非空声明收窄范围；声明显式存在但格式错误时返回元数据校验错误，不静默等同于没有权限。目录/登录态探测与草稿是否可发布是不同判定。

原生 MCP 必须逐个绑定其支持的认证类型：Cookie、Header、Token 或连接级凭据。对于要求 OAuth 的 MCP，需实现对应授权流程，不能把平台 Cookie 当成 OAuth Token。支持的平台桥接实现和重连/刷新测试随本次交付；缺少协议映射的连接返回 MCP_AUTH_UNSUPPORTED，不算“已支持”。本期不设计自动接管用户浏览器个人登录状态。

## 8. 生命周期与隔离

每次实际命令/Skill 执行都有独立 invocation 绑定和 broker；Run 与后台 Task 不共享临时授权句柄。长期工作区保持文件，但不会保存 broker socket 或永久凭据。

入口生命周期覆盖整次实际执行。子进程继承本次环境；再次调用 execute 创建新环境，不能使用前一次 socket 或 Session 文件路径。执行结束、超时、取消、租约丢失和崩溃回收时关闭连接、清理临时材料；共享 Redis Session 不因此被删除。

沙箱只挂载执行期目录，不向脚本暴露 Console 内部服务令牌、Redis DSN、原始用户凭据或宿主全局环境。可信 broker 负责平台解析与登录。容器、远程 Worker 的 socket 与状态文件必须在实际消费该文件的进程可见范围创建，不能回传 Runtime 主机路径给其他 Pod。

Session 不在启动普通对话时预先导出全部平台 Cookie。按需获取降低外部平台故障对普通命令的影响。没有平台、Redis 暂不可用时普通 ls/python 命令仍可执行；只有真正请求 Session 时返回相应错误。

## 9. 错误与可观测性

错误响应统一为 version + error.code + message + retryable，返回非零退出码；不返回成功形状的空 Session。

| 情况 | 行为 |
| --- | --- |
| 无任何已配置可用平台 | list 返回空列表；get 指定平台返回平台不可用 |
| 平台不存在、禁用、无凭据、无权限 | 明确分类；不得输出其他用户信息或伪造命令缺失 |
| broker 缺失或本次绑定已结束 | PLATFORM_EXECUTION_CONTEXT_UNAVAILABLE |
| Redis / Console 不可达 | 分别报告存储或解析服务不可用，标记可重试性 |
| 平台验证/登录失败 | 返回脱敏诊断，不返回旧失效文件 |
| refresh 不受支持 | PLATFORM_REFRESH_UNSUPPORTED |
| 未支持的执行后端或认证映射 | 准备阶段显式报能力不支持，不能转成“沙箱都没有登录态” |
| CLI 未注入、PATH 缺失、socket 未挂载 | 执行装配缺陷，测试必须失败，不作为正常产品边界 |

审计记录执行身份、入口类别、平台 key、复用/登录/刷新结果、耗时和稳定错误码；不记录 Cookie 或凭据正文。可区分“未调用入口”“没有注入入口”“授权失败”“平台登录失败”，不能仅靠模型自然语言推断。

## 10. 模型使用说明与产品文案

在所有可执行脚本的工具说明中注入同一段平台能力说明：环境始终提供 session-manager；先 list-platforms，按目录 key 获取；需要明确刷新时调用 refresh；不得读取 Redis、全局找 Cookie 或安装同名包替代。

平台目录以安全元数据供模型发现；目录过大采用查询/分页，不能为了节省提示词裁掉后就宣称平台不存在。模型无需知道“草稿沙箱有没有 session-manager”，只需选择工作区执行或草稿试跑来匹配文件操作。

用户可见回复区分配置、获取、刷新、业务访问结果。模拟成功不能写成真实平台登录成功，命令存在不能写成登录成功。

## 11. 一次性交付的实现清单

1. contracts：确定 scope、目录、获取/刷新、响应、错误与状态文件的唯一版本契约。
2. Console：增加按可信 RUN/TASK 查询的可用平台目录；复用现有凭据选择及授权服务，获取时重验。
3. SDK：重构执行级 Session 服务与 broker；提供 list/get/refresh；去除平台集合为空即跳过注入的判断。
4. 执行环境：统一 launcher/PATH/socket/目录注入，接通 workspace、Skill、draft、Worker 和 agentic Worker；不遗漏进程恢复和子任务。
5. MCP/宿主工具：盘点所有现有接入点，将已支持认证方式接入同一服务；列出不支持映射的明确结果及测试。
6. 一次性迁移第一方 Skill、提示词、CLI 样例与集成文档；清理旧“只在 Skill 里存在”等描述。现有第三方制品导入转换给出逐项报告，无法转换的清晰报错。
7. 补齐下述验收证据；构建并更新用户实际在使用的整套本地服务，核对制品 revision。不能只在隔离测试环境跑过就声称本地部署已生效。

以上各项属于同一个需求的交付范围。若某项未实现或验证失败，不把整个需求标为完成；PR 中逐项记录已验证与未验证。实施前将本设计转为一份任务清单，各项对应具体文件和测试，不再只用“统一 execute”作为完成标准。

## 12. 验收矩阵与完成门槛

所有脚本入口先验：未传 platforms、未声明 Skill 平台时，command -v session-manager、--help、list-platforms、get-state 均符合契约。存在平台声明的路径是额外收窄测试，不能替代默认路径。

| 维度 | 必测内容 |
| --- | --- |
| 全入口 | workspace / 同步 Skill / 未声明平台的草稿 / 异步 Task / 定时任务 / 批量子任务 / agentic Worker |
| 全后端 | 本地 dev-unsafe、Linux 真实 bwrap、实际 Docker 部署；远程执行的路径在远端验证 |
| 平台配置 | 0 / 1 / 多平台；用户凭据、共享凭据、优先级；NONE 认证模式；启停及权限撤销 |
| Session | 首次登录、有效复用、已过期、无过期时间、凭据轮换、强制刷新不支持、刷新失败与并发刷新 |
| 中途使用 | 先执行无关命令，再自然要求查询平台；新调用自动具备入口，不要求重开聊天 |
| 草稿开发 | 尚无完整 SKILL.md、无 platforms、合法限制、错误声明；能先获取平台信息再编写集成逻辑 |
| 进程边界 | 同次子进程可用、下次调用重建、超时/取消后失效、Worker 重启不复用旧路径 |
| 真实业务 | 使用已配置平台获取 Session 后发一条只读业务请求，确认返回真实数据；不能仅断言文件存在 |
| 出站策略 | 开启与关闭时入口一致；允许登录和业务成功；拒绝时有明确审计；预算耗尽不走旁路 |
| MCP | 现有支持的认证映射获取和刷新成功；不支持的明确失败；不以 Shell 成功冒充 MCP 成功 |
| 用户身份 | 不同用户/租户互不串用；平台声明不能扩大权限；后台身份不能由脚本参数替换 |

自然语言测试提示示例（不指定工具或获取方法）：

- “先帮我看看工作目录里有什么，再查一下我配的平台有哪些目标。”
- “写个新 Skill，统计 MSSP 最近的目标数量，先试跑，暂时别发布。”
- “运行这个没声明 platforms 的查询 Skill。”
- “刚才可能复用了旧登录态，明确刷新后再查一次，并告诉我是否真的刷新。”
- “把刚才的查询交给后台执行，结束后给我结果。”

每条保留：实际 execute 目标、可信执行引用、broker 调用、Session 处理结局、真实业务响应断言、Run/Task 终态和用户实际收到的回复。模型说“拿到了”不作通过依据。

最终完成需要：契约测试、所有入口回归、真实 bwrap 与 Docker 验证、用户已配置平台的真实只读业务验证、自然语言场景验证及部署 revision 核验全部形成证据。若目标平台或某执行环境无法访问，明确标记未验证，不以模拟平台、Mock、dev-unsafe 或某一入口成功替代。

## 13. 当前设计结论

保持一个 execute 执行工具和一个 session-manager 获取入口。执行环境始终注入该入口，平台目录与授权由 Console 决定，登录/复用/刷新由执行级 Session 服务统一负责。平台声明只收窄范围，不决定能力是否存在。脚本、宿主工具与 MCP 共用服务，但分别验证各自真实消费链路。

本文件完成的是目标设计，不代表代码、部署或真实登录态验收已经完成。
