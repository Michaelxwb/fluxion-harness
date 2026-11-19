# MSSW 接入 — 协议事实与决策记录

> 关联：`01-jwt-spec.md`、`02-requirements.md`、`04-user-journeys.md`、`05-interface-mapping.md`
>
> **本文档性质已变更**：原为"待确认清单"，现为**历史决策记录**。被新方案取代的条目已删除；仍有效的协议事实与代码事实保留，供追溯。
>
> **需求基线**：`.code-flow/tasks/2026-11-13/mssw-external-im-channel/`

## 保留内容与删除内容

**已删除（被新方案取代）**：

| 原条目 | 原结论 | 取代原因 |
|---|---|---|
| Q6 | 映射键用 `job_number` | 新方案 `identity_keys` 可配，MSSW 用 `_id` + `tenant_id` |
| Q8 | 工号全局唯一 | 新方案按 `identity_hash` 唯一，不依赖单字段 |
| Q14 | `/permissions` 返回 `visible` 布尔 | 新方案为 `GET /identity`，返回 `allowed` + `reason` + `grants[]` |
| Q15 | 单入口、`bot_id` 由网关配置指定 | 新方案入口由管理员在 Console 创建、`bot_id` 由管理员填 |
| Q18 | 未配置 → 404 `MSSW_CHANNEL_NOT_CONFIGURED` | 新方案 **503 `EXTERNAL_ASSERTION_UNAVAILABLE`** |
| Q23/Q24/Q28 | 归属按 `(tenant, user)`、`conv` = `conversation_id` | 新方案扩展为 user + Agent，且入口授权独立建模 |
| Q25 | 工号变更走人为删除账户 | 新方案支持编辑身份，不需要删账户 |
| Q31 | 不设本地时间窗 | 新方案加**最大会话年龄兜底**（不主动踢人） |
| D3 | 四个 `MSSW_*` 错误码 | 新方案为 `EXTERNAL_ASSERTION_*` 体系 |
| D5 | `user_code` 填工号 + `channel_identity` | 新方案为 `external_identity` + `entry_access_grant` |

**已删除（问题本身消解）**：Q16、Q21、Q22、Q26、Q30（原拟索取 token 校验接口，现定责任在 BFF）。

## 输入文档的定位（易错）

本次需求的输入是**同一个提交**（`0384f3db`）带来的 6 份文档，其中两份在 `docs/operations/` 下，容易被误认为仓库既有资产：

| 文档 | 性质 | 说明 |
|---|---|---|
| PRD / backend.design / frontend.design / scenarios | 需求与设计基线 | 明确是输入 |
| **`docs/operations/bff-agent-integration.md`** | **对外接口契约** | 15 个接口的完整定义，是需求方交付物 |
| **`docs/operations/im-channel-playbook.md`** | **实现规范** | 十项必改清单 + 13 条反模式 + 22 项 DoD，颗粒度**高于** PRD |

> **教训**：曾因 `bff-agent-integration.md` 已存在于 `docs/` 而误判 `/client-config` 是"设计里顺手加的"，进而提议移除。实际它是需求方明确定义的接口。**判断依据应看来源提交，而不是文件所在目录。**

---

## A. 协议与代码事实（仍然有效）

| # | 结论 | 依据 |
|---|---|---|
| Q1 | 公钥为 **PKCS#1**（`BEGIN RSA PUBLIC KEY`），PyJWT 可直接加载 | `01` §5 实测 |
| Q2 | `issue` = `bce0e2127824dea11cb153bad3ae4512`（开发环境） | 需求方 + 实测 |
| Q3 | `audience` = `9d7cdc4178c342482b0fb26fa75b8f71`（开发环境） | 需求方 + 实测 |
| Q4 | `salt` = `dxbwzhls2019qflzwcwwyc510zxgwlzm`（开发环境，32 字节，无需补零） | 需求方 + 实测 |
| Q7 | `_id` 是专家 ID（24 位 ObjectId），`job_number` 是工号（明文） | 签发方代码 + 实测 |
| Q9 | token 在 Cookie，键名 **`soc-token`** | 真实 curl 样例 |
| Q10 | 不实现签发方的免校验分支 | 我方安全决策 |
| Q13 | **无权限 → 拒绝**，不自动建档（不做 JIT provisioning） | 需求方 |
| Q17 | `customer_id` 在本仓库**完全不存在** | 全仓搜索 |
| Q27 | **不是跨站**——BFF 后端服务直接转发，`SameSite=Strict` Cookie 可正常送达 | 需求方 |
| Q29 | **BFF 直接适配我方现有接口**，无需实现 `/internal/agent-provider/v1/*` | 需求方 |
| Q5 | **密钥固定，不轮换**——单公钥静态配置，不实现轮换、不做多密钥并存、不预留版本槽位 | 需求方 |

> **AES key 派生算法**：`salt` 右侧补 `\x00` 到 32 字节（`salt` 长度须 ≤32），PKCS#7 补位，ECB 模式。详见 `01` §3。这条曾是阻塞项，现已由实测确认，**不再是开放问题**。
>
> **`iss` 是单元素数组**：token 里是 `["<密文>"]`，比对时也要按数组处理，否则 100% 失败。

## B. 补充论证（保留，供理解"为什么"）

### 幂等为什么"以我方为准"

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

五个键全是我方的值，BFF 只需原样回传自己上次用的 `Idempotency-Key`，**不需要维护 `conversation_id` 映射表**。

### Q20-c：历史分页与事件流是同一套水位

**结论：两者都读 `conversation.last_seq` 这一个 canonical seq。**

| 面 | 水位来源 | 位置 |
|---|---|---|
| 历史分页 | `watermark = conversation.last_seq` | `webchat_history.py:375`、`:384` |
| 事件流游标校验 | `through = await self.watermark(...)`，取「会话行 `last_seq` 与已持久行最大 seq」的较大者 | `event_tail_hub.py:652-675`、`:20-22` |

两边是**同一个 seq 空间**，`through_seq`（历史）与 `after_seq`（事件流）可直接续接——"游标过期按历史补全"在数据层面成立。

**但"补全"这一步仍不存在**：`check_cursor` 对 `CURSOR_EXPIRED` 只抛错（`event_tail_hub.py:669-674`），不返回补全数据。R6 需自行实现"过期 → 转查历史"。

> **注意**：新版方案区分了**消息序号**与**通知序号**——这两者才是不能互相比较的两套序列。上面的 canonical seq 指消息序号。

### 为什么用「加密期望值比对」而非解密

上游 AES 实现是非标准 padding，解出的明文尾部可能带脏字节，**解密再比对不可靠**。所以改为「加密期望值 + 密文逐字节比对」。这也是不实现 AES 解密的原因。

> **易踩的坑**：`easm-exposure-service-mssw` 的 `py_jwt_cipher.py` 是 **CBC 模式、key 由三段拼接**，与 MSSW token 的 ECB 无关，不要照抄。

## C. 需向 MSSW 侧索取

| # | 需要 | 状态 | 说明 |
|---|---|---|---|
| M4 | 真实响应样例 | 可选 | 用于对照，不阻塞 |
| M5 | **生产环境** `issue`/`audience`/`salt`/公钥 | **后续会给** | 只阻塞生产联调，**不阻塞开发**（开发环境值见 A 组 Q2-Q4） |

**唯一待答项**（编号与 `.code-flow/tasks/2026-11-13/mssw-external-im-channel/mssw-external-im-channel.open-questions-for-mssw.md` 一致）：

| # | 问题 | 状态 | 影响 |
|---|---|---|---|
| ~~N4~~ | ~~MSSWWEB 入口的 `bot_id` 是否必要~~ | **已定案：保留必填** | `bot_id` 是 `bot_account` 既有字段（`NOT NULL`、全局唯一），参与出站路由原值与旧 `identity_key`；不为单通道放宽三通道共用表约束 |

**已定案（留档）**：

| # | 事项 | 结论 |
|---|---|---|
| N1 | 用户规模与录入方式 | 第一期管理员在 Console 用户详情页逐条手工录入 `expert_id`；不做批量导入、不依赖 MSSW 名单文件；批量能力推迟到后续需求 |
| N2 | 名单包含哪些字段 | 不依赖名单；手工录入必填项即身份键 `expert_id` + `tenant_id` |
| N3 | `tenant_id` 稳定性 | **相对固定**，跨租户调动属罕见情形；`identity_hash` 含 `tenant_id` 可接受，若将来调动导致会话断裂，按新身份处理即可 |

> **已确认**：`_id`（= `expert_id`）在员工离职/重新入职时**不变**，可长期作身份键（原「改用 `job_number` 作映射键」的判断已作废）。

## D. 我方内部决策

| # | 决策点 | 状态 | 结论 |
|---|---|---|---|
| D1 | 引入 `PyJWT` | 待落地 | 只加 `PyJWT` 一个包。`cryptography` 已在依赖树中（`wecom-aibot-python-sdk` 传递引入），**不需要 `pycryptodome`** |
| D6 | 是否解密 `email`/`phone_login` | 已定 | **不需要**，也不落库 |

> **关于 `cryptography`**：它在 `requirements.txt` 里已因 `wecom-aibot-python-sdk` 而固定存在，测试代码也早就在用（`tests/e2e/wecom_probe_app.py` 生成 RSA 密钥、`wecom_media_server.py` 用 AES）。理论上 RSA 验签 + AES-ECB 可用它独立实现，但 `alg=none`、段长度、padding 等 JWT 边界情况自己写风险更高，**推荐用 `PyJWT`**。

## E. 角色与责任边界

| 事项 | 责任方 | 说明 |
|---|---|---|
| token 有效性 / 是否吊销 | **BFF** | 吊销状态在 MSSW 侧 Redis，我方无数据源。BFF 转发前判定 |
| 签名与 claim 校验 | **我方（Console）** | RS256 + AES(`aud`/`iss`) + claim 策略，fail-closed |
| `entry_id` / `conversation_id` / `run_id` 的所有权 | **我方** | 全部是我方生成的 UUID，BFF 存下复用，不维护映射表 |
| 身份绑定 | **管理员（Console）** | 预先建 `external_identity` + `entry_access_grant`，不做 JIT |
| 最大会话年龄 | **我方（兜底）** | 不主动踢人，只限制 SSE 续订的绝对上限 |
