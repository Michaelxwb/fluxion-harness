---
design_template: full
---

# WebChat 会话删除与附件格式支持 后端设计

> **文档编号**: MOD-WEBCHAT-LIFECYCLE-ATTACH-1.0
> **文档版本**: v1.0
> **创建日期**: 2026-11-12
> **文档状态**: 设计评审中

## 1. 文档控制

| 项目 | 内容 |
|---|---|
| 模块 | WebChat 会话生命周期（Gateway + Runtime）、附件门控与读取（Gateway + Runtime + contracts） |
| Owner | `muad-ai-gateway`（门控与公开路径）；`muad-agent-runtime`（会话与 Run 的唯一 Owner）；`muad_contracts`（附件格式唯一事实） |
| 数据 Owner | `runtime` schema（conversation / run_record / canonical_event / channel_notification） |
| 前置模块 | agent-capacity-webchat（会话/历史/通知）、attachment-round-trip（附件往返）、webchat-capacity（上传门控） |
| 建议代码位置 | `packages/contracts/src/muad_contracts/attachments.py`（新增）、`apps/im-gateway/.../application/{webchat_service,webchat_uploads,attachment_gate,runtime_client}.py`、`apps/agent-runtime/.../application/{conversation_deletion,webchat_history,channel_notifications}.py`、`apps/agent-runtime/.../application/attachments/{archive_reader,member_paths,tools}.py` |

### 1.1 修订历史

| 版本 | 日期 | 作者 | 变更描述 |
|---|---|---|---|
| v1.0 | 2026-11-12 | fluxion-harness | 初始设计：软删会话 + 同请求取消活跃 Run + 通知收口；zip 准入与受限读取；附件格式共同能力收口到 contracts |

## 2. 需求分析

### 2.1 需求概述

两件互相独立但共用同一批附件/会话读路径的事：

1. **会话删除**：WebChat 目前只能依赖保留期过期与达上限自动归档。本次给用户一个主动删除能力：软删会话行（既有 `is_deleted` 口径），同请求内取消该会话的活跃 Run，并把该会话的通知与未读数一起收口。
2. **zip 附件**：附件 MIME 白名单是网关里的一个常量，Runtime 的抽取器另有一份能力分支，两者互不知道。本次把「支持哪些附件格式」收敛为 `muad_contracts` 的唯一事实，网关门控、Runtime 抽取与前端展示三处共同引用；并补齐 zip 的准入与受限读取。

### 2.2 功能方案

| 功能ID | 功能名称 | 来源 |
|---|---|---|
| FEAT-01 | 会话删除接口（公开 + 内部受信） | PRD |
| FEAT-02 | 活跃 Run 同请求取消 | PRD |
| FEAT-03 | 通知收口（删除时批量软删 + 写入侧判定） | PRD |
| FEAT-05 | zip 准入与白名单收口 | PRD |
| FEAT-06 | Runtime 受限 zip 读取 | PRD |
| FEAT-07 | 支持格式随客户端配置投影 | PRD |
| FEAT-08 | 规范同步 | PRD |

FEAT-04（前端删除入口与状态收敛）见 `webchat-conversation-delete-and-attachment-formats.frontend.design.md`。

### 2.3 范围与边界

- In：软删会话、活跃 Run 取消、通知与未读数收口、zip 两种 MIME 准入、受限 zip 读取、格式清单投影。
- Out：Console 管理侧会话管理、硬删与存储回收、rar/7z/tar、zip 内图片内联为多模态、会话重命名与手动归档。

### 2.4 验收条件

人工场景见 `webchat-conversation-delete-and-attachment-formats.scenarios.md` 的 S-/E-/B- 编号；自动化断言见 §3.5。

## 3. 技术设计

### 3.1 方案选型

| 方案 | 结论 |
|---|---|
| 删除落点在 Gateway | 否：Gateway 不持库，会话与 Run 的终态只归 Runtime（`RULE-im-002` 的同一精神） |
| 删除落点在 Runtime，Gateway 只门控转发 | **采用**：与详情/消息/上传/下载同一条路径 |
| 删除时同步等待 Run 终态 | 否：删除接口会随 Run 执行时长阻塞，且要定义等待超时 |
| 删除时同请求取消、不等终态 | **采用**：取消是既有同步语义，收尾窗口由通知收口兜住 |
| 物理删除会话与关联行 | 否：既有全仓口径是软删（`RULE-data-001`），且有物理 FK 与审计引用 |
| 通知过滤放在读取侧（JOIN 会话未删） | 否：会改变分页 `total` 与 `next_seq` 水位口径（窗口内全被过滤时游标可能原地打转） |
| 通知过滤放在写入侧 + 删除时批量软删 | **采用**：读取侧无需改动，游标语义零变化 |
| zip 读取新加一个工具 | 否：模型要重新学一套分页语义；`read_attachment` 已具备分段与截断 |
| zip 读取复用 `_extract_document` 的文本投影 | **采用**：清单 + 文本成员拼成一份可分段文本 |
| 白名单继续留在 Gateway 常量 | 否：与 Runtime 能力漂移（当前已经开始漂移：`kind_for` 只认白名单，抽取器另有一份） |
| 白名单收口到 `muad_contracts` | **采用**：Gateway、Runtime、client-config 投影、前端 accept 同一来源 |

### 3.2 架构设计

```text
浏览器 DELETE /api/v1/webchat/conversations/{id}
  └─ Gateway WebchatService.delete_conversation(identity, id)     ← CHAT 主体门控
       └─ RuntimeClient.delete_conversation(tenant, user, id)
            └─ DELETE /internal/webchat/conversations/{id}         ← 服务身份门控
                 └─ ConversationDeletionService.delete()
                      ├─ 核属（tenant+user，含软删行 → 幂等）
                      ├─ RunService.cancel_run(active_run_id)       ← 既有取消语义
                      └─ 软删 conversation / channel_conversation / channel_notification

上传 zip
  └─ Gateway webchat_uploads._allowed_media_type()  ──┐
                                                      ├─> muad_contracts.attachments
  └─ Runtime tools._extract_document()  ─────────────┘     （ALLOWED / ARCHIVE / TEXT / EXTENSIONS）

客户端配置
  └─ WebchatClientConfig.from_settings() → WebchatClientLimits.upload_formats（同一清单投影）
```

#### 3.2.1 共同能力收口（本次的硬要求）

| 共同能力 | 收口前 | 收口位置 | 消费方 |
|---|---|---|---|
| 允许上传的媒体类型 | Gateway 常量 `attachment_gate.ALLOWED_MEDIA_TYPES`；Runtime 抽取分支各自判断 | `muad_contracts.attachments.ALLOWED_ATTACHMENT_MEDIA_TYPES` | 网关门控、Runtime 抽取、client-config 投影 |
| 格式的展示形态（扩展名） | 无（前端无法展示） | `muad_contracts.attachments.SUPPORTED_UPLOAD_FORMATS` | 投影 → 前端提示与 `accept` |
| 类型分类谓词（压缩包 / 文本） | Runtime 内按 MIME 字符串硬写；Gateway 无 | `is_archive_media_type()` / `is_text_media_type()` | Runtime 抽取、Gateway 门控（可选） |
| 压缩包成员路径规范化 | 仅 `archive_tools.validate_member_path`（打包用） | `attachments/member_paths.normalize_member_path` | `create_archive` 与 zip 读取共用同一规则 |
| 活跃 Run 定位与取消 | `run_service` 内部 | 复用 `RunService.cancel_run` + 既有活跃状态集合 | 删除编排 |
| 「会话是否已删」的判定 | 读取侧零散过滤 | `channel_notifications._entry_id_of`（写入侧单点）+ 删除时批量软删 | 通知写入、收件箱 |

> 收口原则：一个事实只有一个定义处，其余位置引用它。本次**不新增兼容层**：Gateway 本地常量直接删除，调用方改为从 contracts 导入。

### 3.3 数据设计

**不新增表、不新增列、无迁移。**

会话删除（单事务，取消由 `RunService` 自己提交，见 §3.5 可靠性）：

| 表 | 变更 | 说明 |
|---|---|---|
| `runtime.conversation` | `is_deleted=true, update_time=now()` | 既有列表/详情/续聊/产物下载/事件追尾全部已带 `is_deleted=false`，软删即全面不可见 |
| `runtime.channel_conversation` | 指向该会话的指针行 `is_deleted=true` | 防御性清理；指针目标读取本就过滤软删，避免留下悬空指针 |
| `runtime.channel_notification` | `WHERE tenant/user/conversation_id` 且未删 → `is_deleted=true` | 收件箱是独立表，不随会话自动消失 |

不动的数据与理由：

- `run_record` / `canonical_event` / `artifact` / `tool_call_audit`：审计与事实保留；下载与历史读取已按会话软删过滤，用户侧不可见。
- `channel_upload`：暂存登记有自己的 TTL 清理（`cleanup_expired_staging`），不由会话删除驱动。
- `memory_consolidation_job`：查询已带 `Conversation.is_deleted=false`（`memory_jobs.py`），自然不再产出。

索引：删除查找走 `(id)` 主键 + `(tenant_id, user_id)` 过滤；活跃 Run 查找走既有 `uq_run_record_active_conversation(conversation_id)`；通知批量更新走 `channel_notification(conversation_id)` 既有索引。无需新增索引。

### 3.4 接口设计

#### API-16 会话删除（WebChat 公开）

```http
DELETE /api/v1/webchat/conversations/{conversation_id}
```

- 门控：CHAT 主体（`ChatIdentityDep`），与详情/消息同一入口门控。
- 归属：不要求调用方自报入口/Agent；Runtime 按 `(tenant_id, user_id, conversation_id)` 核属。
- 响应（统一封套）：

```python
class WebchatConversationDeletion(WebchatContract):
    conversation_id: UUID
    deleted: Literal[True] = True
```

- 幂等：同一会话重复删除返回相同响应（已软删视为已达成）。他人/不存在/无入口归属一律 `COMMON_NOT_FOUND`（不泄露存在性）。
- 不新增错误码，不新增文案词条；既有 `COMMON_NOT_FOUND` 即可覆盖全部失败面。

#### API-12 会话删除（Runtime 内部受信）

```http
DELETE /internal/webchat/conversations/{conversation_id}?user_id=...
```

- 门控：`require_internal_service`（与 `/internal/webchat/*` 其余端点一致）；租户取自部署中间件。
- 参数只有 `user_id` 与路径 id：删除是用户对自己数据的清理，**不要求当前仍持有该 Agent 授权**（授权被撤销后用户仍应能清理历史）；入口/Agent 不入参，避免调用方自报。
- 响应与 API-16 同形（内部直接回 `WebchatConversationDeletion` 的 `model_dump`）。

编排 `ConversationDeletionService`：

```python
class RunCancelPort(Protocol):
    async def cancel_run(self, run_id: uuid.UUID, tenant_id: str) -> Any: ...

class ConversationDeletionService:
    def __init__(self, session: AsyncSession, runs: RunCancelPort) -> None: ...

    async def delete(
        self, *, tenant_id: str, user_id: uuid.UUID, conversation_id: uuid.UUID
    ) -> WebchatConversationDeletion: ...
```

步骤（顺序不可换：核属与取活跃 Run 都是只读，写之前不持有任何未提交变更）：

1. `SELECT conversation WHERE id AND tenant_id AND user_id`（**不过滤** `is_deleted`）；缺行或 `channel_account_id IS NULL` → `COMMON_NOT_FOUND`。
2. 已 `is_deleted` → 直接返回成功（幂等分支，不再取消）。
3. `SELECT run_record.id WHERE conversation_id AND status ∈ ACTIVE_RUN_STATUSES AND is_deleted=false AND tenant_id`（至多一行）；有则 `runs.cancel_run(run_id, tenant_id)`。
4. `UPDATE conversation SET is_deleted=true`；`UPDATE channel_conversation SET is_deleted=true WHERE conversation_id`；`UPDATE channel_notification SET is_deleted=true WHERE tenant_id AND user_id AND conversation_id AND is_deleted=false`；提交。
5. 记一条结构化日志（`webchat_conversation_deleted`，含 `conversation_id`、是否取消过 Run）。

`ACTIVE_RUN_STATUSES` 取 `run_service` 的定义（唯一事实）；不在本模块复制第二份。

#### API-19 客户端配置投影扩展

`WebchatClientLimits` 增加一个字段：

```python
class AttachmentFormat(ContractModel):
    media_type: str
    extensions: tuple[str, ...]

class WebchatClientLimits(WebchatContract):
    ...
    upload_formats: tuple[AttachmentFormat, ...]
```

- 值来自 `muad_contracts.attachments.SUPPORTED_UPLOAD_FORMATS`，与门控同一事实；投影里不含任何内部键或存储信息。
- 前端严格解析（缺字段即配置非法），与现有投影字段同一口径。

#### API-06 附件读取语义扩展（`read_attachment`）

- 签名、分页、`search_attachment` 语义**不变**；压缩包被当作可分段文本来读。
- 抽取结果为：

```text
[压缩包：bundle.zip] 共 3 个可读文本成员 / 12 个成员（总解压 1.2 MiB）
- docs/a.md · 4.1 KiB · text
- data/rows.csv · 8.0 KiB · text
- logo.png · 20 KiB · 二进制（不读取）
--- docs/a.md ---
<成员文本>
--- data/rows.csv ---
<成员文本>
```

- 清单永远在最前面，因此「包内有什么」与正文首页同屏可见；正文受既有 `limit` 分段约束。
- 非文本成员只出现在清单，不输出内容、不生成图像块、不触发 `view_image` 的补消息。

### 3.5 质量实现方案

**安全（zip）**

- 全部在内存中读取 `zipfile.ZipFile(io.BytesIO(data))`，**不解压到磁盘**，不产生路径穿越面。
- 成员名按 `normalize_member_path` 规范化（NFC、拒绝绝对路径、`..`、反斜杠、控制/格式字符），与 `create_archive` 完全同一实现；不合规成员在清单里标为不可读，不参与读取。
- 加密成员（`ZipInfo.flag_bits & 0x1`）：整包判为不可读，抛 `ATTACHMENT_EXTRACT_FAILED` 并说明原因。
- 有界解压（模块常量，值在实现中定并与测试钉住边界）：成员数上限、单成员字节上限、累计解压字节上限、单成员压缩比上限。超限一律 `ATTACHMENT_TOO_LARGE`，**在读取途中中止**，不先解完再判。
- 损坏包 / 中央目录不可读 → `ATTACHMENT_EXTRACT_FAILED`，消息里带文件名（沿用 `_extract` 现有的文件名前缀）。
- 文本判定：成员扩展名命中 `SUPPORTED_UPLOAD_FORMATS` 的文本类，且字节可 UTF-8 解码；不可解码按二进制处理，不替换成乱码正文。
- 入站渠道复用同一门控与同一抽取器，因此企微收到的 zip 与 WebChat 行为一致（不额外开一条路径）。

**可靠性（删除）**

- 核属与活跃 Run 查询在同一 Session 中完成，但**写操作之前不 stage 任何变更**——`RunService.cancel_run` 内部会 commit，若先 stage 会被它一并提交。
- `cancel_run` 抛 `RUN_BUSY`（CAS 落败，Run 恰好进入终态）视为「已无活跃 Run」，继续删除；其余异常上抛，不吞。
- 并发删除：两个请求都可能读到未删状态；第二条在 `UPDATE` 后再查一次或直接幂等成功（结果一致），不产生重复取消（`cancel_run` 对终态 Run 直接返回）。
- 并发提交消息：提交走既有核属（`_scope_conditions` 带 `is_deleted=false`），删除先成功则提交 `COMMON_NOT_FOUND`；提交先成功则删除按活跃 Run 处理。
- 删除后仍在收尾的 Run：终态事件照常落库（审计完整），通知由写入侧判定拦下（见下）。

**通知收口（单点）**

- 写入侧：`channel_notifications._entry_id_of(session, run)` 已经为每条 Run 通知查一次会话；在该查询上加 `is_deleted=false` 条件，返回 `None` 即「不写通知」，并补一条可区分的告警（`run_notification_conversation_deleted`），与既有的「无入口归属」日志分开。
- 删除时批量软删历史通知；读取侧 `list_notifications` / `_unread_count` **不改**——它们已经过滤 `is_deleted`，因此分页 `total`、`next_seq` 水位口径零变化。
- 任务结果入箱（`register_task_result`）已有 `_verify_entry_attribution` 的 `is_deleted=false`，删除后的任务结果按 `TOOL_RESULT_BINDING_MISMATCH` 明确拒绝，无需改动。

**可观测性**

- 新增结构化日志：`webchat_conversation_deleted`（conversation_id、cancelled_run）、`run_notification_conversation_deleted`。
- 附件读取的拒绝原因进入既有 `AttachmentToolError` 文本（模型可见、用户可读）；不新增指标族。

**测试建议**

| 层 | 用例 |
|---|---|
| unit（contracts） | 白名单与展示清单一一对应；`is_archive/is_text` 分类；格式清单投影稳定 |
| unit（runtime） | 成员路径规范化两条路径（打包/读取）同结果；zip 读取的清单/文本/二进制标注；加密、损坏、超限、嵌套拒绝路径 |
| unit（runtime） | 删除编排：核属、幂等、`RUN_BUSY` 兜底、事务顺序（取消先提交再软删） |
| integration（runtime） | 删除会话后：列表/详情/消息/续聊 404；通知列表与未读数不含该会话；收尾 Run 的通知不写入 |
| integration（gateway） | 上传 `application/zip` 与 `application/x-zip-compressed` 成功；未登记类型仍 400；client-config 含 `upload_formats` |
| integration（gateway→runtime） | DELETE 转发携带租户与服务身份；重复删除返回成功 |
| contract | `WebchatClientLimits` / `WebchatConversationDeletion` 的线格式与前端类型一致 |

## 4. 部署与运维

- 不新增部署单元、不新增依赖、无数据库迁移。
- 灰度顺序：Runtime 与 Gateway 同批发布；`upload_formats` 是新增必填字段，前端与 Gateway 必须同版本（与既有投影字段同一约束，不做兼容层）。
- 回滚：代码回滚后已软删的会话仍按旧代码的 `is_deleted` 过滤，行为一致；已放宽的 zip 在旧代码会被再次拒绝（安全方向）。

## 5. 风险与依赖

| 风险 | 缓解 |
|---|---|
| 附件清单收口后漏改某处引用（漂移） | 契约测试断言「门控集合 == 抽取器分派集合 == 投影清单」 |
| zip 解压放大（zip bomb） | 累计字节与压缩比双上限、读取途中中止、单测覆盖 |
| 删除与 Run 收尾竞态 | 通知写入侧单点判定；删除只等取消提交，不等终态 |
| 通知过滤改到读取侧会动游标 | 明确放在写入侧 + 批量软删，读取侧不动 |
| `upload_formats` 为必填，旧前端遇新后端或反之失败 | 同版本发布；前端严格解析沿用既有口径 |

## 6. 需求追溯

| FEAT | 设计 | 测试 |
|---|---|---|
| FEAT-01 | §3.2 / §3.4 API-16、API-12 | unit + integration |
| FEAT-02 | §3.4 API-12 步骤 3 / §3.5 | unit + integration |
| FEAT-03 | §3.3 / §3.5 通知收口 | integration |
| FEAT-05 | §3.2.1 / §3.4 API-19 | unit + contract + integration |
| FEAT-06 | §3.4 API-06 / §3.5 安全 | unit + acceptance |
| FEAT-07 | §3.4 API-19 | contract |
| FEAT-08 | 全篇 | 文档检查 |

## 附录：术语表

| 术语 | 定义 |
|---|---|
| 软删 | 置 `is_deleted=true`，行保留、查询过滤、存储不回收 |
| 通知收口 | 会话删除后，其历史通知与收尾 Run 的新通知都不进入收件箱 |
| 受限 zip 读取 | 内存内按上限解压并只投影文本成员，不落盘、不内联二进制 |
| 共享格式清单 | `muad_contracts.attachments` 中的唯一事实，门控/抽取/投影共用 |
