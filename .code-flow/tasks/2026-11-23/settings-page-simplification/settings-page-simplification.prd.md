# PRD：系统设置页精简（技术调参下沉部署配置）

> 版本 v0.2 · 2026-11-27 · 评审中

## 1. 文档控制

v0.2 基于 2026-11-27 评审反馈修订：保留 Agent 执行全部 5 项；后台任务与 Agent 执行共用同一套 agent-core 预算；记忆/学习/产物/MCP/并发容量/网页聊天/定时调度按已认可方向精简；为下沉值提供**完整部署配置 + 内网/生产两套取值**；明确 IM 进度只作用于 IM 渠道；模型池调度移出本需求。

口径：

- 当前 schema 为 **15 个分组 / 101 个叶子**（`compaction` 已是 4 个叶子，见已归档的 `conversation-context-retention`）；Console `build_groups` 按 schema 全量派生。
- 本需求处理其余 14 个分组的 97 个叶子；最终整页 **14 个分组 / 58 个叶子**（含 compaction 4），非压缩 **54 个叶子**。
- **模型池调度（`model_pool`）不在本需求范围**：5 个叶子维持现状，后续单独优化模型接入。
- 本需求不再采用“技术调参一律进代码常量”的口径：运营可能需要按环境调整的值进入部署配置（`SharedSettings` + ConfigMap/Secret），只有协议/算法不变量留在代码。

## 2. 背景与目标

### 2.1 问题陈述

| 维度 | 内容 |
|------|------|
| **问题描述** | 系统设置页同时承载租户业务策略、部署容量、重试/退避/租约/轮询/分页/重连、内部评估阈值等不同层级的值。管理员找不到真正需要决策的开关，误改一个内部参数又可能影响全租户；开发和评审也要为大量不面向用户的字段维护 schema、i18n、迁移和测试。 |
| **当前替代方案** | 靠分组/字段说明和运维口口相传；没有“业务策略 vs 技术调参”的硬边界；部署 ConfigMap 只覆盖 34 个键，内网实际运行时大量参数只能依赖代码默认，排障和调优不可见。 |
| **触发原因** | 内网真实任务频繁触发 Agent 回合/工具调用上限，证明 Agent 执行预算必须保留为租户可调项；同时多轮需求持续把实现常量迁入平台设置，设置页继续膨胀。 |

### 2.2 目标与价值

| 维度 | 内容 |
|------|------|
| **核心目标** | 系统设置页只保留租户级业务策略与安全/配额边界；技术调参进入**完整的部署配置契约**，并为内网/生产提供独立取值；Agent 与后台任务共用同一套 agent-core 执行预算。 |
| **预期价值** | 页面叶子数 101 → 58；管理员可理解、可审计；运维拿到一份可直接部署的全量配置，而不是靠代码默认；新增需求不再把内部常量塞进租户设置。 |

**成功指标**

| 指标 | 当前值 | 目标值 | 衡量方式 |
|------|--------|--------|----------|
| 系统设置页总叶子数 | 101 | 58（含 compaction 4） | schema 字段计数 + Console catalog 契约测试 |
| 非 compaction 分组叶子数 | 97 | 54 | 同上 |
| Agent 执行字段数 | 5 | 5（全部租户可调） | schema/catalog 精确字段集 |
| 后台 Agentic 任务执行预算来源 | 平台默认 + 任务自有预算 | 与 Agent 执行同一份冻结策略 | 快照/预算契约测试 |
| 部署配置覆盖 | 34 个 ConfigMap 键 | 全部非敏感 `SharedSettings` 键 + 内网/生产 overlay | 配置契约测试 + 渲染清单比对 |
| 下沉值隐藏默认 | 未知 | 0（每个被删字段有环境键或明确不变量归类） | 字段清单逐项核对 |
| 旧设置文档/Agent 覆盖迁移后读取失败数 | 未知 | 0 | 真实 PostgreSQL 迁移 + 设置读取 + 新 Run/任务准入 |
| 保留字段行为回归 | — | 0 处非预期变化 | 分组级 consumer 测试 + 人工场景 |

## 3. 用户与场景

**US-01** 作为租户管理员，我希望系统设置页只出现我能决策的业务策略（预算、配额、保留期、安全开关），不要让我面对重试次数、租约、轮询、评估阈值这类实现细节。

**US-02** 作为运维，我希望有一份**最全的部署配置**：所有非敏感环境键都有明确取值，并且内网与生产可以各自定义，而不是在代码默认和残缺 ConfigMap 之间猜。

**US-03** 作为开发，我希望内部算法不变量留在代码里，但运营可能调整的参数有唯一环境键和默认值，不需要为它们维护租户设置 schema/i18n/迁移。

**US-04** 作为评审/测试，我希望设置页和部署配置都有可机检的“保留字段/环境键清单”，新增或删除时能自动发现漏改的 consumer、词条、文档和 ConfigMap。

**US-05** 作为平台使用者，我希望后台定时/Agentic 任务与交互式 Run 使用同一套 Agent 执行预算，不会出现“同一租户两套上限”。

**US-06** 作为验收负责人，我需要在内网/UAT 用真实的 MSSP/MSSW 平台、真实模型和真实企微跑通用户旅程；dev 环境只能用 fake 平台验证契约，不能代表真实用户感受。

## 4. 功能需求

**FEAT-01 · 字段分类基线**（P0，US-01、US-02、US-03）

- [ ] 对其余 14 个分组的 97 个叶子给出唯一分类：`保留`、`下沉部署配置`、`合并`、`删除`；`compaction` 已是 4 个叶子（`conversation-context-retention` 已完成），本需求不重复分类。
- [ ] `保留` 只允许租户级业务策略、安全边界、硬配额、保留期、功能开关。
- [ ] `下沉部署配置` 用于运营可能按环境/规模调整的值（并发、超时、租约、重试/退避、批大小、资源配额、客户端传输参数、算法阈值），必须进入 `SharedSettings`、`.env.example` 和完整部署配置。
- [ ] `合并` 只允许同一语义的两个旋钮收敛为一个；迁移不得缩短保留期或降低租户已有上限。`memory` 的注入条数/字节上限**不合并成布尔开关**：新增 `memory.injection_enabled` 作为总开关，原上限继续保留，避免把租户预算静默改成全局默认。
- [ ] `删除` 只允许全仓无 consumer 的死字段；本需求只删除 `learning.signal_detection_enabled`。
- [ ] 分类表必须覆盖其余 14 个分组的全部 97 个叶子，不能留“待定”；`compaction` 4 个叶子沿用已完成结果，不在本需求分类表中重复。

**FEAT-02 · Agent 执行预算保留与后台任务统一**（P0，US-01、US-05）

- [ ] `agent` 分组 **5 个叶子全部保留**：`max_turns`、`max_tool_calls`、`deadline_ms`、`max_model_retries`、`max_identical_failures`。不得改成“无上限”或代码固定值；内网真实任务触发上限时由租户管理员调高。
- [ ] 交互式 Run 与后台 `AGENTIC` 任务共用同一套 `PlatformSettings.agent` 预算；任务快照/`SnapshotBudget` 只允许从这份策略派生，不得再出现 Worker 自有的回合/工具/模型重试默认。
- [ ] 修复当前 `max_identical_failures` 的冻结缺口：新 Run 的 `policy_json` 必须冻结全部 5 个 Agent 叶子；旧快照缺键时按 schema 默认解释，不按当前租户值补写。
- [ ] 后台 Agentic 任务内的 Skill 工具调用超时不得超过 Agent 预算期限；直接 `SKILL` 任务（无模型/工具循环）仍由 `task.execution_timeout_sec` 限制单次子进程执行。
- [ ] 模型调用、工具调用、失败重试、重复失败终止全部走同一套 `agent-core` 规则；Worker 不得另写执行循环或第二套失败判定。

**FEAT-03 · 已认可分组精简**（P0，US-01、US-02）

- [ ] `memory` 21 → 11：保留写入开关、新增注入总开关、注入条数/字节、推断注入、任务线索注入、单用户条数、三个 TTL、提炼总开关；召回字节/默认条数、单用户总字节、提炼 8 个内部调参下沉部署配置。
- [ ] `learning` 14 → 1：只保留 `learning_enabled`；删除无 consumer 的 `signal_detection_enabled`，其余检索/试验/评估/验证参数下沉部署配置。
- [ ] `artifact` 3 → 1：只保留 `retention_days`；归档文件数上限与清理批大小下沉部署配置。
- [ ] `mcp` 1 → 0：单服务工具数上限下沉部署配置；MCP 连接参数继续由 MCP 页面管理。
- [ ] `capacity` 4 → 3：保留租户/用户未结束 Run 上限与队列等待上限；模型等待上限下沉部署配置。
- [ ] `webchat` 16 → 8：保留访问/会话 TTL、文本/上传大小、每消息附件数、通知/历史/会话保留；上传暂存、分页、轮询、重连、流重放保留、每入口会话上限下沉部署配置。
- [ ] `schedule` 9 → 6：保留配额、最小间隔、每日 Fire/Agentic、并发、合并后的历史保留期；Snapshot 大小上限与确认时效下沉部署配置。
- [ ] `task` 7 → 4：保留默认截止、单次 Skill 超时、最大尝试、批次并发；错过宽限、投递尝试、投递退避下沉部署配置。
- [ ] `im` 4 → 2：保留回复进度节拍与单批附件上限；入站静默窗口/最长等待下沉部署配置。
- [ ] `agent` 5 → 5、`auth` 4 → 4、`locale` 2 → 2、`egress` 2 → 2 不变。
- [ ] `model_pool` 5 → 5 不变，单独需求处理。

**FEAT-04 · 完整部署配置与内网/生产取值**（P0，US-02、US-03）

- [ ] `deploy/k8s/base/configmap.yaml` 覆盖**全部非敏感 `SharedSettings` 键**；Secret 键（数据库/Redis/内部令牌/外部断言/Webhook 签名）只出现在 Secret 模板，不得进 ConfigMap。
- [ ] 新增 `deploy/k8s/overlays/intranet/` 与 `deploy/k8s/overlays/production/`：在 base 之上定义各自取值；渲染后的 ConfigMap 不允许依赖代码默认值来补齐“应该有值”的键。
- [ ] 新增配置档案文档（如 `docs/configuration-profiles.md`）：逐键列出默认值、内网值、生产值、消费服务、是否敏感；与 `.env.example`、`SharedSettings`、ConfigMap 四方一致。
- [ ] 生产容量相关值必须与锁定的 capacity profile 一致，不得在 ConfigMap 里另写一套未经压测的数字。
- [ ] 下沉值在本需求中全部有明确归属：42 个新环境键；无“留在代码但没有任何文档”的运营参数。
- [ ] 环境键缺失时使用 `SharedSettings` 默认并记录 warning；不得回落到平台设置里的旧字段或服务私有默认。

**FEAT-05 · IM 进度作用域澄清**（P0，US-01、US-04）

- [ ] `im.progress_interval_sec` 保留为租户设置，但只作用于经 IM Gateway 回复的 IM 渠道（当前为企微/远程 Connector 路由渠道）。
- [ ] WebChat 使用 SSE 流式事件与心跳，不读取 `im.progress_interval_sec`；设置页标签/说明必须明确“WebChat 不受此设置影响”，避免管理员误以为能调整网页聊天的进度节拍。
- [ ] `im_progress_updates_per_second` 仍是 IM 状态刷新的服务资源预算，留在环境，不与租户节拍混用。
- [ ] 若后续需要 WebChat 进度节拍，另立需求；本需求不新增 WebChat 等价字段。

**FEAT-06 · 迁移与回滚**（P0，US-04）

- [ ] 旧 `control.platform_setting.settings_json` 中的被删字段必须在迁移中移除；旧 Agent `runtime_config_json` 中的对应覆盖键同步清理。
- [ ] 新增 `memory.injection_enabled`：迁移按旧 `max_injected_memories > 0 && max_injected_bytes > 0` 计算；原上限逐字保留。
- [ ] `schedule.max_fire_history_days` / `max_execution_history_days` 合并为 `schedule.history_retention_days`：取两者较大值，只允许延长保留期，不得缩短；写入迁移审计。
- [ ] 迁移追加新 revision + 审计，不原地改历史 revision；旧 revision 恢复时按“schema 过旧”显式拒绝。
- [ ] 开发阶段允许清空 `control.platform_setting` 与 Agent 覆盖后重建；预发/生产必须执行迁移。
- [ ] 回滚方案与迁移方向一致：旧代码读到新文档必须被明确拒绝，或提供反向迁移；不得声称“只回退镜像”。
- [ ] `compaction` 已由 `conversation-context-retention` 迁移到 4 个字段；本需求迁移只处理非压缩旧键，不改写压缩字段，并以回归测试保证压缩分组仍为 4 项。

**FEAT-07 · Console、词条与文档同步**（P0，US-04）

- [ ] Console 设置目录按新 schema 派生；页面不再出现被删字段；分组标签与单位保持完整；字段数为 0 的 `mcp` 分组不渲染。
- [ ] `im.progress_interval_sec` 的标签改为“IM 回复进度节拍”，说明中写明只对 IM 渠道生效。
- [ ] 删除对应 i18n 键，新增的环境键说明进入配置档案文档而不是设置页。
- [ ] `docs/configuration-inventory.csv` / `docs/configuration-inventory.md` 重新生成；`python-constant` 行按 AST 对齐，新增 `shared-setting` 行。
- [ ] 设置页契约测试断言每个分组的**精确字段集合**与总叶子数 58，防止“前端隐藏、schema 还在”。

**FEAT-08 · 守卫与回归**（P0，US-04）

- [ ] 每个保留字段至少有一个 consumer 测试或 API 契约测试；每个下沉字段有默认值等价测试。
- [ ] 配置契约测试覆盖：`SharedSettings` 字段集 == `.env.example` 键集；base+overlay 渲染后的 ConfigMap == 全部非敏感键；Secret 键不出现在 ConfigMap；内网/生产 overlay 的值能通过 `SharedSettings` 校验。
- [ ] Agent 统一预算测试覆盖：同一租户设置下，交互式 Run 与后台 Agentic 任务冻结的 5 个叶子完全一致；修改设置只影响新 Run/新任务；`max_identical_failures` 不再回落默认。
- [ ] 迁移测试覆盖：旧文档含全部被删字段、Agent 覆盖含旧键、记忆总开关计算、调度保留期合并、迁移后读取/保存/新 Run/新任务准入均成功。
- [ ] 删除字段的 consumer 同步删除/改写；不得留下读不到的死代码分支。
- [ ] 新增 `tests/<domain>_inventory.py` 覆盖本需求的字段分类、测试与场景。

**FEAT-09 · 真实用户场景验收**（P0，US-06）

- [ ] 验收分两层：dev/CI 只用 fake MSSP/MSSW 做契约、迁移和 UI 回归，明确不作为“真实感受”验收；内网/UAT 必须接真实 MSSP/MSSW、真实模型、真实企微和真实用户数据跑完整旅程。
- [ ] 内网/UAT 的用户面配置必须与生产一致：Agent 执行预算、记忆/学习、IM/WebChat、定时调度、平台通道、模型接入、功能开关等；基础设施配置（Redis seed、副本数、资源上限、容量 profile、Secret）可以按环境不同。
- [ ] 配置档案增加 `user-facing` / `infrastructure` 分类；`user-facing` 键在内网与生产之间默认必须一致，差异必须进入显式 allowlist 并写明原因。
- [ ] 租户级平台设置提供 production-like 基线（`deploy/profiles/platform-settings.production-like.json`），由 schema 校验；内网/UAT 与生产按同一份基线配置，验收记录保留实际值 diff。
- [ ] 真实场景验收至少覆盖：MSSP/MSSW 登录与入口绑定、流式对话、停止、上传/下载、历史/通知、企微回复进度、后台 Agentic/Skill 任务、Agent 上限调整后的新 Run/新任务。
- [ ] 验收证据记录 run id、平台请求 id、页面截图、关键日志与生效配置；不得记录 Secret 明文。
- [ ] 生产发布门禁：没有内网/UAT 真实场景验收记录，不得把“dev 通过”当成上线依据。

### 4.2 边缘情况

| 场景 | 预期行为 |
|------|----------|
| 旧 `settings_json` 含全部当前字段与被删字段 | 迁移后只保留新 schema；读取、保存、新 Run/新任务均正常 |
| 旧 Agent `runtime_config_json` 含被删键 | 迁移清理；其他 runtime_config 键逐字保留 |
| 旧记忆注入上限为 0 | 新 `memory.injection_enabled=false`；上限值保留但暂不生效 |
| 旧调度两类历史保留期不同 | 合并为较大值；只延长不缩短，迁移审计记录原值 |
| 旧 revision 被恢复 | 显式“schema 过旧”，不静默补默认 |
| 功能关闭（如 learning） | 只保留总开关；调参不在页面上；部署配置默认值仍生效 |
| 环境键缺失 | 启动时用代码默认并记录 warning；不得回落到平台设置里的旧字段 |
| 内网/生产 overlay 漏键 | 配置契约测试失败；不得靠代码默认发布 |
| 前端仍请求旧字段 | API 目录不再返回；前端契约测试变红；旧客户端按未知字段忽略 |
| 迁移中途失败 | 整事务回滚；旧最新 revision 仍可读；修复后重跑幂等 |
| 压缩分组已是 4 项 | 本需求迁移不改写压缩字段；最终 `settings_json` 仍满足压缩 schema |
| 管理员想调 WebChat 进度 | 设置页明确提示 WebChat 使用 SSE，不受 `im.progress_interval_sec` 影响 |
| 内网/UAT 真实 MSSP/MSSW 暂时不可达 | 真实场景验收标记 blocked，不得用 dev fake 平台结果替代；修复后重跑并记录 |

## 5. 非功能需求

| 指标ID | 类型 | 要求 |
|--------|------|------|
| NFR-PERF-01 | 性能 | 设置读取与保存路径不新增每请求 DB 查询；环境值在进程启动时读取并缓存，不引入热路径环境读取 |
| NFR-REL-01 | 可用性 | 迁移事务原子；失败不留下半迁移文档；设置读取 fail-closed 且可定位 |
| NFR-SEC-01 | 安全 | 删除/下沉不影响 auth、egress、配额、密钥边界；Secret 键不得进入 ConfigMap 或配置档案明文 |
| NFR-TEST-01 | 可验证性 | 字段分类、页面字段集、迁移、consumer 默认值、配置档案均可机检 |
| NFR-COMPAT-01 | 兼容性 | 本需求不做旧字段兼容解析；开发阶段允许清库，预发/生产需迁移与回滚方案 |
| NFR-PARITY-01 | 环境一致性 | 内网/UAT 与生产的 `user-facing` 配置默认必须一致；基础设施配置允许显式差异；真实平台验收必须在内网/UAT 执行 |

## 6. 范围与边界

| 类别 | 内容 |
|------|------|
| **范围（In Scope）** | `agent` 5 项保留与统一预算；`task`/`memory`/`learning`/`artifact`/`im`/`mcp`/`capacity`/`webchat`/`schedule` 的精简；42 个新环境键与完整 ConfigMap/内网/生产 overlay；`user-facing`/`infrastructure` 配置分层与 parity 检查；内网/UAT 真实 MSSP/MSSW 场景验收；设置/Agent 覆盖迁移；Console catalog/i18n/docs/tests |
| **非范围（Out of Scope）** | `compaction` 分组（已由 `conversation-context-retention` 完成，本需求只做回归）；`model_pool` 调度（单独讨论）；`auth`、`locale`、`egress` 的保留字段；新功能；模型级配置；前端页面重构为多级导航（如需要另立）；dev 环境真实平台接入（dev 使用 fake 平台） |
| **前置假设** | `compaction` 已稳定为 4 个字段；开发库可清空/重建；预发/生产有一次性迁移窗口；生产容量值来自锁定的 capacity profile；内网/UAT 具备真实 MSSP/MSSW 网络与凭据 |
| **已对齐的决策** | Agent 5 项全保留；后台 Agentic 与交互式 Run 共用 Agent 预算；已认可分组按精简方案执行；下沉值进部署配置而非隐藏代码；IM 进度只作用于 IM 渠道；模型池单独处理 |

## 7. 依赖与风险

**依赖**：`platform_settings.py` schema、`SharedSettings` / `.env.example`、`deploy/k8s` 配置与 overlay、Console 设置目录与前端页面、Agent `runtime_config_json`、`control.platform_setting` 版本表、i18n、配置清单与测试。

| 风险ID | 描述 | 影响 | 缓解方案 |
|--------|------|------|---------|
| RISK-01 | 下沉字段默认值与平台默认不一致 | 行为变化 | 逐字段记录当前默认并作为环境默认；加等价测试 |
| RISK-02 | 遗漏 consumer 或测试 | 运行时读不到字段/静默降级 | 字段表 + 全仓 grep + consumer 测试 + inventory |
| RISK-03 | 迁移遗漏旧 revision / Agent 覆盖 | 设置读取或新 Run/任务准入失败 | 迁移覆盖最新 revision 与 Agent 行；旧 revision 显式拒绝；真实 PG 测试 |
| RISK-04 | 配置档案与代码默认漂移 | 内网/生产行为不一致 | `SharedSettings` 单一默认源 + 配置契约测试 + 渲染清单比对 |
| RISK-05 | 42 个新环境键造成运维负担 | 漏配/误配 | base 全量键 + overlay 只写差异 + 配置档案逐键说明 + 启动校验 |
| RISK-06 | Agent 预算冻结缺口 | 后台任务使用默认值而非租户值 | 全部 5 叶冻结 + 统一预算测试 |
| RISK-07 | 迁移误改已稳定的压缩字段 | `settings_json` 不再满足压缩 schema | 迁移只删除非压缩键；压缩分组回归测试 |
| RISK-08 | 内网/UAT 与生产用户面配置漂移 | 内网测试通过但生产体验不同 | `user-facing` 键 parity 检查；差异 allowlist + 原因 |
| RISK-09 | 真实 MSSP/MSSW 凭据或网络不可用 | 真实场景验收阻塞，退回 dev 假平台导致误判 | 提前申请联调窗口与 Secret；blocked 场景明确记录，不以 fake 结果替代 |

## 8. Existing Spec Constraints

| Spec/Rule | 对本需求的约束 | 状态 |
|-----------|----------------|------|
| `api/harness-api#RULE-api-001` | 设置字段校验错误走统一封套与错误码，文案只来自配置 → FEAT-01/07 | applied |
| `persistence/harness-data#RULE-data-001` | 平台设置版本表追加行、审计与 JSONB 约束；迁移不改历史行 → FEAT-06 | applied |
| `frontend/harness-frontend` | 设置页只通过契约访问 API，不写死字段清单 → FEAT-07 | applied |
| `ui/harness-ui` | 设置页字段/分组展示与错误定位沿用现有组件 → FEAT-07 | applied |
| `i18n/harness-i18n` | 词条增删同步 zh-CN/en-US，禁止硬编码文案 → FEAT-07 | applied |
| `memory/harness-memory` | consolidation 的租约/超时/输入输出不变量仍成立；不得因下沉丢失约束 → FEAT-03/08 | applied |
| `worker/harness-worker` | Task 投递/调度默认值下沉后仍按部署环境与代码常量生效 → FEAT-03/04 | applied |
| `im/harness-im` | 入站批次行为不变；Connector/Gateway 仍从同一配置来源取值；进度节拍只作用于 IM 渠道 → FEAT-03/05 | applied |
| `test/harness-test` | 配置清单重生成、真实 PG 迁移测试、需求 inventory → FEAT-06/08 | applied |
| `arch/harness-arch#RULE-arch-001` | 四个部署单元边界不变；配置注入不建立第五个业务服务 → FEAT-04 | applied |

## 9. 评审反馈闭环

| 反馈 | 处理 |
|---|---|
| Agent 执行 5 项都要保留；内网真实任务一直触发上限 | `agent` 5 → 5 全保留；不改成无上限/代码固定；修复 `max_identical_failures` 未冻结、Run 发起任务未继承平台 Agent 预算、后台 Skill 超时未受 Agent deadline 约束三个缺口 |
| 后台任务执行 Skill 配置要与 Agent 执行统一，工具/模型都走一套 agent-core | 交互式 Run 与后台 Agentic 任务统一由 `AgentPolicy.from_runtime_config` 派生并冻结 5 项；直接 Skill 任务只保留子进程超时；Worker 不保留第二套默认 |
| 记忆/学习/产物/MCP/容量/网页聊天/定时调度精简方案认可；代码固定值要有一版生产配置，ConfigMap 要最全，并区分内网/生产 | 42 个下沉值全部改为部署配置；base ConfigMap 覆盖全部非敏感键；新增内网/生产 overlay 与逐键配置档案；不再把运营参数藏在代码 |
| IM 进度是否只管企微、不涉及 WebChat | 确认 `im.progress_interval_sec` 只作用于 IM Gateway 回复生命周期（当前企微）；WebChat 走 SSE，不读取它；保留字段但改标签/说明，避免误导 |
| 模型池调度单独讨论，后续重新优化模型接入 | `model_pool` 5 项维持现状，移出本需求范围 |
| 内网/生产配置是否一样；dev 连不上真实 MSSP/MSSW，无法给出真实感受 | 用户面配置（Agent 预算、记忆/学习、IM/WebChat、调度、平台通道、模型、开关）内网与生产必须一致；基础设施配置允许不同；真实场景验收必须在内网/UAT 接真实 MSSP/MSSW 执行，dev 只做 fake 契约回归 |

---

*文档结束*
