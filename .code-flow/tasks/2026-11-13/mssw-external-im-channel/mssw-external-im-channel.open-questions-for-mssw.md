# MSSW 外部 IM 通道 — 需与需求方确认的问题

> 目的：以下问题会影响实现方案，需要在开发前对齐。
> **问题 1 与问题 2 已在我方内部定案**（第一期由管理员在 Console 手工录入），保留记录备查。

---

## 背景

我方通过验签 MSSW 签发的 JWT、取其身份字段（`expert_id` + `tenant_id`），映射到我方平台用户。映射必须**由管理员预先建立**，用户首次访问才能通过。

现有能力：管理员可**逐条**创建平台用户、逐条建立外部身份与入口授权。**没有批量接口。**

---

## 问题 1：用户规模与录入方式（已定案）

**结论**：第一期采用**管理员在 Console 手工逐条录入**，不需要批量导入能力，也不需要 MSSW 提供名单文件。

**Console 录入入口（以输入文档既有设计为准）**：

- 入口在**用户详情页**的 `ExternalIdentitySection` 区块，不在用户编辑弹窗；
- 「新增/编辑」打开 `MsswwebIdentityModal`，表单字段由平台配置 `identity_keys` 动态渲染（MSSW 为 `expert_id` + `tenant_id`），标签用 `identity_field_labels` 显示为「专家 ID（24 位）」「租户 ID」；
- 「添加入口」打开 `EntryGrantModal`，从 MSSWWEB 入口列表分页搜索后授权；
- 接口为 `PUT|GET /users/{id}/external-identities` 与 `POST|DELETE .../grants`；
- 数据写入 `external_identity`（用户级）+ `entry_access_grant`。

**为什么不再需要问题 1 的规模答案**：第一期只服务已明确梳理的用户范围，量级在管理员可手工处理的范围内；批量能力推迟到后续需求，届时再按实际规模评估。

<details>
<summary>原问题（保留备查，不再阻塞）</summary>

| 项 | 原需回答 |
|---|---|
| 用户规模 | 预期用户数几十 / 几百 / 上千？ |
| 名单来源 | MSSW 能否导出完整名单？格式与时机？ |
| 变更频率 | 入职/离职怎么同步？ |
| 录入负责人 | 我方管理员录入还是接口拉取？ |

</details>

---

## 问题 2：名单里有哪些字段（已定案）

**结论**：不依赖名单文件。管理员手工录入的必填项就是身份键集合：`expert_id`（= `_id`，24 位 ObjectId）与 `tenant_id`。`job_number` / 姓名 / 邮箱等**不作为第一期输入**。

**影响**：不做自动匹配，管理员在用户详情页直接选定「这个 `expert_id` 对应哪个我方用户」，人工对应。填错由管理员在 Console 编辑修正（编辑身份保留历史会话）。

---

## 问题 3：`expert_id` 与 `tenant_id` 的稳定性（已确认）

**已确认**：`_id` 在员工离职/重新入职时**不变**，可长期作身份键。

**已确认**：`tenant_id` **相对固定**，跨租户调动属罕见情形。

**结论**：身份键沿用 `["expert_id", "tenant_id"]`，`identity_hash` 含 `tenant_id` 可接受。若将来某员工调动导致其历史会话断裂，按新身份处理即可——这是可接受的业务语义，不改身份键设计。

---

## 问题 4（N4）：`bot_id` 是否必要（已定案）

**结论：保留必填**，无需需求方答复。

**依据**

`bot_id` 是 `control.bot_account` 表的**既有字段**（`String(256) NOT NULL`，唯一索引 `uq_bot_account_bot_id` WHERE `is_deleted = false`），不是本次新增。它在三处实际生效：

1. 出站路由原值——随 Run 传到 `agent-worker` 的 `delivery_route`，发消息回上游时按它定位机器人；
2. 旧身份键构造——`channel_service.py` 的 `build_identity_key(channel, bot_id, external_user_id)`（本次新方案用 `external_identity` + `identity_hash`，不使用它）；
3. Console 入口创建必填校验。

去掉它意味着放宽一张 WECOM / WEBCHAT / MSSWWEB 三通道共用表的 `NOT NULL` 约束，并在出站路径引入 `None` 分支，代价高于让管理员为 MSSWWEB 入口填一个唯一值（填什么都可以，只要全局唯一）。

---

## 汇总

| # | 问题 | 阻塞 | 需谁回答 |
|---|---|---|---|
| ~~1~~ | ~~用户规模与录入方式~~ | **已定案**：第一期管理员在 Console 用户详情页手工逐条录入 | — |
| ~~2~~ | ~~名单字段~~ | **已定案**：不依赖名单 | — |
| ~~3~~ | ~~`tenant_id` 稳定性~~ | **已确认相对固定**，不阻塞 | — |
| ~~4~~ | ~~`bot_id` 是否必要~~ | **已定案：保留必填** | — |

---

## 附：我方已定的（无需答复）

| 项 | 结论 |
|---|---|
| 身份字段 | `expert_id`（= `_id`）+ `tenant_id` |
| `_id` 稳定性 | 已确认不变，可长期作身份键 |
| `tenant_id` 稳定性 | 已确认相对固定，`identity_hash` 含 `tenant_id` 可接受 |
| 通道名 / 平台键 | `MSSWWEB` / `mssw` |
| 路由 | 固定前缀 `/api/v1/msswwweb` |
| 未配置行为 | 503 `EXTERNAL_ASSERTION_UNAVAILABLE` |
| token 吊销判定 | 责任在 BFF（转发前校验），我方不判 |
| 绑定方式 | 管理员在 Console 填写身份 JSON，不做 JIT 自动建档 |
