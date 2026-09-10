# Plan: core-domain-publish（核心领域与发布模型 缺口闭合 + 契约对齐）

> 设计输入：`core-domain-publish.design.md` v1.2（Design Gate: pass）
> Spec Context：5 bindings / 5 条 required rule
> 本轮性质：**返工**。上一轮（2026-09-10 归档版）把待办定义为"补测试缺口"，
> 导致 RULE-05 生产侧、FEAT-02 冻结语义、E-01、S-02 幂等分支成为孤儿。本轮从 design 的
> RULE 表 + 场景表**正推**，每条 required rule 分配唯一责任 TASK。

## 0. 上一轮为何不闭合（返工依据）

| # | 失效点 | 证据 |
|---|---|---|
| 1 | plan 产物用 `### T1 —` 标题，非 `^## (TASK-\d+):`，plan gate 解析出 **0 个 TASK** | `cf_spec_gate.py:132` `_task_sections()`；实测 `--stage plan` → `block`，10 errors |
| 2 | 5 条 required rule 从未有责任 TASK，plan 阶段停留 `pending` | 同上 `plan_owner_missing × 5` |
| 3 | design v1.2 §6 预填"已实现"，并把 plan 待办定义为"各行 E2E 联调缺口"，使 plan 退化为补测试清单 | design §6 状态列 |

**结论**：不是拆分粒度问题。机制本来就内建了"每条 required rule 必须有唯一责任 TASK +
verifier + 测试层级"的检查（`cf_spec_gate.py:146-167`），能拦住这些孤儿——产物没走这个机制。

## 1. 验收契约表

覆盖 design v1.2 §2.5.2 全部 P0 场景与 §2.5.1 全部 RULE。

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 |
|---|---|---|---|---|---|
| S-01 | §2.5.2 | integration | Domain Model → Repository Schema | — | 部分实现（现有用例直接插 ORM，未走 domain 层；本轮回填口径） |
| S-02 | §2.5.2 | integration | Console/API → Store → Runtime Resolve | TASK-001, TASK-004 | 本轮补齐（幂等指针 + Agent 冻结） |
| S-03 | §2.5.2 | integration | ExecutionSnapshot → RuntimeDynamicContext | — | 模块 01 半边达成（RULE-04）；权限重校验属模块 05/09 |
| S-04 | §2.5.2 | integration | Object Store → SkillArtifact → Published Resolver | — | 仅模型层；Resolver 属模块 05 |
| S-05 | §2.5.2 | integration | Manifest → ServiceRelease → ExecutionSnapshot | TASK-003 | 本轮补齐（真实 Release 行 + Registry 校验） |
| S-06 | §2.5.2 | integration | Migration Head → Models → Gate | — | 已实现（门禁为子串扫描，见 §4 残留） |
| E-01 | §2.5.2 | integration | Architecture Gate → framework/domain | — | 部分实现（仅 import 片段，无字段维度检查，见 §4 残留） |
| E-02 | §2.5.2 | integration | Repository → Published Snapshot | TASK-002 | 本轮补齐（门禁加固至函数形式） |
| E-04 | §2.5.2 | integration | Manifest → resource_scope_validator | TASK-003 | 本轮补齐（真实 Release 行，非纯单测） |
| NFR-OBS-01 | §2.5.3 | integration | Audit job → request_id / execution_id | TASK-004, TASK-005 | 本轮补齐（指针变化时 audit + request_id 透传） |

RULE → 场景 → 责任 TASK 映射：

| RULE | 验证场景 | 责任 TASK |
|---|---|---|
| RULE-01 Core 不含项目字段 | S-01 / E-01 | 无（既有门禁，见 §4 残留） |
| RULE-02 只有 Service 两态 | S-02 | 无（0002 迁移已达成） |
| RULE-03 已发布不可原地修改 | E-02 | TASK-002 |
| RULE-04 安全状态不进 Snapshot | S-03 | 无（模块 01 半边已达成） |
| RULE-05 scope 进 payload + 冻结 schema_hash | S-05 / E-04 | TASK-003 |
| RULE-06 无 user_agent_binding | S-06 | 无（已达成） |

## 2. 任务清单

## TASK-001: LIB-01 幂等重发必须切换 current_release_id

- **Source**: core-domain-publish.design.md#2.5.2 (S-02)、#3.5 可靠性
- **Spec-Refs**: backend-database#RULE-backend-database-001
- **Acceptance-Refs**: S-02
- **落点**: `adapters/postgres/service_repository.py`

缺陷：撞 `uq_service_release_hash` 时走 `return await self._existing_release(...)`，
**跳过了 `service.current_release_id = row.id`**。实测序列 draft `A → B → A`：
返回 `r-d02ff543acdd`（A），而 `current_release_id` 仍指向 B——"回滚到历史内容"静默失败。

### Checklist
- [ ] [Spec] `backend-database#RULE-backend-database-001` 逐条核对 Guidance/Avoid，逐项标注 **verifier** 与执行命令

- [ ] [S-02][integration] 不得 Mock 的真实边界：真实 PostgreSQL + `ServiceRepository` +
      `service_definition`/`service_release` 真实行；**verifier**: `cf_spec_gate.py --stage code`
- [ ] 复现用例：draft A 发布 → 改 draft B 发布 → 改回 draft A 发布；断言最后返回的 release
      与 `service_definition.current_release_id` **一致**
- [ ] 指针真正变化时才写 `service.published` audit；同内容重复发布不产生第二条 audit
- [ ] 失败路径不回归：validator 抛错后无孤儿 release 行且 `current_release_id` 未动

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 |
|---|---|---|---|
| S-02 | integration | PostgreSQL、ServiceRepository、service_definition/service_release | 重发历史内容后 `current_release_id == 该 release.id`；release 行数不增 |

---

## TASK-002: E-02 已发布快照不可变门禁加固

- **Source**: core-domain-publish.design.md#2.5.2 (E-02)、§3.5 可靠性
- **Spec-Refs**: backend-directory-structure#RULE-backend-directory-001
- **Acceptance-Refs**: E-02
- **落点**: `tests/architecture/test_release_immutable.py`（由 `tests/unit/` 迁入）

缺陷：现有门禁只匹配 `ast.Attribute` 形式的 `.update()` / `.delete()`，且只扫
`service_repository.py` 单文件。实测 `session.execute(update(ServiceReleaseModel)...)`
（函数形式）完全绕过——published payload 被改写且 `content_hash` 静默失配。

设计 v1.2 已明确 **DB 层不加 trigger**（"单一写入者约定由 gate 保证"），
因此本任务只加固 gate，不引入运行时/DB 层强制。

### Checklist
- [ ] [Spec] `backend-directory-structure#RULE-backend-directory-001` 逐条核对 Guidance/Avoid，逐项标注 **verifier** 与执行命令

- [ ] [E-02][integration] 真实边界：`adapters/` + `framework/` 全量源码的 AST 扫描；
      **verifier**: `pytest tests/architecture -q`
- [ ] 检出**函数形式** `update(ServiceReleaseModel...)` / `delete(ServiceReleaseModel...)`
- [ ] 保留**属性形式** `.update()` / `.delete()` 检出
- [ ] 门禁落点从 `tests/unit/` 迁入 `tests/architecture/`，与其余硬约束同处
- [ ] 反例自检：临时注入一行函数形式 UPDATE，断言门禁**失败**（记录 RED）

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 |
|---|---|---|---|
| E-02 | integration | adapters/ + framework/ 全量源码 AST | 任何形式针对 `ServiceReleaseModel` 的 update/delete 语句构造均为 0 命中 |

---

## TASK-003: RULE-05 scope 进 payload 并由 Registry 强校验

- **Source**: core-domain-publish.design.md#2.5.1 (RULE-05)、#2.5.2 (S-05/E-04)、FEAT-04
- **Spec-Refs**: backend-code-quality-performance#RULE-backend-quality-001
- **Acceptance-Refs**: S-05, E-04
- **落点**: `framework/domain/publish.py`、`adapters/postgres/service_repository.py`、
  `framework/integration/resource_scope_registry.py`

缺陷：`resource_scope_type`/`schema_hash` 是 `publish()` 的可选 kwarg，**不进 draft payload**
（与 FEAT-04「放入 Service Draft/Published Payload」矛盾）；`service_repository.py`
**零次引用 `ResourceScopeRegistry`**，传什么字符串都原样存下——RULE-05 无强制点。

### Checklist
- [ ] [Spec] `backend-code-quality-performance#RULE-backend-quality-001` 逐条核对 Guidance/Avoid，逐项标注 **verifier** 与执行命令

- [ ] [S-05][integration] 真实边界：真实 PostgreSQL + 真实 `service_release` 行 + 真实
      `ResourceScopeRegistry`，不得 Mock 校验核心；**verifier**: `pytest tests/integration -q`
- [ ] `build_service_release` 从 `draft["resource_scope_type"]` 读取，移除 kwarg 入口
- [ ] `ServiceRepository` 注入 `ResourceScopeRegistry`；未知 type → `SERVICE_CONFIGURATION_INVALID`
- [ ] `schema_hash` 由 Registry **派生**写入 payload，不接受调用方传入
- [ ] 未声明 scope 的 Service 仍可发布（向后兼容）
- [ ] [E-04][integration] 未知 type / 未声明 type 两条拒绝路径均有真实 Release 行断言

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 |
|---|---|---|---|
| S-05 | integration | PostgreSQL、service_release、ResourceScopeRegistry | 合法 scope 发布成功，payload 含 registry 派生的 schema_hash |
| E-04 | integration | PostgreSQL、ResourceScopeRegistry | 未知 type 抛 `SERVICE_CONFIGURATION_INVALID` 且**无 release 行落库** |

---

## TASK-004: FEAT-02 发布时冻结绑定 Agent 配置

- **Source**: core-domain-publish.design.md#2.5.1 (FEAT-02)、§3.1 关键决策记录、FEAT-02 验收 (S-02)
- **Spec-Refs**: backend-logging#RULE-backend-logging-001
- **Acceptance-Refs**: S-02
- **落点**: `adapters/postgres/service_repository.py`

缺陷：`agent_snapshot` 是可选 kwarg 且**零生产调用方**，每个 release 的
`frozen_payload["agent_snapshot"]` 恒为 `{}`——`Service 发布时冻结 Agent 配置` 在现实中不成立。
`agent_service_binding` 表零生产引用。

### Checklist
- [ ] [Spec] `backend-logging#RULE-backend-logging-001` 逐条核对 Guidance/Avoid，逐项标注 **verifier** 与执行命令

- [ ] [S-02][integration] 真实边界：真实 PostgreSQL + `agent_service_binding` +
      `agent_definition` 真实行；**verifier**: `pytest tests/integration -q`
- [ ] `publish()` 经 `agent_service_binding` 解析绑定 Agent 并冻结其当前配置（revision 等）
- [ ] 冻结结果按 agent_id 稳定排序，保证 canonical hash 可复现
- [ ] 无绑定 Agent 时发布仍合法，`agent_snapshot` 为空
- [ ] [NFR-OBS-01] 发布后修改 Agent，已发布 release 的快照**不变**；audit details 含
      `release_id`/`content_hash`，**不含完整敏感 payload**

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 |
|---|---|---|---|
| S-02 | integration | PostgreSQL、agent_service_binding、agent_definition | 发布后改 Agent 配置，重读 release payload 快照字段不变 |
| NFR-OBS-01 | integration | audit_log 真实行 | 指针变化 → 一条 audit，含 request_id，无完整 payload |

---

## TASK-005: 统一 Envelope / 错误码口径与全量回归

- **Source**: core-domain-publish.design.md#3.4 接口设计、§3.5 可观测性
- **Spec-Refs**: backend-platform-rules#RULE-backend-platform-001
- **Acceptance-Refs**: S-02, E-04, NFR-OBS-01
- **落点**: `tests/`、`framework/web/`

### Checklist
- [ ] [Spec] `backend-platform-rules#RULE-backend-platform-001` 逐条核对 Guidance/Avoid，逐项标注 **verifier** 与执行命令

- [ ] [S-02][integration] 真实边界：全量 pytest（含真实 PG integration）；**verifier**:
      `.venv/bin/python -m pytest -q`
- [ ] 新错误码 `SERVICE_CONFIGURATION_INVALID` 走 `AppError` → 统一 failure Envelope
- [ ] Envelope 结构断言（code/message/data/request_id/timestamp）无回归
- [ ] `ruff check` + `mypy` 对**本轮触碰文件**零新增错误（记录命令与输出）
- [ ] 记录 PG 不可达时的 skip 行为，不得将 skip 计为 pass
- [ ] `cf_spec_gate.py --stage code` 复跑至 pass

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 |
|---|---|---|---|
| S-02 | integration | 真实 PG、全量用例 | 全量 pytest 无 fail；skip 数显式记录 |
| E-04 | integration | AppError → Envelope | 错误码可识别，不泄露内部 schema 细节 |
| NFR-OBS-01 | integration | audit_log | request_id 随发布链路透传 |

## 3. Acceptance Evidence

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-02 | integration | PostgreSQL、ServiceRepository | 重发历史内容切换指针 | `test_publish_atomic.py::test_republishing_earlier_content_switches_current_pointer` | `pytest tests/integration -q` | verified |
| S-02 | integration | PostgreSQL、agent_service_binding | 发布后改 Agent 快照不变 | `test_agent_freeze.py::test_publish_freezes_bound_agent_and_survives_later_agent_edit` | 同上 | verified |
| S-05 | integration | PostgreSQL、ResourceScopeRegistry | registry 派生 schema_hash 入 payload | `test_scope_release.py::test_s05_declared_scope_freezes_registry_schema_hash` | 同上 | verified |
| E-04 | integration | PostgreSQL、ResourceScopeRegistry | 未知 type 抛错且不落库 | `test_scope_release.py::test_e04_unknown_scope_type_rejected_without_writing_release` | 同上 | verified |
| E-02 | integration | adapters/ + framework/ 全量源码 AST | 函数形式 update/delete 零命中 | `test_release_immutable.py::test_no_release_mutation_statements_anywhere` | `pytest tests/architecture -q` | verified |
| NFR-OBS-01 | integration | audit_log 真实行 | 一条 audit + request_id，无完整 payload | `test_publish_audit.py::test_publish_emits_audit_event_without_sensitive_payload` | `pytest tests/integration -q` | verified |
| — | — | 反例自检（RED） | 注入函数形式 UPDATE → 门禁失败 | `framework/__red_probe.py` 临时注入 | `pytest tests/architecture -q` | verified |

**执行结果（2026-09-10 返工轮）**

```text
pytest -q            : 53 passed, 0 skipped（基线 44）
cf_spec_gate design  : pass
cf_spec_gate plan    : pass
cf_spec_gate code    : pass
ruff check/format    : 本轮触碰 10 文件 All checks passed
mypy（hook {files}）  : Success: no issues found in 7 source files
mypy 全仓            : 56 errors（基线 86，净 -30）
cf_stop_hook 模拟    : failures=0
```

**环境修复（阻塞项，非本任务代码缺陷）**：`.code-flow/validation.yml` 原先把四个校验器
硬编码为 `python3`，在 macOS 上落到 `/usr/bin/python3` (3.9)，既无依赖也无 mypy →
每条校验永久失败（HEAD 上实测 9 个 collection error）。已改为项目解释器 `.venv/bin/python`
（`uv` 不在非交互 shell 的 PATH 上，故不用 `uv run`）。

为通过 mypy 校验器，顺带修复了 3 个文件中的既存 strict 违规（非本任务引入）：
`adapters/postgres/models.py` 21 处 bare `dict` → `dict[str, Any]`、
`framework/agent_core/resolver.py` 6 处 `list/dict/int(object)` call-overload、
`tests/integration/test_publish_atomic.py` 3 处注解缺失。

RED 证据：注入 `framework/__red_probe.py` 含 `update(ServiceReleaseModel)` →
`test_no_release_mutation_statements_anywhere` 失败并报 `framework/__red_probe.py:6: update(ServiceReleaseModel)`；
移除后恢复 5 passed。

## 4. 本轮不做（残留缺口，显式登记）

以下**不在本轮范围**，不因未做而记为已实现：

- **S-01**：现有用例直接插 ORM Model，未走 domain 对象；`ServiceDefinition` 域对象全仓零引用。
- **E-01**：`test_core_purity.py` 只查 3 个 import 片段，**无 customer/device 字段维度检查**。
- **S-06 门禁强度**：`test_routing_goes_through_default_agent` 仅断言 `models.py` 含子串
  `default_agent`；`CODE_DIRS` 不含 `integrations/`。
- **模块 13 依赖**：`ResourceScopeRegistry` 目前无 manifest 装载路径，生产默认空 registry
  → 声明了 scope type 的 Service 默认发布失败。这是 RULE-05 fail-closed 的预期行为，
  但需模块 13 落地后才能实际使用。
- **模块 05 依赖**：`PublishedServiceResolver` 仍是 stub（`TODO_HASH`），
  `ExecutionService.create()` 在任何真实输入下必然抛 `SERVICE_CONFIGURATION_INVALID`。
- **E-02 DB 层**：按设计 v1.2 明确不加 trigger；跨进程（psql 等）仍可改写。

## 5. 执行顺序

```
TASK-002 ─┐
TASK-001 ─┼─▶ TASK-003 ─▶ TASK-004 ─▶ TASK-005
          ┘
```

- TASK-001/002 无依赖，先行（纯加固）。
- TASK-003 改 LIB-01 签名，TASK-004 依赖其落点，必须在其后。
- TASK-005 最后收口。
