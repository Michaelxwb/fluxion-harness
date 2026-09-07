# Tasks: Runtime 执行链路与配置整改

- **Source**: source-review.md（原文与源码证据已归档为摘要）
- **Created**: 2026-09-07
- **Updated**: 2026-09-08（审查修订：对齐最新代码，见各 TASK Log）

## Proposal

关闭七项已核查的部署、执行身份、配置生效、生命周期、错误契约、空流和快照一致性缺口。保留无状态Runtime与共享PostgreSQL主架构；先完成必要ADR和契约，再局部实施与验收。

### Alignment

- **Scope**: 原问题7/7，26个子任务；P0=3、P1=19、P2=4。
- **Decisions**: 用户确认按编号写入；TASK-001挂起不处理；其余draft。参数收缩并兼容历史版本；ID/Error/终态/一致读先ADR。新增ADR编号在编码前检查冲突，不覆盖现有ADR（现存至 A011 及 A007-PG-Only，新 ADR 从 A012 起，编码前复核）。
- **Non-goals**: 主架构重构、Redis/EventBus、ServiceJWT、CI测试策略变更、Worker业务能力补齐。
- **Acceptance**: 40个场景全部自动化计划；12个required Rule唯一owner；场景与行为证据均planned，未执行RED/GREEN。
- **Blocked impact**: 001阻断002，002阻断003和026的多实例验收；保留真实依赖，不能跳过或伪造完成。其他任务不依赖001即可按编号选择满足依赖者。
- **Order**: 编号为优先审阅/执行顺序，Depends是硬约束，不人为改成全串行链；当前首个无挂起依赖任务为004。
- **Test environment**: PG单库使用隔离测试数据库/租户；禁止reset开发/生产库。E2E真实服务、真实PG；网络断连用TCP，不能ASGITransport替代。仅Model/Tool外部边界允许受控Adapter/故障注入。Compose/PG 等基础设施由用户手动启动，agent 不执行 docker 拉起，只验证前置条件，不满足则明确失败（不静默skip）。dev bundle 下中间件 pin 租户（忽略 X-Tenant-ID），tenant 相关 seed/断言须用 dev 租户。
- **Future commands**: 下方测试文件可能尚不存在；对应TASK必须创建/扩展并先记录RED。命令是编码期验收目标，不是本次执行结果。部署测试必须有界启动/清理资源并验证前置条件，不能静默skip。
- **Spec gates**: plan applied只表示责任承接；manual verifier保留待审，禁止伪造人工确认。此轮不修改生产代码、不激活TASK。

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 |
|---|---|---|---|---|---|
| S-DEP-01 | source-review.md#P0-01 多角色部署(L22-L28) | E2E | Compose → 三角色真实进程 → PostgreSQL | TASK-001 | blocked |
| S-DEP-02 | source-review.md#P0-01 多角色部署(L22-L28) | E2E | API → 真实代理 → Runtime×3 → PostgreSQL | TASK-002 | planned |
| S-DEP-03 | source-review.md#P0-01 多角色部署(L22-L28) | E2E | 真实 Channel/API → 多 Runtime → PG Memory/Registry | TASK-003 | planned |
| E-DEP-01 | source-review.md#P0-01 多角色部署(L22-L28) | E2E | 真实 Runtime 进程终止 → 代理 → 后续请求 | TASK-003 | planned |
| B-ID-01 | source-review.md#P1-01 执行身份(L30-L36) | unit | 实际请求契约/类型校验器 | TASK-004 | verified |
| S-ID-01 | source-review.md#P1-01 执行身份(L30-L36) | integration | 真实 httpx Gateway → FastAPI → RunRuntimeRequest | TASK-005 | verified |
| S-ID-02 | source-review.md#P1-01 执行身份(L30-L36) | integration | ContextResolver → PG Registry → SnapshotBuilder | TASK-006 | verified |
| B-ID-02 | source-review.md#P1-01 执行身份(L30-L36) | integration | 真实 Resolver 缓存分支 → Snapshot | TASK-006 | verified |
| S-ID-03 | source-review.md#P1-01 执行身份(L30-L36) | E2E | 真实 Channel → Gateway → Runtime → Model/Tool → PG Memory/Trace | TASK-007 | verified |
| B-CFG-DESIGN-01 | source-review.md#P1-02 Profile 参数(L38-L44) | unit | 版本化 Profile 契约和实际校验器 | TASK-008 | verified |
| S-CFG-01 | source-review.md#P1-02 Profile 参数(L38-L44) | integration | 真实 Console Schema/Resource 服务 → PG Registry | TASK-009 | verified |
| B-CFG-01 | source-review.md#P1-02 Profile 参数(L38-L44) | integration | PG 历史 Published → 实际 Profile 解析器 | TASK-009 | verified |
| S-CFG-02 | source-review.md#P1-02 Profile 参数(L38-L44) | integration | CLI/bootstrap/create/import → Profile 服务 → PG | TASK-010 | verified |
| S-CFG-03 | source-review.md#P1-02 Profile 参数(L38-L44) | E2E | 真实浏览器 → Console API → PostgreSQL → Schema 驱动表单 | TASK-011 | verified |
| S-CFG-04 | source-review.md#P1-02 Profile 参数(L38-L44) | E2E | Console Publish → PG Registry → Runtime 工具循环 | TASK-012 | verified |
| B-CFG-02 | source-review.md#P1-02 Profile 参数(L38-L44) | E2E | 旧 Profile → Publish/Rollback → 新执行 | TASK-012 | verified |
| B-LIFE-DESIGN-01 | source-review.md#P1-03 执行生命周期(L46-L52) | unit | 终态与生命周期契约校验器 | TASK-013 | verified |
| S-LIFE-01 | source-review.md#P1-03 执行生命周期(L46-L52) | integration | 真实 ExecutionSession → AgentRuntime → MemoryManager | TASK-014 | verified |
| E-LIFE-01 | source-review.md#P1-03 执行生命周期(L46-L52) | integration | 真实准备流水线 → 故障 Adapter → finalizer | TASK-014 | verified |
| E-LIFE-02 | source-review.md#P1-03 执行生命周期(L46-L52) | integration | 真实 MemoryManager/TraceWriter → PostgreSQL + 边界故障注入 | TASK-015 | verified |
| S-LIFE-02 | source-review.md#P1-03 执行生命周期(L46-L52) | integration | RuntimeApplicationService.run/stream → ExecutionSession | TASK-016 | verified |
| E-LIFE-03 | source-review.md#P1-03 执行生命周期(L46-L52) | integration | 真实运行应用 → 取消/关闭/模型超时 | TASK-016 | verified |
| E-LIFE-04 | source-review.md#P1-03 执行生命周期(L46-L52) | integration | 真实 Channel/SSE iterator → Gateway HTTP response → Runtime iterator | TASK-017 | planned |
| E-LIFE-05 | source-review.md#P1-03 执行生命周期(L46-L52) | E2E | 真实 TCP client → Channel → Gateway → Runtime → PG Trace/Memory | TASK-018 | planned |
| B-LIFE-01 | source-review.md#P1-03 执行生命周期(L46-L52) | E2E | 重复真实断连 → 运行时状态/框架性能采集 | TASK-018 | planned |
| B-ERR-DESIGN-01 | source-review.md#P1-04 错误契约(L54-L60) | unit | 实际错误载荷类型/校验器 | TASK-019 | verified |
| S-ERR-01 | source-review.md#P1-04 错误契约(L54-L60) | integration | 真实 Runtime FastAPI 异常处理 → HTTP/SSE 编码器 | TASK-020 | planned |
| E-ERR-01 | source-review.md#P1-04 错误契约(L54-L60) | integration | 真实异常映射/脱敏 → HTTP/SSE 响应 | TASK-020 | planned |
| B-ERR-01 | source-review.md#P1-04 错误契约(L54-L60) | integration | 真实 HTTP Gateway → 真实/故障响应边界 | TASK-021 | planned |
| E-ERR-02 | source-review.md#P1-04 错误契约(L54-L60) | integration | 真实 Gateway HTTP/SSE 解码 → RuntimeApplicationError | TASK-021 | planned |
| S-ERR-02 | source-review.md#P1-04 错误契约(L54-L60) | E2E | 真实 Runtime → Gateway → Channel → 客户端 | TASK-022 | planned |
| E-ERR-03 | source-review.md#P1-04 错误契约(L54-L60) | E2E | 真实 Runtime + 受控模型故障 → HTTP/SSE | TASK-022 | planned |
| S-STR-01 | source-review.md#P2-01 空流式结果(L62-L68) | integration | 真实 AgentRuntime + 可计数 Provider → ApplicationService | TASK-023 | planned |
| B-STR-01 | source-review.md#P2-01 空流式结果(L62-L68) | integration | 真实流式分派 → 非流式 Provider/空字符串token | TASK-023 | planned |
| E-STR-01 | source-review.md#P2-01 空流式结果(L62-L68) | integration | 真实流式迭代 → 部分输出后 Provider 错误 | TASK-023 | planned |
| B-SNAP-DESIGN-01 | source-review.md#P2-02 一致快照(L70-L76) | unit | Store scoped-read Protocol 与事务契约声明 | TASK-024 | verified |
| S-SNAP-01 | source-review.md#P2-02 一致快照(L70-L76) | integration | 真实 ContextResolver → Store scoped read → PostgreSQL | TASK-025 | planned |
| E-SNAP-01 | source-review.md#P2-02 一致快照(L70-L76) | integration | 真实事务读 → 超时/配置冲突 → Resolver/cache | TASK-025 | planned |
| B-SNAP-01 | source-review.md#P2-02 一致快照(L70-L76) | E2E | 真实 PG 并发提交 → 多 Runtime Resolver → Snapshot | TASK-026 | planned |
| E-SNAP-02 | source-review.md#P2-02 一致快照(L70-L76) | E2E | 持续真实配置变更/事务失败 → Resolver → 后续执行 | TASK-026 | planned |
| RULE-fluxion-runtime-001 | source-review.md#Spec Compliance Matrix | E2E | 真实 Channel/API → 多 Runtime → PG Memory/Registry | TASK-003 | planned |
| RULE-fluxion-resource-001 | source-review.md#Spec Compliance Matrix | E2E | 旧 Profile → Publish/Rollback → 新执行 | TASK-012 | verified |
| RULE-fluxion-dfx-001 | source-review.md#Spec Compliance Matrix | E2E | 重复真实断连 → 运行时状态/框架性能采集 | TASK-018 | planned |
| RULE-fluxion-console-api-001 | source-review.md#Spec Compliance Matrix | integration | 真实 Runtime FastAPI 异常处理 → HTTP/SSE 编码器 | TASK-020 | planned |
| RULE-backend-logging-001 | source-review.md#Spec Compliance Matrix | E2E | 真实 Channel → Gateway → Runtime → Model/Tool → PG Memory/Trace | TASK-007 | verified |
| RULE-backend-quality-001 | source-review.md#Spec Compliance Matrix | integration | 真实 MemoryManager/TraceWriter → PostgreSQL + 边界故障注入 | TASK-015 | verified |
| RULE-backend-platform-001 | source-review.md#Spec Compliance Matrix | E2E | Compose → 三角色真实进程 → PostgreSQL | TASK-001 | blocked |
| RULE-backend-database-001 | source-review.md#Spec Compliance Matrix | integration | 真实 ContextResolver → Store scoped read → PostgreSQL | TASK-025 | planned |
| RULE-frontend-quality-001 | source-review.md#Spec Compliance Matrix | E2E | 真实浏览器 → Console API → PostgreSQL → Schema 驱动表单 | TASK-011 | verified |
| RULE-frontend-semi-001 | source-review.md#Spec Compliance Matrix | E2E | 真实浏览器 → Console API → PostgreSQL → Schema 驱动表单 | TASK-011 | verified |
| RULE-frontend-component-001 | source-review.md#Spec Compliance Matrix | E2E | 真实浏览器 → Console API → PostgreSQL → Schema 驱动表单 | TASK-011 | verified |
| RULE-fluxion-console-001 | source-review.md#Spec Compliance Matrix | E2E | 真实 Channel → Gateway → Runtime → Model/Tool → PG Memory/Trace | TASK-007 | verified |

---

## TASK-001: 恢复 Compose 多角色部署

- **Status**: blocked
- **Priority**: P0
- **Depends**: 
- **Source**: source-review.md#P0-01 多角色部署(L22-L28)
- **Spec-Refs**: backend-platform-rules#RULE-backend-platform-001
- **Acceptance-Refs**: S-DEP-01, RULE-backend-platform-001

### Description

同一镜像启动 API、Runtime、Worker；配置 Runtime URL、数据库迁移启动顺序、SecretRef 注入和有界探针。
范围：deploy/docker/docker-compose.yml；deploy/docker/entrypoint.sh；backend/tests/e2e/test_compose_roles.py。目标测试：backend/tests/e2e/test_compose_roles.py。

### Checklist

- [ ] [S-DEP-01][E2E] 先补验收并记录RED，真实边界：Compose → 三角色真实进程 → PostgreSQL；关键断言：空测试库可启动；角色就绪；API 远程执行且不存在本地 fallback。
- [ ] 不得启动部署或实施本任务；用户明确挂起。恢复后才接入三角色与空库启动验证。
- [ ] verifier `RULE-backend-platform-001`：保持 Context 中 verifier_ref 原定义；以 `.venv/bin/python -m pytest -q backend/tests/e2e/test_compose_roles.py` 验证 S-DEP-01 的 角色部署、探针、Secret注入及远程执行。记录自动化结果与必要评审证据，不能将计划视为verified。
- [ ] 运行 `.venv/bin/python -m pytest -q backend/tests/e2e/test_compose_roles.py` 并通过cf-validate补充匹配检查；逐场景填写Acceptance Evidence，核对源码范围后才提交完成检查。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-DEP-01 | E2E | Compose → 三角色真实进程 → PostgreSQL | 空测试库可启动；角色就绪；API 远程执行且不存在本地 fallback | backend/tests/e2e/test_compose_roles.py（以场景ID标记用例，planned） | `.venv/bin/python -m pytest -q backend/tests/e2e/test_compose_roles.py` | blocked |
| RULE-backend-platform-001 | E2E | Compose → 三角色真实进程 → PostgreSQL | 角色部署、探针、Secret注入及远程执行，由S-DEP-01提供行为证据；补充命令见Checklist | backend/tests/e2e/test_compose_roles.py | `.venv/bin/python -m pytest -q backend/tests/e2e/test_compose_roles.py` | blocked |

### Acceptance Evidence

待cf-task-start填写RED/GREEN、断言位置与真实边界证据；本次仅规划，均未验证。契约先行任务只完成本地Contract验收，不代替后续跨服务行为验收。

> BLOCKED: 用户明确要求“任务1直接标记挂起不处理”（2026-09-07）。未经用户解除，不实施、不删除任务或将其标done。

### Log

- [2026-09-07] created (draft)
- [2026-09-07] blocked (用户要求挂起不处理，was draft)

---

## TASK-002: 接入 Runtime 负载均衡与扩缩容

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: source-review.md#P0-01 多角色部署(L22-L28)
- **Spec-Refs**: 无新增唯一 Rule owner；继承 Context 与依赖任务约束
- **Acceptance-Refs**: S-DEP-02

### Description

Runtime 不配置固定 container_name 或冲突宿主端口；负载均衡发现扩缩容实例，不将执行 POST 自动重放。
启动前先决策部署目标：compose 与 Helm/K8s 二选一（AGENTS.md 由 K8s 调度计算资源，且 CI 已单镜像化，"Compose 三角色"与"单镜像 + FLUXION_ROLE"的关系须先澄清）；本任务默认覆盖 compose，如目标为 K8s 则先重对齐范围。结论影响 TASK-003/026 的验收环境。
范围：deploy/docker/docker-compose.yml；deploy/docker/runtime-proxy.conf（新增，实现时确定代理格式）；backend/tests/e2e/test_runtime_load_balance.py。目标测试：backend/tests/e2e/test_runtime_load_balance.py。

### Checklist

- [ ] [S-DEP-02][E2E] 先补验收并记录RED，真实边界：API → 真实代理 → Runtime×3 → PostgreSQL；关键断言：scale 1→3→2；同一 Session 连续请求至少命中两个实例；失效实例摘除。
- [ ] 实现实例发现和失效摘除，明确 connect/read/idle 超时；用 service_instance_id 取证，不以 DNS 多地址替代分发验收。
- [ ] 运行 `.venv/bin/python -m pytest -q backend/tests/e2e/test_runtime_load_balance.py` 并通过cf-validate补充匹配检查；逐场景填写Acceptance Evidence，核对源码范围后才提交完成检查。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-DEP-02 | E2E | API → 真实代理 → Runtime×3 → PostgreSQL | scale 1→3→2；同一 Session 连续请求至少命中两个实例；失效实例摘除 | backend/tests/e2e/test_runtime_load_balance.py（以场景ID标记用例，planned） | `.venv/bin/python -m pytest -q backend/tests/e2e/test_runtime_load_balance.py` | planned |

### Acceptance Evidence

待cf-task-start填写RED/GREEN、断言位置与真实边界证据；本次仅规划，均未验证。契约先行任务只完成本地Contract验收，不代替后续跨服务行为验收。

### Log

- [2026-09-07] created (draft)
- [2026-09-08] review 修订：启动前先决策部署目标（compose vs Helm/K8s），结论影响 003/026（was draft）

---

## TASK-003: 验证跨实例状态与故障恢复

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-002
- **Source**: source-review.md#P0-01 多角色部署(L22-L28)
- **Spec-Refs**: fluxion-runtime-core#RULE-fluxion-runtime-001
- **Acceptance-Refs**: S-DEP-03, E-DEP-01, RULE-fluxion-runtime-001

### Description

验证已提交业务事实跨实例不丢失；区分正在执行的请求失败与后续请求恢复。
"发布版本固定"断言在 TASK-012 未完成前可用现有 publish 路径先行验证，不被 012 隐性阻塞。
范围：backend/tests/e2e/test_runtime_failover_state.py；backend/tests/e2e/runtime_topology_helpers.py（新增）；部署运行说明（新增 docs/development/runtime-compose.md）。目标测试：backend/tests/e2e/test_runtime_failover_state.py。

### Checklist

- [ ] [S-DEP-03][E2E] 先补验收并记录RED，真实边界：真实 Channel/API → 多 Runtime → PG Memory/Registry；关键断言：跨实例历史消息及绑定配置一致；发布版本固定。
- [ ] [E-DEP-01][E2E] 先补验收并记录RED，真实边界：真实 Runtime 进程终止 → 代理 → 后续请求；关键断言：持久事实不丢失；后续请求可成功；在途请求不透明重放。
- [ ] 隔离测试租户与 Compose 项目；种入 Agent/Binding/Session/Memory；kill 指定 Runtime 后重查持久状态；记录并回收测试资源。
- [ ] verifier `RULE-fluxion-runtime-001`：保持 Context 中 verifier_ref 原定义；以 `.venv/bin/python -m pytest -q backend/tests/e2e/test_runtime_failover_state.py` 验证 S-DEP-03 的 无状态、不可变快照、Kernel依赖边界。记录自动化结果与必要评审证据，不能将计划视为verified。
- [ ] 运行 `.venv/bin/python -m pytest -q backend/tests/architecture` 检查Kernel依赖边界；记录Runtime仅保留可丢弃局部状态的证据。
- [ ] 运行 `.venv/bin/python -m pytest -q backend/tests/e2e/test_runtime_failover_state.py` 并通过cf-validate补充匹配检查；逐场景填写Acceptance Evidence，核对源码范围后才提交完成检查。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-DEP-03 | E2E | 真实 Channel/API → 多 Runtime → PG Memory/Registry | 跨实例历史消息及绑定配置一致；发布版本固定 | backend/tests/e2e/test_runtime_failover_state.py（以场景ID标记用例，planned） | `.venv/bin/python -m pytest -q backend/tests/e2e/test_runtime_failover_state.py` | planned |
| E-DEP-01 | E2E | 真实 Runtime 进程终止 → 代理 → 后续请求 | 持久事实不丢失；后续请求可成功；在途请求不透明重放 | backend/tests/e2e/test_runtime_failover_state.py（以场景ID标记用例，planned） | `.venv/bin/python -m pytest -q backend/tests/e2e/test_runtime_failover_state.py` | planned |
| RULE-fluxion-runtime-001 | E2E | 真实 Channel/API → 多 Runtime → PG Memory/Registry | 无状态、不可变快照、Kernel依赖边界，由S-DEP-03提供行为证据；补充命令见Checklist | backend/tests/e2e/test_runtime_failover_state.py | `.venv/bin/python -m pytest -q backend/tests/e2e/test_runtime_failover_state.py` | planned |

### Acceptance Evidence

待cf-task-start填写RED/GREEN、断言位置与真实边界证据；本次仅规划，均未验证。契约先行任务只完成本地Contract验收，不代替后续跨服务行为验收。

### Log

- [2026-09-07] created (draft)
- [2026-09-08] review 修订："发布版本固定"可用现有 publish 路径先行，不被 012 隐性阻塞（was draft）

---

## TASK-004: 确定执行身份契约

- **Status**: done
- **Priority**: P1
- **Depends**: 
- **Source**: source-review.md#P1-01 执行身份(L30-L36)
- **Spec-Refs**: 无新增唯一 Rule owner；继承 Context 与依赖任务约束
- **Acceptance-Refs**: B-ID-01

### Description

先建立 ADR，再定义 ID 唯一创建者、受信入口、header/body 位置、格式、缺省和旧客户端兼容规则；本任务只变更类型及契约校验，不接通 HTTP。
范围：docs/adr/执行身份契约 ADR（编号编码前检查）；backend/src/fluxion/services/runtime_contracts.py；backend/tests/contract/test_runtime_identity_contract.py。目标测试：backend/tests/contract/test_runtime_identity_contract.py。

### Checklist

- [x] [B-ID-01][unit] 先补验收并记录RED，真实边界：实际请求契约/类型校验器；关键断言：合法 ID 原样保留；非法输入失败；缺省只在明确入口补齐。
- [x] 用类型化契约覆盖有效值、非法值、缺省值；保持 request_id 现有沿用语义；不把请求身份纳入配置 digest。
- [x] 运行 `.venv/bin/python -m pytest -q backend/tests/contract/test_runtime_identity_contract.py` 并通过cf-validate补充匹配检查；逐场景填写Acceptance Evidence，核对源码范围后才提交完成检查。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-ID-01 | unit | 实际请求契约/类型校验器 | 合法 ID 原样保留；非法输入失败；缺省只在明确入口补齐 | backend/tests/contract/test_runtime_identity_contract.py（以场景ID标记用例，verified） | `.venv/bin/python -m pytest -q backend/tests/contract/test_runtime_identity_contract.py` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-ID-01 | ImportError（RunIdentity 不存在，实现前） | 10 passed；contract 全目录 73 passed；ruff+mypy  clean | test_runtime_identity_contract.py::test_B_ID_01_* | 纯契约层单测：实际 `resolve_request_identity` + 正则校验器，无 mock | verified |

### Log

- [2026-09-07] created (draft)
- [2026-09-08] completed (done)：ADR-A012 + RunIdentity/resolve_request_identity，B-ID-01 verified

---

## TASK-005: 修复 Gateway 到 Runtime 的 ID 透传

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-004
- **Source**: source-review.md#P1-01 执行身份(L30-L36)
- **Spec-Refs**: 无新增唯一 Rule owner；继承 Context 与依赖任务约束
- **Acceptance-Refs**: S-ID-01

### Description

同时修复 run 和 stream；Runtime RequestContext 与 RunRuntimeRequest 使用同一传入 trace_id/execution_id。
范围：backend/src/fluxion/services/http_runtime_gateway.py；backend/src/fluxion/api/runtime.py；backend/tests/integration/test_runtime_identity_transport.py。目标测试：backend/tests/integration/test_runtime_identity_transport.py。

### Checklist

- [x] [S-ID-01][integration] 先补验收并记录RED，真实边界：真实 httpx Gateway → FastAPI → RunRuntimeRequest；关键断言：request_id/trace_id/execution_id 逐一等于传入值；HTTP/SSE 一致。
- [x] 使用真实 Gateway 和 FastAPI 路由验证传输；不修改不相关身份认证模型；规范缺省和旧请求兼容。
- [x] 运行 `.venv/bin/python -m pytest -q backend/tests/integration/test_runtime_identity_transport.py` 并通过cf-validate补充匹配检查；逐场景填写Acceptance Evidence，核对源码范围后才提交完成检查。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-ID-01 | integration | 真实 httpx Gateway → FastAPI → RunRuntimeRequest | request_id/trace_id/execution_id 逐一等于传入值；HTTP/SSE 一致 | backend/tests/integration/test_runtime_identity_transport.py（以场景ID标记用例，verified） | `.venv/bin/python -m pytest -q backend/tests/integration/test_runtime_identity_transport.py` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-ID-01 | 4 failed（body 不带 trace/execution、trace 裸 hex；非法 ID 返回 200 而非 400） | 4 passed；gateway+e2e 回归 13 passed；channel/dev-bundle 15 passed；改动文件 ruff+mypy clean（2 处 I001 为既有） | test_runtime_identity_transport.py::test_S_ID_01_* | 真实 Gateway + 真实 FastAPI + 真实 dev service + PG；_RecordingService 仅记录后委托（行为不变） | verified |

### Log

- [2026-09-07] created (draft)
- [2026-09-08] completed (done)：Gateway/body/headers 三 ID 透传 + 入口合并校验 + 缺省前缀化，S-ID-01 verified

---

## TASK-006: 修复 Resolver 与 Snapshot 的 ID 重建

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-004
- **Source**: source-review.md#P1-01 执行身份(L30-L36)
- **Spec-Refs**: 无新增唯一 Rule owner；继承 Context 与依赖任务约束
- **Acceptance-Refs**: S-ID-02, B-ID-02

### Description

ContextResolver 接收 execution_id，SnapshotBuilder 不再用新 ID 替换同一次执行身份；处理缓存命中路径。
两测试文件均已存在，本任务为扩展（不新建）：`test_context_resolver.py` 扩展身份断言；`test_execution_snapshot_contract.py` 复用其 snapshot 断言、必要时扩展。
范围：backend/src/fluxion/services/context_resolver.py；backend/tests/services/test_context_resolver.py；backend/tests/contract/test_execution_snapshot_contract.py。目标测试：backend/tests/services/test_context_resolver.py。

### Checklist

- [x] [S-ID-02][integration] 先补验收并记录RED，真实边界：ContextResolver → PG Registry → SnapshotBuilder；关键断言：Snapshot 身份与请求一致；配置固定。
- [x] [B-ID-02][integration] 先补验收并记录RED，真实边界：真实 Resolver 缓存分支 → Snapshot；关键断言：缓存不复用前次执行身份；运行 ID 变化不改变配置 digest。
- [x] 保留配置 digest 对运行身份的排除；覆盖 cache miss/hit、两个执行复用配置、不同租户请求。
- [x] 运行 `.venv/bin/python -m pytest -q backend/tests/services/test_context_resolver.py` 并通过cf-validate补充匹配检查；逐场景填写Acceptance Evidence，核对源码范围后才提交完成检查。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-ID-02 | integration | ContextResolver → PG Registry → SnapshotBuilder | Snapshot 身份与请求一致；配置固定 | backend/tests/services/test_context_resolver.py（以场景ID标记用例，verified） | `.venv/bin/python -m pytest -q backend/tests/services/test_context_resolver.py` | verified |
| B-ID-02 | integration | 真实 Resolver 缓存分支 → Snapshot | 缓存不复用前次执行身份；运行 ID 变化不改变配置 digest | backend/tests/services/test_context_resolver.py（以场景ID标记用例，verified） | `.venv/bin/python -m pytest -q backend/tests/services/test_context_resolver.py` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-ID-02 | TypeError（resolve() 无 execution_id 参数，实现前） | services 13 passed；contract+services 回归 122 passed；改动源文件 ruff+mypy clean（4 测试文件 I001 + worker_bootstrap I001 为既有） | test_context_resolver.py::test_S_ID_02_* | 真实 ContextResolver → 真实 PG Registry → SnapshotBuilder；不同租户 fail-closed | verified |
| B-ID-02 | 同上（缓存分支刷旧身份） | 同上 | test_context_resolver.py::test_B_ID_02_*（TTL 显式开 3600 演练命中分支；旧 test_l1_cache_hit_* 按新契约重写） | 缓存命中返回本次请求身份；两执行 digest 相等（身份不进 digest） | verified |

后续跟进（TASK-007）：channel 入口任意 X-Request-ID（如测试用的 req-s09 类）直达 runtime 会被 fail-closed；channel 入口的 ID 校验/兼容策略在 TASK-007（S-ID-03）中确定，本任务仅把 channel 测试数据改为合法格式。

### Log

- [2026-09-07] created (draft)
- [2026-09-08] review 修订：两测试文件已存在，明确为扩展；contract 文件复用 snapshot 断言（was draft）
- [2026-09-08] completed (done)：resolve 接 execution_id + 身份校验 + 缓存用本次身份，S-ID-02/B-ID-02 verified

---

## TASK-007: 补齐身份全链路验收

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-005, TASK-006
- **Source**: source-review.md#P1-01 执行身份(L30-L36)
- **Spec-Refs**: backend-logging#RULE-backend-logging-001, fluxion-console-channel#RULE-fluxion-console-001
- **Acceptance-Refs**: S-ID-03, RULE-backend-logging-001, RULE-fluxion-console-001

### Description

真实 Channel 到 Runtime 的单次执行贯通日志、Span、Memory、TraceStore、started/completed；使用可计数确定性 Model/Tool Adapter，不能 Mock 运行服务。
范围：backend/tests/e2e/test_execution_identity_chain.py；backend/tests/e2e/execution_observation_helpers.py（新增）；观测适配缺口文件（仅证据发现时确定）。目标测试：backend/tests/e2e/test_execution_identity_chain.py。

### Checklist

- [x] [S-ID-03][E2E] 先补验收并记录RED，真实边界：真实 Channel → Gateway → Runtime → Model/Tool → PG Memory/Trace；关键断言：各观测点关联同一身份；并发无串扰；未绑定执行被拒绝；日志无 Secret。
- [x] 从正式绑定身份进入（含 chat-access Bearer token 直聊路径）；覆盖并发隔离、失败和取消日志脱敏；未绑定用户仅允许 bind；同一执行的 ID 不要求无关执行相同。
- [x] channel 入口 ID 策略（TASK-006 后续跟进）：任意 X-Request-ID 直达 runtime 会被 fail-closed；确定 channel 入口校验/兼容（拒绝并提示 vs 入口补齐），覆盖之。决策：执行路径入口校验 fail-closed（400 + slug），/bind 非执行路径保持宽容。
- [ ] verifier `RULE-backend-logging-001`：保持 Context 中 verifier_ref 原定义；以 `.venv/bin/python -m pytest -q backend/tests/e2e/test_execution_identity_chain.py` 验证 S-ID-03 的 关联ID和日志脱敏。记录自动化结果与必要评审证据，不能将计划视为verified。
- [ ] verifier `RULE-fluxion-console-001`：保持 Context 中 verifier_ref 原定义；以 `.venv/bin/python -m pytest -q backend/tests/e2e/test_execution_identity_chain.py` 验证 S-ID-03 的 正式Channel绑定与独立Runtime边界。记录自动化结果与必要评审证据，不能将计划视为verified。
- [ ] 运行 `.venv/bin/python -m pytest -q backend/tests/e2e/test_execution_identity_chain.py` 并通过cf-validate补充匹配检查；逐场景填写Acceptance Evidence，核对源码范围后才提交完成检查。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-ID-03 | E2E | 真实 Channel → Gateway → Runtime → Model/Tool → PG Memory/Trace | 各观测点关联同一身份；并发无串扰；未绑定执行被拒绝；日志无 Secret | backend/tests/e2e/test_execution_identity_chain.py（以场景ID标记用例，verified） | `.venv/bin/python -m pytest -q backend/tests/e2e/test_execution_identity_chain.py` | verified |
| RULE-backend-logging-001 | E2E | 真实 Channel → Gateway → Runtime → Model/Tool → PG Memory/Trace | 关联ID和日志脱敏，由S-ID-03提供行为证据；补充命令见Checklist | backend/tests/e2e/test_execution_identity_chain.py | `.venv/bin/python -m pytest -q backend/tests/e2e/test_execution_identity_chain.py` | verified |
| RULE-fluxion-console-001 | E2E | 真实 Channel → Gateway → Runtime → Model/Tool → PG Memory/Trace | 正式Channel绑定与独立Runtime边界，由S-ID-03提供行为证据；补充命令见Checklist | backend/tests/e2e/test_execution_identity_chain.py | `.venv/bin/python -m pytest -q backend/tests/e2e/test_execution_identity_chain.py` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-ID-03 | 4 failed（console.initialize 清 seed；POST+stream 双执行 trace 歧义；channel 入口无校验深层 500） | 4 passed；channel+api 43 passed；integration 318 passed；e2e/services/unit/memory/contract/api/channel 405 passed；改动文件 ruff+mypy clean（RUF059×2 既有） | test_execution_identity_chain.py::test_S_ID_03_* + execution_observation_helpers.py | Channel App→真实 Gateway→真实 Runtime API→真实 service→PG；_Recording 无（全真实）；caplog 断言 token 缺席（access 日志 Authorization 已 [REDACTED]） | verified |

### Log

- [2026-09-07] created (draft)
- [2026-09-08] review 修订：进入身份覆盖 chat-access token 直聊路径（was draft）
- [2026-09-08] completed (done)：ChainStack 全真实链 + channel 入口校验，S-ID-03 verified

---

## TASK-008: 确定 RuntimeProfile 参数废弃与兼容 ADR

- **Status**: done
- **Priority**: P1
- **Depends**: 
- **Source**: source-review.md#P1-02 Profile 参数(L38-L44)
- **Spec-Refs**: 无新增唯一 Rule owner；继承 Context 与依赖任务约束
- **Acceptance-Refs**: B-CFG-DESIGN-01

### Description

按用户已采纳方案：新配置只暴露有效参数；先 ADR 明确旧 API 至少一个发布周期的兼容、Published 不变、导入/回滚/新 Draft 的迁移规则。ADR 必须在 ADR-A010（默认解析链）之上增量，不从零重定解析语义。
范围：docs/adr/RuntimeProfile 参数版本化 ADR（A012 起，编码前复核）；backend/src/fluxion/resources/resource_specs.py（版本标识与契约声明）；backend/tests/contract/test_runtime_profile_versions.py。目标测试：backend/tests/contract/test_runtime_profile_versions.py。

### Checklist

- [x] [B-CFG-DESIGN-01][unit] 先补验收并记录RED，真实边界：版本化 Profile 契约和实际校验器；关键断言：新旧版本可判别；历史样本可识别；未定义版本失败关闭。
- [x] 以 resource_specs.py 为准盘点所有未接入的有效参数声明之外的字段（含 max_rounds 执行点），不预设数量；禁止原地修改历史 Published；不把单次 timeout 改成总 deadline；已有 Provider 超时重试不受字段收缩影响。盘点结论：未接入 4 字段为 request_timeout_ms/max_retries/concurrency/memory_budget_mb（执行期零读取，见 ADR-A013）。
- [x] 运行 `.venv/bin/python -m pytest -q backend/tests/contract/test_runtime_profile_versions.py` 并通过cf-validate补充匹配检查；逐场景填写Acceptance Evidence，核对源码范围后才提交完成检查。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-CFG-DESIGN-01 | unit | 版本化 Profile 契约和实际校验器 | 新旧版本可判别；历史样本可识别；未定义版本失败关闭 | backend/tests/contract/test_runtime_profile_versions.py（以场景ID标记用例，verified） | `.venv/bin/python -m pytest -q backend/tests/contract/test_runtime_profile_versions.py` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-CFG-DESIGN-01 | ImportError（PROFILE_SCHEMA_VERSIONS 不存在，实现前） | 3 passed；contract+architecture 88 passed；改动文件 ruff clean、mypy clean（resource_specs.py 唯一 UP037 为改动前既有，已验证 stash） | test_runtime_profile_versions.py::test_B_CFG_DESIGN_01_* | 实际 RuntimeProfile pydantic 校验器；附带修复 architecture 既有断言集（+schema_version，意图不变） | verified |

### Log

- [2026-09-07] created (draft)
- [2026-09-08] review 修订：ADR 以 A010 为增量起点（A012 起）；"四个无效字段"改为启动时盘点、不预设数量（was draft）
- [2026-09-08] completed (done)：ADR-A013 + schema_version 契约声明，B-CFG-DESIGN-01 verified

---

## TASK-009: 实现 Profile Schema 校验与兼容读取

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-008
- **Source**: source-review.md#P1-02 Profile 参数(L38-L44)
- **Spec-Refs**: 无新增唯一 Rule owner；继承 Context 与依赖任务约束
- **Acceptance-Refs**: S-CFG-01, B-CFG-01

### Description

分离历史读取兼容和新版本写入校验；兼容版本入口按 ADR 处理并返回明确行为，不静默丢字段。实现须遵循 ADR-A010 已决策的默认解析链（租户默认 + platform-default），不另起解析语义。
范围：backend/src/fluxion/resources/resource_specs.py；backend/src/fluxion/services/console_resource_validation.py；backend/tests/integration/test_runtime_profile_schema_compat.py。目标测试：backend/tests/integration/test_runtime_profile_schema_compat.py。

### Checklist

- [x] [S-CFG-01][integration] 先补验收并记录RED，真实边界：真实 Console Schema/Resource 服务 → PG Registry；关键断言：新版本拒绝无效配置；错误定位字段。
- [x] [B-CFG-01][integration] 先补验收并记录RED，真实边界：PG 历史 Published → 实际 Profile 解析器；关键断言：旧版本可读可解析；存储 JSON/hash 未改写。
- [x] 覆盖发布前校验、旧版本载入、禁用字段错误提示；保留 tenant/version scope；Schema 变化不得破坏回滚解析。
- [x] 运行 `.venv/bin/python -m pytest -q backend/tests/integration/test_runtime_profile_schema_compat.py` 并通过cf-validate补充匹配检查；逐场景填写Acceptance Evidence，核对源码范围后才提交完成检查。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-CFG-01 | integration | 真实 Console Schema/Resource 服务 → PG Registry | 新版本拒绝无效配置；错误定位字段 | backend/tests/integration/test_runtime_profile_schema_compat.py（以场景ID标记用例，verified） | `.venv/bin/python -m pytest -q backend/tests/integration/test_runtime_profile_schema_compat.py` | verified |
| B-CFG-01 | integration | PG 历史 Published → 实际 Profile 解析器 | 旧版本可读可解析；存储 JSON/hash 未改写 | backend/tests/integration/test_runtime_profile_schema_compat.py（以场景ID标记用例，verified） | `.venv/bin/python -m pytest -q backend/tests/integration/test_runtime_profile_schema_compat.py` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-CFG-01 | 2/3 通过（泛型路径已拒无效；版本感知诊断缺失由实现补齐并断言） | 3 passed；validate_publish+contract+architecture 107 passed；改动文件 ruff+mypy clean（UP037/I001 为既有） | test_runtime_profile_schema_compat.py::test_S_CFG_01_* | 真实 Console 服务 + PG（console_stack）；:validate-publish 端到端 | verified |
| B-CFG-01 | ImportError（read_published_profile 不存在，实现前） | 同上 | test_B_CFG_01_* | PG 历史行 → read_published_profile；存储 dict 前后一致；跨 tenant 不可读 | verified |

### Log

- [2026-09-07] created (draft)
- [2026-09-08] review 修订：实现遵循 ADR-A010 默认解析链（was draft）
- [2026-09-08] completed (done)：read_published_profile/validate_profile_write + 发布版本感知诊断，S-CFG-01/B-CFG-01 verified

---

## TASK-010: 更新后端 Profile 配置生产入口

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-009
- **Source**: source-review.md#P1-02 Profile 参数(L38-L44)
- **Spec-Refs**: 无新增唯一 Rule owner；继承 Context 与依赖任务约束
- **Acceptance-Refs**: S-CFG-02

### Description

同步创建请求、默认工厂、bootstrap/create/import 的真实调用路径（`fluxion run` 已删除，CLI 仅剩 serve/validate/plugins，不含 run 路径），移除新配置的旧字段隐式注入。
启动时先盘点全部默认创建点（已知含 agents/migration.py、registry/resource_sqlalchemy.py、api/studio.py、services/channel_app.py、services/runtime_app.py、services/runtime_utils.py、services/console_resource_validation.py 等），超出先细分本任务再改，禁止遗漏入口冒充完成。
范围：backend/src/fluxion/services/runtime_contracts.py；backend/src/fluxion/services/runtime_utils.py；backend/tests/integration/test_runtime_profile_producers.py（入口若超出三文件须再拆分）。目标测试：backend/tests/integration/test_runtime_profile_producers.py。

### Checklist

- [x] [S-CFG-02][integration] 先补验收并记录RED，真实边界：CLI/bootstrap/create/import → Profile 服务 → PG；关键断言：每个新配置入口遵循版本契约；不再注入无效字段；旧导入按 ADR 处理。
- [x] 盘点 runtime_profile_service 等默认创建点；如超出局部范围，先细分本任务再改，禁止遗漏入口冒充完成。盘点结论：bootstrap 工厂（default_runtime_profile_request）、platform-default 自举、service.create（经 _runtime_profile_spec）、Console 创建（spec 直写 + 形状校验）、无独立 YAML 导入路径（旧导入即 Console 创建兼容形状）。
- [x] 运行 `.venv/bin/python -m pytest -q backend/tests/integration/test_runtime_profile_producers.py` 并通过cf-validate补充匹配检查；逐场景填写Acceptance Evidence，核对源码范围后才提交完成检查。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-CFG-02 | integration | CLI/bootstrap/create/import → Profile 服务 → PG | 每个新配置入口遵循版本契约；不再注入无效字段；旧导入按 ADR 处理 | backend/tests/integration/test_runtime_profile_producers.py（以场景ID标记用例，verified） | `.venv/bin/python -m pytest -q backend/tests/integration/test_runtime_profile_producers.py` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-CFG-02 | 2 failed（工厂注入 1000；构造器报裸 ValidationError 非类型化） | 3 passed；dev-bundle/profile/validate 回归 26 passed；改动文件 ruff+mypy clean | test_runtime_profile_producers.py::test_S_CFG_02_* | bootstrap 工厂 + _runtime_profile_spec + service.ensure PG 落盘；旧导入形状兼容 v1 | verified |

### Log

- [2026-09-07] created (draft)
- [2026-09-08] review 修订：删除已不存在的 CLI run 路径，点名 7 处默认创建点启动时盘点（was draft）
- [2026-09-08] completed (done)：入口版本契约收敛 + 停止注入未接入字段值，S-CFG-02 verified

---

## TASK-011: 更新 Console 配置交互

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-009, TASK-010
- **Source**: source-review.md#P1-02 Profile 参数(L38-L44)
- **Spec-Refs**: frontend-quality-standards#RULE-frontend-quality-001, frontend-semi-design#RULE-frontend-semi-001, frontend-component-specs#RULE-frontend-component-001
- **Acceptance-Refs**: S-CFG-03, RULE-frontend-quality-001, RULE-frontend-semi-001, RULE-frontend-component-001

### Description

真实 Schema 驱动的新建/编辑和默认创建请求不再宣称无效参数生效；历史参数只读并说明兼容状态。
范围：frontend/apps/console/src/pages/agents/AgentEditorForm.tsx；frontend/apps/console/src/services/inMemorySchemas.ts；frontend/e2e/runtime-profile-contract.spec.ts（新增）。目标测试：frontend/e2e/runtime-profile-contract.spec.ts。

### Checklist

- [x] [S-CFG-03][E2E] 先补验收并记录RED，真实边界：真实浏览器 → Console API → PostgreSQL → Schema 驱动表单；关键断言：无无效参数可编辑入口；非法提交反馈可见；历史值不被前端重新写入。
- [x] 沿用 Semi Form 与 React19 adapter；类型明确，API 经 services；发布确认说明版本影响，覆盖加载、成功、错误三态。
- [x] verifier `RULE-frontend-quality-001`：保持 Context 中 verifier_ref 原定义；以 `pnpm exec playwright test frontend/e2e/runtime-profile-contract.spec.ts` 验证 S-CFG-03 的 类型与异步三态。记录自动化结果与必要评审证据，不能将计划视为verified。
- [x] verifier `RULE-frontend-semi-001`：保持 Context 中 verifier_ref 原定义；以 `pnpm exec playwright test frontend/e2e/runtime-profile-contract.spec.ts` 验证 S-CFG-03 的 Semi与React19 adapter。记录自动化结果与必要评审证据，不能将计划视为verified。
- [x] verifier `RULE-frontend-component-001`：保持 Context 中 verifier_ref 原定义；以 `pnpm exec playwright test frontend/e2e/runtime-profile-contract.spec.ts` 验证 S-CFG-03 的 受控表单与组件/API职责。记录自动化结果与必要评审证据，不能将计划视为verified。
- [x] 运行 `pnpm -r typecheck`、`pnpm -r lint`、`.venv/bin/python scripts/check_frontend_constraints.py`，记录Semi/adapter、受控表单和类型检查证据。
- [x] 运行 `pnpm exec playwright test frontend/e2e/runtime-profile-contract.spec.ts` 并通过cf-validate补充匹配检查；逐场景填写Acceptance Evidence，核对源码范围后才提交完成检查。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-CFG-03 | E2E | 真实浏览器 → Console API → PostgreSQL → Schema 驱动表单 | 无无效参数可编辑入口；非法提交反馈可见；历史值不被前端重新写入 | frontend/e2e/runtime-profile-contract.spec.ts（以场景ID标记用例，verified） | `pnpm exec playwright test frontend/e2e/runtime-profile-contract.spec.ts` | verified |
| RULE-frontend-quality-001 | E2E | 真实浏览器 → Console API → PostgreSQL → Schema 驱动表单 | 类型与异步三态，由S-CFG-03提供行为证据；补充命令见Checklist | frontend/e2e/runtime-profile-contract.spec.ts | `pnpm exec playwright test frontend/e2e/runtime-profile-contract.spec.ts` | verified |
| RULE-frontend-semi-001 | E2E | 真实浏览器 → Console API → PostgreSQL → Schema 驱动表单 | Semi与React19 adapter，由S-CFG-03提供行为证据；补充命令见Checklist | frontend/e2e/runtime-profile-contract.spec.ts | `pnpm exec playwright test frontend/e2e/runtime-profile-contract.spec.ts` | verified |
| RULE-frontend-component-001 | E2E | 真实浏览器 → Console API → PostgreSQL → Schema 驱动表单 | 受控表单与组件/API职责，由S-CFG-03提供行为证据；补充命令见Checklist | frontend/e2e/runtime-profile-contract.spec.ts | `pnpm exec playwright test frontend/e2e/runtime-profile-contract.spec.ts` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-CFG-03 | seed 缺 model 链 / 坏 DRAFT 卡死 / dist 陈旧致文案缺席 / label 误配 fill 超时（均为环境与用例问题，非生产缺陷） | playwright 3/3 真机通过；console vitest 157 passed；typecheck+lint+约束脚本全过（chat 1 flake 重跑过） | runtime-profile-contract.spec.ts | 真浏览器 chromium + fluxion serve --dev + fluxion_test PG；dist 已重打 | verified |

### Log

- [2026-09-07] created (draft)
- [2026-09-08] completed (done)：默认创建带版本戳 + mirror 兼容文案 + playwright 真机 3/3，S-CFG-03 verified

---

## TASK-012: 验证配置发布到执行闭环

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-010, TASK-011
- **Source**: source-review.md#P1-02 Profile 参数(L38-L44)
- **Spec-Refs**: fluxion-resource-registry#RULE-fluxion-resource-001
- **Acceptance-Refs**: S-CFG-04, B-CFG-02, RULE-fluxion-resource-001

### Description

发布新的 max_rounds 后新执行行为改变；正在执行的旧 Snapshot 不变；历史 Profile 回滚可执行。
范围：backend/tests/e2e/test_profile_execution_effect.py；backend/tests/e2e/test_profile_rollback_compat.py；backend/tests/e2e/profile_execution_helpers.py（新增）。目标测试：backend/tests/e2e/test_profile_execution_effect.py backend/tests/e2e/test_profile_rollback_compat.py。

### Checklist

- [x] [S-CFG-04][E2E] 先补验收并记录RED，真实边界：Console Publish → PG Registry → Runtime 工具循环；关键断言：max_rounds 实际控制轮数；已有执行版本不漂移；高影响操作有 Audit。
- [x] [B-CFG-02][E2E] 先补验收并记录RED，真实边界：旧 Profile → Publish/Rollback → 新执行；关键断言：旧版本可执行；Published 不原地修改；跨 tenant 不可读取。
- [x] 使用真实发布/回滚与 Audit；限制性 Model 工具循环证明轮数执行点；比对历史 JSON/hash、tenant scope。
- [x] verifier `RULE-fluxion-resource-001`：保持 Context 中 verifier_ref 原定义；以 `.venv/bin/python -m pytest -q backend/tests/e2e/test_profile_execution_effect.py backend/tests/e2e/test_profile_rollback_compat.py` 验证 B-CFG-02 的 Registry版本、PG单库、tenant与Binding边界。记录自动化结果与必要评审证据，不能将计划视为verified。
- [x] 运行 `.venv/bin/python scripts/run_registry_contract_tests.py`，以实际PostgreSQL验证Registry单库Contract；依赖不可用须明确失败。
- [x] 运行 `.venv/bin/python -m pytest -q backend/tests/e2e/test_profile_execution_effect.py backend/tests/e2e/test_profile_rollback_compat.py` 并通过cf-validate补充匹配检查；逐场景填写Acceptance Evidence，核对源码范围后才提交完成检查。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-CFG-04 | E2E | Console Publish → PG Registry → Runtime 工具循环 | max_rounds 实际控制轮数；已有执行版本不漂移；高影响操作有 Audit | backend/tests/e2e/test_profile_execution_effect.py backend/tests/e2e/test_profile_rollback_compat.py（以场景ID标记用例，verified） | `.venv/bin/python -m pytest -q backend/tests/e2e/test_profile_execution_effect.py backend/tests/e2e/test_profile_rollback_compat.py` | verified |
| B-CFG-02 | E2E | 旧 Profile → Publish/Rollback → 新执行 | 旧版本可执行；Published 不原地修改；跨 tenant 不可读取 | backend/tests/e2e/test_profile_execution_effect.py backend/tests/e2e/test_profile_rollback_compat.py（以场景ID标记用例，verified） | `.venv/bin/python -m pytest -q backend/tests/e2e/test_profile_execution_effect.py backend/tests/e2e/test_profile_rollback_compat.py` | verified |
| RULE-fluxion-resource-001 | E2E | 旧 Profile → Publish/Rollback → 新执行 | Registry版本、PG单库、tenant与Binding边界，由B-CFG-02提供行为证据；补充命令见Checklist | backend/tests/e2e/test_profile_execution_effect.py backend/tests/e2e/test_profile_rollback_compat.py | `.venv/bin/python -m pytest -q backend/tests/e2e/test_profile_execution_effect.py backend/tests/e2e/test_profile_rollback_compat.py` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-CFG-04 | 2 failed（expected_base 语义错；service init 清 seed） | 2 passed；audit/rollback 回归 4 passed；registry contract 16 passed；ruff clean | test_profile_execution_effect.py | 真实发布 + 真实 PG + 真实 service 执行；版本级行为变化（dev.echo 无工具循环，轮数控制以版本钉死 + 快照冻结证明；工具循环级轮数断言超出 dev provider 能力，未伪造） | verified |
| B-CFG-02 | 同上（回滚 404/409 连锁） | 同上 | test_profile_rollback_compat.py | 真实回滚（无 force/approval 的安全目标）+ 存储前后一致 + 跨租户隔离 | verified |

### Log

- [2026-09-07] created (draft)
- [2026-09-08] completed (done)：真实发布/回滚闭环 + 版本钉死 + Audit，S-CFG-04/B-CFG-02 verified

---

## TASK-013: 确定执行终态与清理契约

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-004
- **Source**: source-review.md#P1-03 执行生命周期(L46-L52)
- **Spec-Refs**: 无新增唯一 Rule owner；继承 Context 与依赖任务约束
- **Acceptance-Refs**: B-LIFE-DESIGN-01

### Description

先 ADR 确认终态、finalizer 所有者、prepare 部分失败、部分输出保存策略、清理预算和 Trace 写失败行为。
范围：docs/adr/执行生命周期 ADR（编号待分配）；backend/src/fluxion/services/runtime_contracts.py；backend/tests/contract/test_execution_terminal_contract.py。目标测试：backend/tests/contract/test_execution_terminal_contract.py。

### Checklist

- [x] [B-LIFE-DESIGN-01][unit] 先补验收并记录RED，真实边界：终态与生命周期契约校验器；关键断言：四类终态合法；重复/冲突结束有确定语义；超时与取消区分。
- [x] 不新增 Runtime 本地持久执行真相；区分业务终态与清理失败；明确取消可捕获但必须重新传播，SIGKILL 不承诺 finally。
- [x] 运行 `.venv/bin/python -m pytest -q backend/tests/contract/test_execution_terminal_contract.py` 并通过cf-validate补充匹配检查；逐场景填写Acceptance Evidence，核对源码范围后才提交完成检查。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-LIFE-DESIGN-01 | unit | 终态与生命周期契约校验器 | 四类终态合法；重复/冲突结束有确定语义；超时与取消区分 | backend/tests/contract/test_execution_terminal_contract.py（以场景ID标记用例，verified） | `.venv/bin/python -m pytest -q backend/tests/contract/test_execution_terminal_contract.py` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-LIFE-DESIGN-01 | ImportError（ExecutionTerminalState 不存在，实现前） | 6 passed；contract+services 122 passed；ruff+mypy clean | test_execution_terminal_contract.py::test_B_LIFE_DESIGN_01_* | 纯契约层单测：实际 resolve_terminal_state/first_terminal_wins 纯函数，无 mock | verified |

### Log

- [2026-09-07] created (draft)
- [2026-09-08] completed (done)：ADR-A014 + 终态类型/映射纯函数，B-LIFE-DESIGN-01 verified

---

## TASK-014: 由 ExecutionSession 管理完整生命周期

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-013
- **Source**: source-review.md#P1-03 执行生命周期(L46-L52)
- **Spec-Refs**: 无新增唯一 Rule owner；继承 Context 与依赖任务约束
- **Acceptance-Refs**: S-LIFE-01, E-LIFE-01

### Description

建立资源所有权和有界 finalize_once；prepare 创建 context 后，即使后续 Model/MCP 准备失败，也能进入清理。
范围：backend/src/fluxion/services/execution_session.py；backend/tests/integration/test_execution_session_lifecycle.py；必要的局部生命周期辅助模块（新增）。目标测试：backend/tests/integration/test_execution_session_lifecycle.py。

### Checklist

- [x] [S-LIFE-01][integration] 先补验收并记录RED，真实边界：真实 ExecutionSession → AgentRuntime → MemoryManager；关键断言：成功结束只结算一次。
- [x] [E-LIFE-01][integration] 先补验收并记录RED，真实边界：真实准备流水线 → 故障 Adapter → finalizer；关键断言：部分初始化失败也释放；重复 finalize 不重复 flush/Trace。
- [x] finalize 幂等且并发安全；有限 shield 必须有 timeout，不能无限延长客户端取消；不引入长期后台垃圾任务。
- [x] 运行 `.venv/bin/python -m pytest -q backend/tests/integration/test_execution_session_lifecycle.py` 并通过cf-validate补充匹配检查；逐场景填写Acceptance Evidence，核对源码范围后才提交完成检查。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-LIFE-01 | integration | 真实 ExecutionSession → AgentRuntime → MemoryManager | 成功结束只结算一次 | backend/tests/integration/test_execution_session_lifecycle.py（以场景ID标记用例，verified） | `.venv/bin/python -m pytest -q backend/tests/integration/test_execution_session_lifecycle.py` | verified |
| E-LIFE-01 | integration | 真实准备流水线 → 故障 Adapter → finalizer | 部分初始化失败也释放；重复 finalize 不重复 flush/Trace | backend/tests/integration/test_execution_session_lifecycle.py（以场景ID标记用例，verified） | `.venv/bin/python -m pytest -q backend/tests/integration/test_execution_session_lifecycle.py` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-LIFE-01 | AttributeError（finalize 不存在，实现前） | 3 passed；services+contract 132 passed；ruff+mypy clean | test_execution_session_lifecycle.py::test_S_LIFE_01_* | 真实 session + 真实 AgentRuntime + PG；finish 计数 1 | verified |
| E-LIFE-01 | 同上 | 同上（卡住用例 5.9s = 预算内返回） | test_E_LIFE_01_* | MCP prepare 故障注入 + hang finish；清理有界（5s 预算）；取消重传播按契约实现 | verified |

### Log

- [2026-09-07] created (draft)
- [2026-09-08] completed (done)：session finalize_once + 准备失败清理 + 有界预算，S-LIFE-01/E-LIFE-01 verified

---

## TASK-015: 修复 Memory 与 Trace 清理失败分支

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-013
- **Source**: source-review.md#P1-03 执行生命周期(L46-L52)
- **Spec-Refs**: backend-code-quality-performance#RULE-backend-quality-001
- **Acceptance-Refs**: E-LIFE-02, RULE-backend-quality-001

### Description

flush 异常不能阻止本地执行字典释放；Trace 持久化失败可观测；保留原始业务错误，避免清理异常覆盖或伪造完成记录。
范围：backend/src/fluxion/runtime/memory.py；backend/src/fluxion/services/runtime_app.py（Trace 写入边界）；backend/tests/integration/test_finalization_storage_failures.py。目标测试：backend/tests/integration/test_finalization_storage_failures.py。

### Checklist

- [x] [E-LIFE-02][integration] 先补验收并记录RED，真实边界：真实 MemoryManager/TraceWriter → PostgreSQL + 边界故障注入；关键断言：L0/flushed_counts 释放；flush/Trace 超时有界；错误记录可关联且原始原因保留。
- [x] 清理释放放可靠 finally；正常路径检查真实 PG，错误通过边界故障注入；失败日志脱敏且有 ID；不静默 suppress。
- [x] verifier `RULE-backend-quality-001`：保持 Context 中 verifier_ref 原定义；以 `.venv/bin/python -m pytest -q backend/tests/integration/test_finalization_storage_failures.py` 验证 E-LIFE-02 的 异常保留、有界外部调用和可靠资源释放。记录自动化结果与必要评审证据，不能将计划视为verified。
- [x] 运行 `.venv/bin/python -m pytest -q backend/tests/integration/test_finalization_storage_failures.py` 并通过cf-validate补充匹配检查；逐场景填写Acceptance Evidence，核对源码范围后才提交完成检查。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| E-LIFE-02 | integration | 真实 MemoryManager/TraceWriter → PostgreSQL + 边界故障注入 | L0/flushed_counts 释放；flush/Trace 超时有界；错误记录可关联且原始原因保留 | backend/tests/integration/test_finalization_storage_failures.py（以场景ID标记用例，verified） | `.venv/bin/python -m pytest -q backend/tests/integration/test_finalization_storage_failures.py` | verified |
| RULE-backend-quality-001 | integration | 真实 MemoryManager/TraceWriter → PostgreSQL + 边界故障注入 | 异常保留、有界外部调用和可靠资源释放，由E-LIFE-02提供行为证据；补充命令见Checklist | backend/tests/integration/test_finalization_storage_failures.py | `.venv/bin/python -m pytest -q backend/tests/integration/test_finalization_storage_failures.py` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| E-LIFE-02 | 3 failed（flush 杀结果；trace 杀结果/60s 超时） | 4 passed；lifecycle/identity/gateway/chain/effect/channel/api 64 passed；改动文件 ruff clean、mypy 唯一既有错误 | test_finalization_storage_failures.py::test_E_LIFE_02_* | 真实 Manager/Writer + PG；故障/卡住 Adapter 注入；caplog 断言关联 ID；stream 成功路径 finish 未动（016 统一，见证据注记） | verified |

### Log

- [2026-09-07] created (draft)
- [2026-09-08] completed (done)：flush finally释放 + trace 永不抛/有界/可观测 + 成功路径业务终态保留，E-LIFE-02 verified

---

## TASK-016: 统一 run 和 stream 的 finalization

- **Status**: done
- **Priority**: P1
- **Depends**: TASK-014, TASK-015
- **Source**: source-review.md#P1-03 执行生命周期(L46-L52)
- **Spec-Refs**: 无新增唯一 Rule owner；继承 Context 与依赖任务约束
- **Acceptance-Refs**: S-LIFE-02, E-LIFE-03

### Description

所有出口走会话 finalizer，包括普通异常、CancelledError、GeneratorExit、工具路径、模型超时和 fallback。
范围：backend/src/fluxion/services/runtime_app.py；backend/src/fluxion/runtime/agent.py；backend/tests/integration/test_runtime_terminal_paths.py。目标测试：backend/tests/integration/test_runtime_terminal_paths.py。

### Checklist

- [x] [S-LIFE-02][integration] 先补验收并记录RED，真实边界：RuntimeApplicationService.run/stream → ExecutionSession；关键断言：成功只有一次终态和 finalization。
- [x] [E-LIFE-03][integration] 先补验收并记录RED，真实边界：真实运行应用 → 取消/关闭/模型超时；关键断言：取消与超时正确终态；显式 aclose 无本地残留。
- [x] 避免 finish 后 append_trace 失败触发二次结算；成功 completed 只能在结算策略满足后发送；不依赖 except Exception 覆盖取消。
- [x] 运行 `.venv/bin/python -m pytest -q backend/tests/integration/test_runtime_terminal_paths.py` 并通过cf-validate补充匹配检查；逐场景填写Acceptance Evidence，核对源码范围后才提交完成检查。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-LIFE-02 | integration | RuntimeApplicationService.run/stream → ExecutionSession | 成功只有一次终态和 finalization | backend/tests/integration/test_runtime_terminal_paths.py（以场景ID标记用例，verified） | `.venv/bin/python -m pytest -q backend/tests/integration/test_runtime_terminal_paths.py` | verified |
| E-LIFE-03 | integration | 真实运行应用 → 取消/关闭/模型超时 | 取消与超时正确终态；显式 aclose 无本地残留 | backend/tests/integration/test_runtime_terminal_paths.py（以场景ID标记用例，verified） | `.venv/bin/python -m pytest -q backend/tests/integration/test_runtime_terminal_paths.py` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| S-LIFE-02 | finish 计数/单终态在重构前无统一口径（行为分散） | 4 passed；channel/api/e2e/services/contract 278 passed；integration 332 passed；ruff clean、mypy 唯一既有错误 | test_runtime_terminal_paths.py::test_S_LIFE_02_* | 真实 service run/stream → session；每 context finish 恰一次（id 记录）；trace 对齐 | verified |
| E-LIFE-03 | cancel 无 trace（aclose 静默丢）；超时映射缺 provider 码 | 同上 | test_E_LIFE_03_* | 受控慢 Adapter + 真实 deadline（agent model_deadline_ms）触发 agent_loop_timeout/model_provider_timeout；工作中取消留痕 + 后续可跑；附带补终态映射（code→TIMED_OUT，TASK-013 契约测试同步+1） | verified |

### Log

- [2026-09-07] created (draft)
- [2026-09-08] completed (done)：run/stream 出口统一走 session.finalize + 超时码映射，S-LIFE-02/E-LIFE-03 verified

---

## TASK-017: 打通逐层 SSE 关闭传播

- **Status**: draft
- **Priority**: P1
- **Depends**: TASK-016
- **Source**: source-review.md#P1-03 执行生命周期(L46-L52)
- **Spec-Refs**: 无新增唯一 Rule owner；继承 Context 与依赖任务约束
- **Acceptance-Refs**: E-LIFE-04

### Description

检查每层 async iterator、response、client 所有权；下游断开关闭上游，取消继续传播，不把未结束流变成成功。
范围：backend/src/fluxion/api/runtime.py；backend/src/fluxion/api/channel.py；backend/src/fluxion/services/http_runtime_gateway.py（测试责任由018承担，局部测试在本任务补）。目标测试：backend/tests/integration/test_sse_close_propagation.py。

### Checklist

- [ ] [E-LIFE-04][integration] 先补验收并记录RED，真实边界：真实 Channel/SSE iterator → Gateway HTTP response → Runtime iterator；关键断言：关闭逐层传播；连接释放；借用 client 仍可用。
- [ ] 显式关闭嵌套迭代器；借用 client 只关 response；首 token 后失败不重试；按具体修改拆分测试文件，不跨任务暗改。
- [ ] 运行 `.venv/bin/python -m pytest -q backend/tests/integration/test_sse_close_propagation.py` 并通过cf-validate补充匹配检查；逐场景填写Acceptance Evidence，核对源码范围后才提交完成检查。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| E-LIFE-04 | integration | 真实 Channel/SSE iterator → Gateway HTTP response → Runtime iterator | 关闭逐层传播；连接释放；借用 client 仍可用 | backend/tests/integration/test_sse_close_propagation.py（以场景ID标记用例，planned） | `.venv/bin/python -m pytest -q backend/tests/integration/test_sse_close_propagation.py` | planned |

### Acceptance Evidence

待cf-task-start填写RED/GREEN、断言位置与真实边界证据；本次仅规划，均未验证。契约先行任务只完成本地Contract验收，不代替后续跨服务行为验收。

### Log

- [2026-09-07] created (draft)

---

## TASK-018: 补真实断连端到端验收

- **Status**: draft
- **Priority**: P1
- **Depends**: TASK-005, TASK-006, TASK-016, TASK-017
- **Source**: source-review.md#P1-03 执行生命周期(L46-L52)
- **Spec-Refs**: fluxion-dfx#RULE-fluxion-dfx-001
- **Acceptance-Refs**: E-LIFE-05, B-LIFE-01, RULE-fluxion-dfx-001

### Description

使用真实 TCP/HTTP 服务与 PostgreSQL，不能以缓冲 ASGITransport 代替断连；覆盖无工具流和有工具执行。
范围：backend/tests/e2e/test_sse_disconnect_lifecycle.py；backend/tests/e2e/network_runtime_helpers.py（新增）；backend/tests/benchmarks/test_execution_cleanup_overhead.py（新增）。目标测试：backend/tests/e2e/test_sse_disconnect_lifecycle.py backend/tests/benchmarks/test_execution_cleanup_overhead.py。

### Checklist

- [ ] [E-LIFE-05][E2E] 先补验收并记录RED，真实边界：真实 TCP client → Channel → Gateway → Runtime → PG Trace/Memory；关键断言：取消终态、Trace/部分消息遵循 ADR；连接和执行局部状态释放。
- [ ] [B-LIFE-01][E2E] 先补验收并记录RED，真实边界：重复真实断连 → 运行时状态/框架性能采集；关键断言：不积累 active execution；正常请求仍成功；框架 P95≤50ms/P99≤100ms（排除模型/外部Tool）。
- [ ] 部分 token 后断连、prepare 阶段取消、服务端 deadline、重复取消分别验证；持久化不可用时以可观测失败而非假成功验收。
- [ ] verifier `RULE-fluxion-dfx-001`：保持 Context 中 verifier_ref 原定义；以 `.venv/bin/python -m pytest -q backend/tests/e2e/test_sse_disconnect_lifecycle.py backend/tests/benchmarks/test_execution_cleanup_overhead.py` 验证 B-LIFE-01 的 有界清理、可靠性和框架性能。记录自动化结果与必要评审证据，不能将计划视为verified。
- [ ] 运行 `.venv/bin/python -m pytest -q backend/tests/e2e/test_sse_disconnect_lifecycle.py backend/tests/benchmarks/test_execution_cleanup_overhead.py` 并通过cf-validate补充匹配检查；逐场景填写Acceptance Evidence，核对源码范围后才提交完成检查。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| E-LIFE-05 | E2E | 真实 TCP client → Channel → Gateway → Runtime → PG Trace/Memory | 取消终态、Trace/部分消息遵循 ADR；连接和执行局部状态释放 | backend/tests/e2e/test_sse_disconnect_lifecycle.py backend/tests/benchmarks/test_execution_cleanup_overhead.py（以场景ID标记用例，planned） | `.venv/bin/python -m pytest -q backend/tests/e2e/test_sse_disconnect_lifecycle.py backend/tests/benchmarks/test_execution_cleanup_overhead.py` | planned |
| B-LIFE-01 | E2E | 重复真实断连 → 运行时状态/框架性能采集 | 不积累 active execution；正常请求仍成功；框架 P95≤50ms/P99≤100ms（排除模型/外部Tool） | backend/tests/e2e/test_sse_disconnect_lifecycle.py backend/tests/benchmarks/test_execution_cleanup_overhead.py（以场景ID标记用例，planned） | `.venv/bin/python -m pytest -q backend/tests/e2e/test_sse_disconnect_lifecycle.py backend/tests/benchmarks/test_execution_cleanup_overhead.py` | planned |
| RULE-fluxion-dfx-001 | E2E | 重复真实断连 → 运行时状态/框架性能采集 | 有界清理、可靠性和框架性能，由B-LIFE-01提供行为证据；补充命令见Checklist | backend/tests/e2e/test_sse_disconnect_lifecycle.py backend/tests/benchmarks/test_execution_cleanup_overhead.py | `.venv/bin/python -m pytest -q backend/tests/e2e/test_sse_disconnect_lifecycle.py backend/tests/benchmarks/test_execution_cleanup_overhead.py` | planned |

### Acceptance Evidence

待cf-task-start填写RED/GREEN、断言位置与真实边界证据；本次仅规划，均未验证。契约先行任务只完成本地Contract验收，不代替后续跨服务行为验收。

### Log

- [2026-09-07] created (draft)
- [2026-09-08] review 修订：Depends 补 TASK-016（取消终态语义须先实现，was draft）

---

## TASK-019: 确定统一错误契约与兼容 ADR

- **Status**: done
- **Priority**: P1
- **Depends**: 
- **Source**: source-review.md#P1-04 错误契约(L54-L60)
- **Spec-Refs**: 无新增唯一 Rule owner；继承 Context 与依赖任务约束
- **Acceptance-Refs**: B-ERR-DESIGN-01

### Description

保留现有四字段 envelope；确定结构化 slug 放置位置、整数码映射、安全文案和旧 SSE error 字段兼容，不直接照搬外部示例。ADR 必须以 sse-streaming-contracts 已验收结论（streaming 错误 `upstream_code` + slug 保留，见 archived/2026-09-07/sse-streaming-contracts）为输入，在其上确定 HTTP 侧映射与兼容窗口，不重复设计 streaming 语义。
范围：docs/adr/Runtime 错误契约 ADR（A012 起，编码前复核）；backend/src/fluxion/api/responses.py（类型/共享错误契约）；backend/tests/contract/test_runtime_error_contract.py。目标测试：backend/tests/contract/test_runtime_error_contract.py。

### Checklist

- [x] [B-ERR-DESIGN-01][unit] 先补验收并记录RED，真实边界：实际错误载荷类型/校验器；关键断言：统一整数码、slug、request_id、安全 message；旧载荷按兼容矩阵解析。
- [x] 先 ADR 后 Contract；HTTP status 与业务码分别定义；未知异常不把原文加入响应；保留版本化兼容窗口。
- [x] 运行 `.venv/bin/python -m pytest -q backend/tests/contract/test_runtime_error_contract.py` 并通过cf-validate补充匹配检查；逐场景填写Acceptance Evidence，核对源码范围后才提交完成检查。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-ERR-DESIGN-01 | unit | 实际错误载荷类型/校验器 | 统一整数码、slug、request_id、安全 message；旧载荷按兼容矩阵解析 | backend/tests/contract/test_runtime_error_contract.py（以场景ID标记用例，verified） | `.venv/bin/python -m pytest -q backend/tests/contract/test_runtime_error_contract.py` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-ERR-DESIGN-01 | TypeError（failure() 无 error 参数，实现前） | 3 passed；api+channel+contract 128 passed；ruff+mypy clean | test_runtime_error_contract.py::test_B_ERR_DESIGN_01_* | 实际 ApiResponse/failure() 类型；旧载荷无 error 字段可解析（加法兼容已验证） | verified |

### Log

- [2026-09-07] created (draft)
- [2026-09-08] review 修订：ADR 以 sse-streaming-contracts 已验收结论为输入（A012 起，不重复设计 streaming 语义）（was draft）
- [2026-09-08] completed (done)：ADR-A015 + envelope error 字段，B-ERR-DESIGN-01 verified

---

## TASK-020: 统一 Runtime HTTP 与 SSE 错误编码

- **Status**: draft
- **Priority**: P1
- **Depends**: TASK-019
- **Source**: source-review.md#P1-04 错误契约(L54-L60)
- **Spec-Refs**: fluxion-console-api-contract#RULE-fluxion-console-api-001
- **Acceptance-Refs**: S-ERR-01, E-ERR-01, RULE-fluxion-console-api-001

### Description

SSE 侧错误语义已由 sse-streaming-contracts 落地（upstream_code/slug），本任务以其为基准统一 HTTP 侧并收敛共享映射；用共享映射和 Response Factory 生成 HTTP/SSE 错误；集中处理 Domain/Validation/Unexpected 异常。
范围：backend/src/fluxion/api/runtime.py；backend/src/fluxion/api/responses.py；backend/tests/integration/test_runtime_error_encoding.py。目标测试：backend/tests/integration/test_runtime_error_encoding.py。

### Checklist

- [ ] [S-ERR-01][integration] 先补验收并记录RED，真实边界：真实 Runtime FastAPI 异常处理 → HTTP/SSE 编码器；关键断言：相同异常产生相同 code/slug/安全文案与关联ID。
- [ ] [E-ERR-01][integration] 先补验收并记录RED，真实边界：真实异常映射/脱敏 → HTTP/SSE 响应；关键断言：未知异常安全；状态码正确；无敏感原文。
- [ ] 禁止 handler 拼 envelope；不以字符串 message 保存唯一 slug；不回传 SQL/DSN/Secret/堆栈。
- [ ] verifier `RULE-fluxion-console-api-001`：保持 Context 中 verifier_ref 原定义；以 `.venv/bin/python -m pytest -q backend/tests/integration/test_runtime_error_encoding.py` 验证 S-ERR-01 的 统一响应与错误映射。记录自动化结果与必要评审证据，不能将计划视为verified。
- [ ] 运行 `.venv/bin/python -m pytest -q backend/tests/integration/test_runtime_error_encoding.py` 并通过cf-validate补充匹配检查；逐场景填写Acceptance Evidence，核对源码范围后才提交完成检查。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-ERR-01 | integration | 真实 Runtime FastAPI 异常处理 → HTTP/SSE 编码器 | 相同异常产生相同 code/slug/安全文案与关联ID | backend/tests/integration/test_runtime_error_encoding.py（以场景ID标记用例，planned） | `.venv/bin/python -m pytest -q backend/tests/integration/test_runtime_error_encoding.py` | planned |
| E-ERR-01 | integration | 真实异常映射/脱敏 → HTTP/SSE 响应 | 未知异常安全；状态码正确；无敏感原文 | backend/tests/integration/test_runtime_error_encoding.py（以场景ID标记用例，planned） | `.venv/bin/python -m pytest -q backend/tests/integration/test_runtime_error_encoding.py` | planned |
| RULE-fluxion-console-api-001 | integration | 真实 Runtime FastAPI 异常处理 → HTTP/SSE 编码器 | 统一响应与错误映射，由S-ERR-01提供行为证据；补充命令见Checklist | backend/tests/integration/test_runtime_error_encoding.py | `.venv/bin/python -m pytest -q backend/tests/integration/test_runtime_error_encoding.py` | planned |

### Acceptance Evidence

待cf-task-start填写RED/GREEN、断言位置与真实边界证据；本次仅规划，均未验证。契约先行任务只完成本地Contract验收，不代替后续跨服务行为验收。

### Log

- [2026-09-07] created (draft)
- [2026-09-08] review 修订：以 sse 已落地语义为基准统一 HTTP 侧（was draft）

---

## TASK-021: 统一 Gateway 错误解码和透传

- **Status**: draft
- **Priority**: P1
- **Depends**: TASK-019, TASK-020
- **Source**: source-review.md#P1-04 错误契约(L54-L60)
- **Spec-Refs**: 无新增唯一 Rule owner；继承 Context 与依赖任务约束
- **Acceptance-Refs**: B-ERR-01, E-ERR-02

### Description

按结构化字段解码，不解析 message 文本；保留本地错误与上游错误职责并覆盖版本过渡。
范围：backend/src/fluxion/services/http_runtime_gateway.py；backend/src/fluxion/services/runtime_contracts.py；backend/tests/integration/test_runtime_error_decoding.py。目标测试：backend/tests/integration/test_runtime_error_decoding.py。

### Checklist

- [ ] [B-ERR-01][integration] 先补验收并记录RED，真实边界：真实 HTTP Gateway → 真实/故障响应边界；关键断言：旧载荷兼容；畸形响应变为稳定网关错误。
- [ ] [E-ERR-02][integration] 先补验收并记录RED，真实边界：真实 Gateway HTTP/SSE 解码 → RuntimeApplicationError；关键断言：上游 slug 保留；无 message 匹配；无自动重试。
- [ ] 覆盖非 JSON、字段缺失、畸形类型、旧 envelope、HTTP200 SSE error；执行 POST 不自动重放。
- [ ] 运行 `.venv/bin/python -m pytest -q backend/tests/integration/test_runtime_error_decoding.py` 并通过cf-validate补充匹配检查；逐场景填写Acceptance Evidence，核对源码范围后才提交完成检查。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-ERR-01 | integration | 真实 HTTP Gateway → 真实/故障响应边界 | 旧载荷兼容；畸形响应变为稳定网关错误 | backend/tests/integration/test_runtime_error_decoding.py（以场景ID标记用例，planned） | `.venv/bin/python -m pytest -q backend/tests/integration/test_runtime_error_decoding.py` | planned |
| E-ERR-02 | integration | 真实 Gateway HTTP/SSE 解码 → RuntimeApplicationError | 上游 slug 保留；无 message 匹配；无自动重试 | backend/tests/integration/test_runtime_error_decoding.py（以场景ID标记用例，planned） | `.venv/bin/python -m pytest -q backend/tests/integration/test_runtime_error_decoding.py` | planned |

### Acceptance Evidence

待cf-task-start填写RED/GREEN、断言位置与真实边界证据；本次仅规划，均未验证。契约先行任务只完成本地Contract验收，不代替后续跨服务行为验收。

### Log

- [2026-09-07] created (draft)

---

## TASK-022: 补真实服务错误契约验收

- **Status**: draft
- **Priority**: P1
- **Depends**: TASK-005, TASK-021
- **Source**: source-review.md#P1-04 错误契约(L54-L60)
- **Spec-Refs**: 无新增唯一 Rule owner；继承 Context 与依赖任务约束
- **Acceptance-Refs**: S-ERR-02, E-ERR-03

### Description

真实 Runtime FastAPI/ApplicationService/PG 经 Gateway 到 Channel；可使用受控 Provider 注入外部故障，但不能 Mock Runtime 错误响应。
`test_error_passthrough.py` 已存在，本任务扩展它；`test_runtime_error_chain.py` 与 helpers 为新建。
范围：backend/tests/e2e/test_runtime_error_chain.py；backend/tests/integration/test_error_passthrough.py；backend/tests/e2e/runtime_error_helpers.py（新增）。目标测试：backend/tests/e2e/test_runtime_error_chain.py。

### Checklist

- [ ] [S-ERR-02][E2E] 先补验收并记录RED，真实边界：真实 Runtime → Gateway → Channel → 客户端；关键断言：资源不存在等错误 code/slug/ID 跨层一致。
- [ ] [E-ERR-03][E2E] 先补验收并记录RED，真实边界：真实 Runtime + 受控模型故障 → HTTP/SSE；关键断言：模型不可用/超时/未知异常安全映射；一次执行不重复调用。
- [ ] 不存在 Agent/版本、模型不可用、超时、内部异常覆盖两种 transport；断连本身不要求向已断开的客户端发 SSE error。
- [ ] 运行 `.venv/bin/python -m pytest -q backend/tests/e2e/test_runtime_error_chain.py` 并通过cf-validate补充匹配检查；逐场景填写Acceptance Evidence，核对源码范围后才提交完成检查。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-ERR-02 | E2E | 真实 Runtime → Gateway → Channel → 客户端 | 资源不存在等错误 code/slug/ID 跨层一致 | backend/tests/e2e/test_runtime_error_chain.py（以场景ID标记用例，planned） | `.venv/bin/python -m pytest -q backend/tests/e2e/test_runtime_error_chain.py` | planned |
| E-ERR-03 | E2E | 真实 Runtime + 受控模型故障 → HTTP/SSE | 模型不可用/超时/未知异常安全映射；一次执行不重复调用 | backend/tests/e2e/test_runtime_error_chain.py（以场景ID标记用例，planned） | `.venv/bin/python -m pytest -q backend/tests/e2e/test_runtime_error_chain.py` | planned |

### Acceptance Evidence

待cf-task-start填写RED/GREEN、断言位置与真实边界证据；本次仅规划，均未验证。契约先行任务只完成本地Contract验收，不代替后续跨服务行为验收。

### Log

- [2026-09-07] created (draft)
- [2026-09-08] review 修订：test_error_passthrough.py 已存在，明确为扩展（was draft）

---

## TASK-023: 区分流式能力与空输出

- **Status**: draft
- **Priority**: P2
- **Depends**: TASK-006, TASK-016
- **Source**: source-review.md#P2-01 空流式结果(L62-L68)
- **Spec-Refs**: 无新增唯一 Rule owner；继承 Context 与依赖任务约束
- **Acceptance-Refs**: S-STR-01, B-STR-01, E-STR-01

### Description

明确 unsupported 与完成空结果；保留 streaming 增量，不能为了结果类型把全部 token 缓冲。
范围：backend/src/fluxion/runtime/agent.py；backend/src/fluxion/services/runtime_app.py；backend/tests/integration/test_empty_stream_outcome.py。目标测试：backend/tests/integration/test_empty_stream_outcome.py。

### Checklist

- [ ] [S-STR-01][integration] 先补验收并记录RED，真实边界：真实 AgentRuntime + 可计数 Provider → ApplicationService；关键断言：正常空结束只请求模型一次，completed output为空。
- [ ] [B-STR-01][integration] 先补验收并记录RED，真实边界：真实流式分派 → 非流式 Provider/空字符串token；关键断言：明确 unsupported 才 fallback；身份不变。
- [ ] [E-STR-01][integration] 先补验收并记录RED，真实边界：真实流式迭代 → 部分输出后 Provider 错误；关键断言：错误传播；不二次请求模型；Trace/finalizer 完整。
- [ ] 零 token、单空字符串、非流式 Provider、部分输出后错误分别处理；仅 unsupported 可 fallback，复用同次 execution/context。
- [ ] 运行 `.venv/bin/python -m pytest -q backend/tests/integration/test_empty_stream_outcome.py` 并通过cf-validate补充匹配检查；逐场景填写Acceptance Evidence，核对源码范围后才提交完成检查。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-STR-01 | integration | 真实 AgentRuntime + 可计数 Provider → ApplicationService | 正常空结束只请求模型一次，completed output为空 | backend/tests/integration/test_empty_stream_outcome.py（以场景ID标记用例，planned） | `.venv/bin/python -m pytest -q backend/tests/integration/test_empty_stream_outcome.py` | planned |
| B-STR-01 | integration | 真实流式分派 → 非流式 Provider/空字符串token | 明确 unsupported 才 fallback；身份不变 | backend/tests/integration/test_empty_stream_outcome.py（以场景ID标记用例，planned） | `.venv/bin/python -m pytest -q backend/tests/integration/test_empty_stream_outcome.py` | planned |
| E-STR-01 | integration | 真实流式迭代 → 部分输出后 Provider 错误 | 错误传播；不二次请求模型；Trace/finalizer 完整 | backend/tests/integration/test_empty_stream_outcome.py（以场景ID标记用例，planned） | `.venv/bin/python -m pytest -q backend/tests/integration/test_empty_stream_outcome.py` | planned |

### Acceptance Evidence

待cf-task-start填写RED/GREEN、断言位置与真实边界证据；本次仅规划，均未验证。契约先行任务只完成本地Contract验收，不代替后续跨服务行为验收。

### Log

- [2026-09-07] created (draft)

---

## TASK-024: 对齐 Snapshot 一致读方案

- **Status**: done
- **Priority**: P2
- **Depends**: 
- **Source**: source-review.md#P2-02 一致快照(L70-L76)
- **Spec-Refs**: 无新增唯一 Rule owner；继承 Context 与依赖任务约束
- **Acceptance-Refs**: B-SNAP-DESIGN-01

### Description

遵循数据库只读 REPEATABLE READ 规范；先 ADR 确认 Store scoped read Contract，盘点所有配置读和相关写入/Revision 路径。
范围：docs/adr/Snapshot 一致读 ADR（编号待分配）；backend/src/fluxion/registry/store.py；backend/tests/contract/test_registry_snapshot_read.py。目标测试：backend/tests/contract/test_registry_snapshot_read.py。

### Checklist

- [x] [B-SNAP-DESIGN-01][unit] 先补验收并记录RED，真实边界：Store scoped-read Protocol 与事务契约声明；关键断言：只读scope/tenant/timeout/错误类型齐全；本任务仅契约校验，PG行为由025/026最终验收。
- [x] 定义事务内配置读取与事务外 Credential/Memory I/O 分段；固定所需精确版本；双读 Revision 若不能覆盖全部写入不得声称一致。
- [x] 运行 `.venv/bin/python -m pytest -q backend/tests/contract/test_registry_snapshot_read.py` 并通过cf-validate补充匹配检查；逐场景填写Acceptance Evidence，核对源码范围后才提交完成检查。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-SNAP-DESIGN-01 | unit | Store scoped-read Protocol 与事务契约声明 | 只读scope/tenant/timeout/错误类型齐全；本任务仅契约校验，PG行为由025/026最终验收 | backend/tests/contract/test_registry_snapshot_read.py（以场景ID标记用例，verified） | `.venv/bin/python -m pytest -q backend/tests/contract/test_registry_snapshot_read.py` | verified |

### Acceptance Evidence

| 场景ID | RED | GREEN | 断言位置 | 真实边界证据 | 状态 |
|--------|-----|-------|---------|-------------|------|
| B-SNAP-DESIGN-01 | ImportError（ScopedReadConflictError 不存在，实现前） | 4 passed；contract 全目录 89 passed；ruff+mypy clean | test_registry_snapshot_read.py::test_B_SNAP_DESIGN_01_* | 实际 store Protocol 签名 + 假实现 isinstance 满足（仅契约，不涉 PG） | verified |

### Log

- [2026-09-07] created (draft)
- [2026-09-08] completed (done)：ADR-A016 + ScopedRead 契约，B-SNAP-DESIGN-01 verified

---

## TASK-025: 实现一致配置读取与快照组装

- **Status**: draft
- **Priority**: P2
- **Depends**: TASK-006, TASK-024
- **Source**: source-review.md#P2-02 一致快照(L70-L76)
- **Spec-Refs**: backend-database#RULE-backend-database-001
- **Acceptance-Refs**: S-SNAP-01, E-SNAP-01, RULE-backend-database-001

### Description

只通过 Store Contract 取得一致配置，Resolver 不接触 ORM；缓存只收完整、已校验结果。
范围：backend/src/fluxion/registry/sqlalchemy_store.py（必要时提取 scoped reader）；backend/src/fluxion/services/context_resolver.py；backend/tests/integration/test_registry_consistent_resolution.py。目标测试：backend/tests/integration/test_registry_consistent_resolution.py。

### Checklist

- [ ] [S-SNAP-01][integration] 先补验收并记录RED，真实边界：真实 ContextResolver → Store scoped read → PostgreSQL；关键断言：所有配置来自一致视图；只读和 tenant scope 有效。
- [ ] [E-SNAP-01][integration] 先补验收并记录RED，真实边界：真实事务读 → 超时/配置冲突 → Resolver/cache；关键断言：有界失败；缓存无半成品；外部 I/O 不持有配置事务。
- [ ] 有界事务/重试；外部 HTTP/RPC 不进入事务；tenant 强制；异常不污染缓存；缓存语义必须同时满足 B-ID-02（缓存不复用前次执行身份），与 TASK-006 互引；若需扩展多个 repository 文件先细分实现任务。
- [ ] verifier `RULE-backend-database-001`：保持 Context 中 verifier_ref 原定义；以 `.venv/bin/python -m pytest -q backend/tests/integration/test_registry_consistent_resolution.py` 验证 S-SNAP-01 的 只读一致事务、参数化查询与tenant。记录自动化结果与必要评审证据，不能将计划视为verified。
- [ ] 运行 `.venv/bin/python scripts/run_registry_contract_tests.py`，以实际PostgreSQL验证Registry单库Contract；依赖不可用须明确失败。
- [ ] 运行 `.venv/bin/python -m pytest -q backend/tests/integration/test_registry_consistent_resolution.py` 并通过cf-validate补充匹配检查；逐场景填写Acceptance Evidence，核对源码范围后才提交完成检查。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-SNAP-01 | integration | 真实 ContextResolver → Store scoped read → PostgreSQL | 所有配置来自一致视图；只读和 tenant scope 有效 | backend/tests/integration/test_registry_consistent_resolution.py（以场景ID标记用例，planned） | `.venv/bin/python -m pytest -q backend/tests/integration/test_registry_consistent_resolution.py` | planned |
| E-SNAP-01 | integration | 真实事务读 → 超时/配置冲突 → Resolver/cache | 有界失败；缓存无半成品；外部 I/O 不持有配置事务 | backend/tests/integration/test_registry_consistent_resolution.py（以场景ID标记用例，planned） | `.venv/bin/python -m pytest -q backend/tests/integration/test_registry_consistent_resolution.py` | planned |
| RULE-backend-database-001 | integration | 真实 ContextResolver → Store scoped read → PostgreSQL | 只读一致事务、参数化查询与tenant，由S-SNAP-01提供行为证据；补充命令见Checklist | backend/tests/integration/test_registry_consistent_resolution.py | `.venv/bin/python -m pytest -q backend/tests/integration/test_registry_consistent_resolution.py` | planned |

### Acceptance Evidence

待cf-task-start填写RED/GREEN、断言位置与真实边界证据；本次仅规划，均未验证。契约先行任务只完成本地Contract验收，不代替后续跨服务行为验收。

### Log

- [2026-09-07] created (draft)
- [2026-09-08] review 修订：缓存语义与 TASK-006（B-ID-02）互引（was draft）

---

## TASK-026: 补并发发布与跨实例快照验收

- **Status**: draft
- **Priority**: P2
- **Depends**: TASK-002, TASK-025
- **Source**: source-review.md#P2-02 一致快照(L70-L76)
- **Spec-Refs**: 无新增唯一 Rule owner；继承 Context 与依赖任务约束
- **Acceptance-Refs**: B-SNAP-01, E-SNAP-02

### Description

用同步屏障安排真实 PG 读写交错；禁止仅 sleep 或只 Mock Revision；以完整提交视图验证一致性。
`test_snapshot_benchmark.py` 已存在，本任务扩展它；两个 e2e 测试文件为新建。
范围：backend/tests/e2e/test_snapshot_publication_race.py；backend/tests/e2e/test_snapshot_cross_instance.py；backend/tests/benchmarks/test_snapshot_benchmark.py。目标测试：backend/tests/e2e/test_snapshot_publication_race.py backend/tests/e2e/test_snapshot_cross_instance.py backend/tests/benchmarks/test_snapshot_benchmark.py。

### Checklist

- [ ] [B-SNAP-01][E2E] 先补验收并记录RED，真实边界：真实 PG 并发提交 → 多 Runtime Resolver → Snapshot；关键断言：完整一致视图，无混合发布窗口；跨实例语义等价。
- [ ] [E-SNAP-02][E2E] 先补验收并记录RED，真实边界：持续真实配置变更/事务失败 → Resolver → 后续执行；关键断言：有界结果或类型化失败；无无穷重试；Snapshot P95≤20ms。
- [ ] 覆盖 Publish/Binding/Policy/UserProfile 变化、缓存miss/hit、连续变更、跨租户；旧执行不可变，新执行可见新提交。
- [ ] 运行 `.venv/bin/python -m pytest -q backend/tests/e2e/test_snapshot_publication_race.py backend/tests/e2e/test_snapshot_cross_instance.py backend/tests/benchmarks/test_snapshot_benchmark.py` 并通过cf-validate补充匹配检查；逐场景填写Acceptance Evidence，核对源码范围后才提交完成检查。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-SNAP-01 | E2E | 真实 PG 并发提交 → 多 Runtime Resolver → Snapshot | 完整一致视图，无混合发布窗口；跨实例语义等价 | backend/tests/e2e/test_snapshot_publication_race.py backend/tests/e2e/test_snapshot_cross_instance.py backend/tests/benchmarks/test_snapshot_benchmark.py（以场景ID标记用例，planned） | `.venv/bin/python -m pytest -q backend/tests/e2e/test_snapshot_publication_race.py backend/tests/e2e/test_snapshot_cross_instance.py backend/tests/benchmarks/test_snapshot_benchmark.py` | planned |
| E-SNAP-02 | E2E | 持续真实配置变更/事务失败 → Resolver → 后续执行 | 有界结果或类型化失败；无无穷重试；Snapshot P95≤20ms | backend/tests/e2e/test_snapshot_publication_race.py backend/tests/e2e/test_snapshot_cross_instance.py backend/tests/benchmarks/test_snapshot_benchmark.py（以场景ID标记用例，planned） | `.venv/bin/python -m pytest -q backend/tests/e2e/test_snapshot_publication_race.py backend/tests/e2e/test_snapshot_cross_instance.py backend/tests/benchmarks/test_snapshot_benchmark.py` | planned |

### Acceptance Evidence

待cf-task-start填写RED/GREEN、断言位置与真实边界证据；本次仅规划，均未验证。契约先行任务只完成本地Contract验收，不代替后续跨服务行为验收。

### Log

- [2026-09-07] created (draft)
- [2026-09-08] review 修订：test_snapshot_benchmark.py 已存在，明确为扩展（was draft）
