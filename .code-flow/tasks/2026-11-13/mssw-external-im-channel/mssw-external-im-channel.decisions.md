# MSSW 外部 IM 通道 — 决策记录

> 记录日期：2026-11-13
> 关联：`mssw-external-im-channel.prd.md`、`mssw-external-im-channel.backend.design.md`
> 说明：本文记录**评审过程中拍板的决策**，以及支撑这些决策的事实依据。PRD/设计描述"最终形态"，本文说明"为什么是这个形态"。

---

## 1. 文档关系

本次需求有两处文档：

| 位置 | 内容 | 性质 |
|---|---|---|
| `.code-flow/tasks/2026-11-13/mssw-external-im-channel/` | PRD + 前后端设计 + 场景 | **需求基线**，本期实现依据 |
| `docs/mssw-channel/` | 协议事实（`01`）+ 需求梳理（`02`）+ 决策记录（`03`）+ 旅程（`04`）+ 接口映射（`05`） | 长期资产，已按新方案改写 |
| **`docs/operations/bff-agent-integration.md`** | 15 个接口的完整契约 | **需求方交付物**（同属提交 `0384f3db`），不是仓库既有资产 |
| **`docs/operations/im-channel-playbook.md`** | 十项必改 + 13 反模式 + 22 项 DoD | **需求方交付物**，颗粒度高于 PRD，属实现规范 |

> **易错点**：这两份在 `docs/operations/` 下，容易误认为既有文档。实际与 PRD 同批提交。`/client-config` 曾被误判为"设计顺手加的"并提议移除，就是依据目录而非来源造成的。

原 `.code-flow/tasks/2026-11-13/mssw-channel/`（旧 PRD 与设计）已删除，内容被新目录取代。

---

## 2. 关键事实（已核实，不可凭猜测推翻）

| # | 事实 | 依据 |
|---|---|---|
| F1 | `aud`/`iss` 的 AES key 派生：**`salt` 右侧补 `\x00` 到 32 字节**（`salt` 长度须 ≤32），PKCS#7 补位，ECB 模式 | 实测通过（`docs/mssw-channel/01-jwt-spec.md` §3、§5） |
| F2 | `iss` 在 token 里是**单元素数组** `["<密文>"]` | 同上 |
| F3 | `_id` 是专家 ID（24 位 ObjectId），`job_number` 是工号（明文 5-7 位） | 签发方代码 + 实测 |
| F4 | `easm-exposure-service-mssw` 的 `py_jwt_cipher.py` 是 **CBC 模式、key 三段拼接**，与本需求无关 | 源码比对 |
| F5 | `_load_run_in` 只按 `id + tenant_id` 查，**不含 user_id** | `run_service.py:1872-1882` |
| F6 | 历史分页与事件流读**同一个** `conversation.last_seq` | `webchat_history.py:375`、`event_tail_hub.py:652-675` |
| F7 | `CHANNEL_TOKENS`（`test_channel_neutrality.py:25`）是**手写词表** | 源码 |
| F8 | 通道名硬编码在 **6 处**，其中 `webchat_core.py:69` 的 `_run_request` 决定最终落库 | 源码链路核实 |
| F9 | `cryptography` 已在依赖树中（`wecom-aibot-python-sdk` 传递引入） | `requirements.txt` |

---

## 3. 已定决策

### 3.1 方案与边界（A 组）

| # | 决策 | 说明 |
|---|---|---|
| A1 | **错误码用 `EXTERNAL_ASSERTION_*` 体系** | 平台无关。未配置从 404 改为 **503**，是行为变化而非改名 |
| A2 | **不强制下线** | 加最大会话年龄兜底，但只限制 SSE 续订，**不主动踢人**。token 有效性与吊销由 BFF 保证 |
| A3 | **用户级身份 + 入口授权（两表）** | `external_identity` + `entry_access_grant` |
| A4 | **验签在 Console** | 网关不持因子、不持库；因子放 K8s Secret |
| A5 | **通道名 `MSSWWEB`** | 与 `platform_key=mssw`（小写）是两层概念 |
| A6 | **`bot_id` 由管理员在 Console 填写** | 全局唯一，重复报 `BOT_ID_EXISTS` |
| A7 | **新增 3 张表** | `external_identity`、`entry_access_grant`、`auth_cache_sync_state` + `platform_user.auth_revision` |
| A8 | **接受 Runtime 改动** | `conversation.channel` 来源列、跨入口聚合、通知已读水位 |

### 3.2 旧文档 B 类（会被新方案丢失的事实，全部保留）

| # | 内容 |
|---|---|
| B1 | AES key 派生算法 = F1，用测试向量驱动，不猜 |
| B2 | JWT 完整 claim 清单与实测值（含 `role` 对象内也有 `_id`，与顶层同名不同义） |
| B3 | `_id` 与 `job_number` 的性质区分 = F3 |
| B4 | `iss` 是数组 = F2 |
| B6 | 6 处通道名写死清单 = F8；**漏改 ⑥ 时 ③④⑤ 无效** |
| B7 | `CHANNEL_TOKENS` 必须加 `msswwweb`，否则假绿灯 = F7 |
| B8 | 三处硬断言同步更新 |
| B9 | `MSSWWEB` **进** `LOCAL_STREAM_CHANNELS`、**不进** `OUTBOUND_CHANNELS`（BFF 拉取模式，无主动投递；进错会让附件交付抛 `ATTACHMENT_WRITE_UNAVAILABLE`） |
| B10 | 用「加密期望值比对」而非解密：上游 padding 非标准，解密不可靠。**以能准确解出 JWT 的方式为准** |
| B11 | `soc-token` 是唯一凭据，不需要 `X-Chat-Context` |

### 3.3 开放项处置（C 组）

| # | 处置 |
|---|---|
| C1 | `identity_keys` 以新方案为准：`expert_id` = `_id`，加 `tenant_id` |
| C2 | `login_source` 首版不配置，不纳入校验 |
| C3 | 弃用旧任务目录，以 `mssw-external-im-channel/` 为准；`docs/mssw-channel/` 保留并更新 |
| C4 | `docs/mssw-channel/` 六份文档按新方案改写 |
| C5 | 性能指标沿用新文档「不预设数值、上线后压测」 |
| C6 | 跨入口共享以新方案为准（需求方输入） |
| C8 | 错误码以新文档为准 |
| C9 | 前端补 `identity_field_labels`（claim 名 → 中文名） |

### 3.4 内部确认（I 组）

| # | 结论 | 理由 |
|---|---|---|
| I1 | `identity_keys` 里的 `expert_id` **就是 JWT 的 `_id`** | 不是别名，无需映射层；可读性用 `identity_field_labels` 解决 |
| I3 | 会话**保留来源**（`entry_id`/`channel`），接口必须返回；**页面不强制展示来源** | 与需求方 FEAT-01 验收口径对齐：「会话保留创建来源，Run/通知按实际发送来源记录，页面不强制展示来源」 |
| I4 | 路由**固定前缀** `/api/v1/msswwweb` | 通用化已在数据层实现（`platform_key` 从通道名反查配置），路径变量只省掉 nginx 一行，却要变更 BFF 约定 |

---

## 4. 待外部确认

**待答项**：

| # | 问题 | 影响 | 阻塞 |
|---|---|---|---|
| N4 | MSSWWEB 入口的 `bot_id` 是否必要 | 必填、全局唯一，但不参与身份、BFF 不可见 | **不阻塞**（无意见则保持现状） |
| M5 | 生产环境 `issue`/`audience`/`salt`/公钥 | 生产联调 | 只阻塞生产 |

**已定案**：

| # | 事项 | 结论 |
|---|---|---|
| N1 | 用户规模与录入方式 | 第一期管理员在 Console **用户详情页**手工逐条录入。录入入口是输入文档既有设计：`ExternalIdentitySection` + `MsswwebIdentityModal`（字段按 `identity_keys` 动态渲染，标签取 `identity_field_labels`）+ `EntryGrantModal`。接口 `GET|PUT /users/{id}/external-identities[...]` 与 `POST|DELETE .../grants[...]`，落 `external_identity` + `entry_access_grant`。**不新增批量导入、不依赖名单文件、不用 `metadata_json` 承载身份。** |
| N2 | 名单包含哪些字段 | 不依赖名单；必填项即身份键 `expert_id` + `tenant_id` |
| N3 | `tenant_id` 稳定性 | **相对固定**，`identity_hash` 含 `tenant_id` 可接受 |

> **已确认**：`_id`（= `expert_id`）在员工离职/重新入职时**不变**，可长期作身份键，原风险解除。

---

## 5. 工程实现要点

### 5.1 必须做对的六件事

| # | 事项 | 做错的后果 |
|---|---|---|
| 1 | 6 处通道名参数化（含 `webchat_core.py:69`） | MSSW 请求以 WEBCHAT 落库 |
| 2 | 集成测试断言 `RunRequest.channel == "MSSWWEB"` | 只断言 202 时通道错了也能过 |
| 3 | `CHANNEL_TOKENS` 加 `msswwweb` | 架构机检假绿灯 |
| 4 | 把「注册表通道必须在词表」变成测试 | 以后加通道又忘了加词 |
| 5 | AES 派生按 F1 实现 + 测试向量 | 全量 401 |
| 6 | SSE **每个事件 yield 前**复核 | 越权读 |

### 5.2 配置放置（沿用现有体系）

| 配置 | 归属 | 理由 |
|---|---|---|
| 公钥 PEM | K8s Secret，**文件挂载** + 配置存路径 | 多行 PEM 塞环境变量易被 YAML 破坏；`wecom_ws_ca_file` 有先例 |
| `salt` | K8s Secret，`secretKeyRef` 直出 | 32 字节短值，不需要单独挂文件 |
| `audience`/`issue` 明文 | ConfigMap | 非敏感（离开 salt 无用） |
| `MSSWWEB` 启用开关 | ConfigMap | 非敏感 |
| 历史页大小等运营参数 | `PlatformSettings` | 与 `WebchatSettings` 同机制 |

**无配置中心客户端**：本仓库只有 pydantic-settings 读环境变量，K8s 通过 `envFrom` 注入。ConfigMap 改动需重启 Pod 生效。

### 5.3 fail-closed 要求

- Secret 缺失/损坏 → **只让 MSSWWEB 返回 503**，不影响启动与其他通道
- `IDENTITY_KEYS_MISMATCH` 等配置错误在**启动期**暴露，不留到运行期

---

## 6. 通用化边界

这是「**JWT 类外部平台**」的通用方案，不是「所有外部系统」的通用方案。

| 层 | 换平台要改吗 |
|---|---|
| 身份模型（`external_identity` + `entry_access_grant` + `identity_keys`） | 只改配置值 |
| 验签（RS256 + AES 因子 + 算法白名单） | 不改 |
| 通道与路由 | 加通道名 + nginx location |
| 会话/历史/SSE/附件 | 不改 |

**需要写代码的例外**：
1. JWT 结构差异大到配置表达不了（嵌套 claim、多级签名、非 RS256）→ 平台专用 claim 提取器
2. 平台不是 JWT（OAuth2、mTLS）→ 新的认证适配器

第一期**不实现**平台专用提取器，只保留接口形状。
