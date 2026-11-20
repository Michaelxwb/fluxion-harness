---
design_template: full
---

# IM 附件完整性与后台任务产物交付 模块需求与设计一体化文档

> **文档编号**: MOD-IM-ATTACH-1.0
> **文档版本**: v1.0
> **创建日期**: 2026-11-20
> **文档状态**: 设计评审中
> **需求基线**: `im-attachment-integrity.prd.md` v1.1

**评审边界说明**:

- **需求评审**: PRD 第 2-8 章 → 已作为本设计输入
- **设计评审**: 本文件第 2-4 章 → 通过后锁定设计基线
- **交接契约**: 2.5 验收条件 + `im-attachment-integrity.scenarios.md`

---

## 1. 文档控制

### 1.1 责任人

| 角色 | 姓名 | 职责范围 |
|---|---|---|
| 产品经理 | | 业务规则、验收 |
| 开发负责人 | | Connector/Gateway/Runtime 实现 |
| 测试负责人 | | 集成测试与人工场景 |

### 1.2 修订历史

| 版本 | 日期 | 作者 | 变更描述 |
|---|---|---|---|
| v0.1 | 2026-11-20 | code-flow | 初始设计：Connector 聚合、产物 owner、附件来源标注 |
| v1.0 | 2026-11-20 | code-flow | 细化为可拆分任务的设计基线 |
| v1.1 | 2026-11-20 | code-flow | 纳入 WebChat/MSSWWEB 发布下载、批次 journal、在途成员、精确 task owner、整批重发提示 |

---

## 2. 需求分析

### 2.1 需求概述

| 项目 | 内容 |
|---|---|
| **模块名称** | IM 附件完整性与后台任务产物交付 |
| **模块ID** | MOD-IM-ATTACH |
| **所属系统** | agent-worker / agent-runtime / im-connector / im-gateway |
| **需求类型** | Bug 修复 + 功能补强 |
| **业务背景** | 企微把一次发送拆成文字帧和多个文件帧；现有入站逐帧建 Run，导致文字先执行、附件后到，模型误读历史工具结果。后台任务产物可被 `list_attachments` 列出，但 `read_attachment`/`deliver_artifact` 因缺少 run/conversation 归属而返回 `ATTACHMENT_NOT_FOUND`。 |
| **核心目标** | 让同一逻辑发送的 1..N 个附件与文字一起进入同一个 Run；让附件来源标注真实；让任务产物按 owner 可读、可交付。 |

### 2.2 痛点与价值

- **目标用户**：企微/WebChat/MSSWWEB 业务用户、后台任务结果查看者、平台 on-call。
- **当前问题**：
  - 文字+PDF 被拆成两个 Run，第一个 Run 没有附件；
  - 同一 route 的串行锁让文件事件被第一个 Run 阻塞约 24 秒；
  - `TOOL_RESULT` 被标成“入站”，模型误选历史任务列表；
  - 任务产物 `run_id`/`conversation_id` 为空，归属查询失败。
- **预期价值**：一次问询得到一次正确回答；任务结果无需重跑 Skill；模型不再把内部工具结果当用户附件。

**用户故事**（来自 PRD）：

| 编号 | 用户故事 | 优先级 |
|---|---|---|
| US-01 | 企微用户发送文字 + 1..N 个附件，等全部附件到位后只收到一个正确回复 | P0 |
| US-02 | 用户要求“把最近一次任务结果发我”，直接收到任务当时产物，无需重跑 | P0 |
| US-03 | 用户明确要求查看内部工具结果时可访问，但系统不把它默认标成用户附件 | P0 |
| US-04 | WebChat 文本 + 1..N 个附件仍只产生一个 Run | P1 |
| US-05 | 聚合/失败/越权有可观测指标与日志 | P1 |

### 2.3 功能方案

| 功能ID | 功能名称 | 设计落点 | 优先级 |
|---|---|---|---|
| FEAT-01 | 企微文字+多附件聚合 | Connector `InboundBatchCoordinator` + `InboundPublisher` 多成员去重 | P0 |
| FEAT-02 | 附件来源标注与显式访问 | Runtime `AttachmentToolSet` 类型标签 + 工具说明 + `allow_internal` | P0 |
| FEAT-03 | 任务产物 owner 读取/交付 | Runtime `_owned_by_actor` 增加 `artifact.user_id/agent_id` 直接归属 | P0 |
| FEAT-04 | WebChat 多附件回归 | 现有上传/提交链路，仅补测试；不改提交模型 | P1 |
| FEAT-05 | 可观测与失败语义 | Connector/Gateway 指标、日志、`require_complete_attachments` | P1 |

### 2.4 范围与边界

| 类别 | 内容 |
|---|---|
| **范围（In Scope）** | 生产 `im.inbound` 的 Connector 路径（企微文字/1..N 媒体帧）；附件枚举标签；任务产物 owner 读取/交付；WebChat 与 MSSWWEB 的本地流发布/下载；多附件回归。 |
| **非范围（Out of Scope）** | Gateway 直连适配器路径的企微分帧（该路径不持有企微 socket；生产 WECOM 走 Connector）；其他 IM 通道协议；任务调度/执行/投递模型；新数据库表/迁移；新增 UI 页面。 |
| **前置假设** | 同一 route 的文字/媒体帧由同一 Connector 处理（bot lease 单 owner）；媒体字节在 Connector 已落共享存储；`artifact.user_id/agent_id` 是任务产物的权威 owner；用户明确要求可代表内部产物交付意图。 |
| **有意妥协 / 技术债** | Connector 批次热路径在进程内，Redis `InboundBatchJournal` 只保存批次身份/成员/合并事件元数据用于等待期去重和崩溃恢复，不保存附件字节；批次窗口会给企微文本增加有界延迟。 |

### 2.5 验收条件

#### 2.5.1 业务规则与约束

| ID | 类型 | 描述 | 验证场景 |
|---|---|---|---|
| RULE-01 | 业务规则 | 同一 route 的文字帧 + 1..N 媒体帧在窗口内合成一个逻辑消息；全部附件成功后才创建一个 Run。 | S-01、B-01 |
| RULE-02 | 业务规则 | 批次中任一附件失败，整批不进入业务执行；给用户逐项失败反馈。 | E-01 |
| RULE-03 | 业务规则 | 同一批次任一成员被平台重投，不创建第二个 Run；批次去重身份覆盖全部成员。 | B-02 |
| RULE-04 | 业务规则 | 附件枚举按 artifact_type 标注：入站/自产/工具结果/内部产物；`TOOL_RESULT` 不得标成入站。 | S-03 |
| RULE-05 | 系统约束 | 任务产物即使 `run_id`/`conversation_id` 为空，也可由 owner 按 `artifact.user_id/agent_id` 读取/交付；跨用户仍不可见。 | S-02、E-02 |
| RULE-06 | 系统约束 | 窗口、最大等待、批次上限必须有界；命令帧不参与聚合。 | S-04、B-03 |

#### 2.5.2 功能验收场景

场景唯一源：`im-attachment-integrity.scenarios.md`。设计影响：

| 场景ID | 设计影响 |
|---|---|
| S-01 | Connector 批次生成一个 `InboundEvent`，Gateway 只建一个 Run；附件顺序保留 |
| S-02 | `AttachmentToolSet._owned_by_actor` 走 artifact owner 直连；`deliver_artifact` 走当前会话路由 |
| S-03 | `list_attachments` 标签映射；`TOOL_RESULT` 显式读取/交付 |
| S-04 | WebChat 多 `upload_ids` 单 Run |
| E-01 | `require_complete_attachments=True`，Gateway `_handle_message` 不建 Run；提示重发整批 |
| E-02 | 跨用户任务产物 `ATTACHMENT_NOT_FOUND`，不泄露存在性 |
| E-04 | 下载超过最大等待：整批失败，不用文字建 Run |
| B-01/B-02/B-03 | 多附件、重投、窗口超时边界 |
| B-04/B-05 | 命令穿插、系统设置页上限变更 |
| S-06 | MSSWWEB 任务产物发布/下载共用本地流裁决 |

#### 2.5.3 非功能指标

| 指标ID | 要求 |
|---|---|
| NFR-SEC-01 | 任务产物/工具结果按租户 + user/agent 校验；未授权不进入 ToolRegistry 视野。 |
| NFR-REL-01 | 批次成员去重覆盖全部 `message_id`；重投不产生第二个 Run；发布失败保留批次重试。 |
| NFR-PERF-01 | 文本消息只增加有界聚合窗口延迟；窗口值由系统设置页 `im` 配置定义，不无限等待。 |
| NFR-OBS-01 | 合并/超时/失败/越权有指标与结构化日志，日志不含附件内容/URL/密钥。 |
| NFR-COMPAT-01 | 不新增数据库迁移；WebChat/MSSWWEB 提交契约不变；单帧消息保持原行为。 |

#### 2.5.4 验证环境假设

| 维度 | 假设 | 验证方式 | 不满足时 |
|---|---|---|---|
| 依赖 | 真实 PostgreSQL、Redis、Connector、Gateway、WS 探针；真实 WeCom 可选 | 集成/验收栈 | 记录缺失项与 owner |
| 数据 | 可构造文字 + 2..5 个媒体帧的真实回调 | WS 探针或真实企微 | 记录缺失项 |
| 预算 | Task 级 unit/integration；浏览器/真实企微人工场景 | `task finish` + 人工 | 记录未覆盖风险 |

---

## 3. 技术设计

### 3.1 方案选型

#### 3.1.1 聚合放在 Connector 还是 Gateway

| 对比维度 | 权重 | 方案A：Connector 聚合 | 方案B：Gateway 聚合 |
|---|---|---|---|
| 同 route 帧可达性 | 30% | 高：bot lease 单 owner，能看到该 bot 全部帧 | 低：Gateway 多副本 + consumer group 不保证同 route 落同一 Pod |
| 附件字节 | 25% | 高：Connector 已下载/落盘，直接合并 refs | 中：Connector 路径可合并 refs；直连适配器路径需重构取件 |
| 路由锁 | 20% | 高：聚合发生在 `im.inbound` 之前，不碰 Gateway 路由锁 | 中：必须在路由锁前做分布式批次 |
| 实现复杂度 | 15% | 中 | 高 |
| 可恢复性 | 10% | 高：批次热路径在内存 + Redis journal 恢复 | 中：需跨 Pod 批次协调 |
| **结论** | | **采用** | 否决：跨 Pod 批次协调成本过高 |

#### 3.1.2 关键决策记录

| 决策点 | 选择 | 被否决项 | 理由 | 可逆性 |
|---|---|---|---|---|
| 聚合层 | Connector `InboundBatchCoordinator` | Gateway 分布式批次 | bot lease 单 owner，天然 route 亲和 | 中 |
| 批次去重 | 内存成员集合 + Redis `InboundBatchJournal` + `InboundPublisher` 全部成员 id | 只查 PUBLISHED/只用文字 id | 覆盖等待期重复帧与 XADD 后崩溃窗口 | 中 |
| 失败语义 | 批次 `require_complete_attachments=True` | 部分成功继续 | 用户要求“等附件成功拿到”；避免误导性回答 | 易 |
| 任务产物 owner | `task_id + OUTPUT + user_id + agent_id` 精确匹配，并校验 live `task_execution` | 只查 run/conversation；允许 agent=NULL | 任务产物没有会话；不能把缺失 Agent 归属的产物跨 Agent 放行 | 易 |
| Web 面交付 | WebChat/MSSWWEB 共用 `LOCAL_STREAM_CHANNELS` 发布 + 下载裁决 | 只修 IM | 两个面共用同一套 handler；只修工具层会出现“读得到、发不出” | 易 |
| 附件标签 | 按 artifact_type 映射 | 默认排除 `TOOL_RESULT` | 用户明确要求保留访问 | 易 |
| 内部产物交付 | `allow_internal` 显式开关 | 直接放开/永久拒绝 | 保留显式请求能力，同时默认不主动外发 | 易 |
| 窗口配置 | 系统设置页 `ImSettings`，Connector 拉取并缓存 | 仅 ConnectorSettings | 用户要求默认值可在系统设置页调整；Connector 增加只读设置客户端与缓存 | 易 |
| 企微接入路径 | 固定 Connector | Gateway 直连适配器 | 生产企微由 Connector 持有 WS；直连路径不承载企微，无用户影响 | 易 |

#### 3.1.3 技术栈

Python 3.12、asyncio、Redis Streams、SQLAlchemy async、FastAPI；不新增第三方依赖、不新增数据库迁移。

### 3.2 架构设计

```mermaid
graph TB
    subgraph WeCom
        T["文字帧"]
        F["文件帧 x N"]
    end
    subgraph Connector
        A["WeCom Adapter"]
        M["媒体取件/落盘"]
        B["InboundBatchCoordinator<br/>按 route 聚合"]
        J["InboundBatchJournal<br/>成员/主消息/恢复"]
        P["InboundPublisher<br/>多成员去重 + XADD"]
    end
    subgraph Gateway
        S["im.inbound 消费"]
        R["InboundPipeline<br/>require_complete_attachments"]
        L["Run 创建"]
    end
    subgraph Runtime
        R1["Run"]
        AT["AttachmentToolSet"]
        AR["runtime.artifact"]
    end
    T --> A
    F --> A
    A --> M --> B
    B <-->|成员/状态/恢复| J
    B -->|一个 InboundEvent| P --> S --> R --> L --> R1
    R1 --> AT --> AR
    AR -->|list/read/deliver| AT
    AT -->|WebChat/MSSWWEB 发布/下载| AR
```

#### 3.2.1 Connector 批次（含在途成员与 journal）

新增 `InboundBatchCoordinator`（建议放在 `packages/im-core/src/muad_im_core/inbound_batch.py`，保持渠道中立）和轻量 `InboundBatchJournal`（Redis）：

- 输入是原始帧 + 已物化的 `InboundEvent`；按 `route_key(channel, tenant_id, bot_id, external_conversation_id, external_user_id)` 分组。
- **下载前登记**：`raw_sink` 或 `_publish_inbound` 在取件前调用 `register_frame(route, message_id, kind)`，把成员标为 `PENDING`；文字帧立即 `READY`，媒体帧取件成功后 `complete_frame(event)`，失败则 `fail_frame(message_id, code)`。这样最大等待到点时不会把“还在下载的附件”当成不存在。
- 批次参数从 Console 内部 `platform-settings` 快照读取（见 §3.3/§3.4），Connector 按租户缓存；Console 暂不可用时使用最近一次成功快照，若从未取到则使用代码默认值并记 WARNING，不阻断入站。
- 每个 route 一个 `_Batch`：
  - `members: list[BatchMember]`（`message_id`、`kind`、`status`、`event`、`failure`）
  - `primary`（第一条文字；没有文字则第一条媒体）
  - `created_at` / `last_at`
  - `timer: asyncio.Task`
- 入批规则：
  - 若成员 key 已映射到 `PUBLISHED` 批次：直接丢弃（重复）。
  - 若成员 key 已映射到当前 `PENDING` 批次：同一 `message_id` 直接忽略；新成员加入。
  - 命令帧（`/bind`、`/new`、`/stop`、`/skills`、`/confirm`、`/memory` 等）：不加入批次；先 flush 当前批次，再返回该命令事件单独发布。
  - `unsupported_media`：不加入批次；先 flush 当前批次，再单独发布。
  - 批次为空：创建，登记成员，启动计时。
  - 新事件是文字且批次已有文字：立即 flush 当前批次，以新文字开新批次（避免合并两条独立指令）。
  - 新媒体：登记 `PENDING`；取件完成后加入；媒体数达到 `im.inbound_batch_max_attachments` 后停止接收新媒体，等待静默/最大等待到期；若到期前又来新媒体，追加 `ATTACHMENT_COUNT_EXCEEDED` 失败并立即 flush。
- 计时（**按 route，不按 bot 全局队列**）：
  - 静默窗口：当本 route 没有 `PENDING` 成员、且最近一次成员完成后开始/刷新；同 bot 其他用户流量不影响本批。
  - 最大等待：从 `created_at` 起算。到点仍有 `PENDING` 成员 ⇒ 追加 `ATTACHMENT_FETCH_TIMEOUT` 并整批失败；**不允许只带文字 flush**。
  - 三个值来自系统设置页 `im` 配置（见 §3.4），必须 `quiet < max`、`max` 有上界。
- `_flush(batch, reason)`：
  - 合并 `InboundEvent`：`external_message_id` = primary；`text` = primary；`attachments`/`attachment_failures` 按成员顺序拼接；`reply_ref` = primary；`require_complete_attachments = len(members) > 1`。
  - `InboundBatchJournal` 写 `PUBLISHING` + 合并事件；调用 `InboundPublisher.publish(merged, dedupe_ids=全部成员 id)`；成功后写 `PUBLISHED` 并标记成员；失败回 `PENDING` 并退避重试。
- Journal 结构（Redis，不落库）：
  - `muad:connector:inbound-batch:{tenant}:{bot}:{batch_id}` → JSON：主消息 id、成员列表、合并事件、状态、时间戳；TTL 覆盖重投窗口。
  - `muad:connector:inbound-batch-member:{tenant}:{bot}:{message_id}` → `batch_id`；TTL 同上。
  - Connector 启动/接管时扫描 owned bot 的 `PENDING/PUBLISHING` journal：超过最大等待仍未完成则按当前成员整批失败/重发，不把文字单独放行。
- 命令/不支持媒体的顺序：先 flush 当前批次，再发布命令/不支持媒体事件；保证用户文字/附件不被静默丢弃。
- 停机：`stop()` 时对全部 pending 批次做一次有界 flush；失败只记日志，journal 留给接管者/重投。

#### 3.2.2 Publisher 多成员去重

扩展 `muad_im_core.ingress.InboundPublisher`：

- `publish(event, *, dedupe_ids: Sequence[str] | None = None)`：
  - 身份集合 = `dedupe_ids or (event.external_message_id,)`。
  - 对每个身份复用现有 `_begin`：任一已是 `PUBLISHED` ⇒ 整个批次视为重复，返回 `DUPLICATE`；`RECEIVED`/`LOST`/缺失 ⇒ 继续。
  - XADD 一次；随后把全部身份写为 `PUBLISHED:<entry_id>`。
  - 任一步失败保持现有语义：标记停在 `RECEIVED`，由 `sweep_lost` 显式处理。
- 新增 `is_published(message_id) -> bool`：批次入口先查，重复帧不下载、不合并。
- 批次主消息 id = 文字 id（无文字则首个媒体 id）；Runtime 幂等键继续使用这个 id。
- XADD 成功、全部 PUBLISHED 标记写入前崩溃：`InboundBatchJournal` 保持 `PUBLISHING` + 合并事件；任一成员重投先从 member key 找到原主消息 id，按 Journal 重发/确认，不用文件 id 另起 Run。Journal 与 PUBLISHED 标记不追求跨 key 原子，靠“主消息 id 稳定 + Runtime 幂等 + 重投检查”收敛。

#### 3.2.3 Gateway 失败语义

- `InboundEvent` / `ChannelEnvelope` 增加可选 `require_complete_attachments: bool = False`。
- Connector 合并批次在 `len(members) > 1` 时置 true。
- `_handle_message` 在 `_collect_attachments` 之后：
  - 若 `require_complete_attachments` 且 `collected.audit_outcome == "FAILED"`：不创建 Run，直接返回已受理（回执/审计已由附件链路发送）。
  - 无失败：按现有逻辑创建 Run，附件列表为全部成员 refs。
- 单帧消息保持原行为（`require_complete_attachments=False`），避免扩大行为变更。
- 超限反馈走消息目录：`ATTACHMENT_COUNT_EXCEEDED` 文案改为友好引导，例如“一次最多接收 {limit} 个附件，请分批重新发送；本次未处理”。`limit` 取当前租户 `im.inbound_batch_max_attachments`，不在网关硬编码；中英文词条同时更新。
- 多附件批次失败使用 `ATTACHMENT_BATCH_INCOMPLETE` 文案：“本次附件未全部收到，未处理；请连同说明重新发送整批附件。” 具体失败原因复用现有 `ATTACHMENT_FETCH_*`/类型/大小文案；不承诺只补失败附件。

#### 3.2.4 任务产物 owner

```mermaid
flowchart LR
    A["任务执行 task_execution"] --> B["runtime.artifact<br/>task_id, user_id, agent_id"]
    B --> C["_scoped_query<br/>tenant + 未删 + owner"]
    C --> D["list/read/deliver"]
```

- `_owned_by_actor` 只对**目标任务产物**增加直接归属：
  ```python
  by_task = sa.and_(
      Artifact.task_id.is_not(None),
      Artifact.artifact_type == AGENT_OUTPUT_ARTIFACT_TYPE,
      Artifact.user_id == user_id,
      Artifact.agent_id == self._agent_id,  # 必须非空且精确匹配
  )
  return sa.or_(by_conversation, by_run, by_task)
  ```
- `_load` / `_load_for_update` 对命中 `task_id` 的行再复用 `task_artifacts` 的 `_load_owner` 读 `task.task_execution`，复核 `tenant_id + actor_user_id + agent_id + is_deleted=false`；不一致即 `ATTACHMENT_NOT_FOUND`。这样不在列表 SQL 里加跨 schema 子查询，也不新增模型。
- **不放行 `agent_id=NULL`**；`user_id`/`agent_id` 任一为空或 task_execution 不匹配，一律按不可访问处理。
- 保留租户、软删校验；跨租户/跨用户仍返回 `ATTACHMENT_NOT_FOUND`。
- `list_attachments(scope="agent")` 对 `task_id` 行追加与读取相同的 live `task_execution` 条件（同租户、`actor_user_id`、`agent_id`、未删），避免“列得出但读不到”；非 task 行保持原过滤。
- 统一谓词放在 `task_artifacts` 的 owner helper 中，读/列复用，不复制第二套判断。
- 数据现状：当前任务写入方 `_new_row` 会写 `user_id/agent_id`；库内存在历史 `task_id` 但 owner 为空、且找不到 live `task_execution` 的行（本地抽查），本次不回填、不放行，按“归属不完整”拒绝。

#### 3.2.4.1 WebChat / MSSWWEB 发布与下载

WebChat 与 MSSWWEB 共用 `LOCAL_STREAM_CHANNELS` 和同一套 `publish_artifact_to_conversation` / `webchat_artifacts`，必须一起修：

- `publish_artifact_to_conversation(..., allow_internal=False)`：
  - `_load_publishable` 允许 `OUTPUT + task_id` 且无 `conversation_id` 的任务产物；`TOOL_RESULT` 仅在 `allow_internal=true` 时允许；`TRANSCRIPT` 继续拒绝。
  - `_source_owner` 增加 `task_id` 分支：复用 `task_artifacts` 的 `_load_owner`（`task.task_execution` 的 `actor_user_id/agent_id`）。
  - `_assert_same_owner` 要求 task owner 与目标 Run 的 user/agent 一致。
- `webchat_artifacts`：
  - `_owned_artifact` 允许 `task_id` 非空、非 TRANSCRIPT 的无会话任务产物；`TOOL_RESULT` 仍需目标会话发布事实（显式 `allow_internal` 发布后才存在）。
  - `_source_owner` 增加 task owner 分支；跨会话下载仍要求 `_assert_published` 命中目标会话的发布事实与 checksum。
  - `_reference_at` 用发布时的 artifact 行/checksum，保证下载原版本；任务版本是独立 artifact 行，不回退最新版。
- `deliver_artifact` 走 `_publish_to_conversation` 时把 `allow_internal` 透传；IM 路径仍走 Gateway `/internal/artifacts/{id}`，不新增直读存储。

#### 3.2.5 附件来源标注

- `DIRECTIONS` 增加 `internal`；`DIRECTION_LABELS` 增加：
  - `inbound` → 入站
  - `outbound` → 自产
  - `internal` → 内部产物
- `_list_line` 按 `artifact_type` 映射：
  - `INBOUND_*` → 入站
  - `OUTPUT` → 自产
  - `TOOL_RESULT` → 工具结果
  - 其他 → 内部产物
- `direction=inbound` 仍只查 `INBOUND_ARTIFACT_TYPES`；默认（无 direction）保留全量，保证显式访问。
- `LIST_ATTACHMENTS_DESCRIPTION` 明确“工具结果不是用户附件；找用户附件用 inbound”。
- 模型可见说明追加一条权威口径：**当前用户消息里的附件列表是当前请求的唯一输入依据**；当前消息没有附件时，不得拿历史附件代替，必须明确告诉用户没看到附件并请其重发。历史附件只有在用户明确要求“之前那个文件”时才使用。
- `deliver_artifact` 增加可选 `allow_internal: bool = False`：
  - `TOOL_RESULT` 默认拒绝；`allow_internal=true` 且 owner 校验通过时允许交付。
  - 工具描述要求模型只在用户明确要求时置 true；`tool_call_audit.args_preview` 保留该参数，便于审计。
  - 任务产物 `OUTPUT` 不需要该开关。

### 3.3 数据设计

- **无新增表、无迁移**。
- 复用 `runtime.artifact`：
  - `task_id`（任务执行 id）
  - `agent_id` / `user_id`（owner，历史产出跨会话检索）
  - `conversation_id` / `run_id`（可空）
  - 现有索引 `ix_artifact_agent_user_time`、`ix_artifact_task`。
  - 当前任务写入方 `_new_row` 写 `user_id/agent_id`；库内历史 `task_id` 但 owner 为空、且无 live `task_execution` 的行不回填、不放行，按“归属不完整”拒绝。
- Redis：
  - 复用 `InboundPublisher` 的 `inbound_dedupe_key` 多身份标记。
  - 新增轻量 `InboundBatchJournal`：batch JSON + member→batch 映射，只保存主消息 id、成员 message_id、合并事件（文字/附件 refs/failures）和发布状态；TTL 覆盖重投窗口。不保存附件字节、不建数据库表。
  - 热路径批次仍在 Connector 内存；Journal 只用于等待期重复帧、崩溃恢复和接管，避免每次帧都做全量状态机。
- 配置新增（系统设置页 `ImSettings`，非 ConnectorSettings）：
  - `im.inbound_batch_quiet_ms`：默认 300，范围 100–2000
  - `im.inbound_batch_max_wait_ms`：默认 5000，范围 500–30000
  - `im.inbound_batch_max_attachments`：默认 5，范围 1–20
  - 具体值在 `ImSettings` 内定义并由单测钉住；Console `platform_settings_catalog` 增加对应条目，页面按既有 catalog 渲染。
- Connector 读取路径：复用 Console 内部 `GET /internal/v1/platform-settings`（已有服务身份），按租户做进程内缓存；成功快照优先，取不到时用最近一次成功快照，从未成功则用 `ImSettings` 默认值并记 WARNING。
- 缓存周期由 Connector 实现常量定义（不在页面配置），页面保存后最多延迟一个缓存周期对新批次生效；批次参数只影响新批次，不追溯正在等待的批次。
- 本版不新增“单批总字节上限”，沿用现有单附件大小限制；若 1000 用户压测发现单批总体积需要额外保护，再评估增加 `im.inbound_batch_max_total_bytes`，不阻塞本次修复。

### 3.4 接口设计

#### 3.4.1 函数/库接口

| 接口 | 入参 | 返回 | 错误处理 |
|---|---|---|---|
| `ConnectorPlatformSettings.batch(tenant_id)` | 租户 | `ImBatchSettings`（quiet/max/count） | 缓存/最近成功/默认值；WARNING 日志 |
| `InboundBatchCoordinator.register_frame(route, message_id, kind)` | 原始帧元数据 | member 状态 | 重复/已发布直接忽略 |
| `InboundBatchCoordinator.complete_frame(event)` | 物化后的 `InboundEvent` | 需要立即发布的合并事件或 `None` | 发布失败保留批次；不抛给入站循环 |
| `InboundBatchCoordinator.fail_frame(message_id, code)` | 失败原因 | 批次失败状态 | 整批不执行业务 |
| `InboundBatchCoordinator.flush_all()` | 无 | 无 | 停机有界 flush |
| `publish_artifact_to_conversation(..., allow_internal=False)` | 目标会话/Run/产物 | 发布 seq | task owner 校验；TOOL_RESULT 需显式允许 |
| `InboundPublisher.publish(event, dedupe_ids=...)` | 事件 + 成员身份集合 | `PUBLISHED` / `DUPLICATE` | 保持现有 Redis 故障语义 |
| `InboundPublisher.is_published(message_id)` | 平台消息 id | bool | Redis 故障按现有降级/告警 |
| `AttachmentToolSet._owned_by_actor(user_id)` | 当前 user | SQL 谓词 | 失败 fail closed |
| `list_attachments(direction=internal)` | 过滤 | 文本 | 非法枚举显式错误 |
| `deliver_artifact(allow_internal=...)` | 布尔 | 交付结果 | 非法/越权显式错误 |

#### 3.4.2 工具 schema 变化

- `list_attachments`：`direction` 枚举增加 `internal`；默认行为不变。
- `deliver_artifact`：增加可选 `allow_internal`（boolean，默认 false）；用户明确要求时由模型置 true，不需要确认卡片；`additionalProperties: false` 保持。
- `read_attachment`：不变。
- `execute`/`list-platforms`：不变。
- 系统设置页新增 `im.inbound_batch_*` 三项；`ATTACHMENT_COUNT_EXCEEDED` 词条更新为友好分批引导；新增 `ATTACHMENT_BATCH_INCOMPLETE` 词条，失败原因复用现有附件原因码。

#### 3.4.3 契约模型变化

- `InboundEvent`：增加 `require_complete_attachments: bool = False`。
- `ChannelEnvelope`：增加 `require_complete_attachments: bool = False`。
- 默认值保证旧生产者/旧测试序列化兼容；不新增数据库列。

### 3.5 质量实现方案

#### 3.5.1 性能

- 批次热路径只在 Connector 内存保存成员/引用；`InboundBatchJournal` 只存元数据与合并事件（不含字节），每个 route 至多一个批次，成员数受 `im.inbound_batch_max_attachments` 限制。
- 按 1000 并发用户估算：1000 个批次只持有文字 + 附件引用（不复制字节），内存量级可忽略；每个批次一个 asyncio 定时任务，远低于 Connector 既有 10k 入站队列上限。
- 文本消息最多增加一个静默窗口延迟（默认 300ms）；最大等待（默认 5s）只在连续来帧/慢下载时生效，不拖慢纯文本。
- 发布仍是每批一次 XADD，不增加每附件一次 Stream。
- 媒体下载/落盘仍是现有串行链路；批次不改变单附件大小上限，也不把字节读入内存批次。

#### 3.5.2 可靠性

- 静默/最大等待/数量上限三个 flush 条件；计时任务异常必须记 ERROR 并保留批次。
- 发布失败保留批次并退避重试；停机有界 flush；Journal 状态允许接管者重发同一主消息 id。
- 成员去重覆盖全部 `message_id`：等待期由内存成员集合 + Journal，发布后由 `InboundPublisher` multi-identity；重复帧不下载、不合并。
- 最大等待到点仍有 `PENDING` 媒体：整批失败并给友好提示，不允许文字单独执行。
- 批次失败（附件失败/超限/超时）发布一个事件，由 Gateway 统一反馈与审计；不创建 Run。

#### 3.5.3 安全

- 任务产物直接归属只对 `task_id + OUTPUT + user_id + agent_id` 精确匹配生效；`agent_id` 为空明确拒绝；`task_execution` 必须同租户且 live。
- WebChat/MSSWWEB 发布/下载同样按 task owner 校验；跨会话下载还要求目标会话存在对应 publish 事实与 checksum。
- `TOOL_RESULT` 默认不可交付；`allow_internal=true` 仅在 owner 校验通过后生效；审计参数可见。
- 日志只记 batch_id、primary message_id、member_count、reason、code；不记附件内容/URL/Cookie/AK/SK。

#### 3.5.4 可观测性

| 指标/日志 | 说明 |
|---|---|
| `im_inbound_batch_total{reason}` | reason = quiet / max_wait / count_limit / command / unsupported |
| `im_inbound_batch_members` | 批次成员数直方图 |
| `im_inbound_batch_failures_total{code}` | 附件失败/超限 |
| `attachment_owner_fallback_total` | 任务产物走 `artifact.user_id/agent_id` 的次数 |
| 日志 | `inbound_batch_flush batch_id=... primary=... members=... reason=...`；无正文 |

---

## 4. 部署与运维

### 4.1 部署架构

- 变更镜像：`agent-runtime`（附件工具/契约）、`im-connector`（批次/发布器）、`im-gateway`（契约/失败语义）。
- 无数据库迁移；系统设置页新增 `im.inbound_batch_*` 三项，使用代码默认值，租户可在页面调整。Connector 读取 Console 内部设置快照；无新增 Connector 环境变量。
- 回滚：回滚三个镜像即可；已发布批次事件与新字段向后兼容（默认 false）。

### 4.2 发布与回滚

- 先在验收栈验证真实 WS 探针 + 真实 PG/Redis；再灰度一个 Connector 副本（bot lease 单 owner，灰度时注意 lease 接管）。
- 回滚条件：批次异常导致消息积压、重复 Run 指标上升、附件失败率上升。

### 4.3 监控告警

- `im_inbound_batch_total{reason=max_wait}` 持续偏高：窗口/上限配置或 Connector 处理慢。
- `im_inbound_batch_failures_total` 上升：附件取件/权限/上限问题。
- `attachment_owner_fallback_total` 异常：任务产物归属路径被频繁触发，需检查是否有异常访问。

### 4.4 数据迁移

无。

---

## 5. 风险与依赖

| 风险ID | 类型 | 描述 | 概率 | 影响 | 应对 | 场景 |
|---|---|---|---|---|---|---|
| RISK-01 | 性能 | 静默窗口增加文本延迟 | 中 | 中 | 窗口可配、默认小、指标观察 | S-04 |
| RISK-02 | 可靠 | Connector 崩溃丢失内存批次 | 低 | 中 | Journal PENDING/PUBLISHING 恢复；平台重投；停机 flush | E-03 |
| RISK-03 | 一致 | XADD 成功但 PUBLISHED 标记未完成 | 低 | 中 | Journal 保留主消息 id + 合并事件；重投按 Journal 重发/跳过；Runtime 幂等 | B-02 |
| RISK-04 | 安全 | 任务产物 owner 放宽 | 低 | 高 | task_id + OUTPUT + user + agent 精确匹配 + live task_execution；跨用户负例 | E-02 |
| RISK-05 | 安全 | `allow_internal` 被模型误用 | 中 | 中 | 默认 false；工具说明；审计参数；用户明确要求 | S-03 |
| RISK-06 | 产品 | 多附件部分失败整批拒绝 | 中 | 中 | 明确提示重新发送整批；不承诺只补失败项 | E-01 |
| RISK-07 | 配置 | Console 平台设置不可读导致 Connector 无窗口值 | 低 | 中 | 最近一次成功快照；无快照用默认值 + WARNING；不阻断入站 | S-05 |
| RISK-08 | 一致 | Journal 与 XADD 非原子，崩溃窗口重复 | 低 | 中 | Journal 先写 PUBLISHING，主消息 id 稳定；重投按 Journal 重发/跳过；补故障注入 | B-02 |
| RISK-09 | 兼容 | 历史 task 产物 owner 为空 | 中 | 低 | 明确拒绝、不回填；当前写入方已写 owner；补列表不泄露 | E-02 |
| RISK-10 | 安全 | WebChat/MSSWWEB 发布 task/内部产物 | 低 | 高 | task owner + 目标会话 publish 事实 + checksum；跨用户负例 | E-02、S-06 |

---

## 6. 需求追溯矩阵

| 用户故事 | 功能ID | 设计落点 | 场景ID | 测试层级 | 执行方式 |
|---|---|---|---|---|---|
| US-01 | FEAT-01 | Connector 批次 §3.2.1 | S-01、B-01、B-02 | integration | acceptance + manual |
| US-02 | FEAT-03 | `_owned_by_actor` + 发布/下载 §3.2.4/§3.2.4.1 | S-02、S-06、E-02 | integration | pytest + manual |
| US-03 | FEAT-02 | 标签/`allow_internal` §3.2.5 | S-03 | unit + integration | pytest + manual |
| US-04 | FEAT-04 | WebChat/MSSWWEB 回归 | S-04、S-06 | integration | pytest |
| US-05 | FEAT-05 | 指标/失败语义 §3.5 | E-01、E-03、E-04 | integration | pytest + manual |

---

## 7. Spec Compliance Matrix

| Spec/Rule | 设计影响 | 设计落点 | 验收场景 | 状态 |
|---|---|---|---|---|
| `im/harness-im#RULE-im-002` | 批次在 Connector/`im-core`，核心域不出现企微帧形状 | §3.2.1 | S-01 | applied |
| `im/harness-im#RULE-006` | 任务产物按执行 owner 读取，不要求会话身份 | §3.2.4 | S-02、E-02 | applied |
| `im/harness-im#RULE-007` | 交付仍走网关受信投递，不直读存储 | §3.2.4/§3.2.5 | S-02 | applied |
| `auth/harness-auth#RULE-auth-001` | 未授权产物不进入 ToolRegistry 视野 | §3.2.4/§3.5.3 | E-02 | applied |
| `secret/harness-secret#RULE-secret-001` | 日志/审计不含附件内容、URL、密钥 | §3.5.3 | E-01、E-03 | applied |
| `test/harness-test#RULE-test-001` | 真实 PG/Redis/Connector/WS 探针覆盖关键链路 | §2.5.4、任务测试 | S-01/S-02 | applied |
| `test/harness-test#RULE-002` | 分帧/落库/交付测试必须有界等待 | 任务测试 | S-01、S-02 | applied |

**建议的 Spec Delta**（由后续 TASK 声明，archive 应用）：

- `im/harness-im` 新增一条规则：同一逻辑发送的文字与 1..N 个媒体帧必须在 Connector 按 route 有界聚合；全部附件物化后发布一个 `InboundEvent`；批次成员共享去重；失败整批不执行业务。
- `im/harness-im` 新增一条规则：附件枚举必须按 `artifact_type` 标注来源，`TOOL_RESULT` 是内部产物而非入站；显式访问保留，内部产物默认不交付。

---

## 附录：术语表

| 术语 | 定义 |
|---|---|
| 批次 | 同一 route 内文字 + 1..N 个媒体帧的聚合单元 |
| 静默窗口 | 最后一次帧到达后等待新帧的时间 |
| 最大等待 | 从批次创建起的最长等待上限 |
| 主消息 | 批次的幂等/回复身份：文字帧优先，否则首个媒体帧 |
| `require_complete_attachments` | 批次失败时禁止创建 Run 的契约标志 |
| owner | `artifact.user_id` + `artifact.agent_id`（租户外层限定） |

---

*文档结束*
