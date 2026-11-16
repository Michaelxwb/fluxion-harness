---
{"version":1,"requirement":"mssw-external-im-channel"}
---
# Tasks: mssw-external-im-channel

<!--
需求：上游 MSSW 平台通过 BFF 接入「智能客服」通道（MSSWWEB）。
基线：本目录 PRD / backend.design / frontend.design / scenarios / decisions / open-questions。
实现文档：docs/mssw-channel/（协议事实与决策记录）。

依赖批次（同批次可并行）：
  批次 1  TASK-001 契约、TASK-002 规范沉淀               （无依赖）
  批次 2  TASK-003 表结构与迁移                           （← 001）
  批次 3  TASK-004 外部断言认证                           （← 001, 003）
  批次 4  TASK-005 Console 管理面、TASK-007 Gateway 挂载、TASK-010 Runtime 会话
                                                          （005 ← 003,004；007 ← 001,004；010 ← 001,003）
  批次 5  TASK-006 认证缓存失效、TASK-011 Console 前端、TASK-008 取消、TASK-009 上传
                                                          （006 ← 005；011 ← 005；008/009 ← 007）
  批次 6  TASK-012 部署与运维配置                         （← 004, 006）
  批次 7  TASK-013 端到端验收                             （← 全部）

场景覆盖矩阵（scenarios.md 共 31 个场景，全部有归属）：
  001 —               （契约层，无独立场景；S-01 依赖其通道名）
  002 —               （规范沉淀，无场景）
  003 —               （表结构，S-02 依赖其持久化）
  004 S-03, E-01, E-02, E-03, B-02
  005 S-01, S-02, S-09, E-04
  006 E-05, E-07, E-10, E-12, E-13, E-14
  007 S-03, B-03, B-04
  008 S-04
  009 S-06
  010 S-05, S-07, S-08, S-10, S-11, E-04, E-06, E-08, E-11, B-01, B-05, B-06
  011 S-02, S-09
  012 E-03, E-09
  013 全量回归（31 个场景全部再跑一遍）
-->

## TASK-001: 契约与通道枚举扩展

- **Status**: done
- **Depends**:
- **Source**: mssw-external-im-channel.backend.design.md#26-契约与枚举
- **Scenarios**:
- **Spec-Refs**: im/harness-im, api/harness-api, test/harness-test
- **Paths**: packages/contracts/src/muad_contracts/enums.py, packages/contracts/src/muad_contracts/webchat.py, tests/architecture/test_channel_neutrality.py, tests/test_attachment_contract.py, tests/agent_worker/test_capacity_webchat_worker_notifications.py
- **E2E**:
- **Skip-Tests**:
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "contracts",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/test_attachment_contract.py", "tests/architecture/test_channel_neutrality.py"],
    "timeout": 300,
    "parallel": false
  }
]
```

### Description

把 `MSSWWEB` 加入内建通道名，并同步全部机检词表与硬断言。这是所有后续 TASK 的前置：契约不扩，其它模块连通道名都无法引用。

关键点：
1. `BUILTIN_CHANNEL_NAMES` 由二元组改为三元组 `("WECOM", "WEBCHAT", "MSSWWEB")`。
2. `MSSWWEB` 必须进 `LOCAL_STREAM_CHANNELS`（BFF 拉取式 SSE），**不得进** `OUTBOUND_CHANNELS` —— 进错会让产物交付路径抛 `ATTACHMENT_WRITE_UNAVAILABLE`。
3. 两个分组必须**完全划分** `BUILTIN_CHANNEL_NAMES`（`tests/test_attachment_contract.py` 已有该断言）。
4. `tests/architecture/test_channel_neutrality.py` 的 `CHANNEL_TOKENS` 必须加 `"msswwweb"`，否则新增通道的泄漏扫不到，机检变成假绿灯。
5. 三处硬断言 `== ("WECOM","WEBCHAT")` 同步改为三元组：`tests/test_attachment_contract.py` 两处、`tests/agent_worker/test_capacity_webchat_worker_notifications.py` 一处。
6. `webchat.py` 契约补充 `has_unread`、已读回执、外部身份/未绑定契约、`ChatAuthMethod` 扩值。

### Checklist

- [x] `BUILTIN_CHANNEL_NAMES` 含 `MSSWWEB`，且 `LOCAL_STREAM_CHANNELS`/`OUTBOUND_CHANNELS` 完全划分它
- [x] `CHANNEL_TOKENS` 含 `msswweb`；新增「注册表通道名必须在词表中」断言（`WEBCHAT` 显式豁免，避免把既有 30+ 文件的 webchat 命名一次性判违规）
- [x] 三处硬断言改为**结构性断言**（内建名成员判定，而非枚举全表），新增通道不再需要改断言
- [x] `webchat.py` 契约补充：`has_unread`、已读回执、`ExternalPrincipal*`、`ExternalIdentity*`、`ChannelAuthMethod` 扩值
- [x] `tests/test_attachment_contract.py`、`tests/architecture/test_channel_neutrality.py` 全绿（11 passed）
- [x] `test_capacity_webchat_worker_notifications.py` 本机未跑：该文件依赖 `agent-core`（Unix `resource` 模块 + 真实 PG/uvicorn），Windows 无法收集；断言已改为结构性断言，留 CI 验证

### Spec Delta

```json
[]
```

### Log

- [2026-11-16] created
- [2026-11-16T03:03:42.557Z] started tests=ea5bb3067dc4

### Verification

- 尚未执行

---
- 2026-11-16T03:25:30.850Z PASS commands=1/1 executed=1 deduped=0 elapsed=15039ms tests=ca37b54a98d9 head=dcfa5524094a dirty=1 tests-changed
  - contracts PASS exit=0

---

## TASK-002: 规范沉淀（Spec Delta）

- **Status**: done
- **Depends**:
- **Source**: mssw-external-im-channel.backend.design.md#28-规范沉淀
- **Scenarios**:
- **Spec-Refs**: im/harness-im, auth/harness-auth, secret/harness-secret, persistence/harness-data
- **Paths**: .code-flow/specs/im/harness-im.md, .code-flow/specs/auth/harness-auth.md, .code-flow/specs/secret/harness-secret.md, .code-flow/specs/persistence/harness-data.md
- **E2E**:
- **Skip-Tests**: true
- **Skip-Reason**: 纯规范沉淀，无代码可执行；Delta 由 cf-task:archive 应用

### Tests

```json
[]
```

### Description

把本次接入沉淀为可复用的规范规则，供后续同类 JWT 平台（MSSP 等）直接引用。只写规范与编码要求，**不写具体实现值**（端口、超时秒数、容量上限、指标名）——按项目规范，这些引用配置或契约所有者。

四条规则：
1. `im/harness-im`：`ChatSurface(channel, auth_kind, entry_resolver)` 抽象；通道名参数化，禁止字面量决定 Run 落库或能力分组。
2. `auth/harness-auth`：外部断言五步链；未绑定返回可空主体、不伪造身份。
3. `secret/harness-secret`：因子只放 Secret；缺失只让该通道 503，不影响启动与其它通道。
4. `persistence/harness-data`：`external_identity` 用户级 + `entry_access_grant` 入口授权两表分离；解绑只删 grant；旧 `channel_identity` 不动。

### Checklist

- [x] 每条规则有稳定 `RULE-*` ID，编号取自各 spec 的 `next_rule`（`im-006`、`auth-002`、`secret-002`、`data-002`）
- [x] 规则写规范不写实现值；需要具体值时引用 `*Settings` 或契约所有者
- [x] 关键规则配 ✅/❌ 对照示例
- [x] Spec Delta JSON 合法，`target` 相对 `.code-flow/specs` 且不带 `.md`
- [x] 四个 spec 的 `next_rule` 同步 +1（`im` 6→7，其余 1→2），文件仍只有 Rules/Conventions/Avoid 三个 H2

### Spec Delta

```json
[
  {"id":"DELTA-001","op":"add","target":"im/harness-im","rule":"外部 JWT 平台接入以 ChatSurface(channel, auth_kind, entry_resolver) 抽象复用同一套 router/service：新平台只注册 surface 与适配器，不复制 handler。通道名必须参数化——任何以字面量硬编码通道名决定 Run 落库、能力分组或路由的位置都必须改为取当前 surface 的 channel。","evidence":"backend.design.md#26；docs/mssw-channel/02-requirements.md §3.6 六处写死清单"},
  {"id":"DELTA-002","op":"add","target":"auth/harness-auth","rule":"外部断言认证按固定五步链校验：验签（算法白名单）→ AES 因子加密期望值比对 → claim 策略（最大会话年龄等）→ 按平台配置的 identity_keys 提取身份字段并计算 identity_hash → 按 (tenant, platform_key, identity_hash) 查外部身份。验签通过但无身份记录时返回可空主体（allowed=false + reason），不得伪造 platform_user 身份继续处理。","evidence":"backend.design.md；docs/mssw-channel/02-requirements.md §3.3"},
  {"id":"DELTA-003","op":"add","target":"secret/harness-secret","rule":"外部平台的验签因子（公钥、issuer、audience、salt）只存放于 Secret，不落库、不回显、不写日志。Secret 缺失或损坏只让该通道的认证返回不可用（503），不影响进程启动与其它通道；配置错误在启动期暴露为显式错误。","evidence":"backend.design.md#26；docs/mssw-channel/02-requirements.md §3.2"},
  {"id":"DELTA-004","op":"add","target":"persistence/harness-data","rule":"外部身份与入口授权分为两张表：external_identity 为用户级（唯一 (tenant_id, platform_key, identity_hash) WHERE is_deleted=false），entry_access_grant 为入口授权（唯一 (tenant_id, external_identity_id, bot_account_id) WHERE is_deleted=false）。解绑只删 grant 不删身份，编辑身份保留历史会话归属。旧 channel_identity 表不改动。","evidence":"backend.design.md#表结构；docs/mssw-channel/02-requirements.md §2.3"}
]
```

### Log

- [2026-11-16] created
- [2026-11-16T03:29:02.749Z] started tests=4f53cda18c2b

### Verification

- 尚未执行

---
- 2026-11-16T03:29:35.161Z SKIPPED commands=0/0 executed=0 deduped=0 tests=4f53cda18c2b head=7a9106a90266 dirty=1 reason=纯规范沉淀，无代码可执行；Delta 由 cf-task:archive 应用

---

## TASK-003: 外部身份与入口授权表结构与迁移

- **Status**: draft
- **Depends**: TASK-001
- **Source**: mssw-external-im-channel.backend.design.md#表结构
- **Scenarios**:
- **Spec-Refs**: persistence/harness-data, auth/harness-auth
- **Paths**: apps/console-platform/backend/src/muad_console_platform/infrastructure/models/channel.py, apps/console-platform/backend/src/muad_console_platform/infrastructure/models/control.py, apps/console-platform/backend/src/muad_console_platform/infrastructure/repositories/external_identity_repository.py, migrations/versions/**
- **E2E**:
- **Skip-Tests**:
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "models",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/console_platform"],
    "timeout": 300,
    "parallel": false
  }
]
```

### Description

建立 `external_identity`、`entry_access_grant` 两张表与仓库层，并为 `platform_user` 增加 `auth_revision` 列。

关键点：
1. `external_identity`：`platform_key`、`identity_json`、`identity_hash`、`platform_user_id`；唯一 `(tenant_id, platform_key, identity_hash)` WHERE `is_deleted=false`；索引 `(tenant_id, platform_user_id)`。
2. `entry_access_grant`：`external_identity_id`、`bot_account_id`；唯一 `(tenant_id, external_identity_id, bot_account_id)` WHERE `is_deleted=false`；索引 `(tenant_id, bot_account_id, external_identity_id)` 支撑入口停用时反查受影响用户。
3. `platform_user.auth_revision`：存量行由迁移初始化。
4. 仓库层提供 `find_by_hash()`、`list_for_user()`、grant 查询；按平台 + hash 定位，不扫用户表。
5. 迁移必须可重复执行、可回滚，符合 `migrations/` 既有风格。

### Checklist

- [ ] 两张表模型与索引按唯一键口径建立
- [ ] `platform_user.auth_revision` 列新增且存量初始化
- [ ] 仓库方法 `find_by_hash` / `list_for_user` / grant 查询可用
- [ ] 迁移 up/down 可执行
- [ ] tenant 隔离：`find_by_hash` 必须带 `tenant_id`，不能全局查

### Spec Delta

```json
[]
```

### Log

- [2026-11-16] created

### Verification

- 尚未执行

---

## TASK-004: 外部断言认证（Console 验签与身份解析）

- **Status**: draft
- **Depends**: TASK-001, TASK-003
- **Source**: mssw-external-im-channel.backend.design.md#外部断言
- **Scenarios**: S-03, E-01, E-02, E-03, B-02
- **Spec-Refs**: auth/harness-auth, secret/harness-secret, api/harness-api
- **Paths**: apps/console-platform/backend/src/muad_console_platform/application/external_assertion/**, apps/console-platform/backend/src/muad_console_platform/api/internal_channel.py, packages/common/src/muad_common/settings.py
- **E2E**:
- **Skip-Tests**:
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "external-assertion",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/console_platform"],
    "timeout": 300,
    "parallel": false
  }
]
```

### Description

在 Console 实现 `soc-token` 验签与身份解析，暴露 `POST /internal/channel/external-principal`。

关键点：
1. 模块拆分：`config`（平台因子加载）、`aes_ecb`（期望值加密比对）、`verifier`（RS256 验签 + claim 策略）、`principal`（`identity_keys` 提取 + `identity_hash` + 查身份/授权）。
2. 算法白名单只允许 RS256；`alg=none` 必须拒绝。
3. `aud`/`iss` 用「加密期望值 + 密文逐字节比对」，**不解密**（上游 padding 非标准）。
4. `identity_keys` 从平台 Secret/ConfigMap 读取；取指定顶层 claim，全部存在且非空字符串才算通过，按 key 排序生成规范 JSON 后 `sha256`。
5. 未绑定：验签通过但无 `external_identity` → `platform_user_id=null`、`grants=[]`、`allowed=false`、`reason=UNBOUND`，**不构造 ChatIdentity**。
6. 身份命中但无该入口 grant → `platform_user_id` 与主体**照常返回**，`allowed=false`、`reason=NO_ENTRY_GRANT`。区分「查无此人」与「有此人不许进」，前者不能伪造身份、后者不能丢主体。
7. Secret 缺失/损坏：只有该通道认证 503，进程仍启动（E-03）。
8. `max_session_age` 兜底 `exp=-1`：超出返回 `EXTERNAL_ASSERTION_EXPIRED`（B-02）。
9. `settings.py` 增加 `external_assertion_platforms_json`（`repr=False`）与全局 `external_assertion_max_session_age_sec`、`external_assertion_cache_ttl_sec`。
10. `soc-token` 长度上限由配置定义（与 nginx header buffer 对齐），超限显式拒绝。

### Checklist

- [ ] 五步链按顺序实现，任一步失败 fail-closed
- [ ] 合法 token 得到正确 `identity_hash` 与主体映射
- [ ] `alg=none`、错误签名、篡改密文、`aud`/`iss` 不匹配均被拒（E-01）
- [ ] 未绑定返回 nullable 主体，`reason=UNBOUND`，不伪造身份（E-02）
- [ ] 身份命中但无入口 grant 时返回主体 + `reason=NO_ENTRY_GRANT`，不当成 UNBOUND
- [ ] Secret 缺失时进程可启动且该通道 503（E-03）
- [ ] 超出最大会话年龄返回 `EXTERNAL_ASSERTION_EXPIRED`（B-02）
- [ ] 因子不出现在日志、响应、`repr`
- [ ] `/internal/channel/external-principal` 契约与 backend.design 一致

### Spec Delta

```json
[]
```

### Log

- [2026-11-16] created

### Verification

- 尚未执行

---

## TASK-005: Console 外部身份与入口授权管理面

- **Status**: draft
- **Depends**: TASK-003, TASK-004
- **Source**: mssw-external-im-channel.backend.design.md#管理面接口
- **Scenarios**: S-01, S-02, S-09, E-04
- **Spec-Refs**: api/harness-api, auth/harness-auth, im/harness-im
- **Paths**: apps/console-platform/backend/src/muad_console_platform/api/users.py, apps/console-platform/backend/src/muad_console_platform/api/external_platforms.py, apps/console-platform/backend/src/muad_console_platform/application/external_identity_service.py, apps/console-platform/backend/src/muad_console_platform/application/dto.py, apps/console-platform/backend/src/muad_console_platform/application/channel_admin_service.py
- **E2E**:
- **Skip-Tests**:
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "identity-api",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/console_platform"],
    "timeout": 300,
    "parallel": false
  }
]
```

### Description

实现管理面接口，让管理员能创建 MSSWWEB 入口、手工录入外部身份并授权入口。

接口清单：
- `GET /api/v1/external-platforms`（API-17）：平台元数据 + `configured` + `identity_keys` + `identity_field_labels`；**不返回任何因子**。
- `GET /api/v1/external-platforms/{platform_key}/entries`（API-19）：可绑定入口，支持 `query/page/page_size`。
- `PUT /api/v1/users/{user_id}/external-identities`（API-18）：`{platform_key, identity}`，key 集合必须与 `identity_keys` 完全一致，否则 `IDENTITY_KEYS_MISMATCH`。
- `PUT /api/v1/users/{user_id}/external-identities/{identity_id}`（API-21）：编辑 `identity_json`，重算 hash，历史归属不变。
- `GET /api/v1/users/{user_id}/external-identities`（API-24）：身份 + grants。
- `POST /api/v1/users/{user_id}/external-identities/{identity_id}/grants`（API-22）：`{channel_account_id}`。
- `DELETE /api/v1/users/{user_id}/external-identities/{identity_id}/grants/{entry_id}`（API-23）：只删 grant，不删身份。

关键点：
1. 已绑他人 → `IDENTITY_ALREADY_BOUND`（409），不产生第二条。
2. `channel_admin_service.py` 的 `SECRETLESS_CHANNELS` 加 `MSSWWEB`（该通道无 Secret）；`bot_id` 仍必填（S-01）。
3. 全部写操作走统一失效服务（TASK-006 提供），禁止旁路写权限。

### Checklist

- [ ] 七个接口按契约实现，响应模型 `extra="forbid"`
- [ ] key 集合不匹配、重复绑定、跨 tenant 访问均显式拒绝
- [ ] 只建身份不建 grant 时，`/identity` 对该入口返回 `allowed=false` / `NO_ENTRY_GRANT`，主体仍返回
- [ ] 同一身份可授权多个入口，各自独立增删
- [ ] 编辑身份不改变历史会话归属（S-09）
- [ ] 解绑只删 grant，身份保留且可重绑
- [ ] 响应与日志不出现 salt/公钥/issuer/audience
- [ ] `MSSWWEB` 在 `SECRETLESS_CHANNELS` 中，入口创建不再要求 Secret（S-01）
- [ ] 同一用户可挂多身份且各自独立授权（E-04）

### Spec Delta

```json
[]
```

### Log

- [2026-11-16] created

### Verification

- 尚未执行

---

## TASK-006: 认证缓存与撤权失效

- **Status**: draft
- **Depends**: TASK-005
- **Source**: mssw-external-im-channel.backend.design.md#认证缓存
- **Scenarios**: E-05, E-07, E-10, E-12, E-13, E-14
- **Spec-Refs**: auth/harness-auth, secret/harness-secret
- **Paths**: apps/console-platform/backend/src/muad_console_platform/application/auth_cache_invalidator.py, apps/console-platform/backend/src/muad_console_platform/application/grant_service.py, apps/console-platform/backend/src/muad_console_platform/application/user_service.py, apps/console-platform/backend/src/muad_console_platform/main.py, packages/common/src/muad_common/session_store.py
- **E2E**:
- **Skip-Tests**:
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "cache-invalidate",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/console_platform"],
    "timeout": 300,
    "parallel": false
  }
]
```

### Description

实现「撤权秒级生效」的核心机制：PG 状态机 + Redis 屏障 + 版本号 + fence CAS。

关键点：
1. PG 状态机 `PREPARING → COMMITTED → READY` + Redis 屏障；`auth_revision` 与 `policy_epoch` 双版本。
2. 所有权限变更（grant/revoke、用户停用、Agent/入口启停删除、身份编辑删除）接入统一失效服务，禁止旁路写。
3. fence CAS 保证并发变更不丢；advisory lock + Lua 同槽保证原子（E-12）。
4. 崩溃恢复：`main.py` 接流量前恢复同步状态并核对平台激活版本（E-13）。
5. `GET /identity` **永远查 PG**（成功后写 Redis），资源请求 Redis-first + revision 校验（E-10）。
6. 屏障期间返回可重试的不可用，**不能放行旧授权**（E-07）。
7. 配置变化需换激活版本，与普通重启区别对待（E-14）。
8. 失效降级必须可见（warning 日志 + 指标），不静默吞。

### Checklist

- [ ] 状态机三态与屏障实现，版本号单调递增
- [ ] 所有权限写路径接入失效服务，无旁路（E-05）
- [ ] 并发撤权不丢更新（fence CAS，E-12）
- [ ] 重启后同步状态可恢复，无旧授权穿透（E-13）
- [ ] `/identity` 永远读 PG；资源接口命中失效 revision 时拒绝（E-10）
- [ ] 长流期间撤权可被 SSE 复核感知（E-07）
- [ ] 配置变化换版本与普通重启可区分（E-14）
- [ ] 失效失败有可见告警，不静默

### Spec Delta

```json
[]
```

### Log

- [2026-11-16] created

### Verification

- 尚未执行

---

## TASK-007: Gateway 通道挂载与通道名参数化

- **Status**: draft
- **Depends**: TASK-001, TASK-004
- **Source**: mssw-external-im-channel.backend.design.md#通道名参数化位置
- **Scenarios**: S-03, B-03, B-04
- **Spec-Refs**: im/harness-im, api/harness-api
- **Paths**: apps/im-gateway/src/muad_im_gateway/channels/webchat.py, apps/im-gateway/src/muad_im_gateway/channels/registry.py, apps/im-gateway/src/muad_im_gateway/main.py, apps/im-gateway/src/muad_im_gateway/application/webchat_core.py, apps/im-gateway/src/muad_im_gateway/application/webchat_service.py
- **E2E**:
- **Skip-Tests**:
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "gateway",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/agent_worker", "tests/test_attachment_contract.py"],
    "timeout": 300,
    "parallel": false
  }
]
```

### Description

用 `ChatSurface` 抽象把同一套 router 按 channel 多挂载，暴露 `/api/v1/msswwweb`，并消除 6 处通道名写死。只做挂载与参数化，不改取消/上传的归属语义（分别由 TASK-007b、TASK-007c 承接）。

六处写死（漏改任何一处都会导致 Run 落错通道或能力分组缺失）：
1. `webchat_service.py` — 会话/入口相关处
2. `webchat_service.py` — 消息发送处
3. `webchat_service.py` — scope 解析处
4. `webchat_service.py` — 取消处
5. `webchat_service.py` — 恢复处
6. **`webchat_core.py` 的 `_run_request`** —— 最关键，决定 Run 落库通道

关键点：
1. `WebchatChannelAdapter(name=...)` 参数化；registry 注册 `MSSWWEB`。
2. `main.py` 同一 router 双挂载；**通知 router 只挂 WebChat**（MSSW 不暴露通知中心）。
3. 路由前缀固定 `/api/v1/msswwweb`（决策 I4），不做成平台变量。
4. 测试兜底必须断言 `RunRequest.channel == "MSSWWEB"`，**不能只断言 HTTP 202**。
5. 文本长度边界（B-03）与上传大小/类型边界（B-04）沿用现有门控，不因新通道放宽。

### Checklist

- [ ] 六处写死的通道名取值改为从 surface 读取
- [ ] registry 注册 `MSSWWEB`，路由 `/api/v1/msswwweb` 可达（S-03）
- [ ] 通知 router 未挂到 MSSWWEB
- [ ] 测试断言 `RunRequest.channel` 实际值
- [ ] 文本长度与上传边界沿用既有门控（B-03、B-04）
- [ ] `tests/architecture/test_channel_neutrality.py` 仍全绿

### Spec Delta

```json
[]
```

### Log

- [2026-11-16] created
- [2026-11-16] 从原 TASK-007 拆出取消路径与上传归属，本任务只保留挂载与参数化

### Verification

- 尚未执行

---

## TASK-008: 停止生成的通道参数化取消路径

- **Status**: draft
- **Depends**: TASK-007
- **Source**: mssw-external-im-channel.backend.design.md#取消
- **Scenarios**: S-04
- **Spec-Refs**: im/harness-im, api/harness-api
- **Paths**: apps/im-gateway/src/muad_im_gateway/application/webchat_service.py, apps/im-gateway/src/muad_im_gateway/application/webchat_streams.py
- **E2E**:
- **Skip-Tests**:
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "cancel",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/agent_worker"],
    "timeout": 300,
    "parallel": false
  }
]
```

### Description

让「停止生成」在 MSSWWEB 上走与 WebChat 相同的取消语义，且不因通道不同而越权。

关键点：
1. 取消入口按 `owned_run_state` 核属（user + Agent），**不按通道断言**——同 user 多入口共享会话时，从另一入口也能停掉本次 Run。
2. 通道名只影响 Run 落库与能力分组，不影响取消权限判定。
3. 取消后 SSE 连接收到终态并关闭；已订阅方不挂起。
4. 取消的 Run 转 `CANCELLED`，不得回写他人会话或他人 Run。

### Checklist

- [ ] MSSWWEB 上停止生成生效，Run 转终态（S-04）
- [ ] 取消核属按 user + Agent，不按通道
- [ ] 跨入口共享会话时取消权限一致
- [ ] 取消后 SSE 收到终态并关闭

### Spec Delta

```json
[]
```

### Log

- [2026-11-16] created（从原 TASK-007 拆出）

### Verification

- 尚未执行

---

## TASK-009: 上传归属与产物路径的通道参数化

- **Status**: draft
- **Depends**: TASK-007
- **Source**: mssw-external-im-channel.backend.design.md#附件
- **Scenarios**: S-06
- **Spec-Refs**: im/harness-im, api/harness-api
- **Paths**: apps/im-gateway/src/muad_im_gateway/application/webchat_service.py, apps/agent-runtime/src/muad_agent_runtime/application/channel_uploads.py
- **E2E**:
- **Skip-Tests**:
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "upload",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/agent_worker", "tests/test_attachment_contract.py"],
    "timeout": 300,
    "parallel": false
  }
]
```

### Description

保证附件上传与产物交付在 MSSWWEB 上按「入口」正确归属，且产物走本地流式而非出站推送。

关键点：
1. 暂存上传的核属与**本次发送入口**一致，不把会话创建入口当上传入口。
2. `MSSWWEB` 属 `LOCAL_STREAM_CHANNELS`（TASK-001 已归类），产物按 BFF 拉取式 SSE 交付，**不走** `OUTBOUND_CHANNELS` 的出站路径，否则抛 `ATTACHMENT_WRITE_UNAVAILABLE`。
3. 上传大小/类型门控沿用既有实现，不因新通道放宽。
4. 产物下载核属按 user + Agent，不按通道。

### Checklist

- [ ] 上传归属与本次发送入口一致（S-06）
- [ ] 产物走本地流式，不触发出站交付错误
- [ ] 大小/类型门控沿用既有实现
- [ ] 产物下载核属按 user + Agent

### Spec Delta

```json
[]
```

### Log

- [2026-11-16] created（从原 TASK-007 拆出）

### Verification

- 尚未执行

---

## TASK-010: Runtime 会话、来源与通知水位

- **Status**: draft
- **Depends**: TASK-001, TASK-003
- **Source**: mssw-external-im-channel.backend.design.md#runtime
- **Scenarios**: S-05, S-07, S-08, S-10, S-11, E-04, E-06, E-08, E-11, B-01, B-05, B-06
- **Spec-Refs**: im/harness-im, api/harness-api, persistence/harness-data
- **Paths**: apps/agent-runtime/src/muad_agent_runtime/api/webchat_history.py, apps/agent-runtime/src/muad_agent_runtime/application/webchat_history.py, apps/agent-runtime/src/muad_agent_runtime/application/channel_notifications.py, apps/agent-runtime/src/muad_agent_runtime/application/channel_notification_queries.py, apps/agent-runtime/src/muad_agent_runtime/application/channel_conversations.py, apps/agent-runtime/src/muad_agent_runtime/application/run_service.py, apps/agent-runtime/src/muad_agent_runtime/application/channel_uploads.py
- **E2E**:
- **Skip-Tests**:
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "runtime",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/agent_worker"],
    "timeout": 300,
    "parallel": false
  }
]
```

### Description

让 Runtime 支持「同一 user + 同一 Agent 跨入口共享」，并补上来源追踪、未读 EXISTS、已读水位、删除与 resume。

关键点：
1. 会话列表按 `user + agent` 聚合，不再按入口过滤；item 带来源 `entry_id`/`channel`/`has_unread`（S-11）。
2. 详情/历史/取消/删除/产物按 user + Agent 核属，**不做 entry/channel 断言**（入口只有入口级接口才校验）。
3. 隐式 thread 指针被显式 `conversation_id` 取代时，必须三方核属，且受理前检查、事务内复查、实际解析三处一致。
4. 通知来源跟随 Run，会话可见性另查；不因 Run 来源不同回退到会话来源（B-05）。
5. `has_unread` 用 EXISTS 判；先分页再 EXISTS（S-05）。
6. `POST /internal/webchat/conversations/{id}/read` 带通知水位，只标记不高于水位的通知；超前返回 `CURSOR_AHEAD`。
7. 删除会话：先按既有取消语义处理活跃 Run 再软删，删除同时软删该会话通知（S-07）。
8. 解绑入口后重绑，历史与产物保留（S-08）。
9. resume 只接受 `WAITING_INPUT`，且要求 user ACTIVE + Agent 授权有效（S-10、E-11）。
10. 暂存上传核属与**本次发送入口**一致，不把会话创建入口当上传入口。
11. 空会话首次发送、`message_id` 去重、断线重连游标（B-01、E-06、E-08）、未知来源与迁移尾差（B-06）。

### Checklist

- [ ] 会话列表跨入口聚合，带来源与 `has_unread`（S-11、S-05）
- [ ] 详情/历史/取消/删除/产物不按入口断言（E-04）
- [ ] 通知来源跟随 Run，不与会话来源混用（B-05）
- [ ] 未读 EXISTS + 先分页再判
- [ ] mark-read 水位语义正确，超前返回 `CURSOR_AHEAD`
- [ ] 删除会话先取消活跃 Run 再软删，并软删通知（S-07）
- [ ] 解绑重绑后历史与产物保留（S-08）
- [ ] resume 校验 `WAITING_INPUT` + user ACTIVE + 授权有效（S-10、E-11）
- [ ] 上传归属与本次发送入口一致
- [ ] 去重、重连游标、空会话首send、未知来源处理正确（E-06、E-08、B-01、B-06）

### Spec Delta

```json
[]
```

### Log

- [2026-11-16] created

### Verification

- 尚未执行

---

## TASK-011: Console 前端外部身份入口

- **Status**: draft
- **Depends**: TASK-005
- **Source**: mssw-external-im-channel.frontend.design.md#34-组件设计
- **Scenarios**: S-02, S-09
- **Spec-Refs**: frontend/harness-frontend, ui/harness-ui, ui-detail/harness-ui-detail
- **Paths**: apps/console-platform/frontend/src/modules/user-identity/**, apps/console-platform/frontend/src/modules/agent-management/**, tests/frontend/**
- **E2E**:
- **Skip-Tests**:
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "frontend-contract",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/frontend"],
    "timeout": 300,
    "parallel": false
  }
]
```

### Description

在 Console 用户详情页加 `ExternalIdentitySection`，实现管理员手工录入外部身份与授权入口（第一期不建批量导入）。

关键点（按 frontend.design §3.4）：
1. `ExternalIdentitySection` 挂在用户详情 SideSheet，展示用户级外部身份、身份 JSON、状态、grants。
2. `MsswwebIdentityModal`：表单字段按平台 `identity_keys` **动态渲染**，标签取 `identity_field_labels`（缺失回退 claim 名），提交用 claim 名；`platform_key` 只显示不可编辑，编辑态不回传。
3. `EntryGrantModal`：API-19 分页搜索入口，`query/page/page_size`，改变 query 重置页码并丢弃旧返回。
4. `MsswwebPlatformBanner`：显示 `mssw` 配置状态与 `identity_keys`，不显示因子；未配置时橙色提示但保存不禁用（便于先建入口），身份创建禁用。
5. 错误：`IDENTITY_ALREADY_BOUND` / `IDENTITY_KEYS_MISMATCH` 走字段内联提示，label 用中文名。
6. 前端契约测试是源码字符串断言（`tests/frontend/*contract*.py`），不是组件单测——按现有约定加断言。

### Checklist

- [ ] `ExternalIdentitySection` 在用户详情页可见，列表含身份与 grants（S-02）
- [ ] 身份表单按 `identity_keys` 动态渲染，标签用中文 label
- [ ] 入口授权弹窗支持分页搜索，不以前端过滤冒充完整搜索
- [ ] Banner 显示平台状态与 keys，不泄露因子
- [ ] 两类错误内联提示，文案用 label 中文名
- [ ] 编辑身份后历史不断链（S-09）
- [ ] `tests/frontend/` 契约断言新增并通过

### Spec Delta

```json
[]
```

### Log

- [2026-11-16] created

### Verification

- 尚未执行

---

## TASK-012: 部署与运维配置

- **Status**: draft
- **Depends**: TASK-004, TASK-006
- **Source**: mssw-external-im-channel.backend.design.md#部署
- **Scenarios**: E-03, E-09
- **Spec-Refs**: arch/harness-arch, secret/harness-secret
- **Paths**: deploy/k8s/base/secret.example.yaml, deploy/k8s/base/console-platform.yaml
- **E2E**:
- **Skip-Tests**:
- **Skip-Reason**:

### Tests

```json
[]
```

### Description

补齐部署侧的平台因子与配置注入。

关键点：
1. `secret.example.yaml`：新增 `mssw` 平台因子 key（公钥、issuer、audience、salt），标记 `optional: true` —— 缺失时进程仍启动，只让 MSSWWEB 认证 503（E-03）。
2. `console-platform.yaml`：通过 `envFrom` 注入 Secret + ConfigMap；平台元数据（`identity_keys`、`identity_field_labels`）走 ConfigMap。
3. 公钥轮换流程：因不热更新，轮换走维护窗口换 Secret + 重新部署（E-09）。
4. 不新增配置中心客户端——仓库只有 `SharedSettings`（环境变量）+ K8s 注入。

### Checklist

- [ ] Secret key 齐全且 `optional: true`（E-03）
- [ ] ConfigMap 承载 `identity_keys` / `identity_field_labels`
- [ ] Console Deployment `envFrom` 引用正确
- [ ] 缺因子时进程可启动，MSSWWEB 返回 503
- [ ] 公钥轮换流程写入运维说明（E-09）

### Spec Delta

```json
[]
```

### Log

- [2026-11-16] created

### Verification

- 尚未执行

---

## TASK-013: 端到端验收

- **Status**: draft
- **Depends**: TASK-001, TASK-002, TASK-003, TASK-004, TASK-005, TASK-006, TASK-007, TASK-008, TASK-009, TASK-010, TASK-011, TASK-012
- **Source**: mssw-external-im-channel.scenarios.md
- **Scenarios**: S-01, S-02, S-03, S-04, S-05, S-06, S-07, S-08, S-09, S-10, S-11, E-01, E-02, E-03, E-04, E-05, E-06, E-07, E-08, E-09, E-10, E-11, E-12, E-13, E-14, B-01, B-02, B-03, B-04, B-05, B-06
- **Spec-Refs**: test/harness-test, e2e/harness-e2e
- **Paths**: tests/acceptance/msswweb/**, tests/e2e/mssw_token_factory.py, e2e/tests/msswweb/**, e2e/playwright.msswweb.config.ts
- **E2E**: ci:e2e
- **Skip-Tests**:
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "acceptance-api",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "-m", "not capacity_load", "tests/acceptance/msswweb"],
    "timeout": 600,
    "parallel": false
  },
  {
    "id": "acceptance-browser",
    "argv": ["bash", "-lc", "npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test -- --config playwright.msswweb.config.ts"],
    "timeout": 900,
    "parallel": false
  }
]
```

### Description

按 scenarios 逐条走通端到端，并固化回归。这是最终门禁：前面每个 TASK 只验证自己那段，这里验证跨模块串起来的行为。

**测试分层（按 `harness-e2e` RULE-e2e-001）**

| 层 | 覆盖 | 方式 |
|---|---|---|
| pytest 集成 | E-01 ~ E-14 全部错误路径、B-01 ~ B-06 边界、跨用户隔离 | `tests/acceptance/msswweb/`，真实 PG + Redis，可行内造 token |
| Playwright 浏览器 | S-03 完整对话、S-02 建身份与授权、S-05 列表与未读 | `e2e/tests/msswweb/`，真实构建产物 + 系统 Chrome + 真实后端 |

错误路径用 pytest 而非浏览器：`E-01` 要造错误签名、`alg=none`、篡改密文、`aud`/`iss` 不匹配，在行内直接签发/改写 token 最直接；在浏览器里做这些要么 mock 路由（违反规范），要么不可行。成功路径必须走真实浏览器，不得 `page.route` 拦截。

**`soc-token` 的生成方式**

测试侧不能持有 MSSW 真实私钥（我方只有公钥），所以用例内**自签**：

1. `tests/e2e/mssw_token_factory.py` 生成临时 RSA-2048 密钥对，把公钥写进该测试环境的 `mssw` 平台因子（Secret 注入）；
2. 用私钥按 RS256 签发 payload（`_id`、`tenant_id`、`aud`、`iss`、`exp=-1`）；
3. `aud`/`iss` 按 `01-jwt-spec.md` 的算法加密成密文（AES-256-ECB，salt 右补 `\x00` 至 32 字节，PKCS#7）；
4. 同一工厂支持负面变体：错误签名、`alg=none`、错误 key、过期 claim，供 E-01/E-02 复用。

该工厂与 TASK-004 的验签实现引用同一份算法常量，避免测试与实现各写一遍而漂移。

**S-03 的四个断点**

1. 发消息 → 断言 `202` 与 `message_id`；
2. SSE → 走 UI，断言 `chat-streaming-reply` 的 testid 最终呈现探针返回的文本（参照 `e2e/tests/webchat/attachments-notifications.spec.ts`）；
3. 历史 → 导航会话详情，断言消息列表含刚才问答，且 `through_seq` 等于 SSE 收到的最后序号；
4. 通道 → 查询 Run 记录断言 `channel == "MSSWWEB"`，不只看 HTTP 码。这一步浏览器不可见，由 spec 通过 API 或测试 DB 完成。

**浏览器会话的建立**

MSSW 的身份在 Cookie `soc-token`。Playwright 用 `context.addCookies()` 种入自签 token，后续导航与请求自动携带，与 `e2e/tests/webchat/` 里种 `muad_chat_session` 的方式一致。

**LLM 回复来源**

复用 `tests/e2e/openai_probe_app.py` 真实本地探针，与 Console/Vite 并列写进 `webServer`；不 mock 业务响应。

**域配置要求**

`e2e/playwright.msswweb.config.ts` 按规范固定：`channel: 'chrome'`、`preview --strictPort`、`use.timezoneId: 'Asia/Shanghai'`、`locale: 'zh-CN'`、`workers: 1`（共用租户种子）、端口由场景号偏移并冻结进 `process.env`。定位符必须引用前端源码真实存在的 testid（机检 `test_e2e_testids_are_defined_in_frontend_source`）。

**其余**

1. S-01、S-04、S-06 ~ S-11 由 pytest 集成覆盖（API 级已能证明跨入口共享、取消、删除、解绑重绑、编辑身份）。
2. 跨用户隔离：A 的 token 不能读 B 的会话与产物；同 user 多身份收敛。
3. 跑前停 dev 服务、查残留进程（`--reload` Worker 会抢任务，表现为 `SKILL_ARTIFACT_UNAVAILABLE` 一类假故障）；E2E 创建的 `e2e-` 前缀数据在清理钩子中删除。

### Checklist

- [ ] `tests/e2e/mssw_token_factory.py` 能签发合法 token 并支持四类负面变体
- [ ] pytest 覆盖 E-01 ~ E-14 全部错误路径
- [ ] pytest 覆盖 B-01 ~ B-06 全部边界
- [ ] pytest 覆盖 S-01、S-04、S-06 ~ S-11
- [ ] Playwright 覆盖 S-02、S-03、S-05，成功路径无 `page.route`
- [ ] S-03 四个断点（202 / SSE 文本 / 历史水位 / `channel`）全部通过
- [ ] `RunRequest.channel == "MSSWWEB"` 断言通过
- [ ] 跨用户隔离验证通过
- [ ] `playwright.msswweb.config.ts` 通过 `tests/frontend/test_e2e_suite_contract.py` 与 testid/tz 机检
- [ ] 无残留进程干扰，`e2e-` 前缀数据已清理

### Spec Delta

```json
[]
```

### Log

- [2026-11-16] created

### Verification

- 尚未执行
