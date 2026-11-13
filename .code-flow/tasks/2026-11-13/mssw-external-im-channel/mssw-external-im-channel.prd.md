# PRD: MSSW 外部 IM 入口（BFF 接入）

> **文档编号**: PRD-2026-11-13-01
> **文档版本**: v1.2
> **创建日期**: 2026-11-13
> **产品负责人**: fluxion-harness
> **状态**: 评审中

**评审边界说明**:
- **需求基线**: 第 2-8 章 → 通过后锁定
- **设计输入**: 第 3-8 章 → 为 `cf-task:align` 提供 US / FEAT / NFR / 范围 / Spec refs

**ID 体系**: US（用户故事）、FEAT（功能）、NFR（非功能指标）、Spec/Rule（已有规范引用）

---

## 1. 文档控制

### 1.1 责任人

| 角色 | 姓名 | 职责范围 |
|------|------|---------|
| 产品经理 | fluxion-harness | 需求定义、业务验收 |
| 开发负责人 | fluxion-harness | 技术方案确认 |

### 1.2 修订历史

| 版本 | 日期 | 作者 | 变更描述 |
|------|------|------|---------|
| v0.1 | 2026-11-13 | fluxion-harness | 初始草稿 |
| v1.0 | 2026-11-13 | fluxion-harness | 口径确认：只接 MSSW；`channel=MSSWWEB`、`platform_key=mssw`；因子放 K8s Secret、不热更新；未读复用通知红点 |
| v1.1 | 2026-11-13 | fluxion-harness | 对齐修正：身份是用户级（一个外部身份 → 一个 platform_user）；会话/产物/通知按 user+Agent 跨入口共享，entry/channel 只做来源标识；`/identity` 永远 PG，其他接口 Redis-first；resume 保留确认动作 |
| v1.2 | 2026-11-13 | fluxion-harness | 明确成功撤权前立即清缓存及阻止旧结果回填；凭证/权限分层；配置 epoch；现有 WebChat 适配；后台来源、可获取通知水位与 SSE 完整应用协议 |

---

## 2. 背景与目标

### 2.1 问题陈述

| 维度 | 内容 |
|------|------|
| **问题描述** | MSSW 业务平台要在自己的页面内提供“MSS 智能机器人”入口。前端由业务平台负责，后端通过 BFF 代理转发到我们。现有 WebChat 只面向独立 Console 页面：认证是管理员签发的不透明用户 Token + `muad_chat_session` Cookie，入口与会话按 `WEBCHAT` 硬编码；没有“外部业务平台 JWT 断言”的认证面，也没有管理员直绑业务 `user_id` 的能力。 |
| **当前替代方案** | 业务平台无法直接复用 WebChat：Cookie 会话不能跨平台携带，业务用户也没有我们的 CHAT Token；只能由业务平台另建一套代理和身份映射，成本高且与我们的会话/历史割裂。 |
| **触发原因** | MSSW 首次对接。后续还有一个认证逻辑相同、仅 JWT 因子不同的 MSSP 平台；本次方案必须为“新增同类平台”留下可复用的规范，而不是复制一套 WebChat。 |

### 2.2 目标与价值

| 维度 | 内容 |
|------|------|
| **核心目标** | 以纯后端方式提供 MSSWWEB IM 入口：业务平台 BFF 携带 `soc-token` 调用，我们完成验签、用户级身份绑定、会话/消息/SSE/上传/产物等全部对话能力；同一用户 + 同一 Agent 的会话/产物/通知在 WebChat 与 MSSWWEB 之间共享，entry/channel 只做来源标识；同时沉淀可复用的外部 BFF 接入协议与新增 IM 通道规范。 |
| **预期价值** | MSSW 用户无需离开业务平台即可使用 Agent；业务平台无需自建会话/流式/附件能力；未来 MSSP 只增加平台配置与入口，不复制 handler。 |

**成功指标**

| 指标 | 当前值 | 目标值 | 衡量方式 |
|------|--------|--------|----------|
| MSSW 用户可在业务平台内完成一次完整对话 | 不支持 | 支持（流式、停止、确认恢复、历史、上传、下载） | 联调 + E2E |
| 同一用户 + 同一 Agent 跨入口数据共享 | 无第二入口 | WebChat 创建的会话在 MSSWWEB 可见，反之亦然；不同用户不可见 | 跨入口共享用例 |
| 新增同类平台的代码增量 | 需复制 WebChat | 只加配置 + 入口 + 枚举，不复制 handler | 设计评审 + 代码审查 |
| 因子缺失对服务的影响 | — | 只影响 MSSWWEB 鉴权（503），不影响启动与其他通道 | 降级用例 |

---

## 3. 用户与场景

### 3.1 目标用户

| 维度 | 内容 |
|------|------|
| **用户画像** | 使用 MSSW 业务平台的业务用户（由 MSSW 登录态识别）；MSSW BFF/Agent 代理（系统调用方）；我们的 Console 管理员；后续接入新平台的研发/运维。 |
| **使用场景** | 用户在 MSSW 页面打开“MSS 智能机器人”面板，提问、看流式回答、停止生成、查看最近会话、上传图片/文件、下载 Agent 产物；管理员在 Console 为用户绑定 MSSW `user_id`。 |

### 3.2 用户故事

| 编号 | 用户故事 | 优先级 |
|------|---------|--------|
| US-01 | 作为 MSSW 用户，我希望在业务平台内直接和 Agent 对话，以便不离开当前工作台。 | P0 |
| US-02 | 作为 MSSW 用户，我希望回答是流式输出的，并且可以随时停止，以便长任务可控。 | P0 |
| US-03 | 作为 MSSW 用户，我希望看到最近会话和历史消息，以便继续之前的对话。 | P0 |
| US-04 | 作为 MSSW 用户，我希望未读会话有红点提示，以便知道哪些回答还没看。 | P0 |
| US-05 | 作为 MSSW 用户，我希望上传图片/文件并下载 Agent 生成的文件，以便完成多模态任务。 | P0 |
| US-06 | 作为 Console 管理员，我希望把 MSSW 用户的业务身份标识绑定到我们的平台用户和入口，以便控制谁能使用、用哪个 Agent。 | P0 |
| US-07 | 作为安全负责人，我希望同一用户 + 同一 Agent 的数据在 WebChat/MSSWWEB 之间共享，但不同用户的数据不可见；entry/channel 只做来源标识，不做数据隔离。 | P0 |
| US-08 | 作为业务平台接入方，我希望有一份稳定的接口协议（必配/选配、错误码、SSE、上传），以便独立完成 BFF。 | P0 |
| US-09 | 作为我们的研发，我希望新增同类平台（MSSP）时只加配置和入口，不复制 WebChat 的 15 个 handler。 | P1 |
| US-10 | 作为 MSSW 用户，我希望解绑后重新绑定仍能看到之前的会话和产物，以便不丢历史。 | P0 |
| US-11 | 作为 Console 管理员，我希望编辑已有的用户级外部身份，以便纠正填错的身份值而不丢失会话。 | P0 |
| US-12 | 作为 MSSW 用户，我希望 Agent 请求确认（如保存记忆）时能选择并继续同一个 Run，以便确认动作真正生效。 | P0 |

---

## 4. 功能需求

### 4.1 功能清单

| 功能ID | 功能名称 | 功能描述 | 优先级 | 来源用户故事 |
|--------|---------|---------|--------|-------------|
| FEAT-01 | MSSWWEB 通道与 surface | 新增 `MSSWWEB` IM 类型；后端能力与 WebChat 一致；入口/路由按 channel 参数化，不复制 handler。 | P0 | US-01/02/03 |
| FEAT-02 | 外部断言认证 | 逐请求校验 `soc-token`（RS256 + AES `aud/iss` + claim 策略）；验签只在 Console；因子放 K8s Secret。 | P0 | US-01/07 |
| FEAT-03 | 用户级身份绑定 | 一个外部身份 → 一个 `platform_user`；入口授权单独记录；绑定 JSON 多字段 AND、平台级 `identity_keys`；解绑只删入口授权，不删身份；支持编辑。 | P0 | US-06/10/11 |
| FEAT-04 | 权限/入口可用接口 | `GET /identity` 永远查 Console/PG，返回用户级主体、入口授权、Agent 授权与 `allowed/reason`；成功后写 Redis。 | P0 | US-06 |
| FEAT-05 | 对话闭环 | 复用入口列表、会话创建/列表/详情、消息提交/历史、Run SSE、取消/恢复；会话按 user+Agent 跨入口共享。 | P0 | US-01/02/03 |
| FEAT-06 | 最近会话与红点未读 | 用户+Agent 跨入口聚合；item 带来源 `entry_id`/`channel`；`has_unread` + 会话级 mark-read；通知用户级共享，不做 IM 数据隔离。 | P0 | US-03/04 |
| FEAT-07 | 上传与产物 | 复用附件上传、`upload_ids`、Agent 产物发布与下载；同一 user+Agent 跨入口共享。 | P0 | US-05 |
| FEAT-08 | 来源标识与共享数据 | entry/channel 只做来源标识，不作为访问边界；不同用户隔离；同用户同 Agent 跨入口共享。 | P0 | US-07 |
| FEAT-12 | 确认与 resume | SSE `confirmation.required` / `WAITING_INPUT` 支持 BFF 展示选项并 resume 同一 Run；resume 要求用户 ACTIVE + Agent 授权。 | P0 | US-12 |
| FEAT-09 | 协议与规范文档 | 内部 `docs/operations/im-channel-playbook.md` + 外部 `docs/operations/bff-agent-integration.md`；接口分级、错误码、SSE、上传、幂等。 | P0 | US-08/09 |
| FEAT-10 | 运维配置降级 | Secret 未配置/损坏不影响启动与其他通道；只有 MSSWWEB 返回 503；失败关闭。 | P0 | US-07 |
| FEAT-11 | Console 管理面 | 创建 MSSWWEB 入口（无 bot secret，填写 bot_id）、绑定用户；写审计。 | P1 | US-06 |

#### FEAT-01: MSSWWEB 通道与 surface

- **描述**: 把 WebChat 的入口面抽象为 `ChatSurface`（channel + auth kind + entry resolver），同一套 router/service 以 `MSSWWEB` 挂载；执行面（Runtime Owner 接口、上传、SSE、产物）原样复用。
- **验收标准**:
  - [ ] 启用 MSSWWEB 入口后，`/api/v1/msswwweb/*` 提供与 `/api/v1/webchat/*` 同形的资源接口。
  - [ ] WebChat Cookie 登录与既有能力回归；共享列表/续聊按本次契约修正。会话保留创建来源，Run/通知按实际发送来源记录，页面不强制展示来源。
  - [ ] 新增通道不需要复制 15 个 handler；channel 由 surface 注入。
  - [ ] `MSSWWEB` 进入 `LOCAL_STREAM_CHANNELS`，Agent 产物交付走会话内发布，而不是 IM 出站管道。

#### FEAT-02: 外部断言认证

- **描述**: 业务平台 BFF 在每个请求携带 `soc-token`。Gateway 原样转发 Console 内部主体端点；Console 用 Secret 中的因子验签（RS256、AES-ECB 计算/解密 `aud/iss`、`exp` 哨兵、可选 `login_source`、全局最大会话年龄），按平台 `identity_keys` 从 JWT 取值并做哈希匹配，解析用户级业务身份。
- **验收标准**:
  - [ ] 有效 token → 返回统一 principal + 当前授权 Agent + 绑定入口集合。
  - [ ] 缺失/伪造/签名错/`aud`/`iss` 不匹配 → 401，稳定错误码；不泄露内部细节。
  - [ ] `login_source` 可选：配置了才校验签名 claim 相等；不配置不校验。请求头仅作交叉提示。
  - [ ] `exp=-1` 不按过期处理，但受我们的最大会话年龄约束；正数 `exp` 正常校验。
  - [ ] 因子只注入 Console；Gateway/日志/审计/响应均不出现 `soc-token`、salt、私钥。
  - [ ] 配置缺失/损坏 → 服务正常启动；MSSWWEB 请求 503 `EXTERNAL_ASSERTION_UNAVAILABLE`；WebChat/WeCom 不受影响；`/readyz` 不受影响。
  - [ ] 不引入我们自己的无状态 JWT 会话；仍不签发 JWT。

#### FEAT-03: 用户级身份绑定

- **描述**: 一个外部身份 → 一个 `platform_user`；入口授权单独记录。身份 JSON 的 key 是 JWT 顶层 claim 名、value 是期望值；多字段为 AND；平台级 `identity_keys` 定义字段集合。解绑只删除入口授权，不删除用户级外部身份。
- **验收标准**:
  - [ ] Console 支持创建/编辑用户级外部身份（平台 + 身份 JSON）；同一 `(tenant, platform, identity_hash)` 只能对应一个 `platform_user`。
  - [ ] `identity` 是 JSON 对象，至少 1 个键；值为非空字符串；拒绝 `exp`/`aud`/`iss`/`iat`/`nbf` 等非身份 claim；key 集合必须与平台 `identity_keys` 一致。
  - [ ] 多字段同时比对（AND）：任一 key 缺失或值不等都不命中。
  - [ ] 入口授权单独创建/删除；同一外部身份可授权多个入口，但始终是同一个 `platform_user`。
  - [ ] 解绑某个入口只删除该入口授权；外部身份保留，删除/取消/已读仍能识别本人；重绑同一入口后历史会话/产物恢复。
  - [ ] 编辑外部身份影响其名下所有入口授权；历史会话/产物仍归同一 `platform_user`。
  - [ ] 绑定/编辑/解绑写 `config_audit_log`；不泄露其他用户身份。
  - [ ] Console 用户详情页提供身份编辑与入口授权管理（P1）。

#### FEAT-04: 权限/入口可用接口 + Redis 认证缓存

- **描述**: `GET /api/v1/msswwweb/identity` 永远查 Console/PG，返回用户级主体、入口授权、Agent 授权与 `allowed/reason`；成功后 Gateway 把认证结果写 Redis，后续资源接口优先读 Redis。
- **验收标准**:
  - [ ] token 有效且身份存在、入口授权、Agent 授权 → `allowed=true`。
  - [ ] token 有效但无外部身份 → 200 `allowed=false`、`reason=UNBOUND`，不伪造 `platform_user_id`。
  - [ ] 入口禁用、Agent 禁用、无 Agent 授权 → 200 `allowed=false` + 稳定 `reason`。
  - [ ] token 无效 → 401；平台配置不可用 → 503。
  - [ ] `/identity` 每次都验签并查 PG；凭证与当前权限分层缓存，不保存 token 原文；WebChat 不再把 Agent 授权冻结在登录会话中。
  - [ ] 资源缓存命中必须核对独立权限版本、同步状态与平台配置版本；miss 时有界回源；Redis 不可用只允许在 PG 同步状态可确认时用当前事实核验。
  - [ ] 身份/入口 grant、用户状态、Agent 授权、Agent/入口启停或删除，都在成功响应前删除对应权限缓存；凭证变更还删相关凭证。旧的并发回源不能回填，也不能直接用于本次放行。
  - [ ] TTL 仅限制存活时间，不作为撤权兜底；同步未完成明确报告并拒绝使用旧权限，支持按持久状态恢复。Agent 撤权不强迫有效 WebChat 会话退出其他 Agent。
  - [ ] 因子、identity_keys、有效期策略变更/平台停用都变更 policy_epoch 并失效平台凭证；Console 重启不能让 Redis warm cache 绕过新配置。

#### FEAT-05: 对话闭环与共享历史

- **描述**: 复用 WebChat 的资源接口与契约；会话按 `tenant + user + agent` 跨入口共享，entry/channel 只做来源标识。
- **验收标准**:
  - [ ] 首次发送前显式创建会话；消息带 `conversation_id`、`message_id`、`Idempotency-Key`。
  - [ ] 202 受理、SSE、心跳、断线重连与 WebChat 同形。
  - [ ] 用户+Agent 的会话列表跨入口聚合；item 带 `entry_id`/`channel` 来源标识。
  - [ ] 现有 WebChat UI 同步使用共享列表；列表选择、URL 刷新与续聊不再要求来源入口等于当前入口；服务端仍校验用户/Agent，本次发送入口独立校验。
  - [ ] 按 ID 读详情/消息只校验 user+agent，不要求目标入口匹配；同用户同 Agent 的 WebChat 会话在 MSSWWEB 可见。
  - [ ] 不同用户的会话仍不可见（404，不泄露存在性）。
  - [ ] 停止/恢复按现有 Run 语义；`WAITING_INPUT` 只走合法 resume。

#### FEAT-06: 最近会话与红点未读

- **描述**: 复用现有“通知未读”口径：`channel_notification.read_at IS NULL` 按 `conversation_id` 聚合；会话列表返回 `has_unread`，新增带水位的会话级 mark-read。
- **验收标准**:
  - [ ] Run 终态/等待输入/任务结果产生未读通知时，对应会话 `has_unread=true`。
  - [ ] `POST /api/v1/msswwweb/conversations/{id}/read` 接受已看到的通知水位，只标记不高于该水位的通知；新到达的通知保留未读。
  - [ ] 会话详情在同一快照返回 `notification_through_seq` 与 `last_seq`/当前状态；按后者获取历史并展示后，read 提交前者。通知 feed、消息 canonical 和 SSE 游标不能混用；超前水位明确拒绝。
  - [ ] 通知是用户级共享：WebChat 可看到同一用户的 MSSW 通知；MSSW BFF 可选择不展示通知中心。
  - [ ] 不做消息级未读；`has_unread` 按会话聚合，不按 entry 过滤。
  - [ ] 产物发布本身不写通知；Run 终态通知点亮红点。

#### FEAT-07: 上传与产物

- **描述**: 复用附件上传（multipart、暂存、格式/大小门控、`upload_id`）与 Agent 产物发布/下载；同一 user+Agent 跨入口共享。
- **验收标准**:
  - [ ] 上传认证先于读 body；被拒请求不落暂存；格式/大小与 `client-config` 一致。
  - [ ] 消息带 `upload_ids` 后 Agent 可读；单条附件数量/大小按平台设置。
  - [ ] Agent 产物在会话历史/实时流中显示为附件卡；下载按 user+agent + 发布事实/checksum 裁决，不按 entry/channel 拦截。
  - [ ] `MSSWWEB` 在 `LOCAL_STREAM_CHANNELS`；不在 `OUTBOUND_CHANNELS`。

#### FEAT-08: 来源标识与共享数据

- **描述**: entry/channel 只做来源标识，不作为数据访问边界；同一 user+Agent 跨入口共享，不同用户隔离。
- **验收标准**:
  - [ ] 会话保留不可变创建来源；每个 Run 保留实际发送来源；任务、通知与交付跟随来源 Run，不强求与会话入口一致。后台保留即可，页面不强制增加来源标签。
  - [ ] 旧来源无法核实时允许未知，不猜成 WebChat；新创建路径完整写入；发布停旧 writer 后再校验回填尾差。
  - [ ] 同一用户 + 同一 Agent 可通过 MSSWWEB 读取 WebChat 创建的会话，反之亦然。
  - [ ] 不同用户的会话/Run/产物一律 404/403，不泄露存在性。
  - [ ] 删除/取消/已读按用户级核属；不要求入口仍启用；重复删除幂等。
  - [ ] 不再按 entry/channel 做“跨通道 404”断言。

#### FEAT-09: 协议与规范文档

- **描述**: 产出两份文档：内部 `docs/operations/im-channel-playbook.md` 与外部 `docs/operations/bff-agent-integration.md`。
- **验收标准**:
  - [ ] 外部文档包含：认证、P0/P1/P2 接口分级、逐接口请求/响应/错误码、SSE、上传、幂等、最近会话/未读、限流/超时、版本策略。
  - [ ] 内部文档包含：Channel/Platform/Entry/Identity 模型、决策树、必改清单、禁止清单、测试清单、DoD。
  - [ ] 文档接口清单与 Gateway router/OpenAPI 一致；错误码与 `error_codes.py` + `api-messages.yaml` 一致（契约测试）。

#### FEAT-10: 运维配置降级

- **描述**: Secret 只注入 Console；启动加载一次，不热更新；缺失/损坏按平台降级。
- **验收标准**:
  - [ ] Secret 未配置：正常启动 + WARN；MSSWWEB 503；WebChat/WeCom 正常；`/readyz` 正常。
  - [ ] JSON 损坏/单条非法：正常启动；只影响对应平台；错误日志不含因子。
  - [ ] 管理面显示 `configured=false`；指标 `external_assertion_platform_available` 可见。
  - [ ] 缺配置绝不跳过验签（失败关闭）。

#### FEAT-11: Console 管理面

- **描述**: 管理员可创建 MSSWWEB 入口、填写 `bot_id`（与 WebChat 一致的入口标识）、维护用户级外部身份并授权入口；入口不携带 bot secret。
- **验收标准**:
  - [ ] `SECRETLESS_CHANNELS` 包含 `MSSWWEB`；创建/更新不要求 secret。
  - [ ] 管理员填写 `bot_id`，按全局唯一校验；冲突返回 `BOT_ID_EXISTS`；BFF 只见 `entry_id`。
  - [ ] 用户详情页维护一个用户级外部身份（平台 + 身份 JSON），并单独授权/解绑入口。
  - [ ] 身份 JSON 的 key 集合必须匹配平台 `identity_keys`；多字段 AND。
  - [ ] 用户详情页支持编辑外部身份 JSON；编辑影响其名下所有入口授权，历史不丢。
  - [ ] 入口列表展示 channel、平台 key、启用状态、Agent；不展示因子。
  - [ ] 绑定/编辑/解绑/授权入口均写审计。
  - [ ] 前端表单不出现 salt/公钥/私钥输入。

#### FEAT-12: 确认与 resume

- **描述**: Agent 请求确认（如保存记忆）时，SSE 下发 `confirmation.required` / Run 进入 `WAITING_INPUT`；BFF 展示选项并调用 `POST /runs/{run_id}/resume` 继续同一个 Run。
- **验收标准**:
  - [ ] `confirmation.required` 的 `submission_id`/`source_event_id`/`options` 原样透出；BFF 按选项调用 resume。
  - [ ] resume 继续原 Run，不新建会话/Run；后续工具副作用按原执行语义。
  - [ ] resume 要求用户 ACTIVE + 当前 Agent 授权；Agent 授权撤销后默认拒绝，用户仍可取消。
  - [ ] entry 不是 resume 的授权边界；同用户同 Agent 可通过任一入口继续。
  - [ ] 重复 resume 按原 submission/source 幂等；非法选择/过期确认显式失败。

---

### 4.2 边缘情况

| 场景 | 预期行为 |
|------|----------|
| `soc-token` 缺失/超长 | 401 / 431 或 413；不进入业务逻辑 |
| token 有效但无外部身份 | 资源接口 403/404；`/identity` 200 `allowed=false`、`reason=UNBOUND` |
| token 有效但平台用户停用 | 403；不泄露用户是否存在 |
| token 的 `exp=-1` | 按最大会话年龄兜底；不无限接受 |
| token 的 `login_time` 在未来/过旧 | 超出 leeway 拒绝 |
| token 有效但无外部身份 | `/identity` 200 `allowed=false`、`reason=UNBOUND`；资源接口 403/404；不伪造主体 |
| 入口禁用/删除 | `/identity` 200 `ENTRY_DISABLED`；同用户同 Agent 的已有会话仍可按 user+agent 访问；新建/发消息不可用 |
| Agent 禁用/授权撤销 | `/identity` 200 `AGENT_DISABLED`/`NO_AGENT_GRANT`；读/发/SSE/resume 拒绝；删除/取消/已读保留 |
| Secret 缺失/损坏 | MSSWWEB 503；其他通道正常；启动不失败 |
| Redis 不可用 | `/identity` 仍走 PG；资源接口降级回 Console；不把 Redis 当唯一权威 |
| 业务平台登出/停止转发 token | 下一次请求 401；无需我们的吊销列表 |
| 长流期间 token 失效 | 下次复核边界内结束流；BFF 回登录 |
| 产物发布但 Run 未终态 | 不亮红点；与 WebChat 一致 |
| Run 失败/取消但已发布产物 | 终态通知亮红点；列表排序可能不因产物更新（现有行为） |
| 同用户同 Agent 跨入口访问 | 允许；entry/channel 只做来源标识 |
| 不同用户的会话/Run/产物 ID | 一律 404/403，不泄露存在性 |
| Agent 撤权后 resume | 当前准入核验默认拒绝；用户仍有效时可取消。已受理 Run/Task 维持冻结快照；撤权不承诺自动取消或回滚已发生副作用 |

---

## 5. 非功能需求

| 指标ID | 类型 | 要求 |
|--------|------|------|
| NFR-PERF-01 | 性能 | `/identity` 每次查 PG；资源接口正常命中只读 Redis 凭证/权限与独立版本状态；SSE 不按增量调控制面；上传不按分片鉴权。分别压测冷热路径、同步失效屏障、批量停用和混合负载的 p50/p95/p99；不预设未经测量的时延结论。 |
| NFR-PERF-02 | 性能 | 认证/复核的 p95/p99、控制池等待、DB 等待分开测；不以 p50 代替峰值；不写未实测的 5ms 结论。 |
| NFR-SEC-01 | 安全 | 因子只在 Console Secret；`soc-token`/salt/私钥不进日志、审计、响应；失败关闭；租户来自部署而非 JWT；不同用户隔离，同用户同 Agent 共享。 |
| NFR-REL-01 | 可用性 | Secret 缺失/损坏不影响服务启动与其他通道；仅 MSSWWEB 降级为 503；Redis 不可用时降级回 Console；`/readyz` 不受影响。 |
| NFR-COMPAT-01 | 适配性 | 共用 `muad_contracts.webchat`；WebChat 登录与既有能力回归，权限缓存、列表及跨入口续聊按新契约同步修正；未来 MSSP 复用通道抽象，不复制 handler。 |
| NFR-OPS-01 | 可运维性 | 平台可用性、认证失败原因、验签/缓存耗时、未绑定数、控制池等待、revision 变化有指标/日志。 |

> 无 NFR 时整节可省略。

---

## 6. 范围与边界

| 类别 | 内容 |
|------|------|
| **范围（In Scope）** | MSSWWEB 后端接入；身份/入口授权；同步缓存失效与恢复；对话/历史/SSE/取消/resume/上传/产物；现有 WebChat 共享列表与跨入口续聊前后端适配；后台来源；通知水位已读；Secret 降级与配置版本；两份指南；Console 管理。 |
| **非范围（Out of Scope）** | MSSP（仅保留配置扩展点，不实现）；业务平台前端/BFF 实现；IM 数据隔离（明确不做）；通知中心 UI（MSSW BFF 可不展示）；消息级未读；Agent prompt/技能效果；文件在线预览扩展；密钥热更新；多租户单部署。 |
| **前置假设** | MSSW 提供真实 JWT 因子与测试 token；AES-ECB 派生算法可复现；`login_source` 可选（启用时以真实 token 为准）；一个部署服务一个租户；业务平台 BFF 支持 SSE 透传。 |

---

## 7. 依赖与风险

### 7.1 项目依赖

| 依赖方 | 依赖内容 | 最晚交付时间 | 风险等级 |
|--------|----------|--------------|---------|
| MSSW 业务平台 | 公钥、issuer、audience、salt、真实测试 token、AES 实现/测试向量 | 设计前 | 高 |
| MSSW 业务平台 | `login-source=mssw` 的真实口径确认 | 设计前 | 中 |
| 部署/运维 | K8s Secret 注入 Console；`MSSWWEB` 入口创建 | 联调前 | 中 |
| 业务平台 BFF | SSE 透传、超时/缓冲配置、错误码处理 | 联调前 | 中 |

### 7.2 风险识别

| 风险ID | 描述 | 影响 | 缓解方案 |
|--------|------|------|---------|
| R-01 | `PyAesCipherECB` 的 key 派生算法未知，`aud/iss` 无法逐字节匹配 | 阻塞验签 | 先做 JWT spike：拿源码或测试向量（salt + 明文 + 密文） |
| R-02 | 样例 token 的 `login-source=mss`，与确认的 `mssw` 不一致 | 若配置 `login_source` 会全量 401 | 联调用真实 token；配置按实际值，或首版不配置该可选校验 |
| R-03 | `exp=-1` 表示不过期，撤销只能靠业务平台停止转发 | 被盗 token 长期有效 | 最大会话年龄 + 绑定/用户状态复核 + SSE 定时复核 |
| R-04 | 逐请求 Console 认证成为热点/单点 | 延迟/可用性 | `/identity` 查 PG 并写 Redis；资源接口 Redis-first + revision；控制池预算与降级 |
| R-05 | BFF 缓冲 SSE | 首帧/心跳被压住 | 接入指南明确 `no-store`/不缓冲/`Last-Event-ID` |
| R-06 | JWT 含 email/phone 等 PII | 泄露面 | 只取绑定 JSON 的 key 集合；不解密/不落库/不日志 |
| R-07 | 单公钥轮换无重叠 | 旧 token/暖缓存不一致 | 维护窗口关闭平台并失效缓存，替换重启全部副本、核对配置并激活新 epoch；通知业务平台换发 token |
| R-08 | MSSWWEB 的 `bot_id` 由管理员填写 | 与现有入口键冲突/不稳定 | 沿用全局唯一校验 `BOT_ID_EXISTS`；约定稳定命名（如 `msswweb-<agent_key>`）；BFF 只见 `entry_id` |
| R-09 | 通知未读语义不是“消息级” | 产品预期偏差 | PRD 明确：红点=终态通知未读；消息级未读另立需求 |
| R-10 | Console 前端工作量被低估 | 排期风险 | FEAT-11 标 P1；后端 API 先行，UI 可分批 |

---

## 8. Existing Spec Constraints

| Spec/Rule | 约束 | 对范围/验收的影响 | 状态 |
|-----------|------|------------------|------|
| `harness-im#RULE-im-001` | 一个 Agent 可绑 0..N 个 IM 入口；每个 `bot_id` 只路由一个 Agent | MSSWWEB 入口按 Agent 创建；`bot_id` 全局唯一 | applied |
| `harness-im#RULE-im-002` | 渠道差异只活在适配器层；核心域零渠道字样 | 不在 Runtime/Worker 写 `MSSWWEB` 分支；Gateway 只参数化 surface/service | applied |
| `harness-im#RULE-im-003` | 附件格式清单单一事实 | 上传/下载复用 `muad_contracts.attachments` | applied |
| `harness-im#RULE-im-005` | 会话删除是 Owner 侧软删 | MSSWWEB 删除复用用户级语义；不预解析、不按 entry 断言，保持幂等 | applied |
| `harness-auth#RULE-auth-001` | 三层授权：User→Agent、Agent→Skill/MCP、SELECTED 资源 | `/identity` 与入口列表按有效 Agent 授权判定 | applied |
| `harness-auth`（CHAT 认证面约定） | CHAT 面与 Console 会话分离；不签发无状态 JWT | 外部断言验签不产生我们的会话；不新增 JWT 签发 | applied |
| `harness-auth`（租户口径） | 用户态租户来自主体，不来自请求头 | MSSWWEB 内部端点用 `HeaderTenantId` + 服务身份；公开面租户来自部署 | applied |
| `harness-secret#RULE-secret-001` | 密钥不进日志/审计/Snapshot；明文存 Owner 表 | 本需求的因子是**部署 Secret**（不落 DB）；需在 Spec Delta 中明确“不可变外部因子”例外 | applied（需 Delta） |
| `harness-data#RULE-data-001` | 表设计/索引/软删/时间口径 | 新增 `external_identity`/`entry_access_grant`；`conversation.channel`；未读索引；平台元数据不落库；旧 `channel_identity` 不动 | applied |
| `harness-api`（封套/错误码） | 统一封套、稳定错误码、消息目录 | 新错误码需登记 `error_codes.py` + `api-messages.yaml` | applied |
| `harness-test`（验收隔离） | 测试分层、隔离库、残留处理 | 新增验签向量、用户共享/隔离矩阵、SSE/上传用例 | applied |

---

## 附录：术语表

| 术语 | 定义 |
|------|------|
| MSSW | 本次对接的业务平台；IM 类型为 `MSSWWEB`，平台配置键为 `mssw` |
| BFF | 业务平台侧的 Backend-for-Frontend，负责把浏览器 Cookie 中的 JWT 转发为 `soc-token` |
| `soc-token` | 业务平台签发的 JWT，放在请求头；我们只验签，不签发 |
| 外部断言 | 外部平台签发的身份断言（JWT），验签后映射为我们的 `platform_user` |
| Entry | `control.bot_account` 一行，代表一个 IM 入口；MSSWWEB 入口无 bot secret |
| Channel | 呈现/接入类型（`MSSWWEB`）；只做来源标识，不做数据隔离 |
| External identity | 用户级外部身份：一个 JWT 身份 → 一个 `platform_user` |
| Entry grant | 外部身份对某个入口的授权；解绑只删授权，不删身份 |
| Auth revision | PG 用户权限版本；业务授权变更同事务递增，Redis 独立当前版本参与校验 |
| Policy epoch | 外部平台已激活配置的非密版本；配置变化时失效旧凭证缓存 |
| 通知未读 | `channel_notification.read_at IS NULL` 按会话聚合的红点；不是消息级未读 |

---

*文档结束*
