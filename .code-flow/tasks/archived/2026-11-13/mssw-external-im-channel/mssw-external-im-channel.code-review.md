# MSSW 外部 IM 通道 — 代码审查报告

- **审查对象**：`feat/mssw-channel` 分支上 jasonje 的提交（相对 `main` 的约 45 个非 merge 提交）
- **审查基准**：本需求目录下的 `mssw-external-im-channel.md`（任务文档，TASK-001~013）、`.backend.design.md`（v1.2 + §3.1.2 缓存定稿）、`.frontend.design.md`、`.prd.md`
- **审查范围**：Console（验签/身份/授权/失效）、Gateway（surface/认证缓存/长流复核）、Runtime（会话共享/通知水位/删除）、契约、部署、前端
- **审查日期**：2026-11-19（首轮）；2026-11-19 第二轮修复复核
- **审查人**：高级架构师（AI 辅助）

---

## 〇、待修复人确认清单（先看这里）

状态图例：✅ 已修复并复核通过 ｜ ⬜ 待修复人确认（文档/注释同步）｜ 🔵 观察点（不阻塞，择机处理）

**当前进度（第三轮，2026-11-19）**：P1-1~P2-6 全部 ✅ 已修复/已同步并经复核；仅剩 NEW-1（`write_permission_plain` 首写未封顶）、NEW-2（`channel=None` 兼容分支）、NEW-3（Linux CI 复跑）三个 🔵 非阻塞观察点。

| 编号 | 级别 | 问题摘要 | 当前状态 | 需修复人确认的动作 |
| --- | --- | --- | --- | --- |
| P1-1 | P1 | `channel` 参数 Gateway→Console 全链路未消费 | ✅ 已修复 | 确认 `resolve/describe` 的 `_check_channel` 测试覆盖；见 §6.1 |
| P1-2 | P1 | `grant_entry` 不校验被授权入口通道（跨通道越权） | ✅ 已修复 | 确认 `test_grant_rejects_entry_from_another_channel` 在 CI（真库）通过 |
| P1-3 | P1 | 屏障/revision/fence 已实现但 MSSWWEB 热路径未消费；TASK-006 正文不符 | ✅ 已修复 | TASK-006 正文改两态+plain 事实；`_try_cached_external` 补注"不消费屏障/版本/epoch"；见 §6.5 |
| P1-4 | P1 | `entry_agnostic` 在三个读端点硬编码 `true` | ✅ 已修复 | 三处调用点 + `list_conversations/get_conversation/list_messages` docstring 均注明 §API-05 设计意图；见 §6.5 |
| P2-1 | P2 | 权限层缓存到期未二次校验（会话到期后继续放行） | ✅ 已修复 | 见 §6.1；确认 touch 封顶测试 |
| P2-2 | P2 | 多平台 Secret 静默取首键 | ✅ 已修复 | **确认 5 条 P2-2 测试在 CI 触发**（本地无法全触发） |
| P2-3 | P2 | `PHASE_COMMITTED` 死常量 | ✅ 已修复 | 常量已删；周边 `control.py`/`invalidator.py` 注释一并改为两态；见 §6.5 |
| P2-4 | P2 | TASK-006 / TASK-010 正文与设计/代码不一致 | ✅ 已修复 | 任务文档已按代码事实修订（TASK-006 两态+plain；TASK-010 去掉 item 的 `channel`）；见 §6.5 |
| P2-5 | P2 | `_entry_id_of` 非受控 `ValueError` 回滚 Run 终态 | ✅ 已修复 | 见 §6.1 |
| P2-6 | P2 | Gateway `delete_conversation` 无 Agent 复核（设计意图） | ✅ 已修复 | `delete_conversation` docstring 已显式说明"不要求 Agent 当前授权是有意设计"；见 §6.5 |
| NEW-1 | 🔵 低 | `write_permission_plain` 首写未按 `expires_at` 封顶（读侧已兜底） | 🔵 观察 | 择机补封顶，或 docstring 注明"首写不封顶、读侧兜底" |
| NEW-2 | 🔵 低 | `channel=None` 后向兼容分支，本仓库 Gateway 恒不触发 | 🔵 观察 | 确认无跨版本灰度需求后可收紧为"必须声明" |
| NEW-3 | 🔵 低 | TASK-013 端到端 + Runtime 侧用例未在 Linux CI 验证 | 🔵 待验证 | 在 CI（Linux）复跑 `tests/agent_runtime/*` 与端到端 |

> **NEW-1/NEW-2 为第二轮复核新发现**（首轮未列），详见 §6.3；**NEW-3** 是环境限制导致的未验证项，非代码缺陷。

---

## 一、总体结论

> **第二/三轮复核（2026-11-19 更新）**：P1-1、P1-2、P2-1、P2-2、P2-5 已修复并经代码复核通过（§6.1）；P1-3、P1-4、P2-3、P2-4、P2-6 的文档/注释同步于第三轮完成（§6.5）。**至此发现问题 P1-1~P2-6 全部闭环**，仅剩 NEW-1/NEW-2（非阻塞观察点）与 NEW-3（Linux CI 复跑待确认）。

整体质量**偏高**，达到可合并标准。核心安全链路（验签只在 Console、跨租户复核、撤权即时失效、租户隔离核属、凭据脱敏）实现扎实，边界注释与设计条目对应清晰，多数"显式失败而非静默兜底"的处理符合项目 CLAUDE.md 的价值观。

> 关于"内部服务鉴权是否足以信任调用方"：经与作者核对，Console 的 `X-Internal-Service` 鉴权实现合格（密钥缺失即拒绝、`hmac.compare_digest` 防时序），且该端点**不接受调用方自报身份**——请求体只收 `platform_key/channel/assertion/entry_id`，身份一律从 JWT 现验 RS256 解出。因此"持有内部密钥 + 凭空造身份"不可行；风险仅限于 `INTERNAL_SERVICE_TOKEN` 的保管范围（K8s Secret 注入、不进日志、只 Gateway↔Console 共享），属**运维/架构层面**，不在本需求修代码范围。

本地可运行的单元测试全部通过：

```
tests/gateway/test_msswwweb_chat_auth.py
tests/gateway/test_msswwweb_cached_resolution.py
tests/gateway/test_msswwweb_surface.py
tests/console_platform/test_external_assertion.py           → 83 passed
tests/console_platform/(auth_cache_invalidation / external_identity_api /
   external_principal_grants / mssw_token_factory / external_identity_contract)
                                                             → 30 passed（其余 10 条为需真库的集成用例，本地无 DATABASE_URL，非代码问题）
```

Runtime 侧（`tests/agent_runtime/*`）因 `packages/agent-core` 依赖 Linux 专有的 `resource` 模块，在当前 **Windows** 环境无法导入，未能在本地复跑，需在 CI（Linux）验证。TASK-013 端到端验收仍为 `in_progress`。

发现的问题按严重度归纳如下。**没有 P0（会导致数据泄露/越权的即时缺陷）**；主要是 P1（设计契约与实现/文档漂移、可运维性）与 P2（健壮性、可维护性）。

| 级别 | 数量 | 主题 | 状态 |
| --- | --- | --- | --- |
| P1 | 4 | `channel` 参数全程未消费；`grant_entry` 不校验入口通道；屏障/revision 机制"已实现但未接线"；`entry_agnostic` 全局硬编码 | P1-1/P1-2 已修复；P1-3/P1-4 文档同步 |
| P2 | 6 | 权限层缓存到期未二次校验；多平台 Secret 静默取首键；`COMMITTED` 相位死代码；TASK 文档与设计/代码不一致；`_entry_id_of` 非受控异常；删除缺 Agent 复核（设计意图待澄清） | P2-1/P2-2/P2-5 已修复；P2-3/P2-4 文档同步；P2-6 设计意图 |

---

## 二、逐项发现

### P1-1　`channel` 参数从 Gateway 传到 Console，但 Console 全链路不消费　✅ **已修复（见 §六）**

**证据**：

- Gateway 侧把 `channel` 打进请求体：`apps/im-gateway/src/muad_im_gateway/application/console_client.py:279,299`。
- 契约声明了 `channel: ChatText`：`packages/contracts/src/muad_contracts/webchat.py`（`ExternalPrincipalRequest` / `ExternalIdentityQuery`）。
- **但 Console 路由从不读取它**：`apps/console-platform/backend/src/muad_console_platform/api/internal_channel.py:167-190`（`external_principal`）、`:198-219`（`external_identity`）都只传 `platform_key/assertion/entry_id`，`payload.channel` 无任何引用。
- 服务层签名也不收 `channel`：`external_assertion_service.py:73-80`（`resolve`）、`:136-143`（`describe`）；`principal.resolve_principal` 的 `allowed` 判定只用 identity hash + grant + entry_id + agent 状态，`GrantView.channel` 只作**输出**（`principal.py:142,209-215`）。

**设计预期**：设计 §API-16 / §3.4 要求断言面按 `platform_key + channel` 校验，`channel` 应与入口一致性关联。

**影响**：`channel` 目前是"装饰性参数"。功能上不致命（身份由 `platform_key` 隔离），但会造成两种误读：(1) 后来者以为 channel 已做校验而依赖它；(2) 契约与实现形成"看似有约束、实则无校验"的假象。

**建议**：二选一并同步文档：
1. 让 Console 在 `resolve/describe` 接收并校验 `channel`（例如断言 `channel` 必须等于该 `platform_key` 绑定的通道，或入口的 `bot_account.channel`），或
2. 从 Gateway→Console 的请求体与契约中移除 `channel`，并把设计文档改为"channel 仅由 surface 前缀区分，不入认证判定"。**优先方案 1**（与设计一致），若时间紧则方案 2 并明确记录取舍。

---

### P1-2　`grant_entry` 不校验被授权入口的通道　✅ **已修复（见 §六）**

**证据**：

- `ExternalIdentityService.grant_entry` 只收 `identity_id + channel_account_id`，无 `channel` 参数：`external_identity_service.py:279-287`。
- 仓库 `get_entry` 仅按 `tenant_id + id + is_deleted` 取，**不校验 `BotAccount.channel`**：`external_identity_repository.py:163-176`。
- 对比：`list_entries` 是唯一按 `_EXTERNAL_PLATFORM_CHANNEL` 过滤入口的方法（`external_identity_service.py:245-253`）。

**影响**：管理员通过 API-22 可以把"别的通道"的 bot_account 授权给 MSSW 身份，只要它是同租户的合法入口。受身份层 `platform_key` 隔离，不会直接造成越权，但：
- 与设计"入口授权仅限 MSSWWEB 入口"的约束不符；
- `resolve_principal` 的 `GrantView.channel` 会因此出现 `WEBCHAT` 等非 MSSWWEB 值，`/entries` 的展示语义被污染。

**建议**：在 `grant_entry` 内校验 `entry.channel == _EXTERNAL_PLATFORM_CHANNEL`（MSSWWEB 面），不匹配抛 `COMMON_VALIDATION_ERROR`/`FORBIDDEN`，并补一条测试。与 P1-1 一起修，形成"入口通道一致性"的完整闭环。

---

### P1-3　屏障 / revision / fence 机制"已实现但未被消费"，与 TASK-006 正文冲突　✅ **已修复（见 §6.5）**

**证据**：

- `AuthCacheDirectory` 完整实现了 barrier + fence + `auth_revision` + 协调表 + `recover_tenant`：`auth_cache_invalidator.py`（全文件）。
- **但 Gateway 热路径只读 plain 键**：`chat_principal.py:277`（`read_credential_plain`）、`:328`（`read_permission_plain`）、`:295`（`touch_credential`）、`:356`（`touch_permission`）。grep 确认 Gateway **不调用** `get_barrier / get_user_version / get_platform_epoch / read_permission / read_credential`。
- 即：MSSWWEB 的资源请求**不做**屏障检查、不做版本比对、不做 epoch 比对。
- 设计 §3.1.2（2026-11-18 定稿）**确实**声明了这一取舍（"不启用屏障回填协议……缺口是失效期间的并发回填"）。
- 但 TASK-006 的 Description/Checklist 仍写"资源请求 Redis-first + revision 校验""fence CAS 保证并发变更不丢""`/identity` 永远读 PG；资源接口命中失效 revision 时拒绝"，与实现不符；且 `PHASE_COMMITTED` 常量（`auth_cache_invalidator.py:56`）**从未被写入**（`_write_state` 只写 PREPARING/READY）——三态状态机实际只有两态。

**影响**：
- 安全性层面：撤权"即时生效"靠的是"权限层被 `invalidate` 删除 → 网关 miss → 回源 Console 重查"，这条链路是成立的（我在代码中确认闭环）。**残留风险**是设计已承认的"撤权与并发回源交错时旧授权可能 TTL 内回填"窗口。
- 可维护性层面：屏障/revision 是一整套**当前无消费者**的代码，容易让后续开发者误以为热路径已受保护；TASK-006 正文与代码事实脱节，违背 CLAUDE.md "Spec 跟随代码事实"。

**建议**：
1. 短期：在 TASK-006 / 设计 §3.1.2 明确写"屏障/fence/revision 为完整形态预留，MSSWWEB 热路径本期使用 plain 路径，不消费屏障与版本"，并在 `chat_principal.py` 的 `_try_cached_external` docstring 里补一句"本路径不校验屏障/版本，见 §3.1.2 已知取舍"（现在只提了"凭证命中不代表权限可信"，未点明无屏障）。
2. 中期：若要求"严格零窗口"，把屏障协议接进 Gateway 读侧（`read_permission(expected_user_version=..., expected_epoch=...)`），并让 `_write_state` 真正经过 `COMMITTED`。
3. 删除或标注 `PHASE_COMMITTED`，避免"死常量"误导。

---

### P1-4　`entry_agnostic` 在 Gateway 三个读端点被全局硬编码为 `true`　✅ **已修复（见 §6.5）**

**证据**：

- `webchat_service.py:412`（`list_conversations`）、`:427`（`get_conversation`）、`:462`（`list_messages`）都**无条件**传 `entry_agnostic=True`，与 `surface.channel` 无关。
- 设计 §API-05 明确：会话列表的 `entry_id`"只用于鉴权与归属……不是过滤条件；按 user+Agent 聚合（跨入口共享）"。RULE-08 / S-11 也要求"WebChat/MSSWWEB 互相可见"。

**结论**：从**列表/历史**语义看，这与设计一致（WebChat 与 MSSWWEB 本就共享）。**但它同时意味着**：即使部署里只启用 WEBCHAT、从未配置 MSSWWEB，WebChat 的详情/历史也走 `entry_agnostic=true`，即"入口严格核属"这条分支在正常读路径上**默认不会被触发**。

**影响/待确认**：
- `get_conversation`/`list_messages` 仍先经 `_resolve_scope` 做 Agent 当前授权复核（`webchat_service.py:420,452`），安全边界由 Agent 授权承担，可接受。
- 但入口严格核属（`assert_entry_attribution` 的非 agnostic 分支）只在隐式 thread 路由（受理链路）才用到，"跨入口共享"与"入口隔离"的边界容易被误读。
- 这不是缺陷，但如果设计只想让 MSSWWEB"额外"共享、而 WEBCHAT 仍需入口核属，则当前实现过度放宽。

**建议**：在 `webchat_service.py` 三处显式注明"按设计 §API-05 会话读取恒按 user+Agent 聚合，入口仅来源"，避免后来者以为是漏判；若产品语义要求 WEBCHAT 保留入口核属，则改为 `entry_agnostic = (surface.channel == "MSSWWEB")` 并同步设计。

---

### P2-1　权限层缓存的 `expires_at` 在 Gateway 命中时未二次校验　✅ **已修复（见 §六）**

**证据**：
- Console 写权限层 payload 含 `expires_at`（ISO）但**不含** `expires_at_epoch`：`external_assertion_service.py:226-238`。
- 凭证层命中时校验了 `now >= expires_at_epoch`（`chat_principal.py:292`），但**权限层**读回后直接 `model_validate`，未比较 `result.expires_at`：`chat_principal.py:314-360`。
- 权限层 TTL 由 Store 的闲置窗口决定（`write_permission_plain` 用 `self._ttl`），**未按会话绝对到期封顶**（对比凭证层 `min(ttl, expires_at-now)`，`auth_cache.py:463-464`）。

**影响**：若凭证层先过期而权限层仍在闲置窗口内（两者 TTL 口径不同），理论上存在"凭证已到期但权限层被读到的时段"。不过 `_try_cached_external` 的流程是**先读凭证层、再读权限层**，凭证层 miss 会直接回源，不会单独用权限层——所以实际路径被凭证层的到期校验挡住了。风险为"防御性缺口"而非可利用漏洞。

**建议**：在 `_read_cached_permission` 还原后增加 `if now >= result.expires_at: return None` 的一行防御；或让 `write_permission_plain` 也按 `expires_at` 封顶 TTL，与凭证层口径对齐。

---

### P2-2　多平台 Secret 时静默取首键　✅ **已修复（见 §六）**

**证据**：`_single_external_platform_key` 用 `next(iter(parsed), None)` 取第一个平台键：`main.py:533-546`。

**影响**：设计反复强调"单平台/单公钥"。若 Secret 误配多个平台，网关**静默**只取第一个，不报错、不告警，运维难以察觉配置错误。

**建议**：`len(parsed) != 1` 时记 `logger.error` 并（视策略）禁用 MSSWWEB 面或直接启动失败，而不是静默取首键。

---

### P2-3　`PHASE_COMMITTED` 为死代码（并入 P1-3）　✅ **已修复（见 §6.5）**

见 P1-3 第 3 点。

---

### P2-4　TASK-006 / TASK-010 正文与设计/代码不一致　✅ **已修复（见 §6.5）**

除 P1-3 提到的 TASK-006 屏障描述外，TASK-010 也有内部矛盾：

- TASK-010 Description §1 与 Checklist 首条写"item 带来源 `entry_id`/`channel`/`has_unread`"，但设计 §API-05 明确"**不含 `channel`**"，实际契约 `WebchatConversationItem` 也只有 `entry_id`/`has_unread`，无 `channel`（`packages/contracts/src/muad_contracts/webchat.py:480-491`）。
- TASK-010 的离线验证只跑了单元测试，集成/端到端"留待后续批次"，而 TASK-013 仍 `in_progress`。

**建议**：任务文档以代码事实为准修订（去掉 `channel`、把屏障描述改为 §3.1.2 窄方案），TASK-013 完成后再标 archive。

---

### P2-5　`_entry_id_of` 对非法 `entry_id` 抛非受控 `ValueError`　✅ **已修复（见 §六）**

**证据**：`channel_notifications.py:254`（`return _EntryAttribution(uuid.UUID(str(run_entry)))`）与 `:257`（回落 `uuid.UUID(str(row.channel_account_id))`）。

**影响**：若 `run.channel_json["entry_id"]` 或会话入口是非法 UUID 字符串，`uuid.UUID(...)` 抛 `ValueError` 而非 `AppError`。该函数在 Run 终态写通知的事务内被调用，异常会**整体回滚终态**，导致用户看到 Run 未终态（或终态但无通知）。虽然入站校验正常情况下不会产生坏值，但"内部一致性错误不该回滚用户结果"是通知模块自己声明的原则（见 `terminal_delivery_state` 对坏载荷的处理）。

**建议**：捕获 `ValueError`，按"无入口归属"处理并 `logger.error` 留痕（与 `terminal_delivery_state` 处理坏 `channel_json` 的一致口径），而不是让它冒泡回滚终态。

---

### P2-6　Gateway `delete_conversation` 无 Agent 授权复核（设计意图待澄清）　✅ **已修复（见 §6.5）**

**证据**：
- Gateway `delete_conversation` **不调用** `_resolve_scope`，直接按 (tenant,user,conversation_id) 转发：`webchat_service.py:431-440`（对比 `get_conversation` 在 `:420`、`list_messages` 在 `:452`、`mark_conversation_read` 在 `:514` 都调了 `_resolve_scope`；`delete_conversation` 是唯一不调它的写操作）。
- Runtime `ConversationDeletionService._load_for_delete` 只按 `(tenant, user, id)` 核属，无 agent/entry 校验：`conversation_deletion.py:69-83`。

**结论**：这与 `conversation_deletion.py` docstring 明示的"授权撤销后用户仍应能清理自己的历史"**一致**，是有意设计，**不是越权**（不会删到别人或别的用户的会话）。把它列为 P2 是因为：
- 与 `get_conversation`/`list_messages` 的处理不对称，容易让审查者误判；
- 建议在 Gateway `delete_conversation` 的 docstring 显式写明"删除不要求 Agent 当前授权（撤权后仍可清理历史），核属由 Owner 按 (tenant,user) 承担"，减少歧义。

---

## 三、正面确认（做得好的地方）

为避免只列问题，以下关键点经代码核对**实现到位**，值得保留：

1. **验签只在 Console**：Gateway 只转发断言，`resolve_external` 不自行解析凭据（`chat_principal.py:218-260`），符合 RULE-02。
2. **跨部署租户复核**：Gateway 在拿到 Console 主体后再比对 `principal.tenant_id == self._tenant_id`，不一致 403（`chat_principal.py:169,253`）；单租户部署边界成立。
3. **上下文比对双层**：Console 侧 + Gateway 侧都对 `session_id == X-Chat-Context` 做 `AUTH_CONTEXT_CHANGED`（WebChat 面，`chat_principal.py:171`）。
4. **时效校验严谨**：`exp` 恒 -1 不可信时用 `login_time/time` 做绝对上限，且**未来时间也拒绝**（`verifier.py:120-134`），`missing_issue_time` 显式拒绝，不放行无时间线索 token。
5. **凭据卫生**：断言以 `SecretStr` 传递，`repr`/日志无明文（`chat_principal.py:84-95`）。
6. **失效链路完整**：Agent 更新/删除、授权、入口增删、身份编辑/删除、用户停用都接 `invalidate`（`api/agents.py:118,137,307,340,404,427`；`api/users.py:521,551,580,613,643`），且业务服务已自增版本时传 `bump_revision=False` 避免双重递增（`external_identity_service` 全部 `_touch_auth_revision` + 路由层 `bump_revision=False`），逻辑自洽。
7. **通知写入的事务性与幂等**：终态/待确认通知与 Run 同事务、`delivery_key` 稳定重投命中原回执不新占 seq（`channel_notifications.py:126-197`），feed 行锁分配 seq，设计正确。
8. **通知读取的安全投影**：DTO 不含 `delivery_state`/`delivery_key`/租约/`storage_key`（`channel_notification_queries.py` 模块 docstring + `_project`），`CURSOR_AHEAD` 显式不夹取。
9. **删除幂等 + 同事务收口**：`_soft_delete` 同事务软删会话、指向它的指针、该会话通知（`conversation_deletion.py:109-137`），重复删除返回成功。
10. **跨入口共享不重复列出**：`list_conversations` 单表查询无 join 到入口，同一 conversation 行只出现一次（`webchat_history.py:479-497`）。
11. **部署分层清晰**：验签因子走单键 JSON Secret（`secret.example.yaml`），网关据此决定是否挂载 MSSWWEB 面（`main.py:517-531`），ingress 与 helm 的 `/api/v1/msswwweb`、`/runs` 路由一致。
12. **测试覆盖**：认证/缓存/跨入口/通知相关单测齐全且本地全绿（见第一节）。

---

## 四、建议的处理优先级

> **状态更新（第二轮）**：本节为首轮计划。截至 2026-11-19，第 1、2 项中的 P1-1/P1-2/P2-1/P2-2/P2-5 **已完成**；剩余仅"文档/注释同步"（P1-3/P2-3/P1-4/P2-4/P2-6）与 CI 验证（NEW-3）。**最新状态以 §〇 清单为准。**

1. **合并前**（P1，1–2 天）：处理 P1-1/P1-2（`channel` 一致性与 `grant_entry` 入口通道校验，二者一起做）；同步 TASK-006/TASK-010 正文到代码事实（P1-3、P2-4）。
2. **合并前（低成本）**：P2-1 一行防御、P2-2 显式报错、P2-5 捕获 `ValueError`、P2-6 补 docstring。
3. **TASK-013 收尾**：在 Linux CI/Pod 复跑 `tests/agent_runtime/*` 与端到端，确认会话共享/通知水位/删除的真实链路（本地 Windows 受 `resource` 模块限制未能验证）。
4. **后续（可选）**：若要"严格零窗口"撤权，把屏障协议接入 Gateway 读侧并让 `_write_state` 走 `COMMITTED`（P1-3 中期项）。

---

## 五、审查方法说明

- 代码范围来自 `git log --author=jasonje` 与 `docs/mssw-channel/*`、TASK Paths 的交集；不含 merge 带入的他人提交。
- 逐模块通读：`external_assertion/{config,aes_ecb,verifier,principal}.py`、`external_assertion_service.py`、`external_identity_service.py`、`auth_cache_invalidator.py`、`muad_common/auth_cache.py`、`chat_principal.py`、`deps.py`、`webchat_service.py`、`main.py`、`channel_conversations.py`、`channel_notifications.py`、`channel_notification_queries.py`、`conversation_deletion.py`、`webchat_history.py`、部署 yaml/helm、前端 `ExternalIdentitySection.tsx` 等。
- 交叉核实由两个只读调查代理并行完成（`channel` 消费链、`entry_agnostic` 共享语义），结论均以本报告为准并附 file:line。
- 本地测试：`.venv/Scripts/python.exe -m pytest` 运行上述单测，83 + 30 passed；集成/端到端因环境（无 DATABASE_URL、Windows 缺 `resource`）未跑，已在报告中如实标注。

---

## 六、第二轮复核：修复验证（2026-11-19）

作者提交了五条修复，本节逐条核对代码落地情况、回归风险与残余观察点。

### 6.1 已修复项逐条确认

| 编号 | 修复点 | 复核结论 |
| --- | --- | --- |
| **P1-1** | `ExternalAssertionService.resolve/describe` 新增 `channel` 形参，调用 `_check_channel(channel)` 后置校验：非 `MSSWWEB` 抛 `ExternalAssertionRejected`（复用 `EXTERNAL_ASSERTION_INVALID`），`None` 视为"未声明"保持后向兼容。 | ✅ 正确。`_check_channel` 在任何存储访问（`_require_config`/`_verify`/`resolve_principal`）**之前**执行，符合"先拒后查"。`MSSWWEB_CHANNEL = "MSSWWEB"`（`channel_admin_service.py:25`）与 Gateway surface 字面量（`main.py:527`）一致，WebChat 面走 `USER_TOKEN` 不经过这两个函数，无误拒风险。测试：`test_resolve_rejects_assertion_on_another_channel`、`test_resolve_treats_missing_channel_as_unspecified`。 |
| **P1-2** | `grant_entry` 在取到 `bot` 后新增 `if bot.channel != _EXTERNAL_PLATFORM_CHANNEL: raise COMMON_NOT_FOUND`（`external_identity_service.py:299-302`）。 | ✅ 正确。拒绝用 `NOT_FOUND` 而非 `FORBIDDEN`，不泄露跨通道/跨租户存在性，与既有"入口不存在"分支同义。`_EXTERNAL_PLATFORM_CHANNEL` 即 `MSSWWEB_CHANNEL`（`:21`）。测试：`test_grant_rejects_entry_from_another_channel`（构造真实 WEBCHAT 入口验证拒绝）。 |
| **P2-1** | 读侧 `_read_cached_permission` 还原后新增 `if result.expires_at <= now: return None`（`chat_principal.py:355-358`）；`touch_permission` 改为 `max(0, min(闲置窗口, expires_at-now))` 封顶，与 `touch_credential` 同口径。 | ✅ 正确且**重要**——作者判断此条被首轮低估成立：此前权限层可凭闲置窗口在会话绝对到期后继续放行，现读侧到期即视为 miss 回源，与凭证层语义对齐。`auth_cache.py:501-523` 的 `touch_permission` 已接受 `expires_at_epoch`/`now_epoch` 并封顶。 |
| **P2-2** | `_single_external_platform_key` 对 `len(parsed) > 1` 记 `logger.error` 并返回 `None`（走 503）；新增 `parse_float=Decimal` 防超长数字平台 id 精度丢失。 | ✅ 正确。多平台时 `external_platforms_enabled` 仍返回 True → surface 挂载但 `platform_key=None` → 认证 503 + ERROR 日志，是**显式失败且可观测**的路径，非静默。`Decimal` 处理与 Console 侧写入口径一致性诉求一致。 |
| **P2-5** | `channel_notifications._entry_id_of` 的两处 `uuid.UUID(...)` 收拢到 `_parse_entry_id`，捕获 `(TypeError, ValueError)` 返回 `None` 并 `logger.error`。 | ✅ 正确。坏值按"无入口归属"处理，不再冒泡回滚 Run 终态，与 `terminal_delivery_state` 处理坏 `channel_json` 的口径一致（`channel_notifications.py:260-272`）。另注意到新增了 `run_entry_attribution_diverged` 的 INFO 日志（`:247-253`），对"Run 落点入口 ≠ 会话创建入口"的可观测性有增益。 |

### 6.2 测试验证

本轮在本地复跑：`test_external_assertion / test_external_identity_api / test_msswwweb_chat_auth / test_msswwweb_cached_resolution / test_msswwweb_surface` → **85 passed, 10 errors**。10 条 error 全部是 `RuntimeError: DATABASE_URL is required`（需真库的集成用例，本地环境限制，非代码问题）。新增用例（P1-1 两条、P1-2 一条、P2-1 一条）均在列。P2-2 的 5 条据作者说明已加；需真库/需构造 settings 的用例在本地无法全部触发，建议 CI 环境确认。

作者报告的大范围回归（`tests/console_platform`/`common`/`gateway` 20 项失败，均为集群 Redis `SELECT is not allowed in cluster mode` 与 `MovedError`）与本次改动无关，属测试环境与集群 Redis 不兼容，已于基线复现——认可该判断。

### 6.3 复核中新发现的残余观察点（非阻塞）

1. **NEW-1 `write_permission_plain` 与 `touch_permission` 封顶口径不一致（低｜🔵 观察）**：`write_permission_plain` 首写仍用固定闲置窗口 `self._ttl`（`auth_cache.py:490-499`），未按 `expires_at` 封顶；只有 `touch_permission` 封顶。当前**不会**造成超期放行——因为读侧 `chat_principal.py:355` 已独立校验 `expires_at` 兜底。但两个写入路径语义不同，建议在 `write_permission_plain` 也传入并封顶 `expires_at_epoch`，或在其 docstring 注明"首写不封顶、由读侧兜底"，避免后续维护者误判。
2. **NEW-2 `channel=None` 后向兼容的语义窗口（提示｜🔵 观察）**：`_check_channel(None)` 放行，注释解释为"Gateway 老版本未声明"。当前本仓库 Gateway **恒定**传 `channel=surface.channel`（`deps.py:151,169`；`webchat.py:303`），故 `None` 分支只在跨版本灰度期有意义。确认无老版本 Gateway 与新版 Console 混跑的需求后，可考虑收紧为"必须声明"，否则保留即可。

### 6.4 未修复项结论（与作者一致，不改代码）

- **P1-3 / P2-3（屏障未接线 / `COMMITTED` 死常量）** → 文档同步：设计 §3.1.2 已声明窄方案，需把 TASK-006 正文改为"plain 路径、不消费屏障与版本"，并在 `chat_principal._try_cached_external` 补注"本路径不校验屏障/版本"。`PHASE_COMMITTED` 建议删或注明预留。
- **P1-4（`entry_agnostic` 硬编码 true）** → 与 §API-05"跨入口聚合"一致，属设计意图；建议在三处调用点加一句来源注释。
- **P2-4（TASK-006/TASK-010 正文与代码不一致）** → 以代码事实修订任务文档（去掉 item 的 `channel`、屏障描述改为 §3.1.2 窄方案）。
- **P2-6（删除无 Agent 复核）** → 与 `conversation_deletion.py` docstring"撤权后仍可清理历史"一致，属设计意图；建议补 Gateway `delete_conversation` docstring 说明。

**待修复人确认汇总（对照 §〇 清单）**：

- 需**同步文档/注释**（不改行为）：P1-3、P2-3、P1-4、P2-4、P2-6 —— 请修复人确认后再随 TASK-013 收尾落地。
- 需**CI 环境确认**（NEW-3）：P2-2 的 5 条测试在本地无法全触发；Runtime 侧会话/通知/删除用例受 Windows `resource` 限制未验证 —— 请在 Linux CI 上确认全绿。
- 🔵 观察点 NEW-1 / NEW-2 不阻塞合并，择机处理即可。

> 上述"同步文档/注释"五项已于第三轮完成，见 §6.5。

### 6.5 第三轮复核：文档/注释同步确认（2026-11-19）

本轮修复人处理了 §6.4 列出的全部"文档/注释同步"项，逐条核对如下：

| 编号 | 同步动作 | 复核结论 |
| --- | --- | --- |
| **P1-3** | `chat_principal._try_cached_external` docstring 新增"本路径不校验屏障/用户版本/平台 epoch……撤权即时生效靠 miss→回源，缺口是并发回填 TTL 窗口"。 | ✅ 与实际一致，明确点破"plain 路径不消费屏障"，消除了后续误读风险。 |
| **P2-3** | 删除 `PHASE_COMMITTED` 常量，改为注释说明"两态机，不保留 COMMITTED"。 | ✅ 常量确已删除。**本轮复核发现两处周边注释仍残留三态描述**（`control.py` 模型 docstring/字段注释、`invalidator.recover_tenant` docstring），已一并改为"两态 `PREPARING → READY`"（见下条"本轮附加修正")。 |
| **P1-4** | `webchat_service` 的 `list_conversations`/`get_conversation`/`list_messages` 三处 `entry_agnostic=True` 补来源注释，docstring 注明 §API-05 设计意图。 | ✅ 注释准确，且明确"安全边界是 `_resolve_scope` 的当前 Agent 授权复核，而非入口核属"。 |
| **P2-4** | 任务文档 TASK-006 正文改为"两态 + plain 热路径"事实描述并加"已知取舍"引注；TASK-010 去掉 item 的 `channel`。 | ✅ 与代码事实一致，标注"以代码事实为准"，符合 CLAUDE.md "Spec 跟随代码事实"。 |
| **P2-6** | `delete_conversation` docstring 显式说明"不要求 Agent 当前授权是有意设计，越权边界由 Owner 按 (tenant,user,conversation_id) 核属承担"。 | ✅ 消除了与 `get_conversation`/`list_messages` 的处理不对称带来的误读。 |

**本轮附加修正（审查方直接落地）**：P2-3 删常量后，以下三处注释仍描述三态，属"删了常量没同步周边"的收尾遗漏，已改为两态口径：
- `infrastructure/models/control.py` — `AuthCacheSyncState` 类 docstring 的"`PREPARING` → `COMMITTED` → `READY`"及 `PLATFORM` 行"Gateway 每次命中都比对 epoch"（后者已注明热路径不消费），以及 `phase` 字段注释。
- `application/auth_cache_invalidator.py:232` — `recover_tenant` docstring 的"`PREPARING`/`COMMITTED` 都表示……"改为"任何非 READY（即 PREPARING）"。

**测试**：`tests/common/test_auth_cache_plain.py`、`tests/console_platform/test_external_assertion.py`、`tests/gateway/test_msswwweb_cached_resolution.py` → **60 passed**。

**结论**：P1-1~P2-6 全部处理完毕。除 NEW-3（Linux CI 复跑）外，无剩余待办；NEW-1/NEW-2 为非阻塞观察点。
