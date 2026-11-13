# MSSW 接入 — 决策与待确认清单

> 关联：`01-jwt-spec.md`、`02-requirements.md`、`04-user-journeys.md`、`05-interface-mapping.md`
>
> **当前状态：全部问题已闭合**。A 组 20 条已确认、B 组清零、C 组只剩 M5 生产密钥（需求方"后续会给"）、D 组四个内部决策结论全定、E 组 3 条已定案待单向告知 BFF、F 组 2 条搁置。**无阻塞动工项，也无待双方讨论的问题。**
>
> **重要变更**：BFF 是后端服务，**直接适配我方现有接口**。因此原先"等 BFF 文档"的问题大幅消解——接口形态由我方决定，BFF 侧配合。
>
> **Q 编号空号说明**：Q16、Q21、Q22、Q26 不存在，属随上述变更**已消解**的问题，编号保留不复用（便于对照历史记录）。

## 总览

| 分类 | 数量 | 说明 |
|---|---|---|
| A. 已确认 | 20 | 无需再问（含 Q5/Q18/Q19） |
| B. 原需双方对齐 | 0 | ~~Q18/Q19~~ **已全部定案并并入 A 组** |
| C. 需向 MSSW 侧索取 | 1 | 仅 **M5 生产密钥**（后续会给）；M4 可选 |
| D. 我方内部决策 | 4 | 不需外部输入，结论全部已定；D1/D3 只差写进代码，D5/D6 无需额外动作 |
| E. 已定案、单向告知 BFF | 3 | 不是"待对齐"（无需双方讨论、结论不会因 BFF 而变），是我方拍板后告知（Q23/Q28 需告知，Q24 纯我方内部实现） |
| F. 已搁置 | 2 | 需求方明确"先不关心" |

> **已无任何阻塞动工/联调的开放问题**——C 组只剩 M5 生产密钥（需求方明确"后续会给"）。
>
> **B 组已清零**：Q18（选配未配置 → 404）、Q19（幂等以我方 `Idempotency-Key` 为准）由需求方拍板；Q20 参数形态在 `05` §4.5 已定，其子问题 Q20-c 由代码事实确认，见下方「Q20-c」。

---

## A. 已确认

| # | 结论 | 依据 |
|---|---|---|
| Q1 | 公钥为 **PKCS#1**，PyJWT 可直接加载，实测验签通过 | `01-jwt-spec.md` §5 |
| Q2 | `issue` = `bce0e212...4512`（开发环境） | 需求方 + 实测 |
| Q3 | `audience` = `9d7cdc41...8f71`（开发环境） | 需求方 + 实测 |
| Q4 | `salt` = `dxbwzhls...wlzm`（开发环境，32 字节） | 需求方 + 实测 |
| Q6 | 映射键用 **`job_number`**；工号变更靠人为删除账户 | 需求方 |
| Q7 | `_id` 是专家 ID（24 位 ObjectId），**不作映射键** | 签发方代码 + 实测 |
| Q8 | **工号全局唯一**，`identity_key` 无需加租户维度 | 需求方 |
| Q9 | token 在请求头内的 Cookie，键名 **`soc-token`** | 真实 curl 样例 |
| Q10 | 不实现签发方的免校验分支 | 我方安全决策 |
| Q13 | **无权限 → 拒绝**，不自动建档 | 需求方 |
| Q14 | `/permissions` 语义为**布尔**，落为新增 `GET /api/v1/mssw/permissions`；响应 `{"visible": bool, "entry_id": uuid, "agent_id": uuid}`——**`entry_id` 由这里下发给 BFF**（`resolve` 是内部接口） | 需求方 + 我方设计决策 |
| Q17 | `customer_id` 在本仓库**完全不存在** | 全仓搜索 |
| Q25 | 工号变更走人为删除账户 | 需求方 |
| **Q27** | **不是跨站**——BFF 是后端服务直接转发请求，`SameSite=Strict` 的 Cookie 可正常送达 | **需求方（本轮新增）** |
| **Q29** | **BFF 直接适配我方现有接口**，无需我方实现 `/internal/agent-provider/v1/*` | **需求方（本轮新增）** |
| **Q15** | **单入口**：MSSW BFF 只有一个上游服务，`bot_id` 由**网关配置项**指定，不做多入口区分。`entry_id` 由 Console `resolve` 响应直接返回（`entry_id=bot.id`），无需推导 | 需求方确认"BFF 只有一个" + 代码事实（`channel_service.py:99-127`、`contracts/channel.py:152-154`） |
| **Q31** | ~~绝对时间窗 TTL~~ **已作废**：不设本地时间窗。`time` 是签发时刻，做窗口会误杀活跃用户且不解决重放（`02` §3.3.1） | 设计决策（本轮推翻） |
| **Q5** | **密钥固定，不轮换**——按静态单密钥配置读取，不实现轮换、不做多密钥并存、不预留版本槽位。代价：公钥泄露只能换密钥 + 重新部署 | 需求方（本轮） |
| **Q18** | **选配能力未配置 → 404 `MSSW_CHANNEL_NOT_CONFIGURED`**（不用 501） | 需求方（本轮） |
| **Q19** | **幂等以我方 `Idempotency-Key` 为准**，不由 BFF 传 `conversation_id`；Runtime 指纹五键全是我方值（`run_service.py:1751-1765`） | 需求方（本轮）+ 代码事实 |

## B. 补充论证（原"需双方对齐"两组，结论已并入 A 组）

> Q18/Q19 的结论见 A 组；本节保留**论证过程**，供后来者理解"为什么这么定"。

### Q19 说明：为什么"以我方为准"就能闭合

需求方答复：「`conversation_id` 是账号的会话 ID 吧，是不是以我们的为准就行了」——**是的，而且代码本身就是这么做的**。

Runtime 的新建会话幂等**不依赖任何外部 ID**，而是 `Idempotency-Key` + 请求指纹：

```python
# run_service.py:1751-1765
fingerprint = submission_fingerprint(
    endpoint=ENDPOINT_CREATE_CONVERSATION,
    key_payload={
        "tenant_id": ..., "agent_id": ...,
        "platform_user_id": ..., "entry_id": ..., "external_thread_id": ...,
    },
)
if idempotency_key:
    replay = await self._find_conversation_replay(tenant_id, idempotency_key, fingerprint, scope)
    if replay is not None:
        return replay          # 命中则原样重放，不建第二个会话
```

- **去重身份完全由我方构造**：`tenant_id` / `agent_id` / `platform_user_id` / `entry_id` / `external_thread_id` 五个键都是我方的值，BFF 只需原样回传自己上次用的 `Idempotency-Key`。
- **BFF 不需要维护 `conversation_id` 映射**：它拿到我方返回的 `conversation_id` 存下来直接用即可（`05` §4.1.1）。
- 因此 `04` 旅程 F 的 F2 从"需确认上游是否提供该字段"改为"**已定：以我方 `Idempotency-Key` 为准**"。

### Q20 说明（已定，不需 BFF 确认参数形态）

原担心"上游 cursor 是不透明串"已不成立——**BFF 适配我们，可以直接用我们的参数**。

建议约定：历史接口用 `GET .../messages?page=1&page_size=20&through_seq=<首屏返回的值>`，响应带 `through_seq`。BFF 若需对外暴露 `cursor`，由它自己编码。

### Q20-c（原本标"待确认"，现已由代码事实确定）

**结论：是同一套水位，两者都读 `conversation.last_seq` 这一个 canonical seq。**

| 面 | 水位来源 | 位置 |
|---|---|---|
| 历史分页 | `watermark = conversation.last_seq`（`through_seq` 就是这个 seq） | `webchat_history.py:375`、`:384` |
| 事件流游标校验 | `through = await self.watermark(...)`，而 `watermark` 是「会话行 `last_seq` 与已持久行最大 seq 取大者」 | `event_tail_hub.py:652-675`、`:20-22` |

两边是**同一个 seq 空间**，所以 `through_seq`（历史）与 `after_seq`（事件流）可以直接互相续接——概设要求的"游标过期按历史补全"在数据层面成立。

**但"补全"这一步目前仍不存在**：`check_cursor` 对 `CURSOR_EXPIRED` 只抛错（`event_tail_hub.py:669-674`），不返回补全数据。所以 R6 要自己实现"过期 → 转查历史"，水位可比对这一点不再是障碍。仍需我方决定的只是补全的**实现方式**（由网关编排，还是让 BFF 拿到 409 后自行转历史接口）。

## C. 需向 MSSW 侧索取

| # | 需要 | 状态 | 说明 |
|---|---|---|---|
| M4 | 真实响应样例 | 可选 | 用于对照，不阻塞 |
| M5 | **生产环境** `issue`/`audience`/`salt`/公钥 | **后续会给** | 需求方明确"后续会给"。仍阻塞生产联调，但**不阻塞开发与开发环境联调**（开发环境值已实测确定，见 A 组 Q2-Q4） |

> **Q5 不在本组**：需求方已定为"密钥固定，直接用"，不需要索取轮换策略。后果是**公钥一旦泄露无法靠轮换止损**，只能换密钥 + 重新部署——属上游选择，我方按单密钥静态配置实现，不预留多密钥槽位。
>
> **Q30 已撤销**：原拟索取「token 校验接口」。现定 token 有效性由 BFF 转发前判定（上游只有 BFF），我方不索取、不调用。见 `02` §3.3.1。

## D. 我方内部决策

| # | 决策点 | 状态 | 结论 |
|---|---|---|---|
| D1 | 引入 `PyJWT` | **结论已定，未落地** | **只加 `PyJWT` 一个包**，加进 `apps/im-gateway/pyproject.toml` 的 `dependencies`。`cryptography` 已在依赖树中（`wecom-aibot-python-sdk` 传递引入），**不需要 `pycryptodome`** |
| D3 | 错误码命名 | **结论已定，未落地** | `MSSW_JWT_INVALID` / `MSSW_USER_NOT_MAPPED` / `MSSW_CHANNEL_FORBIDDEN` / `MSSW_CHANNEL_NOT_CONFIGURED`（末项为 `/permissions` 入口未配置/已停用 → 404，见 `05` §4.1） |
| D5 | 用户映射建立方式 | **已定** | 管理员在 Console 新建用户时 `user_code` 直接填 MSSW 工号；再为该用户建 MSSW 通道映射（`channel_identity`）。两步都不能省，运行时查的是后者（见 `02` §3.4） |
| D6 | 是否保留 `email`/`phone_login` 解密 | **已定** | **不需要** |

> D1/D3 只是"认定"而非"已做"：代码里没有对应实现，也没有对应常量/错误码，但结论不会再变，只等动工。D5/D6 已是最终决策且无需额外动作（D5 落为管理员操作，D6 落为"不实现"）。
>
> **关于 `cryptography`**：它在 `requirements.txt` 里已因 `wecom-aibot-python-sdk` 而固定存在，测试代码也早就在用（`tests/e2e/wecom_probe_app.py` 生成 RSA 密钥、`wecom_media_server.py` 用 AES）。理论上 RSA 验签 + AES-ECB 可用它独立实现（约 60 行），但 `alg=none`、段长度、padding 等 JWT 边界情况自己写风险更高，**推荐用 `PyJWT`**。

## E. 已定案、单向告知 BFF

> Q29 定了"BFF 直接适配我方现有接口"之后，这三条**在语义上都已确定**，不再是开放问题。
>
> **"已定案"≠"已对齐"**：对齐意味着双方讨论、结论可能被修改；这三条不是。结论由我方拍板、不因 BFF 而变，剩下的只是**单向告知**。所以既不能并进 A 组（A 组是"不用告诉别人"），也不该叫"待确认"（会让人误以为还没定）。
>
> **三条的共同前提**：ID 的所有权在我方。`platform_user_id`（用户）、`conversation_id`（会话）、`run_id`（执行）、`entry_id`（入口）全部是**我方数据库生成的 UUID**，BFF 只负责存储与回传，不做编码/转换/映射。这正是 Q29 的直接推论——如果换成"BFF 自己维护一套 ID"，下面三条每一条都要重新论证。

### Q23：归属按 `(tenant_id, user_id)` 查库比对，BFF 直接用我方 UUID

**问的是什么**：BFF 拿到 `conversation_id` 后要不要自己维护一张「MSSW 会话 ↔ 我方会话」的映射表？归属校验到底按哪个键查？

**结论**：不维护映射，`conversation_id` 原样存、原样回传。归属校验按 `conversation_id + tenant_id + user_id` 三键查库定位。

**为什么**：`_resolve_scope` 把 `conversation_id` 连同 `tenant_id`、`identity.principal.platform_user_id` 一起交给 Runtime 查（`webchat_service.py:445-457`，实际调用在 `:449-453`）：

```python
data = await self._runtime.resolve_conversation_scope(
    conversation_id,
    tenant_id=self._tenant_id,
    user_id=identity.principal.platform_user_id,
)
```

三键命中才算归属成立，且随后还要校验会话的 `agent_id` 在当前授权集合内（撤销授权会影响后续访问，`:455-456`）。因为 `conversation_id` 是我方 UUID，BFF 没有构造它的能力，也就没有"猜一个 ID 去试探"的空间。

**BFF 要做什么**：`conversation_id` 存下来复用，不做任何转换。**BFF 不需要、也不应该**维护映射表——多了这一层就多一份不一致的可能。

### Q24：`user_id` 与 `platform_user_id` 是同一 UUID，且网关必须自行补归属校验

**问的是什么**：请求里带的 `user_id` 和网关从 Console `resolve` 拿到的 `platform_user_id` 是不是同一个值？如果 BFF 只传 `run_id` 不传 `conversation_id`，归属还能不能校验？

**结论**：是同一个 UUID。**并且无论 BFF 传不传 `conversation_id`，网关都必须按 `platform_user_id` 复核归属。**

**为什么**：这是**我方必做项**，不是等 BFF 确认的事项。根因在 Runtime——`_load_run_in` 的查询条件只有 `id + tenant_id + is_deleted`（`run_service.py:1872-1882`），**完全不含 user_id**：

```python
sa.select(RunRecord).where(
    RunRecord.id == run_id,
    RunRecord.tenant_id == tenant_id,
    RunRecord.is_deleted.is_(False),
)
```

这意味着**同一租户内任何人拿到 `run_id` 都能读到别人的 Run**。补偿责任在调用方（网关）：`owned_run_state` 用 `str(state["user_id"]) != str(identity.principal.platform_user_id)` 做比对，不等则抛 `COMMON_NOT_FOUND`（`webchat_service.py:184-188`）——用 404 而不是 403，是刻意不泄露资源存在性。

**两条路径都要经过它**，MSSW 接入时两条都得保留：

| 路径 | 调用点 |
|---|---|
| 资源面（查 Run / 取消 / 订阅前的公共校验） | `WebchatService._owned_run` → `webchat_service.py:461` |
| SSE 事件流建连 | `webchat_streams.py:893`（在 `get_run_scope` 之后、`open_event_stream` 之前） |

**这条对 MSSW 的额外含义**：MSSW 的 SSE 建连**不能只凭 `run_id`**。即便 BFF 有全部上下文，网关也必须先 `get_run_scope(run_id, tenant_id, platform_user_id)` 再 `owned_run_state` 比对，否则就是越权（见 G 组风险表「`run_id` 单独可达」）。原文档把它写成"若 BFF 只传 `run_id` 才需补"的条件式是错的——**风险是客观存在的，与 BFF 传什么无关**。

### Q28：`conv` = 我方 `conversation_id`

**问的是什么**：概设/上游文档里的 `conv` 字段，对应我方哪个 ID？

**结论**：就是我方的 `conversation_id`，与 Q23 是同一套 UUID，没有第二套。

**为什么**：`conversation_id` 由 Runtime 在建会话/首次受理时生成并返回（`04` 旅程 B/C、`05` §4.1.1），全程只有这一个会话标识；`run_id` 是它下面的一次执行，`entry_id` 是入口账号。三者层级不同，不存在"`conv` 另指一个 ID"的可能。

**BFF 要做什么**：不用管这个名字的差异，就是把 `conversation_id` 传回来。**网关侧不做字段别名兼容**——不接受 `conv` 作为入参名，避免多一套需要维护的输入形态（项目规范：选最优设计，不做兼容垫层）。

> **三条一起看**：Q23 定"按什么查"（三键），Q24 定"谁来补"（网关，因为 Runtime 不查 user_id），Q28 定"叫什么"（`conversation_id`）。前两条是**实现要求**，只有 Q28 纯属命名对照——需要真正"通知 BFF"的其实只有 Q28 与 Q23 的"不维护映射"。

## F. 已搁置

| # | 问题 | 原因 | 后果 |
|---|---|---|---|
| Q11 | `exp` 恒为 -1，真实会话过期机制 | 需求方"先不关心" | 我方**不依赖 `exp`**；`time` 仅用于日志可观测性，不做拒绝判定（`01` §2.1.1） |
| Q12 | token 吊销与 SSE 超时策略 | 同上 | **历史 token 可重放**的根治责任归 **BFF**（转发前校验，见 `02` §3.3.1）；SSE 超时由我方单轮时长上限覆盖 |

## G. 风险现状（Q27 解除后）

| 风险 | 状态 |
|---|---|
| ~~跨站 Cookie 不发送~~ | **已解除**（Q27：非跨站） |
| `run_id` 单独可达 | 仍存在——Runtime 的 `_load_run_in` 不带 user_id，必须调用方补 |
| **历史 token 重放** | 我方不处理——`exp` 无意义、吊销不可见。**责任前移给 BFF**（转发前校验，上游只有 BFF）；我方只保留连接寿命 + 责任边界（`02` §3.3.1） |
| `CURSOR_RUN_MISMATCH` 无人抛 | 仍存在——Runtime 零命中，需网关自行判定 |
| `CURSOR_EXPIRED` 补全未实现 | 仍存在——现有只抛错不补全 |
| 生产密钥未取得 | 仍存在（M5），只影响**生产**联调；开发环境值已实测确定，需求方"后续会给" |
| 密钥不轮换 | 仍存在（Q5 已定）——公钥泄露无法靠轮换止损，只能换密钥 + 重新部署。属上游选择，我方不额外兜底 |

## H. 现在的阻塞程度

| 级别 | 条目 |
|---|---|
| **可立即动工** | **全部 R1-R8**——A 组全确认、B 组清零、C 组只剩 M5（后续会给）、D 组结论全定、E 组已定案（仅需单向告知 BFF，不阻塞动工） |
| ~~需先定内部~~ | ~~D1（依赖）、D3（错误码命名）~~ **结论都已定**，只剩"写进代码"这一个动作 |
| 阻塞生产联调 | M5 生产密钥（需求方：后续会给） |
| ~~阻塞开发/开发环境联调~~ | 无——开发环境 `issue`/`audience`/`salt`/公钥已实测确定（A 组 Q2-Q4） |
| ~~阻塞安全验收~~ | ~~Q30~~（已撤销：token 校验责任在 BFF，我方不索取接口） |
| 不再阻塞 | ~~Q27~~、~~BFF 文档~~、~~Q18~~、~~Q19~~、~~Q5~~、~~Q20-c~~ |

> R1 动工时注意：`BUILTIN_CHANNEL_NAMES` 有**两处测试硬断言**（`tests/test_attachment_contract.py:118`、`tests/agent_worker/test_capacity_webchat_worker_notifications.py:824`），且 `tests/test_attachment_contract.py:235` 要求 `set(BUILTIN_CHANNEL_NAMES) == set(OUTBOUND_CHANNELS) | set(LOCAL_STREAM_CHANNELS)`——加 `MSSW` 时必须同时加进 `LOCAL_STREAM_CHANNELS`，否则该断言直接失败。
