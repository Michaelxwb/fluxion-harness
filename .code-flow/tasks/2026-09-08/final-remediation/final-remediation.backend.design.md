# 终版整改（105-commits 问题清单）后端设计简报

> **文档编号**: MOD-FLXRM-V2-v0.1
> **文档版本**: v0.1（草稿）
> **创建日期**: 2026-09-08
> **文档状态**: 草稿
> **来源**: `/Users/jahan/Downloads/fluxion-105-commits-final-issues.md`（7 项）＋代码复核结论
> **全局约束（用户已拍板）**: 无兼容设计；数据库可删除重建；P0-01 纳入本次（含 PG/Redis 外部化，TASK-001 挂起视为解除）；Hook 完整 8 点；Trace 含 Console 展示；V2 去 `schema_version` 标记；Hook 发现用 `entry_points`；`status` 列 nullable

**评审边界说明**:
- **需求评审**: 第 2 章 → 通过后锁定为需求基线 v1.0
- **设计评审**: 第 3-4 章 → 通过后锁定设计基线 v1.x
- **交接契约**: 2.5 验收条件

**ID 体系**: FEAT（功能）、API（接口）、RULE（业务规则/系统约束）、TC（测试用例）、RISK（风险）、NFR（非功能指标）；场景编号 S-（正常）/E-（异常）/B-（边界）

---

## 目录

- [1. 文档控制](#1-文档控制)
- [2. 需求分析](#2-需求分析)
  - [2.1 需求概述](#21-需求概述)
  - [2.2 痛点与价值](#22-痛点与价值)
  - [2.3 功能方案](#23-功能方案)
  - [2.4 范围与边界](#24-范围与边界)
  - [2.5 验收条件](#25-验收条件)
- [3. 技术设计](#3-技术设计)
  - [3.1 方案选型](#31-方案选型)
  - [3.2 架构设计](#32-架构设计)
  - [3.3 数据设计](#33-数据设计)
  - [3.4 接口设计](#34-接口设计)
  - [3.5 质量实现方案](#35-质量实现方案)
- [4. 部署与运维](#4-部署与运维)
- [5. 风险与依赖](#5-风险与依赖)
- [6. 需求追溯矩阵](#6-需求追溯矩阵)
- [附录：术语表](#附录术语表)

---

## 1. 文档控制

### 1.1 责任人

| 角色 | 姓名 | 职责范围 |
|------|------|---------|
| 开发负责人 | | 技术方案、代码实现 |
| 测试负责人 | | 测试策略、质量保证 |

### 1.2 修订历史

| 版本 | 日期 | 作者 | 变更描述 |
|------|------|------|---------|
| v0.1 | 2026-09-08 | | 初始草稿（cf-task-align 两轮对齐后生成） |

---

## 2. 需求分析

### 2.1 需求概述

| 项目 | 内容 |
|------|------|
| **模块名称** | 终版整改（105-commits 问题清单收敛） |
| **模块ID** | MOD-FLXRM-V2 |
| **所属系统/产品线** | Fluxion Harness（Agent Runtime 底层） |
| **需求类型** | 技术重构（删无效配置、补机制缺口、部署补齐） |
| **业务背景** | 105-commits 版本复核确认 7 个开放问题；主架构已冻结，只做收敛性整改 |
| **核心目标** | 一句话：删掉不生效的配置、堵住长跑内存与一致性缺口、补齐部署拓扑与 Hook SPI，使底层架构进入稳定阶段 |

### 2.2 痛点与价值

| 痛点（现状证据） | 影响 | 预期价值 |
|---|---|---|
| RuntimeProfile 4 字段可配不可生效（`profile.request_timeout_ms` 等全仓执行期零读取） | 用户配了没效果，虚假承诺 | 删后无歧义；`max_rounds` 唯一有效参数语义清晰 |
| `ContextResolver._l1_cache` TTL=0 仍无条件写入（`context_resolver.py:425`） | 长跑 Runtime 按 tenant×agent×user 维度内存无限增长 | 有界内存，长期稳定 |
| `begin_scoped_read` 零调用方，resolve 逐条独立事务 | 并发 Publish 可能构造混合 Revision 快照 | 快照一致性闭环 |
| `TraceRecord` 无终态，靠 `error != None` 二分 | CANCELLED/TIMED_OUT 在运维视角与 FAILED 混同 | 四态可观测 |
| `PluginLoader` 对 HOOK 显式早退（`loader.py:188-191`） | 开发者无法不改核心代码扩展框架 | SPI 闭环 |
| `HookScope`/`scope_id`/`IGNORE` 死抽象留存 | 误导后续开发（以为要做动态 binding） | 代码诚实 |
| compose 只有 api 单角色 | 无法验证多 Runtime 无状态 | 生产同形态可验证 |

### 2.3 功能方案

| FEAT-ID | 优先级 | 功能 | 来源 |
|---|---|---|---|
| FEAT-01 | P0 | Compose 应用拓扑：api/runtime/worker 三角色同镜像，`--scale runtime=3`；PG/Redis 全外部化（compose 内无 postgres/redis 服务；`FLUXION_DATABASE_URL`/`FLUXION_REDIS_URL` 必填外部传入） | 105 §3 + 外部依赖原则 |
| FEAT-02 | P1 | RuntimeProfile V2：删除 `request_timeout_ms`/`max_retries`/`concurrency`/`memory_budget_mb`/`schema_version`，仅保留 `max_rounds`/`default`/`bootstrapped_from`；无 v1 兼容读（DB 可重建） | 105 §4（方案 A） |
| FEAT-03 | P1 | Hook loader 接线：`PluginLoader` 分派 HOOK 到 `HookRegistryProtocol`；`entry_points(group="fluxion.hooks")` 发现；composition root 启动加载；`AuditHookPlugin` 示例 | 105 §5 |
| FEAT-04 | P1 | 补齐 8 个固定 Hook Point（现有 `before_tool_call`＋新增 7 个），统一经 `TypedEventBus.dispatch` | 105 §5.4 |
| FEAT-05 | P2 | 删除 `HookScope`/`scope_id`/`IGNORE`；`HookRegistration` 收敛五字段 | 105 §9 |
| FEAT-06 | P1 | L1 Cache TTL≤0 时读写双 bypass | 105 §6 |
| FEAT-07 | P2 | `resolve()` 配置读包进 `begin_scoped_read`（Credential/Memory I/O 保持在外） | 105 §7 |
| FEAT-08 | P2 | `TraceRecord.status` 四态落盘（nullable）＋ Console runs 展示 | 105 §8 |

### 2.4 范围与边界

**In Scope**：上表 8 个 FEAT；附带同步改测试 seed/断言集、frontend mirror（见配套前端设计）。

**Out of Scope（明确不做）**：
- BaseHTTPMiddleware 缓冲重写（真增量直达，另案；TASK-017/018 已记录）
- `error` 改 `error_code`（维持现状，105 文档未要求）
- v1 历史数据迁移脚本（DB 删除重建，无迁移）
- Redis L2 缓存启用（`TenantRedisCache` 保持死代码；只定义 `FLUXION_REDIS_URL` 命名供未来用）
- TASK-018 缺口（部分 token 断连，待缓冲重写后）
- Worker 业务能力补齐（架构 Non-goals 既有约束）

### 2.5 验收条件

#### 业务规则

| RULE-ID | 规则描述 |
|---|---|
| RULE-01 | V2 RuntimeProfile 仅含 `max_rounds`/`default`/`bootstrapped_from`；写请求带已删字段即 422 fail-closed |
| RULE-02 | TTL≤0 时 `_l1_cache` 零读写 |
| RULE-03 | scoped read 内读到 revision 固定；跨 tenant 复用 reader 即失败 |
| RULE-04 | trace status ∈ {completed, failed, cancelled, timed_out}，与终态一一对应 |
| RULE-05 | Hook 安装/卸载不改 Runtime 核心源码（entry_points）；未安装插件时行为不变 |
| RULE-06 | compose 内无 postgres/redis 服务；缺 DSN 时 fail-fast |

#### 验收场景

| TC-ID | 类型/层级 | 真实边界 | 输入 → 可观测输出 | 覆盖 |
|---|---|---|---|---|
| S-01 | E2E/manual | docker 引擎＋用户自备 PG | `up --scale runtime=3` → 三 runtime 就绪；kill 一个后请求仍成功且 trace 完整 | FEAT-01（manual：需 docker＋外部 PG，无法自动化，原因记录） |
| S-02 | integration | 真实 Console 服务＋PG | 建含已删字段的 profile → 422，错误定位字段 | FEAT-02 |
| S-03 | integration | 真实 Console 服务＋PG | 建合法 v2 → 发布 → 执行，`max_rounds` 生效 | FEAT-02 |
| E-01 | unit | 纯契约层 | `read_published_profile` 等 v1 兼容入口已删除（import 即失败） | FEAT-02 |
| S-04 | integration | 真实 service＋entry_points fixture 包 | 安装 AuditHook → tool 调用前后各一条 audit 记录；卸载后无记录且执行正常 | FEAT-03/04 |
| S-05 | unit | 纯契约层 | `HookScope`/`IGNORE` import 失败；既有 30+ 用例全绿（仅删参数） | FEAT-05 |
| S-06 | unit | 真实 resolver＋PG | 1000 个不同 user resolve 后 `_l1_cache == {}` | FEAT-06 |
| S-07 | integration | 真实 PG＋并发 publish | resolve 中途 publish → 快照全旧版；无混合 revision | FEAT-07 |
| S-08 | integration | 真实 service＋PG | completed/failed/cancelled/timed_out 四执行 → trace status 一一对应 | FEAT-08 |
| B-01 | integration | 真实 PG | scoped read 超时 → 类型化错误，无无穷等待 | FEAT-07 |
| E-02 | integration | 真实 service | hook handler 抛错＋FAIL_CLOSED → 业务阻断；FAIL_OPEN → 业务继续＋异常记录 | FEAT-03/04 |

#### 非功能指标（NFR）

| NFR-ID | 指标 |
|---|---|
| NFR-01 | L1 bypass 后 resolve p95 不劣化（以 `test_resolver_benchmark` 为基线对比） |
| NFR-02 | scoped read 单次快照组装 p95 ≤ 20ms（AGENTS 既有基线，PG 本地） |
| NFR-03 | hook dispatch 框架开销沿用既有基准（`test_hook_benchmark` 不劣化；8 点位默认无注册时零开销） |

---

## 3. 技术设计

### 3.1 方案选型

| 决策点 | 备选 | 选用 | 依据 |
|---|---|---|---|
| V2 删除 vs 接线 | 方案 A 删除 / 方案 B 接线 | **A**（105 文档拍板） | 接线需分布式语义（concurrency 集群协调），贵一个数量级；ADR-A013 已留版本开关，本次直接落 v2 形状 |
| Hook 发现机制 | entry_points / composition root 显式注册 | **entry_points**（用户确认：直观） | 装包即用，满足"安装不改核心源码"；显式注册仍要改 composition root 代码，属伪插件化 |
| Scoped 接入方式 | resolver 内包 scope / 双读 revision 重试（105 旧案 §12.3） | **包 scope** | REPEATABLE READ 强于事后重试；机制已实现并实证，只剩接线（半天量级） |
| status 列定义 | nullable / 非空默认 completed | **nullable**（用户确认） | DB 可重建，无存量负担；新写入必带值由类型保证 |
| P0-01 e2e 化 | pytest 驱动 compose / manual | **manual** | docker 守护进程＋用户自备 PG 属于外部条件，无法在 CI/单测自动化；以 checklist＋命令记录为准 |

否决项记录：`hasattr` 降级嗅探 store 能力（行为分叉，难测试，否决）；`IGNORE` 保留（与 FAIL_OPEN 语义重复，否决）。

### 3.2 架构设计

Compose 目标拓扑（外部依赖在外）：

```text
External PostgreSQL ◄──┐
External Redis     ◄──┐│
                     ││
        ┌────────────┼┼───────────┐
        ▼            ▼▼           ▼
       api      runtime×N      worker
     （同 image，FLUXION_ROLE 区分）
```

Hook 闭环（补齐后）：

```text
pip package (entry_points fluxion.hooks)
        │
        ▼
PluginLoader ──HOOK──► HookRegistryProtocol ──► TypedEventBus
                                                        │
                        Fixed Hook Points（8 个，见 3.4）
```

Resolver scoped 接入（FEAT-07）：`resolve()` 内 `async with store.begin_scoped_read(tenant)` 包裹"读 revision→读 6 类配置→组 snapshot"全程；`credential_resolver`/`memory_retriever` 调用保持在 scope 之外（现状即在组装后/独立分支，需逐点核对不进 scope）。

### 3.3 数据设计

`trace_records` 新增列（`schema.py`，`init_db.py` 唯一事实源；DB 删除重建，**无迁移脚本**）：

| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `status` | String(32) | nullable | completed/failed/cancelled/timed_out，与 `ExecutionTerminalState` 对齐 |

`RuntimeProfile` V2（`resource_specs.py`）：仅 `max_rounds`/`default`/`bootstrapped_from`；删 `schema_version`（用户拍板：无兼容则连标记一起删）；删 `read_published_profile` 兼容入口、`PROFILE_SCHEMA_VERSIONS`、`_profile_spec_default` 相关；`validate_profile_write` 更名为严格校验主入口（或内联，plan 阶段定）。

`HookRegistration` 收敛（`kernel/events.py`）：`event_type/handler/priority/timeout_ms/fail_policy`；删 `scope/scope_id`；`FailPolicy` 删 `IGNORE`。

性能注记：scoped 读单连接多查询（相对现状多连接相当，正确性优先）；L1 bypass 省内存无查询增量；hook 无注册时 dispatch 空转（既有 benchmark 守门）。

### 3.4 接口设计

环境变量（compose/deploy）：

| 变量 | 必填 | 说明 |
|---|---|---|
| `FLUXION_DATABASE_URL` | 生产必填（compose 内必填，无默认） | 外部 PG DSN |
| `FLUXION_REDIS_URL` | 按需（未来 L2 用；本次只定义命名） | 外部 Redis URL，默认缺席即 L2 禁用 |
| `FLUXION_SECRET_MASTER_KEY` | 生产必填 | 不变 |

函数入口变更：
- `PluginLoader.__init__` 新增 `hook_registry: HookRegistryProtocol | None`；`load()` 新增 HOOK 分支；新增 `discover_hooks() -> list[HookPluginSpec]`（`importlib.metadata.entry_points(group="fluxion.hooks")` 解析，格式错误 fail-fast）。
- composition root（`runtime_app.create_dev_bundle`/`production_bundle`/`cli serve` 装配点）：启动时 `discover → load → register`；无 entry_points 时为空集（行为不变）。
- 新增 7 个 payload 类型（`kernel/events.py`，`frozen=True`，只读语义同 `BeforeToolCallPayload`）：`BeforeExecution/AfterExecution/BeforeModelCall/AfterModelCall/AfterToolCall/OnExecutionError/OnExecutionCancelled`；分发点分别位于 `run()/stream()` 出入口、`run_step` 模型调用前后、`_execute_model_tool` 前后、`except` 取消/错误分支。
- `begin_scoped_read` 纳入 store Protocol（`ChannelRegistryStore` 或其父，二选一；同步更新测试 fakes——plan 阶段盘点 fakes 清单）。
- `_append_trace` 新增 `status` 入参；`run()/stream()` 取 `session.finalize()` 返回值传入（现状返回值被丢弃）。

Console API 无新端点；`validate-publish` 对 v1 字段行为变为拒绝（由 model 收紧自动获得）。

### 3.5 质量实现方案

- **可靠性**：hook 分发异常一律按 `fail_policy` 收敛（既有 `dispatch` 逻辑复用，新点位不得自创异常语义）；entry_points 解析失败 fail-fast（与项目 fail-fast 风格一致）。
- **安全**：hook payload 保持 `frozen`＋拷贝传参（`runtime_tool_ops.py:168` 模式复制到新点位）；授权先于 hook（新点位若接触敏感参数，同款前置检查）。
- **可观测**：trace status 落盘即观测；hook 执行经既有 trace sink（`trace_sink=context` 模式）。
- **测试策略**：TC 表即来源；compose 项 manual；其余自动化。回归范围：contract＋services＋integration＋frontend console vitest（删字段牵连断言集）。

---

## 4. 部署与运维

### 4.1 部署架构

见 3.2 拓扑。compose 文件只含 `api/runtime/worker`；`depends_on` 仅表达应用间顺序（如 api 等 runtime 健康），**不含任何基础设施服务**。

### 4.2 发布与回滚

应用发布：单镜像 tag 滚动。三角色同镜像，回滚即回滚 tag。

### 4.3 监控告警

按需：trace status 分布（completed/failed/cancelled/timed_out 占比）建议进 Console 运营视图后续迭代，**本次只落盘＋列表展示，不建告警**。

### 4.4 数据迁移

**明确无迁移**：`trace_records.status` 新列、`resource_definitions` 存量 v1 spec JSON，均随 DB 删除重建消化。`init_db.py` 更新即全部。

---

## 5. 风险与依赖

| RISK-ID | 描述 | 等级 | 缓解 |
|---|---|---|---|
| RISK-01 | TASK-001 挂起与 P0-01 的关系 | 中 | 本次设计即视为解挂依据；若用户反悔，FEAT-01 整块剔除不影响其余 |
| RISK-02 | V2 删字段牵连面广（seed/mirror/断言/e2e） | 中 | 清单制：`resource_specs`＋6 个后端构造点＋mirror＋editor＋断言集＋e2e seed，plan 阶段逐文件列出，漏一处即红 |
| RISK-03 | 新增 7 hook 点位误触执行语义 | 中 | 只读 payload＋返回值丢弃（既有三件套复制）；fail-closed 点位默认 FAIL_OPEN？**否**——默认沿用各点位现有缺省，plan 阶段逐点定（文档只要求语义明确） |
| RISK-04 | entry_points 在 dev（`pip install -e`）下发现行为 | 低 | 无 entry_points＝空集，现有行为不变；示例插件走测试 fixture 注册，不依赖真实安装 |
| RISK-05 | resolver 包 scope 后长事务风险 | 低 | scope 内仅配置读（SELECT），Credential/Memory I/O 在外；读超时沿用 engine 2s＋scope 5s 双预算 |

依赖：外部 PG/Redis 由用户提供（compose/E2E manual 验证前置）；Python `importlib.metadata`（标准库，无新依赖）。

---

## 6. 需求追溯矩阵

| FEAT | 来源 | API/接口 | TC | 状态 |
|---|---|---|---|---|
| FEAT-01 | 105 §3 | compose.yml＋3 env | S-01(manual) | 待 plan |
| FEAT-02 | 105 §4 | V2 model＋validate-publish 行为 | S-02/S-03/E-01 | 待 plan |
| FEAT-03 | 105 §5 | loader＋entry_points＋示例 | S-04/E-02 | 待 plan |
| FEAT-04 | 105 §5.4 | 7 payload＋分发点 | S-04/E-02 | 待 plan |
| FEAT-05 | 105 §9 | ——（删除） | S-05 | 待 plan |
| FEAT-06 | 105 §6 | ——（内部） | S-06 | 待 plan |
| FEAT-07 | 105 §7 | store Protocol | S-07/B-01 | 待 plan |
| FEAT-08 | 105 §8 | trace status＋runs 列 | S-08 | 待 plan |

RULE→TC：RULE-01→S-02/S-03/E-01；RULE-02→S-06；RULE-03→S-07/B-01；RULE-04→S-08；RULE-05→S-04；RULE-06→S-01。RISK-01→决策项（用户已拍板纳入）；RISK-03→E-02。

---

## 附录：术语表

| 术语 | 含义 |
|---|---|
| Scoped Read | 同一 REPEATABLE READ 事务内的一致配置视图（ADR-A016 机制） |
| fail-closed/fail-open | Hook 失败阻断业务／记录后继续（IGNORE 已删） |
| v1/v2 | RuntimeProfile 契约版本（v1 冻结现状，v2 删字段；无兼容桥） |

---

## Spec Compliance Matrix

| Spec/Rule | enforcement | 设计影响 | 设计落点 | 验证场景 | 状态 |
|---|---|---|---|---|---|
| backend-platform-rules / RULE-backend-platform-001 | required | compose 只编排应用角色；外部依赖 env 化 | FEAT-01，§3.2/§3.4 | S-01(manual，docker＋外部 PG，无法自动化) | 待确认 |
| backend-code-quality-performance / RULE-backend-quality-001 | required | 有界调用（scope 5s＋engine 2s）、异常保留、资源释放 | FEAT-06/07/08 | S-06/S-07/B-01 | 待确认 |
| backend-database / RULE-backend-database-001 | required | 参数化查询延续；REPEATABLE READ 只读事务；外部 I/O 不进事务 | FEAT-07 | S-07/B-01 | 待确认 |
| backend-logging / RULE-backend-logging-001 | required | 清理/hook 失败日志带 ID 脱敏（沿用既有） | FEAT-03/04/08 | S-04/S-08/E-02 | 待确认 |
| architecture/runtime-core / RULE-fluxion-runtime-001 | required | 无状态保持；hook 与 kernel 解耦（只依赖 Contract） | FEAT-03/04 | S-04 | 待确认 |
| architecture/resource-registry / RULE-fluxion-resource-001 | required | PG 唯一实现；tenant scope 全链路 | FEAT-02/07 | S-02/S-07 | 待确认 |
| architecture/dfx / RULE-fluxion-dfx-001 | required | 基准对比（resolver/hook benchmark 不劣化） | NFR-01/03 | S-06＋benchmark | 待确认 |
