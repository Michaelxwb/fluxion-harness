# Tasks: Redis 单机/Cluster 双模式接入

- **Source**: `.code-flow/tasks/2026-11-03/redis-cluster-support/redis-cluster-support.design.md`
- **Created**: 2026-11-03
- **Updated**: 2026-11-04

## Proposal

把五个进程的 Redis 接入从"单机客户端连集群"改为"环境级 `REDIS_MODE` + 公共客户端入口"：keyed 命令走 `RedisCluster`，pub/sub 走从集群拓扑派生的普通连接；三族 key 加 hash tag 收敛到契约单一来源；集群异常归一；启动做拓扑自校验；Stream 保留改为时间窗口驱动。目标是让内网 3M3R 集群上的 IM 入站、租约、出站与通知在不改业务语义的前提下恢复可用，并把内存与故障窗口变成可推算的量。

---

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 | 执行命令 | 工作目录 | 超时 | 优先级 | 执行方式 | 环境 | 输入 | E2E理由 | 触发路径 | 冒烟 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| S-01 | redis-cluster-support.design.md#2.5 验收条件 | integration | 真实五进程子进程 + 真实 standalone Redis + 真实 PostgreSQL | TASK-007 | verified | ["uv", "run", "pytest", "-q", "tests/acceptance/redis_cluster/test_standalone_regression.py"] | . | 1800 | P0 | auto | local-api | - | - | - | no |
| S-02 | redis-cluster-support.design.md#2.5 验收条件 | integration | 真实 Gateway + 真实 3 节点 cluster Redis + 真实 PostgreSQL | TASK-003 | verified | ["uv", "run", "pytest", "-q", "tests/acceptance/redis_cluster/test_cluster_inbound.py"] | . | 1200 | P0 | auto | local-api | - | - | - | no |
| S-03 | redis-cluster-support.design.md#2.5 验收条件 | integration | 两个真实 Connector 子进程 + 真实 cluster Redis + 真实 Console 快照 | TASK-004 | verified | ["uv", "run", "pytest", "-q", "tests/acceptance/redis_cluster/test_cluster_lease.py"] | . | 1200 | P0 | auto | local-api | - | - | - | no |
| S-04 | redis-cluster-support.design.md#2.5 验收条件 | integration | 真实 Gateway 入队 + 真实 Connector XREAD + 真实 cluster Redis + 真实 PG | TASK-004 | verified | ["uv", "run", "pytest", "-q", "tests/acceptance/redis_cluster/test_cluster_outbound.py"] | . | 1200 | P1 | auto | local-api | - | - | - | no |
| S-05 | redis-cluster-support.design.md#2.5 验收条件 | integration | 真实 Console 子进程 + 真实 cluster Redis（多 slot session） | TASK-005 | verified | ["uv", "run", "pytest", "-q", "tests/acceptance/redis_cluster/test_cluster_session.py"] | . | 900 | P1 | auto | local-api | - | - | - | no |
| S-06 | redis-cluster-support.design.md#2.5 验收条件 | integration | 真实五进程 + 真实 cluster Redis（pub/sub、单 key、pipeline、scan、token） | TASK-005 | verified | ["uv", "run", "pytest", "-q", "tests/acceptance/redis_cluster/test_cluster_surfaces.py"] | . | 1800 | P1 | auto | local-api | - | - | - | no |
| S-07 | redis-cluster-support.design.md#2.5 验收条件 | contract | `create_redis_client` / `create_pubsub_client` 的纯配置校验（不建立连接） | TASK-001 | verified | ["uv", "run", "pytest", "-q", "tests/common/test_redis_client.py"] | . | 300 | P0 | auto | none | - | - | - | no |
| S-08 | redis-cluster-support.design.md#2.5 验收条件 | unit | 纯 Python key builder + redis.crc.key_slot + 源码扫描 | TASK-002 | verified | ["uv", "run", "pytest", "-q", "tests/contracts/test_im_contracts.py", "tests/architecture/test_redis_client_construction.py"] | . | 300 | P0 | auto | none | - | - | - | no |
| E-01 | redis-cluster-support.design.md#2.5 验收条件 | integration | 真实 3 主 3 从 Redis Cluster + 真实 Gateway/Connector + 真实 PostgreSQL | TASK-008 | planned | - | . | 3600 | P0 | deferred | staging | - | - | - | no |
| E-02 | redis-cluster-support.design.md#2.5 验收条件 | integration | 真实五进程 + 真实 cluster Redis；运行中停止集群 | TASK-005 | verified | ["uv", "run", "pytest", "-q", "tests/acceptance/redis_cluster/test_cluster_degradation.py"] | . | 1200 | P1 | auto | local-api | - | - | - | no |

---

## TASK-001: 公共 Redis 客户端入口（keyed + pub/sub + 异常归一 + 拓扑自校验）

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: redis-cluster-support.design.md#3.4 接口设计, redis-cluster-support.design.md#3.5.2 可靠性设计, redis-cluster-support.design.md#3.1 方案选型
- **Spec-Refs**: harness-arch#RULE-arch-001, harness-log#RULE-log-001, harness-secret#RULE-secret-001
- **Rule-Owner-Refs**: harness-arch#RULE-arch-001, harness-log#RULE-log-001, harness-secret#RULE-secret-001
- **Acceptance-Refs**: S-07, RULE-01, RULE-04, RULE-09, RULE-10
- **Estimate**: 1.5d
- **Capability**: 任意进程调用 `create_redis_client` / `create_pubsub_client` 得到正确类型的客户端；cluster 配置错误启动即失败；standalone 行为与今天一致

### Description

在 `muad_common.redis_client` 落 `RedisMode`、`RedisClient`、`RedisConfigError`、`create_redis_client`、`create_pubsub_client` / `create_pubsub_client_from_cluster`、`verify_redis_topology`。keyed 客户端在 cluster 模式返回 `RedisCluster`（`require_full_coverage=True`、`retry=Retry(...)`、db=0 校验、seeds 解析与去重、IPv6）；pub/sub 客户端永远返回普通 `Redis`（standalone 与 keyed 等价；cluster 从 `get_primaries()` 派生节点，全部不可达抛 `ConnectionError`）。把 `RedisClusterException` 归一为 `RedisUnavailableError(RedisError)`，保证现有降级路径可捕获。

### Checklist

- [x] 交付 `<Capability>`，演示命令：`uv run pytest -q tests/common/test_redis_client.py tests/common/test_redis_pubsub_client.py`
- [x] `create_redis_client`：standalone 分支保持现有 `Redis.from_url` 参数；cluster 分支校验 db=0、单/多 seed、IPv6、非法 seed、`require_full_coverage`
- [x] `create_pubsub_client` / `create_pubsub_client_from_cluster`：cluster 派生节点连接；全不可达抛 `ConnectionError`；不使用 sharded pub/sub
- [x] `verify_redis_topology`：cluster 下 `INFO server.redis_mode=cluster`、槽位全覆盖、db=0；standalone 连到 cluster 失败
- [x] 异常归一：`RedisClusterException` → `RedisUnavailableError(RedisError)`，错误消息只含 host/port/mode
- [x] [S-07][contract] 覆盖 standalone/cluster 模式选择、db=0、缺失 URL、非法 mode、非法 seed、多 seed 去重与主 seed 合并；不建立真实连接
- [x] [harness-arch#RULE-arch-001] verifier：`uv run pytest -q tests/architecture`；不新增部署单元，四单元边界与 Connector 角色不变
- [x] [harness-log#RULE-log-001] verifier：`uv run pytest -q tests/common/test_redis_client.py`；启动/错误日志单行、走 logging-kit、不输出 DSN
- [x] [harness-secret#RULE-secret-001] verifier：同命令；错误与日志不含密码或完整 DSN，`REDIS_URL` 仍只来自 Secret

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-07 | contract | SharedSettings + 工厂纯校验（不建连接） | standalone 返回 Redis；cluster db=0 返回 RedisCluster；db≠0/缺 URL/非法 mode/非法 seed 抛 RedisConfigError；多 seed 去重并合并主 seed；standalone 收到 seeds 只 warning | tests/common/test_redis_client.py | uv run pytest -q tests/common/test_redis_client.py | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-07 | FAIL: ModuleNotFoundError: No module named 'muad_common.redis_client' | PASS: 27 passed（tests/common/test_redis_client.py + test_redis_pubsub_client.py） | tests/common/test_redis_client.py 全文件；tests/common/test_redis_pubsub_client.py 全文件 | 纯配置校验（不建立真实连接）；集群异常归一用 fake client 注入 RedisClusterException | verified |
| RULE-01/RULE-04 | 同 S-07 | PASS: 模式/DB/拓扑校验用例通过 | test_cluster_requires_db_zero / test_topology_* | SharedSettings + fake client | verified |
| RULE-09/RULE-10 | 同 S-07 | PASS: pub/sub 派生与异常归用例通过 | test_pubsub_from_cluster_derives_primary_node / test_topology_normalizes_cluster_exception | fake cluster + fake redis | verified |
- S-07: verified — automated command passed; run_id=e15f3e0d9dcb4559a811c7ab0233b5dc (confirmed_by: runner)
- S-07: verified — automated command passed; run_id=bd470983b08a493986bea2eb4af783c2 (confirmed_by: runner)

### Log

- [2026-11-03] created (draft)
- [2026-11-03] started
- [2026-11-03] completed (done)

---

## TASK-002: key 契约收敛（三族 hash tag、单一来源、删除本地副本）

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: redis-cluster-support.design.md#3.3 数据设计, redis-cluster-support.design.md#3.4 接口设计
- **Spec-Refs**: harness-im#RULE-im-001
- **Rule-Owner-Refs**: N/A
- **Acceptance-Refs**: S-08, RULE-02, RULE-05, RULE-06
- **Estimate**: 1d
- **Capability**: 三族 key 的字面值只由 `muad_contracts.im` 提供；业务代码不再拼 key；slot 契约测试全绿

### Description

入站改为 `im.inbound:{inbound}:{shard}`、出站 `im.outbound:{tenant_id}:{bot_id}`（字面 tag，`{tenant_id}` 由 f-string 生成）、租约 `connector:bot:{tenant_id:bot_id}:lease` 与 `:epoch`。新增 `inbound_stream_key_for_shard`；删除 Gateway `INBOUND_KEY_PREFIX`、Connector `_stream_key`/`_bot_of_stream` 的本地拼装、`RedisLeaseManager.lease_key/epoch_key` 静态方法。新增架构扫描测试：业务代码不得直接构造客户端，也不得出现本地 key 前缀。

### Checklist

- [x] 交付 `<Capability>`，演示命令：`uv run pytest -q tests/contracts/test_im_contracts.py tests/architecture/test_redis_client_construction.py`
- [x] contracts 提供三族 key 的字面值与 tag；`lease_key` 签名统一为 `(tenant_id, bot_id)`
- [x] 删除三处本地副本（Gateway 前缀、Connector `_stream_key`/`_bot_of_stream`、`RedisLeaseManager` 静态方法）
- [x] [S-08][unit] 32 个 inbound key 同一 slot；lease/epoch 同一 slot；同租户出站同一 slot；key 字面值与设计一致
- [x] [S-08][unit] 架构扫描：`apps/packages` 除工厂外无 `Redis.from_url`/`RedisCluster`，无本地 key 前缀常量
- [x] [RULE-02] 断言 standalone 与 cluster 使用同一套带 tag 的 key 字面值
- [x] [RULE-06] 断言跨 slot 多 key 组合不存在（slot 集合大小为 1）

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-08 | unit | 纯 Python key builder + `redis.crc.key_slot` + 源码扫描 | 32 inbound 单 slot；lease/epoch 单 slot；同租户出站单 slot；无新增直连构造（冻结清单只减不增，TASK-005 清零）；无本地 key 前缀 | tests/contracts/test_im_contracts.py, tests/architecture/test_redis_client_construction.py | uv run pytest -q tests/contracts/test_im_contracts.py tests/architecture/test_redis_client_construction.py | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-08 | 契约收紧与用例同批收敛；新断言在旧字面值下不成立（旧 key 无 tag、lease_key 签名不同），未单独留存 RED 运行记录（纯契约改造，失败可推演） | PASS: 12 passed | tests/contracts/test_im_contracts.py::test_key_contracts_share_hash_slots；tests/architecture/test_redis_client_construction.py 全文件 | 纯 Python key builder + redis.crc.key_slot + AST 源码扫描 | verified |
| RULE-02/RULE-06 | — | PASS: 32 inbound 单 slot / lease+epoch 单 slot / 同租户出站单 slot | 同上 slot 断言 | 无 IO | verified |
| harness-im#RULE-im-001 | — | PASS: 533 passed（tests/console_channel tests/gateway） | tests/console_channel、tests/gateway | 真实 PG + 进程 | verified |
- S-08: verified — automated command passed; run_id=ab25d40104ca4895afe7b6e6b7149acd (confirmed_by: runner)

### Log

- [2026-11-03] created (draft)
- [2026-11-03] started
- [2026-11-03] completed (done)

---

## TASK-003: Gateway 接入集群（keyed + pub/sub 拆分、入站保留裁剪、降级可见）

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001, TASK-002
- **Source**: redis-cluster-support.design.md#3.2 架构设计, redis-cluster-support.design.md#3.3 数据设计, redis-cluster-support.design.md#3.5.2 可靠性设计, redis-cluster-support.design.md#3.5.4 可观测性设计
- **Spec-Refs**: harness-im#RULE-im-001, harness-im#RULE-im-002, harness-log#RULE-log-001, harness-api#RULE-api-001, harness-api#RULE-api-002
- **Rule-Owner-Refs**: harness-api#RULE-api-001, harness-api#RULE-api-002
- **Acceptance-Refs**: S-02, RULE-03, RULE-07, RULE-09, RULE-10, RULE-11
- **Estimate**: 2d
- **Capability**: cluster 模式下 Gateway 完成 32 流入站消费与通知订阅；保留窗口裁剪生效且不裁 pending；集群故障时降级可见不崩溃

### Description

`main.py` 拆 keyed 客户端与 pub/sub 客户端：Stream/session/dedupe 与通知水位走 keyed，`NotificationStreamHub` 订阅走 pub/sub（Hub 同时接收两个客户端）。消费循环捕获 `RedisClusterException`，建组/读失败按退避限频。新增周期 `XTRIM MINID`：下界取 `min(now − 窗口, 最早 pending, last-delivered-id)`，绝不越过未投递条目；触顶（未投递 ≥ MAXLEN）记 `inbound_stream_backlog_overflow_total` + ERROR 日志（按 RET-01 更新口径：MAXLEN 裁剪后无法定位会话，改为指标 + 运维告警 + 平台重投）。出站 `XADD` 带 `minid`。`/readyz` 暴露 `redis_mode`/`redis_ok`（短超时 + 5s 缓存）；启动日志输出 key 落点。

### Checklist

- [x] 交付 `<Capability>`，演示命令：`uv run pytest -q tests/acceptance/redis_cluster/test_cluster_inbound.py tests/gateway/test_inbound_stream_retention.py`
- [x] `main.py` 拆两个客户端；`NotificationStreamHub` 接收 pub/sub + keyed 两个依赖
- [x] 消费循环捕获 `RedisClusterException`；建组/读失败限频与退避；恢复时输出一条 recovered
- [x] 周期 `XTRIM MINID`（pending 与 `last-delivered-id` 双下界）；触顶检测与指标（按 RET-01 更新口径：ERROR + 指标 + 运维告警）
- [x] 出站 `XADD ... MINID`（窗口常量 ≥ Connector 命令过期时间）
- [x] `[S-02][integration]` 覆盖 32 流同 slot 消费、XAUTOCLAIM 接管、窗口裁剪不裁 pending（真实边界：Gateway + cluster Redis + PG）
- [x] `[RULE-07]` 集群不可用时 `inbound_stream_group_unavailable` 等日志与指标可见，不静默吞错
- [x] `[RULE-09]` 断言 `NotificationStreamHub` 的订阅句柄不是 `RedisCluster`，跨节点发布可送达
- [x] `[RULE-10]` 注入 `RedisClusterException` 时循环不退出、进程不崩溃
- [x] `[RULE-11]` 断言裁剪下界不越过 pending 与 `last-delivered-id`；`XLEN` 与窗口语义一致
- [x] [harness-api#RULE-api-001] verifier：`uv run pytest -q tests/test_api_i18n.py tests/test_error_catalog.py tests/acceptance/test_foundation_api_envelope.py`；探针 detail 变更不影响业务封套与错误码
- [x] [harness-api#RULE-api-002] verifier：`uv run pytest -q tests/console_skill/test_import_idempotency.py`；本需求未新增 POST，幂等契约回归通过

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-02 | integration | 真实 Gateway 子进程 + 真实 3 节点 cluster Redis + 真实 PostgreSQL | 32 条事件各一次；无 MovedError/CROSSSLOT；32 key 同 slot；重启 pending 归零；窗口外已确认条目被裁、pending 保留；跨节点 pub/sub 可送达 | tests/acceptance/redis_cluster/test_cluster_inbound.py | uv run pytest -q tests/acceptance/redis_cluster/test_cluster_inbound.py | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-02 | 夹具首跑失败（非 SUT）：`reset_redis_state` 用单机同步客户端跨 slot DEL 报 CROSSSLOT；Connector 尚未迁移导致回复等待。修正基座（集群清理 + webhook 适配器）后通过 | PASS: 1 passed in 80.12s | tests/acceptance/redis_cluster/test_cluster_inbound.py 全文件 | 真实 Gateway 子进程 + 真实 3 节点 cluster + 真实 PG | verified |
| RULE-07/RULE-11 | — | PASS: 7 passed | tests/gateway/test_inbound_stream_retention.py（pending/last-delivered 双下界、触顶检测、集群异常不致命） | fake redis + 真实 cluster 验收链路 | verified |
| RULE-09 | — | PASS: 跨节点 PUBLISH/订阅断言（S-02 内） | test_cluster_inbound.py:101-123 | 普通 Redis 订阅 + 集群客户端发布 | verified |
| RULE-10 | — | PASS: 裁剪异常不致命 + 消费循环捕获 RedisClusterException | test_inbound_stream_retention.py::test_trim_failure_is_visible_and_non_fatal | fake redis / 真实栈 | verified |
| harness-api#RULE-api-001/002 | — | PASS: 18 passed + 5 passed | tests/test_api_i18n.py、test_error_catalog.py、test_foundation_api_envelope.py、tests/console_skill/test_import_idempotency.py | 真实 API 封套/幂等套件 | verified |

- S-02: verified — automated command passed; run_id=e2a9f5d0f50b4d06b9e82884c8ba5121 (confirmed_by: runner)

### Log
- [2026-11-04] 生产代码完成：keyed/pubsub 双客户端、RedisClusterException 归一捕获、周期 XTRIM MINID（pending + last-delivered 双下界）、触顶指标 `inbound_stream_backlog_overflow_total`、出站 XADD MINID、/readyz 暴露 redis_mode；mypy 57 文件全绿。
- [2026-11-04] 待补：S-02 集群验收夹具与用例（tests/acceptance/redis_cluster）。
- [2026-11-04] 设计限制：MAXLEN 裁剪发生在生产者侧且条目已消失，无法定位受影响会话去做“用户可见反馈”；当前实现为 ERROR 日志 + 指标，需产品确认是否改为“指标 + 运维告警”，并同步 design RET-01。

- [2026-11-03] created (draft)
- [2026-11-03] started
- [2026-11-03] blocked (等待 TASK-007 的 3 节点集群测试夹具与 cluster 验收基座（S-02 需要真实 Gateway + 真实 cluster Redis；acceptance conftest 的 DB 10-15 隔离与 cluster 不兼容）。先执行同批次中不依赖夹具的 TASK-006/007，再 resume。)
- [2026-11-03] resumed (in-progress)
- [2026-11-04] resumed (in-progress)
- [2026-11-04] completed (done)

---

## TASK-004: Connector 接入集群（租约、出站读取、入站发布）

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001, TASK-002
- **Source**: redis-cluster-support.design.md#3.2 架构设计, redis-cluster-support.design.md#3.3 数据设计, redis-cluster-support.design.md#3.4 接口设计
- **Spec-Refs**: harness-im#RULE-im-001, harness-im#RULE-im-002
- **Rule-Owner-Refs**: harness-im#RULE-im-001, harness-im#RULE-im-002
- **Acceptance-Refs**: S-03, S-04, RULE-06
- **Estimate**: 1.5d
- **Capability**: cluster 模式下两个 Connector 竞争同一 bot 时租约单 owner、epoch 递增；同租户多 bot 出站一次 `XREAD` 可读；入站发布正常

### Description

Connector 主客户端改由 `create_redis_client` 创建；删除启动日志对 `connection_pool.connection_kwargs` 的假设。`RedisLeaseManager` 使用契约的 lease/epoch key（同 tag），Lua 不变。`OutboundRunner` 使用契约的 outbound key 与 `_bot_of_stream` 映射；`InboundPublisher` 走契约的 inbound key。恢复 `bot→Agent` 路由与适配器边界语义不变。

### Checklist

- [x] 交付 `<Capability>`，演示命令：`uv run pytest -q tests/acceptance/redis_cluster/test_cluster_lease.py tests/acceptance/redis_cluster/test_cluster_outbound.py`
- [x] `main.py` 用工厂创建客户端；启动日志改为输出模式与 host/port，不读 `connection_pool`
- [x] `RedisLeaseManager` 使用契约 lease/epoch key；acquire/renew/release Lua 语义不变
- [x] `OutboundRunner` 用契约 outbound key；同租户多 bot 一次 `XREAD`；cursor 与 result 行为不变
- [x] `InboundPublisher` 用契约 inbound key；去重标记与 LOST 扫描行为不变
- [x] [harness-im#RULE-im-001] verifier：`uv run pytest -q tests/gateway`；bot 只路由到一个 Agent，不建立 agent→Pod 映射
- [x] [harness-im#RULE-im-002] verifier：`uv run pytest -q tests/architecture/test_channel_neutrality.py`；渠道差异仍在适配器层
- [x] `[S-03][integration]` 覆盖双 Connector 抢租约、owner 唯一、epoch 递增、旧 owner fencing；lease/epoch 同 slot
- [x] `[S-04][integration]` 覆盖同租户多 bot 出站一次 `XREAD`、结果回读、cursor 只由 owner 推进、窗口外命令被裁

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-03 | integration | 两个真实 Connector 子进程 + 真实 cluster Redis + 真实 Console 快照 | 单 owner；lease/epoch 同 slot；Lua 无跨 slot；epoch 严格递增；旧 owner 写被拒 | tests/acceptance/redis_cluster/test_cluster_lease.py | uv run pytest -q tests/acceptance/redis_cluster/test_cluster_lease.py | verified |
| S-04 | integration | 真实 Gateway 入队（DeliveryStreams）+ 真实 Connector XREAD + 真实 cluster Redis + 真实 PG | 同租户多 bot 同 slot；命令只执行一次；result 可读；cursor 只由 owner 推进；窗口外命令被裁 | tests/acceptance/redis_cluster/test_cluster_outbound.py | uv run pytest -q tests/acceptance/redis_cluster/test_cluster_outbound.py | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-03 | 首跑未单独 RED（契约 key 已在 TASK-002 落地）；脚本首版读取逻辑返工 | PASS: 1 passed | tests/acceptance/redis_cluster/test_cluster_lease.py | 两个真实 Connector 子进程 + 真实 cluster + 真实 Console 快照 | verified |
| S-04 | 首跑 RED：显式旧 ID 注入与非空流冲突；`MINID ~` 近似裁剪在小流量流不生效 | PASS: 1 passed | tests/acceptance/redis_cluster/test_cluster_outbound.py | 真实 DeliveryStreams 生产端 + 真实 Connector XREAD + 真实 cluster + PG | verified |
| RULE-06 | — | PASS: lease/epoch 同 slot 双 key Lua 正常执行；出站同租户同 slot | 同上两份验收 | 真实 cluster | verified |
| harness-im#RULE-im-001/002 | — | PASS: Done Gate 内 tests/gateway + channel neutrality | tests/gateway、tests/architecture/test_channel_neutrality.py | 真实栈 | verified |
- S-03: verified — automated command passed; run_id=e0dd1fddf00e4618ba1e251265c0ff0c (confirmed_by: runner)
- S-04: verified — automated command passed; run_id=e0dd1fddf00e4618ba1e251265c0ff0c (confirmed_by: runner)

### Log

- [2026-11-03] created (draft)
- [2026-11-04] started
- [2026-11-04] completed (done)

---

## TASK-005: Runtime / Worker / Console 迁移（pub/sub 派生、会话、缓存、令牌）

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: redis-cluster-support.design.md#3.2 架构设计, redis-cluster-support.design.md#3.3 数据设计, redis-cluster-support.design.md#3.5.2 可靠性设计
- **Spec-Refs**: harness-project-platform#RULE-platform-001, harness-worker#RULE-worker-001, harness-log#RULE-log-001, harness-snapshot#RULE-snapshot-001
- **Rule-Owner-Refs**: harness-project-platform#RULE-platform-001, harness-worker#RULE-worker-001, harness-snapshot#RULE-snapshot-001
- **Acceptance-Refs**: S-05, S-06, E-02, RULE-03, RULE-06, RULE-08, RULE-10
- **Estimate**: 2.5d
- **Capability**: cluster 模式下 Runtime/Worker 的 cancel/唤醒/通知可用，Console 的 session/MCP 缓存/取件令牌可用，Skill 平台访问（PlatformSession）可用，集群不可用时按既有语义降级不崩溃

### Description

Runtime：`event_tail_hub`、`notification_publisher` 改用 pub/sub 客户端（watermark 仍走 keyed）；`cancel_hint` 走工厂。Worker：wakeup notifier/listener 走 pub/sub 客户端，`cancel_hint` 走工厂。Console：`main.py` session store、`deps.py` 的 PlatformSession invalidator / MCP cache / fetch tokens 走工厂。**2026-11-03 合并进来的 platform-sdk**：`SkillPlatformAccess` 不再自建 `Redis.from_url`，改由 Runtime/Worker 装配根注入工厂客户端；`RedisPlatformSessionManager` 的 session/index/generation/lock 四类 key 统一加 `{tenant_id:platform_id}` tag，保证 4-key `EVAL` 同 slot；`skill_bridge` 的异常捕获扩到 `RedisClusterException`。`PlatformSession` 失效保持 SMEMBERS 后删除语义。

### Checklist

- [x] 交付 `<Capability>`，演示命令：`uv run pytest -q tests/acceptance/redis_cluster/test_cluster_session.py tests/acceptance/redis_cluster/test_cluster_surfaces.py tests/acceptance/redis_cluster/test_cluster_degradation.py`
- [x] Runtime event tail / notification publisher 拆 keyed + pub/sub；cancel hint 走工厂
- [x] Worker wakeup notifier/listener 走 pub/sub；cancel hint 走工厂；失败仍记既有降级指标
- [x] Console session store / PlatformSession invalidator / MCP cache / fetch tokens 走工厂
- [x] platform-sdk：`SkillPlatformContext` 改为注入已构造的 keyed 客户端（Runtime/Worker 装配根负责构造与关闭），`SkillPlatformAccess` 不再自建连接
- [x] platform-sdk：session/index/generation/lock key 加 `{tenant_id:platform_id}` tag；4-key `EVAL` 同 slot；更新 `invalidate` 前缀与 Console 侧索引键
- [x] platform-sdk：`skill_bridge` 捕获 `RedisClusterException`，返回 `PLATFORM_SESSION_STORE_UNAVAILABLE`
- [x] 同步 `harness-project-platform#RULE-platform-001` 的 key 格式描述（加 tag），并重跑 `spec refresh` 与绑定
- [x] 所有 Redis 故障入口捕获 `RedisClusterException`，进程不崩溃、循环不退出
- [x] [harness-project-platform#RULE-platform-001] verifier：`uv run pytest -q tests/console_platform`；PlatformSession 仍可重建、按 tenant/platform 隔离
- [x] [harness-worker#RULE-worker-001] verifier：`uv run pytest -q tests/agent_worker`；PG 仍是 Task/lease 权威源，Redis 仅 hint
- [x] [harness-snapshot#RULE-snapshot-001] verifier：`uv run pytest -q tests/agent_runtime/test_snapshot_freeze.py tests/agent_runtime/test_run_reaper.py && uv run pytest -q tests/agent_runtime -k "executor or resolve"`；本任务不改快照冻结语义，跑回归
- [x] `[S-05][integration]` 覆盖三 actor session 清理、index 清理、其他 tenant/platform 不受影响
- [x] `[S-06][integration]` 覆盖通知/唤醒/取消/会话/令牌/MCP/dedupe 的 cluster 使用面与跨节点 pub/sub
- [x] `[E-02][integration]` 覆盖停止集群后的降级、`/readyz` 503、恢复后自动恢复
- [x] `[RULE-08]`、`[RULE-10]` 断言 session 可重建与异常不崩溃

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-05 | integration | 真实 Console 子进程 + 真实 cluster Redis（多 slot session） | 三个 session 与 index 全删；重新认证可重建；其他 tenant/platform 不受影响；滑动续期/登录发布的 4-key EVAL 同 slot 无 CROSSSLOT | tests/acceptance/redis_cluster/test_cluster_session.py | uv run pytest -q tests/acceptance/redis_cluster/test_cluster_session.py | verified |
| S-06 | integration | 真实五进程 + 真实 cluster Redis | 通知/唤醒/取消送达；session/token/MCP/dedupe 可读写；跨节点 pub/sub 可用；降级可见 | tests/acceptance/redis_cluster/test_cluster_surfaces.py | uv run pytest -q tests/acceptance/redis_cluster/test_cluster_surfaces.py | verified |
| E-02 | integration | 真实五进程 + 真实 cluster Redis（运行中停止集群） | Gateway `/readyz` 503；Runtime/Worker PG 主链路继续；hint 显式降级并记指标；恢复后自动恢复；进程不崩溃 | tests/acceptance/redis_cluster/test_cluster_degradation.py | uv run pytest -q tests/acceptance/redis_cluster/test_cluster_degradation.py | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-05 | SDK key 断言与旧格式冲突；PlatformSession dataclass 字段顺序错误 | PASS: 1 passed | tests/acceptance/redis_cluster/test_cluster_session.py | 真实 cluster + 真实 MSS 适配器 + 真实 HTTP 平台探针（Console HTTP 层不参与本场景） | verified |
| S-06 | 首跑未单独 RED（组件级组合） | PASS: 1 passed | tests/acceptance/redis_cluster/test_cluster_surfaces.py | 真实 cluster；通知/唤醒/取消/session/MCP/dedupe 均用五进程同一实现（进程边界由组件承担） | verified |
| E-02 | `RedisClusterException` 会穿透 hint 降级路径（非 RedisError）；补齐捕获元组后通过 | PASS: 2 passed | tests/acceptance/redis_cluster/test_cluster_degradation.py | 不可达 cluster 客户端 + 各降级工厂 | verified |
| RULE-08 | — | PASS: 失效清理后 index/session 全删、其他 tenant/platform 不受影响 | test_cluster_session.py | 真实 cluster | verified |
| RULE-10 | — | PASS: 不可达时归一为 RedisUnavailableError / Null 实现，进程不崩溃 | test_cluster_degradation.py | 连接被拒 | verified |
| harness-project-platform#RULE-platform-001 | — | PASS: Done Gate 内 platform verifier + 4-key EVAL 同 slot | 见执行命令 | 真实 cluster | verified |
| harness-worker#RULE-worker-001 | — | PASS: Done Gate 内 tests/agent_worker | tests/agent_worker | 真实 PG | verified |
- S-05: verified — automated command passed; run_id=67632f7d1f92413b961757418a32e6e2 (confirmed_by: runner)
- S-06: verified — automated command passed; run_id=67632f7d1f92413b961757418a32e6e2 (confirmed_by: runner)
- E-02: verified — automated command passed; run_id=67632f7d1f92413b961757418a32e6e2 (confirmed_by: runner)

### Log

- [2026-11-03] created (draft)
- [2026-11-04] started
- [2026-11-04] resumed (in-progress)
- [2026-11-04] completed (done)

---

## TASK-006: 部署与配置收口（ConfigMap、清单、配置清单、前置检查）

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: redis-cluster-support.design.md#2.3 功能方案, redis-cluster-support.design.md#4.1 部署架构, redis-cluster-support.design.md#3.5.5 测试策略
- **Spec-Refs**: harness-secret#RULE-secret-001
- **Rule-Owner-Refs**: N/A
- **Acceptance-Refs**: S-07, RULE-04
- **Estimate**: 1d
- **Capability**: 一份可部署的清单：`REDIS_MODE`/seeds/保留参数进入 ConfigMap，配置清单与部署文档同步，上线前置检查可执行

### Description

`deploy/k8s/base/configmap.yaml` 增加 `REDIS_MODE`、`REDIS_CLUSTER_SEEDS`、`REDIS_MAX_CONNECTIONS`、`INBOUND/OUTBOUND_STREAM_MAXLEN`、`INBOUND/OUTBOUND_STREAM_RETENTION_SEC`；`with-connector` 清单同步；`docs/configuration-inventory.csv` 与 `build/INTRANET-K8S-DEPLOY.md` 更新（删除"原生 Redis Cluster 不支持"的过时结论）；清单测试覆盖新键。提供上线前置检查脚本：六通告地址可达、`INFO server` 版本/模式、槽位覆盖、`maxmemory`/`maxclients`。

### Checklist

- [x] 交付 `<Capability>`，演示命令：`uv run pytest -q tests/architecture tests/test_configuration_inventory.py`
- [x] ConfigMap 与 connector 清单增加新配置键；`tests/architecture` 的 settings↔manifest 一致性测试同步
- [x] `docs/configuration-inventory.csv`/`.md` 同步；`tests/test_configuration_inventory.py` 通过
- [x] `build/INTRANET-K8S-DEPLOY.md` 更新为"支持原生 Redis Cluster"，写明 seeds/端口/前置检查
- [x] 新增上线前置检查脚本（六地址可达 + 版本/模式/槽位/maxmemory/maxclients），可从 Pod 内执行
- [x] `[RULE-04]` 断言清单默认 `standalone`，生产由 ConfigMap 显式 `cluster`；启动自校验已接线

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| RULE-04 | contract | 清单文件 + SharedSettings 字段一致性 | ConfigMap 含 `REDIS_MODE` 等键；清单测试通过；配置清单无漂移 | tests/architecture, tests/test_configuration_inventory.py | uv run pytest -q tests/architecture tests/test_configuration_inventory.py | verified |
| S-07 | contract | SharedSettings + 工厂纯校验（不建连接） | `REDIS_MODE`/seeds 默认值与 ConfigMap 声明一致；工厂按配置返回 Redis/RedisCluster 或抛 RedisConfigError | tests/common/test_redis_client.py, tests/architecture | uv run pytest -q tests/common/test_redis_client.py tests/architecture | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| RULE-04 | ConfigMap 新增键缺字段/消费点会让 manifest 测试直接失败；常量行号漂移曾使盘点测试红 | PASS: 216 passed（tests/architecture + tests/test_configuration_inventory.py） | ConfigMap 声明测试、消费点测试、常量盘点 | 清单文件 + SharedSettings 字段一致性 | verified |
| S-07 | 工厂/配置校验先于本任务已由 TASK-001 落地（见该任务证据）；本任务补充清单侧不变量 | PASS: 216 passed（tests/architecture + tests/test_configuration_inventory.py）；工厂纯校验 27 passed（tests/common） | ConfigMap 键与 SharedSettings 字段一致；`REDIS_MODE` 默认 `standalone` | 清单文件 + 工厂纯校验（TASK-001 证据） | verified |
| 预检脚本 | 本地集群未设 maxmemory 时脚本 FAIL（符合预期：生产前置要求） | 六地址/版本/模式/槽位/primaries 全 PASS；maxmemory 按预期 FAIL | scripts/probe_redis_cluster.py 实跑 | 真实本地 3 主集群 | verified |

### Log

- [2026-11-03] created (draft)
- [2026-11-04] started
- [2026-11-04] completed (done)

---

## TASK-007: 测试设施与 standalone 回归（3 节点集群、隔离、探针）

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001, TASK-002
- **Source**: redis-cluster-support.design.md#3.5.5 测试策略, redis-cluster-support.design.md#2.5.4 验证环境假设
- **Spec-Refs**: harness-test#RULE-test-001
- **Rule-Owner-Refs**: harness-test#RULE-test-001
- **Acceptance-Refs**: S-01
- **Estimate**: 1.5d
- **Capability**: 本地一条命令起 3 节点集群并跑 cluster 用例；standalone 回归全绿且 key 布局断言更新；探针可判定环境是否可用

### Description

提供 3 节点本地集群夹具（复用 `localdb` 的 compose profile），cluster 用例使用 `muad_probe:{uuid}` key 前缀隔离并在收尾清理（不能用 DB 10–15）。新增探针（11 项命令面/key 契约）。更新既有约 52 处旧 key 断言与清理模式。S-01 回归覆盖五进程 standalone 行为与 key 布局不变。

### Checklist

- [x] 交付 `<Capability>`，演示命令：`uv run pytest -q tests/acceptance/redis_cluster/test_standalone_regression.py`
- [x] 集群夹具：未启动时 `env_blocked` 失败而非 skip；用例用 key 前缀隔离并清理
- [x] 探针脚本（同 slot `XREADGROUP`、`XAUTOCLAIM`、双 key `EVAL`、`SCAN`、跨节点 `PUBLISH`、跨 slot 拒绝）
- [x] 更新既有 key 断言与清理模式；`tests/acceptance/im_gateway` 等套件在 standalone 下全绿
- [x] [harness-test#RULE-test-001] verifier：`uv run pytest -q tests/acceptance/redis_cluster/test_standalone_regression.py`；真实 PG/Redis/进程，不 mock 业务边界
- [x] `[S-01][integration]` 断言 standalone 行为与 key 布局（含 tag）、`/readyz` 200、无 MovedError/CROSSSLOT

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| S-01 | integration | 真实五进程子进程 + 真实 standalone Redis + 真实 PostgreSQL | Redis 相关回归全绿；`/readyz` 200；日志无 MovedError/CROSSSLOT；三族 key 与设计一致 | tests/acceptance/redis_cluster/test_standalone_regression.py | uv run pytest -q tests/acceptance/redis_cluster/test_standalone_regression.py | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-01 | 纯回归补测（无对应缺陷）；首跑暴露断言位置错误：`/readyz` 是 api-kit Envelope，`payload["status"]` KeyError | PASS: 1 passed（`test_standalone_regression.py`，8.24s）；五进程 + 带 tag 入站事件产生 Run | 五进程集合、`/readyz data.status/redis_mode`、三族 key slot、日志无 MovedError/CROSSSLOT、Run 计数 | 真实 uvicorn 子进程 + standalone Redis + PostgreSQL | verified |
| S-01（回归） | 旧 key 布局写入不被新实现消费：`test_connector_primitives.py` 4 failed / 12 passed（outbound runner 读 tagged 流，测试写旧键） | PASS: 16 passed；`tests/acceptance/im_gateway` 111 passed；failover+lease 20 passed（含 primitives） | `outbound_stream_key_for`/`lease_key`/`lease_epoch_key`/`inbound_stream_key_for_shard` 替换旧字面量 | 真实 Redis + 真实 Connector 子进程 | verified |
| 探针 | 首跑 2 项失败：双 key EVAL 脚本只写 KEYS[1] 读 KEYS[2]；跨 slot 由客户端侧拒绝（无 CROSSSLOT 字样） | PASS: 11/11（`scripts/probe_redis_cluster_commands.py` 与 `test_cluster_commands.py`） | cluster_state/槽位/primaries/版本/三族 key 同 slot/XREADGROUP/XAUTOCLAIM/EVAL/SCAN/跨节点 PUBLISH/跨 slot 拒绝 | 真实 3 节点 Redis 6.2.18 | verified |
| 夹具 | 集群不可达时原实现是裸 ConnectionError（非明确 env_blocked） | `MUAD_CLUSTER_SEEDS=127.0.0.1:1` 时 fixture 以 `env_blocked: ...` 失败（ERROR），不是 skip | conftest `cluster_client` | 真实不可达端点 | verified |
- S-01: verified — automated command passed; run_id=0f791c7948664f5184732700b35867b9 (confirmed_by: runner)
- S-01: verified — automated command passed; run_id=92560761d4ee4d6cb56b56f3247c5862 (confirmed_by: runner)

### Log

- [2026-11-03] created (draft)
- [2026-11-04] started
- [2026-11-04] completed (done)

---

## TASK-008: 3M3R 本地故障与 reshard 演练（E-01）

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-003, TASK-004, TASK-005
- **Source**: redis-cluster-support.design.md#3.5.2 可靠性设计, redis-cluster-support.design.md#4.2 发布与回滚, redis-cluster-support.design.md#3.5.5 测试策略
- **Spec-Refs**: harness-test#RULE-test-001
- **Rule-Owner-Refs**: N/A
- **Acceptance-Refs**: E-01
- **Estimate**: 0.5d
- **Capability**: 在本地 3M3R 上演练受控 failover 与 reshard，客户端自动恢复、入站 at-least-once、租约单 owner、非主 seed 发现

### Description

在本地 3M3R（`localdb` compose profile，3 主 6391–6393 / 3 从 6394–6396）执行 E-01：运行 S-02/S-03 的代表性流量 → 对持有入站 shard 槽位的 master 执行受控 failover（本地替代主机级 kill）等待 replica 提升 → 对一个空闲 slot 执行 reshard → 继续投递并检查重定向、消息结果与租约 → 仅用原 master（现为 replica）作为 seed 重建客户端，验证拓扑发现。主机级故障与跨主机通告地址变化仍需 staging 复验（design §2.5.4）。

### Checklist

- [ ] 交付 `<Capability>`，本地 3M3R 演练并记录：failover 窗口、reshard 槽位、入站重复/丢失、租约 owner 变化
- [ ] 复用入站消费与租约的代表性流量，在 failover/reshard 前后持续投递并核对结果
- [ ] `[E-01][integration]` 断言客户端在既有重试窗口内恢复、无需重启；日志/指标可见重定向与恢复；仅提供非主 seed 时仍可发现

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|--------|---------|--------------------|---------|----------------|---------|------|
| E-01 | integration | 真实 3 主 3 从 Redis Cluster（本地单容器 6 进程）+ 真实 Gateway/Runtime/Worker 子进程 + 真实 Connector 租约实现 + 真实 PostgreSQL | failover/reshard 后自动恢复；入站至少一次、无重复副作用；租约单 owner；仅非主 seed 时仍可发现 | tests/acceptance/redis_cluster/test_cluster_failover_drill.py | uv run pytest -q tests/acceptance/redis_cluster/test_cluster_failover_drill.py | planned |

### Acceptance Evidence

> 由 `cf-task-start` 在编码期填写。

### Log

- [2026-11-03] created (draft)
