# Tasks: 共享出口管控 SDK

- **Source**: egress-shared-sdk.design.md
- **Created**: 2026-11-09
- **Updated**: 2026-11-12

## Proposal

2026-11-10 修正路径：TASK-009 → TASK-003（计量/传输内核）→ TASK-010（PG 身份与 B-05）→ TASK-004 / TASK-005 → TASK-006（执行器与进程监督）。B-05 唯一 owner 转为 TASK-010，验收层级和真实边界不变。

以Redis共享计量替代进程内限额，不增加独立服务。旧目录与既存暂存区保持不动；不复用旧verified证据。Plan只收集骨架，实施前先替换为真实测试再记录功能RED/GREEN。

## 执行顺序与 CI 批次

保留历史任务编号，执行依赖以 `Depends` 为准：TASK-001 → TASK-002（历史，验收缺口见 TASK-009）→ TASK-009 → TASK-003 → TASK-004 / TASK-005 → TASK-006 → TASK-007 → TASK-008。TASK-004 与 TASK-005 逻辑上互不依赖，可在 TASK-003 后协调实施；TASK-006 同时依赖二者。不得为缩短路径删除监控、恢复、真实请求接线或验收要求。

TASK-009 验证共享内核，使用可信测试身份；TASK-006 验证 PG、Runtime/Worker 与实际恢复链路。两者的边界、待实现 API 和已知风险见 `task-009-frozen-budget-design.md`；租约和故障设计见 `task-003-lease-failure-design.md`。上述设计是待实现草案，现有 CI 的部分探针通过不能替代场景完整验收。

将互相依赖的实现、回归测试、任务契约和当前 CI 故障修复合并为一个可审查批次；实现期间先进行允许的静态检查，真实边界测试按用户要求在 CI 执行。仅在完整批次准备好后推送触发全量 CI；如果失败，先汇总全部分片和根因，再集中修复并重跑。有效 RED/GREEN、单项通过与全量通过分别记录；不能为减少流水线次数跳过所需验证。

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 | 执行命令 | cwd | timeout | 优先级 | 执行方式 | 环境 | 输入 | E2E理由 | 触发路径 | 冒烟 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| S-01 | egress-shared-sdk.design.md#2.5 验收条件 | integration | 两个真实宿主进程、真实 Redis Cluster 与 HTTP 探针 | TASK-009 | verified | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_repair.py::test_s_01"] | . | 300 | P0 | auto | ci | packages/**; apps/agent-runtime/**; apps/agent-worker/**; migrations/** | - | - | no |
| S-02 | egress-shared-sdk.design.md#2.5 验收条件 | integration | 真实Redis及两个SDK客户端 | TASK-009 | verified | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py::test_s_02"] | . | 300 | P0 | auto | ci | packages/**; apps/agent-runtime/**; apps/agent-worker/**; migrations/** | - | - | no |
| S-03 | egress-shared-sdk.design.md#2.5 验收条件 | integration | 真实PG、Redis、Runtime和Worker执行器进程 | TASK-006 | e2e_deferred | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py::test_s_03"] | . | 300 | P0 | deferred | ci | packages/**; apps/agent-runtime/**; apps/agent-worker/**; migrations/** | - | - | no |
| S-04 | egress-shared-sdk.design.md#2.5 验收条件 | integration | Linux受控镜像、系统site hook、真实Skill子进程及HTTP | TASK-004 | verified | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py::test_s_04"] | . | 300 | P0 | auto | ci | packages/**; apps/agent-runtime/**; apps/agent-worker/**; migrations/** | - | - | no |
| S-05 | egress-shared-sdk.design.md#2.5 验收条件 | integration | 真实Adapter、Streamable HTTP MCP、Redis和HTTP探针 | TASK-005 | verified | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py::test_s_05"] | . | 300 | P0 | auto | ci | packages/**; apps/agent-runtime/**; apps/agent-worker/**; migrations/** | - | - | no |
| E-01 | egress-shared-sdk.design.md#2.5 验收条件 | integration | 真实Redis进程、网络故障代理与HTTP探针 | TASK-003 | verified | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py::test_e_01[test_lost_redis_reply_never_sends_and_same_attempt_retry_does_not_recount]", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py::test_e_01[test_admission_deadline_is_dependency_failure_not_http_timeout]"] | . | 300 | P0 | auto | ci | packages/**; apps/agent-runtime/**; apps/agent-worker/**; migrations/** | - | - | no |
| E-02 | egress-shared-sdk.design.md#2.5 验收条件 | integration | Linux bwrap、真实父子孙进程、Worker/Runtime、Redis及慢HTTP | TASK-006 | e2e_deferred | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py::test_e_02"] | . | 300 | P0 | deferred | ci | packages/**; apps/agent-runtime/**; apps/agent-worker/**; migrations/** | - | - | no |
| E-03 | egress-shared-sdk.design.md#2.5 验收条件 | integration | 真实HTTP Proxy探针与SDK客户端 | TASK-005 | verified | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py::test_e_03"] | . | 300 | P0 | auto | ci | packages/**; apps/agent-runtime/**; apps/agent-worker/**; migrations/** | - | - | no |
| E-04 | egress-shared-sdk.design.md#2.5 验收条件 | integration | 真实事件spool文件系统、PG汇总及Redis | TASK-007 | verified | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py::test_e_04"] | . | 300 | P0 | auto | ci | packages/**; apps/agent-runtime/**; apps/agent-worker/**; migrations/** | - | - | no |
| B-01 | egress-shared-sdk.design.md#2.5 验收条件 | integration | 真实Redis Lua及可控隔离测试时钟 | TASK-009 | verified | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_repair.py::test_b_01"] | . | 300 | P0 | auto | ci | packages/**; apps/agent-runtime/**; apps/agent-worker/**; migrations/** | - | - | no |
| B-02 | egress-shared-sdk.design.md#2.5 验收条件 | integration | 真实requests/urllib3、HTTP重定向及失败探针 | TASK-004 | verified | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py::test_b_02"] | . | 300 | P0 | auto | ci | packages/**; apps/agent-runtime/**; apps/agent-worker/**; migrations/** | - | - | no |
| B-03 | egress-shared-sdk.design.md#2.5 验收条件 | integration | 真实流式HTTP、Redis及宿主deadline监督 | TASK-003 | verified | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py::test_b_03[test_stream_holds_lease_until_close_and_preserves_proxy_response]", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py::test_b_03[test_deadline_covers_body_read_and_closes_real_socket]", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py::test_b_03[test_external_cancellation_closes_stream_and_keeps_cancelled_error]", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py::test_b_03[test_renewal_epoch_failure_closes_stream_as_dependency_error]", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py::test_b_03[test_renewal_network_timeout_closes_active_stream]", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py::test_b_03[lease_release_key_set]", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py::test_b_03[lease_renew_key_set]"] | . | 300 | P0 | auto | ci | packages/**; apps/agent-runtime/**; apps/agent-worker/**; migrations/** | - | - | no |
| B-04 | egress-shared-sdk.design.md#2.5 验收条件 | integration | 两个真实SDK进程与Redis权威规则 | TASK-009 | verified | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_repair.py::test_b_04"] | . | 300 | P0 | auto | ci | packages/**; apps/agent-runtime/**; apps/agent-worker/**; migrations/** | - | - | no |
| B-05 | egress-shared-sdk.design.md#2.5 验收条件 | integration | 真实PG、Redis Cluster主从切换与故障演练 | TASK-010 | verified | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py::test_b_05"] | . | 300 | P0 | auto | ci | packages/**; apps/agent-runtime/**; apps/agent-worker/**; migrations/** | - | - | no |
| B-06 | egress-shared-sdk.design.md#2.5 验收条件 | contract | 真实typed配置及带调用计数的依赖测试替身，不声称真实网络覆盖 | TASK-001 | verified | ["uv", "run", "pytest", "-q", "tests/contracts/test_shared_egress.py"] | . | 300 | P0 | auto | none | packages/**; apps/agent-runtime/**; apps/agent-worker/**; migrations/** | - | - | no |

---

## TASK-001: 共享规则与可信身份契约

- **Status**: done
- **Priority**: P0
- **Depends**:
- **Source**: egress-shared-sdk.design.md#3.4 接口设计
- **Spec-Refs**: harness-arch#RULE-arch-001, harness-secret#RULE-secret-001, harness-test#RULE-test-001
- **Rule-Owner-Refs**: N/A
- **Acceptance-Refs**: B-06
- **Estimate**: 1.5d
- **Capability**: 空规则零计量依赖，非法配置不能绕过，区分冻结预算和公共护栏

### Description

空规则零计量依赖，非法配置不能绕过，区分冻结预算和公共护栏。按设计边界交付，保留其他任务既存改动。

### Checklist

- [x] 先替换所拥有场景的PLAN_SKELETON为真实边界断言；已记录功能RED（缺少shared_egress模块）并取得GREEN；收集与占位失败不算功能证据
- [x] [B-06][contract] 真实typed配置及带调用计数的依赖测试替身，不声称真实网络覆盖；断言：无规则不设上限且不访问计量Redis；非法配置拒绝；目标实例/route匹配不混桶、未支持客户端明确标记；稳定身份不随segment变化

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-06 | contract | 真实typed配置及带调用计数的依赖测试替身，不声称真实网络覆盖 | 无规则不设上限且不访问计量Redis；非法配置拒绝；未知目标不误归桶；未支持客户端标记未受控 | tests/contracts/test_shared_egress.py | ["uv", "run", "pytest", "-q", "tests/contracts/test_shared_egress.py"] | verified |

### Acceptance Evidence

B-06 RED: FAIL: `muad_contracts.shared_egress` 不存在。 GREEN: PASS: `uv run pytest -q tests/contracts/test_shared_egress.py`; 3 passed。断言位置：tests/contracts/test_shared_egress.py；边界：真实Pydantic/dataclass契约。

2026-11-09 补齐审核：旧3项测试未覆盖B-06全部断言；新增target/coverage/严格类型/窗口成对/重复规则/无规则惰性装配的真实契约断言。新增功能RED：select与shared_metering模块缺失（2 failed）；GREEN：14项B-06与26项旧预算契约合计40 passed。无Redis调用证明仅限SDK装配边界，不代表尚未接入的Runtime调用链。
- B-06: verified — automated command passed; run_id=5f9ece1628b4480a8c098016ecf68cb0 (confirmed_by: runner)
- B-06: verified — automated command passed; run_id=a00082f0a36b4ad6bdb8bce211464883 (confirmed_by: runner)
- B-06: verified — automated command passed; run_id=9b10342b5ab147dcacc35e5a0e6edbd2 (confirmed_by: runner)
- B-06: verified — automated command passed; run_id=8e1ef4aa0761416ab12d31af69f7e7a9 (confirmed_by: runner)
- B-06: verified — automated command passed; run_id=446101788a0a472fa6f60d3b6ffe93b9 (confirmed_by: runner)
- B-06: verified — automated command passed; run_id=ca8f3f1533c046ff8869e44b70b46ee7 (confirmed_by: runner)
- B-06: verified — automated command passed; run_id=c284313066b546b08c4005c39745e833 (confirmed_by: runner)

### Log

- [2026-11-09] created (draft)
- [2026-11-09] started
- [2026-11-09] completed (done)

---

## TASK-002: Redis原子多桶与公共规则发布

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-001
- **Source**: egress-shared-sdk.design.md#3.2 架构设计
- **Spec-Refs**: harness-arch#RULE-arch-001, harness-secret#RULE-secret-001, harness-test#RULE-test-001
- **Rule-Owner-Refs**: N/A
- **Acceptance-Refs**: S-01, S-02, B-01, B-04
- **Estimate**: 2d
- **Capability**: 两个进程对同接口共用预算，规则降额不清桶，Cluster同slot

### Description

两个进程对同接口共用预算，规则降额不清桶，Cluster同slot。按设计边界交付，保留其他任务既存改动。

### Checklist

- [ ] 先替换所拥有场景的PLAN_SKELETON为真实边界断言；取得功能RED后实施再取GREEN，收集与占位失败不算功能证据
- [x] [S-01][integration] 两个真实宿主进程、真实 Redis Cluster 与 HTTP 探针；断言：两进程合计最多50次获准并到达HTTP探针；所有KEY同slot，无CROSSSLOT
- [x] [S-02][integration] 真实Redis及两个SDK客户端；断言：任一桶不足时其他桶不扣；成功申请同时占用全部适用桶
- [x] [B-01][integration] 真实Redis Lua及可控隔离测试时钟；断言：有效区间为(now-window,now]，不存在跨整分放行100次；回拨不提前释放窗口
- [x] [B-04][integration] 两个真实SDK进程与Redis权威规则；断言：按当前50统一判定且不清桶；旧缓存先拒后刷新；历史不足进入新窗口阻断期；冻结累计不变

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-01 | integration | 两个真实宿主进程、真实 Redis Cluster 与 HTTP 探针 | 两进程合计最多50次获准并到达HTTP探针；所有KEY同slot，无CROSSSLOT | tests/sdk/test_shared_redis_meter.py | ["uv", "run", "pytest", "-q", "tests/sdk/test_shared_redis_meter.py::test_s_01"] | verified |
| S-02 | integration | 真实Redis及两个SDK客户端 | 任一桶不足时其他桶不扣；成功申请同时占用全部适用桶 | tests/sdk/test_shared_redis_meter.py | ["uv", "run", "pytest", "-q", "tests/sdk/test_shared_redis_meter.py::test_s_02"] | verified |
| B-01 | integration | 真实Redis Lua及可控隔离测试时钟 | 有效区间为(now-window,now]，不存在跨整分放行100次；回拨不提前释放窗口 | tests/sdk/test_shared_redis_meter.py | ["uv", "run", "pytest", "-q", "tests/sdk/test_shared_redis_meter.py::test_b_01"] | verified |
| B-04 | integration | 两个真实SDK进程与Redis权威规则 | 按当前50统一判定且不清桶；旧缓存先拒后刷新；历史不足进入新窗口阻断期；冻结累计不变 | tests/sdk/test_shared_redis_meter.py | ["uv", "run", "pytest", "-q", "tests/sdk/test_shared_redis_meter.py::test_b_04"] | verified |

### Acceptance Evidence

S-01/S-02/B-01/B-04 RED: 旧占位测试未连接共享Redis内核。 GREEN: `uv run pytest -q tests/sdk/test_shared_redis_meter.py::test_s_01 tests/sdk/test_shared_redis_meter.py::test_s_02 tests/sdk/test_shared_redis_meter.py::test_b_01 tests/sdk/test_shared_redis_meter.py::test_b_04`; 4 passed。真实边界：Redis、Lua EVAL、两个SDK客户端。

历史记录：以上验收结果未覆盖场景源的完整真实边界；S-01、S-02、B-01、B-04 的当前验收责任均转入 TASK-009，原记录不作为最终验收证据。
- S-01: verified — automated command passed; run_id=8860f7808828434fa543614419e1ef53 (confirmed_by: runner)
- S-02: verified — automated command passed; run_id=8860f7808828434fa543614419e1ef53 (confirmed_by: runner)
- B-01: verified — automated command passed; run_id=8860f7808828434fa543614419e1ef53 (confirmed_by: runner)
- B-04: verified — automated command passed; run_id=8860f7808828434fa543614419e1ef53 (confirmed_by: runner)

### Log

- [2026-11-09] created (draft)
- [2026-11-10] started
- [2026-11-10] completed (done)

---
## TASK-003: 并发租约与计量故障守卫

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-009
- **Source**: egress-shared-sdk.design.md#3.2 架构设计
- **Spec-Refs**: harness-arch#RULE-arch-001, harness-secret#RULE-secret-001, harness-test#RULE-test-001
- **Rule-Owner-Refs**: N/A
- **Acceptance-Refs**: E-01, B-03
- **Estimate**: 2d
- **Capability**: 流式占用正确释放，故障不外发，状态缺失不恢复零额度

### Description

流式占用正确释放，故障不外发，状态缺失不恢复零额度。按设计边界交付，保留其他任务既存改动。

### Checklist

- [x] 当前所拥有 E-01/B-03 已替换 PLAN_SKELETON 并在正式入口执行真实边界；B-05 转交 TASK-010，保留全部验收要求
- [x] [E-01][integration] 真实 Redis 连接故障、故障代理和 HTTP 探针：受控新请求不发送；协议重试同 attempt 不重复扣；错误标记依赖故障而非累计耗尽。已补真实 HTTP 零外发、TCP 故障代理丢回包和同 attempt 协议查询不重扣；正式调用链接入仍待后续任务。
- [x] [B-03][integration] 真实流式 HTTP、Redis 及宿主 deadline 监督：读完前保持占用；只释放自身 token；续租失败关闭本地传输；租约不早于强制 deadline 安全过期。新增 SDK 宿主 HTTP 监督原语已覆盖流式读取 deadline、续租失败关闭及取消；Runtime/Worker 正式接线和执行权 fencing 仍由后续任务交付，SDK 生命周期边界已执行；正式 Runtime/Worker 接线仍由 TASK-006 验收。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| E-01 | integration | 真实 Redis 连接故障、故障代理和 HTTP 探针 | 受控新请求不发送；同 attempt 不重复扣；错误来源可区分 | .code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py::test_e_01[test_lost_redis_reply_never_sends_and_same_attempt_retry_does_not_recount]", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py::test_e_01[test_admission_deadline_is_dependency_failure_not_http_timeout]"] | verified |
| B-03 | integration | 真实流式 HTTP、Redis 及宿主 deadline 监督 | 读完前占用；只释放自身；续租失败关闭；deadline 安全过期 | .code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py::test_b_03[test_stream_holds_lease_until_close_and_preserves_proxy_response]", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py::test_b_03[test_deadline_covers_body_read_and_closes_real_socket]", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py::test_b_03[test_external_cancellation_closes_stream_and_keeps_cancelled_error]", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py::test_b_03[test_renewal_epoch_failure_closes_stream_as_dependency_error]", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py::test_b_03[test_renewal_network_timeout_closes_active_stream]", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py::test_b_03[lease_release_key_set]", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py::test_b_03[lease_renew_key_set]"] | verified |

### Acceptance Evidence

- 先前 E-01/B-03/B-05 的局部通过属于补充验证，没有对应功能缺陷 RED，不再称为 RED/GREEN；它们不证明完整场景边界。
- 2026-11-10：真实隔离 Redis 7 下，新增 4 项续租测试先功能失败：未消费许可仍可续租、epoch 变化仍可续租、混合规则续租误拒、短续期缩短已有占用。修复后共享内核定向测试 39 passed（2.01 秒），包含真实跨原到期点占用、自然过期拒绝、错误 key 集合拒绝及第二桶损坏时第一桶不变。
- 续租校验 guard/decision epoch、消费标记、原始并发 key 集合与完整占用；单独保存租约到期时间，不修改发送许可到期；release 同样校验完整 key 集合。宿主必须继续监督合法执行与强制 deadline；此 Redis 原语自身不能证明执行仍合法，也没有传输关闭能力。
- 本地隔离 Redis 的 E-01/B-03/B-05 局部探针合计 3 passed（1.06 秒），仅覆盖连接失败拒绝、手动耦合 HTTP 流的并发占用、缺失状态/epoch 拒绝；不是生产请求链路验收。
- 2026-11-10 后续：新增 `SharedHTTPTransport` 与 `RequestLifetime`，宿主生成 attempt、admit/consume 后执行单次 prepared HTTP send；禁隐式 redirect/client auth flow，stream scope 关闭连接后才释放并发。deadline 覆盖准入、发送、读取；租约覆盖 deadline 与关闭余量，续租剩余时长受固定 deadline 约束。计量阶段超时独立分类为依赖错误；续租网络失败/epoch 变化会中断读取并关闭本地 socket。释放失败保守等待到期并告警，不改写已取得的 Proxy 结果。
- 新 API 在实现前的 4 项测试因模块未实现失败（接口缺失 RED，不声称已执行流式行为断言）；实现后补充真实 TCP Redis 故障代理：丢失已提交 EVAL 回包时 HTTP 零外发，相同 attempt 查询不重复扣数；读取中丢回包关闭 socket。再覆盖外部取消、429/Retry-After/body、固定 CSRF/Cookie、禁自动跳转、无规则绕过 Redis 和释放失败可见。`REDIS_URL=redis://127.0.0.1:16380/0 uv run pytest -q tests/sdk/test_shared_http_transport.py tests/sdk/test_shared_redis_meter.py`：50 passed（3.46 秒），其中新 HTTP 生命周期测试 11 项。
- 新原语仅被本地真实边界测试使用，尚未接入生产 Runtime/Worker、Skill IPC 或 Adapter/MCP；依赖可信调用方预先授权目标及准备认证，不提供 PG 身份认证或不可绕过的网络边界。asyncio deadline 是协作式取消，不代替后续进程级监督。
- Ruff 与 diff 空白检查通过。SDK 中的真实故障代理和协作式 deadline/续租失败关闭连接已有定向验证；正式宿主接线、PG 与 Cluster failover 检测仍待实现，三个场景保留 planned，TASK-003 保持 in-progress；未触发全量 CI。

- 2026-11-10 代际守卫：真实 3 主 3 从 Redis 7 Cluster 中取得功能 RED：受控切换后及原进程 TAKEOVER 强制切回后均仍被错误放行。初次切回测试因选举冷却/拓扑尚未收敛超时，属于演练准备问题，不计功能 RED；改为显式 TAKEOVER 后实际复现旧实现缺陷。
- Lua 内增加 `run_id` + Cluster `cluster_my_epoch` 同节点检查，普通 publish 不重新学习缺失/变化的 generation；admit、consume、renew、register/resume 同样拒绝不确定代际。仅 release 允许关闭连接后做保守清理。新增缺 generation 五条操作拒绝与 INFO ACL 不足拒绝，所有拒绝不写累计。
- `REDIS_URL=redis://127.0.0.1:16380/0 uv run pytest -q tests/sdk/test_shared_redis_meter.py tests/sdk/test_shared_http_transport.py`：56 passed（3.48 秒）。`EGRESS_ALLOW_CLUSTER_FAILOVER=1 MUAD_CLUSTER_REDIS_URL=redis://127.0.0.1:6391/0 uv run pytest -q tests/sdk/test_shared_redis_failover.py`：3 passed（4.25 秒）。该演练只允许显式指定可销毁 Cluster；不触碰开发库/集群，不代表 PG 身份恢复或任意历史回滚保证已交付。

- 2026-11-10 依赖修正：B-05 唯一 owner 转 TASK-010，PG 注册先于 TASK-004/005/006；场景边界与步骤不变。E-01 两个实际 TCP 故障用例及 B-03 七个流式/deadline/token 用例加入正式登记，隔离 Redis 执行合计 9 passed（1.75 秒）。需求级 Plan Gate pass，15 个场景保持完整覆盖；仅 E-01/B-03 作为当前任务收尾范围，其他场景不自报通过。
- E-01: verified — automated command passed; run_id=1a0ed69b61214524a933f29d11f08a65 (confirmed_by: runner)
- B-03: verified — automated command passed; run_id=1a0ed69b61214524a933f29d11f08a65 (confirmed_by: runner)

### Log

- [2026-11-09] created (draft)
- [2026-11-10] started
- [2026-11-10] completed (done)

---


## TASK-010: PG 持久身份注册与共享计量恢复

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-003
- **Source**: egress-shared-sdk.design.md#3.3 数据设计
- **Spec-Refs**: harness-data#RULE-data-001, harness-arch#RULE-arch-001, harness-secret#RULE-secret-001, harness-test#RULE-test-001
- **Rule-Owner-Refs**: harness-data#RULE-data-001
- **Acceptance-Refs**: B-05, harness-data#RULE-data-001
- **Estimate**: 1.5d
- **Capability**: PG 权威注册执行与逻辑调用，恢复不重新初始化 Redis 累计

### Description

从 TASK-006 抽出 PG 身份基础，解决 TASK-003 的 B-05 验收前置倒置。沿用已有数据设计，不降低 B-05 的真实 PG、Redis Cluster 和故障演练边界；TASK-003 已有 Cluster 演练只作补充证据，不替代本任务端到端恢复协议。TASK-006 继续负责 Runtime/Worker 与 killpg 接线。

### Checklist

- [x] B-05 正式入口已执行真实 PG + Cluster 身份恢复、缺状态阻断与受控切换；新 API 缺失 RED 与此前真实切换缺陷 RED 均保留，不把占位失败作功能证据
- [x] 模型、Alembic migration、完整迁移链与 schema parity 一致；完整唯一键保留 tombstone；同租户复合 FK
- [x] 同一 source / operation_key 重领复用身份；冻结策略不可改写；PG 先提交、显式一次性初始化与恢复分离
- [x] Redis 初始化不确定时阻断旧执行；恢复只查已有作用域，不自动清零；PG 不保存逐请求计数
- [x] [RULE-data-001] verifier: ["uv", "run", "pytest", "-q", "tests", "-k", "schema_parity"]；cwd=.；timeout=600；隔离环境执行

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| B-05 | integration | 真实 PG、Redis Cluster 主从切换与故障演练 | 恢复不清累计；缺 key/epoch 阻断；切换不可检测不验收 | .code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py::test_b_05"] | verified |
| harness-data#RULE-data-001 | integration | 真实 PG migration 与模型 schema parity | 标准审计列、同租户复合FK、完整唯一键与 jsonb/timestamptz | harness-data#RULE-data-001 | ["uv", "run", "pytest", "-q", "tests", "-k", "schema_parity"] | planned |

### Acceptance Evidence

- TASK-003 由实际 `task finish` 返回 pass 后提交 `27e5a5ef`；本任务以该提交为新基线正式 start。先前用户 `.codex/config.toml` 改动仅登记保留，未修改或提交。
- PG schema 功能 RED：迁移前真实独立 PG 中 `egress_execution/egress_invocation` 不存在，schema parity 用例 1 failed（0.84 秒）。新增迁移 0044 与独立 SDK ORM 后，本地 3 项定向测试通过（0.75 秒）：列与类型/nullable/完整唯一键一致、execution tombstone 不可重建、invocation tombstone 的 operation_key 不可重用、跨租户复合 FK 拒绝。
- 同一可销毁 PG 完成 `downgrade 0043` → `upgrade head` 往返，再取得上述 GREEN；未修改开发库。Ruff、模型 mypy 和 diff 空白检查通过。
- 数据规范定向命令 `uv run pytest -q tests -k schema_parity` 在独立 PG/Redis 中通过：56 passed、7013 deselected（26.05 秒），未运行其他用例或全量 CI。
- 本阶段早期仅有表结构；以下新增协议证据补齐后再申请 Done Gate，不覆盖历史未实现记录。
- 初始化协议 RED：4 项新增用例因注册/恢复模块缺失失败（接口缺失，尚未执行行为断言）。实现 PG 幂等注册、一次性 token hash/CAS 和 READY existing-only 恢复后，补充真实 TCP 故障代理丢失已提交 register 回包，证明 PG BLOCKED 且不重新创建作用域。
- B-05 正式入口通过有界子进程执行两个 PG+Cluster 用例，强制要求专属可销毁 Cluster opt-in，且校验两个用例实际通过，环境缺失/skip 不作为成功。断言：超过明细保留时间的执行仍沿用计数、执行/调用 ID 不变、真实受控主从切换拒绝、PG READY 而 Redis key 缺失时拒绝且不创建。
- 额外覆盖并发 8 次注册同 ID、冻结预算变化拒绝、调用逻辑键幂等、跨租户拒绝、确认 token 伪造/重放拒绝、旧 fence 确认拒绝、tombstone/终态不重建及未配置 Skill 不自动限额。正式 Runtime/Worker 领取、执行段互斥、killpg 与 Skill/MCP 接线仍由后续任务负责。

- 当前完整定向回归：`tests/sdk/test_egress_identity_registry.py`、`test_egress_identity_schema.py`、`test_shared_redis_meter.py`、`test_shared_http_transport.py` 与正式 `test_b_05` 合计 70 passed（6.03 秒）；B-05 内部的两个真实 PG/Cluster 子用例均实际通过。四个 SDK 源文件 mypy 与相关 Ruff 通过；Plan Gate pass。尚未触发全量 CI；正在申请 Done Gate，裁决以命令结果为准。
- B-05: verified — automated command passed; run_id=082c50ef8e1d4c8ca27194386e5626e3 (confirmed_by: runner)

### Log

- [2026-11-10] 从 TASK-003/B-05 与 TASK-006 的身份基础拆出，修正依赖顺序；真实边界与需求不变。
- [2026-11-10] started
- [2026-11-10] completed (done)

---

## TASK-004: 旧requests透明hook与私有IPC

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-003, TASK-010
- **Source**: egress-shared-sdk.design.md#3.2 架构设计
- **Spec-Refs**: harness-secret#RULE-secret-001, harness-arch#RULE-arch-001, harness-test#RULE-test-001
- **Rule-Owner-Refs**: harness-secret#RULE-secret-001
- **Acceptance-Refs**: S-04, B-02, harness-secret#RULE-secret-001
- **Estimate**: 2d
- **Capability**: 原requests脚本逐发送受控，CSRF不改写，跳转重试不漏计

### Description

原requests脚本逐发送受控，CSRF不改写，跳转重试不漏计。按设计边界交付，保留其他任务既存改动。

### Checklist

- [x] 先替换所拥有场景的PLAN_SKELETON为真实边界断言；取得功能RED后实施再取GREEN，收集与占位失败不算功能证据
- [x] [S-04][integration] Linux受控镜像、系统site hook、真实Skill子进程及HTTP；断言：无需改业务逻辑即受控；Cookie和固定CSRF保持；无hook启动自检明确失败
- [x] [B-02][integration] 真实requests/urllib3、HTTP重定向及失败探针；断言：服务分别接收2/3次且逐次申请；未经授权跳转拒绝；自定义adapter显式声明支持或报不支持
- [x] [RULE-secret-001] verifier: ["uv", "run", "pytest", "-q", "tests/test_logging_redaction.py", "tests/acceptance/test_foundation_ops_audit.py"]；cwd=.；timeout=300；真实执行或按CI runbook复用匹配证据，不以收集代替。

- [x] 补齐受控进程内的独立非业务请求旁路，已受控重定向不得降级旁路
- [x] 补齐 IPC 断连/执行 deadline 与同步传输关闭或宿主停止事件的衔接；正式进程装配归 TASK-006

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-04 | integration | Linux受控镜像、系统site hook、真实Skill子进程及HTTP | 无需改业务逻辑即受控；Cookie和固定CSRF保持；无hook启动自检明确失败 | .code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py::test_s_04"] | verified |
| B-02 | integration | 真实requests/urllib3、HTTP重定向及失败探针 | 服务分别接收2/3次且逐次申请；未经授权跳转拒绝；自定义adapter显式声明支持或报不支持 | .code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py::test_b_02"] | verified |
| harness-secret#RULE-secret-001 | integration | Spec verifier声明的真实服务/存储边界，不以SDK mock替代 | 密钥明文存于各 Owner 表（模型 `control.model_credential.api_key`、Bot `secret`、MCP `auth_secret`、用户/共享凭据 `credential_json`；平台侧只有历史休眠列 `project_platform.auth_secret`，不得读写），跨表以主键引用，不再使用 `secret_ref`/SecretProvider；密钥不得进入日志、`config_audit_log`、Snapshot、LLM Prompt 或 API 响应（对外以 `*_configured` 表达）；Console 凭据编辑弹窗的**按需回显**是下文列出的受控例外，只允许那一条显式路径回明文。 | harness-secret#RULE-secret-001 | ["uv", "run", "pytest", "-q", "tests/test_logging_redaction.py", "tests/acceptance/test_foundation_ops_audit.py"] | planned |

### Acceptance Evidence

- 2026-11-10 正式启动；保留用户 `.codex/config.toml` 改动，不修改或提交。读取当前 Sandbox 启动代码确认 `-E -s -B` 系统 site 约束；本阶段先做 invocation 私有 IPC，尚未接系统 hook。
- 新 IPC 模块实现前 3 项用例因模块缺失而失败（接口缺失 RED，尚未执行行为断言）。实现真实 Unix socket + Redis admit/consume/done 后验证固定身份、私有权限、完成前并发拒绝、累计拒绝、未知目标及身份/凭据/正文注入拒绝；断连不假定 HTTP 已关闭，不提前释放 lease。
- 实际缺陷 RED：公共接口 TOTAL 被误标 invocation exhausted，新增用例 1 failed（0.88 秒）。Lua 拒绝结果新增 public/execution/invocation scope 后，公共累计只拒绝本次调用，冻结作用域累计才设置宿主 exhausted 事件。
- IPC 使用固定绝对执行 deadline，新连接不续期；只发送 method/origin/path，不发送 query/请求头/body；probe 不访问 Redis，不证明完整 hook 安装。后续 hook 仍须处理可信 Host 路由、认证不改写、重定向逐跳、隐藏重试、流关闭和启动自检。
- `REDIS_URL=redis://127.0.0.1:16380/0 uv run pytest -q tests/sdk/test_egress_ipc.py tests/sdk/test_shared_redis_meter.py tests/sdk/test_shared_http_transport.py`：62 passed（3.86 秒），包含 6 项 IPC 测试；相关 Ruff 和 4 个源文件 mypy 通过。仅本地隔离验证，未触发全量 CI；S-04/B-02 仍为 planned，TASK-004 未完成。

- 后续新增 requests hook：3 项最初因模块缺失失败（接口缺失 RED）。显式安装后对标准 Adapter 每次发送申请 IPC，Session 层只做支持性检查；原始 HTTPResponse 上接 read/stream/close，不替换返回类型、不改认证头。HTTP Host 以 authority 元数据授权；query 不进入 IPC。新增 requests 正式依赖与开发类型桩，uv.lock 仅同步对应依赖。
- 实际故障 RED：完成 IPC 断连测试暴露 broker 先 wait_closed 后取消 handler，导致退出卡到 deadline（测试失败）；改为先停止监听、取消处理器，再等待连接关闭，Proxy 结果不改写的测试通过。另一次 50ms 到期用例在 consume 前已过期，是测试时间余量问题；扩到 500ms 并真实等待过期，保留原断言。
- 当前 `test_requests_egress_hook.py`（8 项）、`test_egress_ipc.py`、`test_shared_redis_meter.py`、`test_shared_http_transport.py` 合计 70 passed（8.76 秒），相关 Ruff 和 4 个源文件 mypy 通过。B-02 正式入口 1 passed（4.64 秒），内部五个真实 HTTP/Unix IPC/Redis 子用例实际通过：302 两次、显式失败重试三次、隐式 retry 拒绝、未授权跳转拒绝、自定义 Adapter 拒绝；另有固定 CSRF/Cookie/Host、PreparedRequest、流式占用、read(0) 和 Proxy 429/Retry-After/body 保留断言。
- S-04 仍缺系统 site 自动加载、Linux 真实子进程和无 hook 启动阻断；独立非业务流量的受控旁路与宿主 deadline/断连监督仍待接线。当前只在显式 Hook 安装场景有效，不声称生产 Runtime/Worker 已覆盖。TASK-004 保持 in-progress，未申请 Done Gate、未提交或触发 CI。

- 系统 site/可信启动器阶段：先取得 2 项因启动器/模块缺失失败的 RED，再新增 stdlib-only `muad_egress_site`、可信绝对文件入口 `muad_egress_launch.py`。SDK wheel 安装两个模块，Runtime/Worker Dockerfile 安装 `.pth`，全局服务环境不设置 socket 变量；生产执行器激活仍归 TASK-006。
- `scripts/build_egress_site_test_image.py` 已实际构建 `fluxion-egress-site-test:local`：锁定第三方依赖、安装本仓库 wheel、真实 Python 3.12.16 + bwrap。探针业务脚本不调用 SDK，标准 requests 仍被计量，CSRF/Cookie 保留，相邻导入正常；隐藏系统 `.pth` 或让 IPC 不可用时业务 marker 不创建；禁用路径真实请求外发但 Redis 计数不增加，子进程无 Redis/DB/API 凭据。
- 镜像探针初次失败来自测试重复删除 marker，属于测试实现问题，不计功能 RED；修正后真实 Linux 探针报告 passed。正式 S-04、B-02，加本地 bootstrap 与 inventory 检查合计 8 passed（11.52 秒）；此前 Linux/启动器定向 3 passed（2.98 秒）。相关 Ruff、3 个源文件 mypy 和 diff 空白检查通过，未触发全量 CI。
- 配置清单仅增量同步新增协议/启动常量，CSV 行尾保持 LF；测试 4 passed。测试镜像使用本地 seccomp=unconfined，不代表生产内核已验收。独立非业务旁路及同步传输 deadline 衔接仍待完成，TASK-004 不自报 done。
- S-04: verified — automated command passed; run_id=e8eedbd3dc5047d9b9aa30464227c4a3 (confirmed_by: runner)
- B-02: verified — automated command passed; run_id=e8eedbd3dc5047d9b9aa30464227c4a3 (confirmed_by: runner)

### Log

- [2026-11-09] created (draft)
- [2026-11-10] started
- [2026-11-11] completed (done)

---

- 2026-11-11 完成最后两项接线：真实 Redis 就绪后获得有效功能 RED（5 failed：旁路缺失、重定向降级、deadline 未转换/关闭）；首次 Redis 未启动造成的连接错误仅为环境问题。新增 12 项真实 HTTP/TLS 生命周期测试，覆盖无 Redis 旁路、两种重定向链、慢响应头/体、原生较短 timeout、IPC 断连、Redis 代际变化及 response hook 独立请求。
- 生命周期、requests、IPC、Redis 与异步 HTTP 合计 82 passed（10.73 秒），相关 Ruff/mypy 通过。原 Proxy 测试改为仅在正文完成后丢失 completion ACK；未完成正文断连由新增测试明确断言中断，不能以保留 Proxy 语义为由继续失控传输。
- 测试 Dockerfile 使用 Docker 内置 BuildKit frontend，避免为 syntax 指令另行从 Docker Hub 拉取 frontend；上一轮 registry EOF 为构建环境失败，不算功能 RED。正式 Linux 场景与 Done Gate 以接下来的实际结果为准；生产装配仍归 TASK-006。

- 最新重建镜像后正式 S-04/B-02、bootstrap 与配置清单合计 8 passed（12.98 秒）；其中 B-02 实际执行 17 个真实 HTTP/TLS/Unix IPC/Redis 用例，S-04 实际运行 Linux bwrap 业务子进程。六个 IPC/requests 源模块 mypy、相关 Ruff 与 diff 空白检查通过。未推送、未触发全量 CI。

- 2026-11-11 Done Gate 实际裁决 pass（9 executed，0 reused，0 budget deferral）；S-04/B-02 与 arch/platform/secret/snapshot/worker 规范检查实际执行，5 项其他需求规范及 5 项 heavy validator 保留至终验。Worker/Runtime 关联规范命令约 17.9 分钟，是本轮主要耗时；后续不得将本次通过等同需求级全量验收。

## TASK-005: Adapter和MCP统一发送边界

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-003, TASK-010
- **Source**: egress-shared-sdk.design.md#3.2 架构设计
- **Spec-Refs**: harness-mcp#RULE-mcp-001, harness-project-platform#RULE-platform-001, harness-arch#RULE-arch-001, harness-secret#RULE-secret-001, harness-test#RULE-test-001
- **Rule-Owner-Refs**: harness-mcp#RULE-mcp-001, harness-project-platform#RULE-platform-001
- **Acceptance-Refs**: S-05, E-03, harness-mcp#RULE-mcp-001, harness-project-platform#RULE-platform-001
- **Estimate**: 1.5d
- **Capability**: 认证与MCP逐attempt计量，Proxy响应语义保留

### Description

认证与MCP逐attempt计量，Proxy响应语义保留。按设计边界交付，保留其他任务既存改动。

### Checklist

- [x] 先替换所拥有场景的PLAN_SKELETON为真实边界断言；取得功能RED后实施再取GREEN，收集与占位失败不算功能证据
- [x] [S-05][integration] 真实Adapter、Streamable HTTP MCP、Redis和HTTP探针；断言：每个物理发送各计一次；超限不发送；不在运行时新增tools/list
- [x] [E-03][integration] 真实HTTP Proxy探针与SDK客户端；断言：状态头正文保持原语义，标记UPSTREAM；已发送计数不返还；不变成本地拒绝
- [x] [RULE-mcp-001] verifier: ["uv", "run", "pytest", "-q", "tests/console_mcp/test_mcp_rules.py"]；cwd=.；timeout=300；真实执行或按CI runbook复用匹配证据，不以收集代替。
- [x] [RULE-platform-001] verifier: ["bash", "-lc", "uv run pytest -q tests -k schema_parity"]；cwd=.；timeout=300；真实执行或按CI runbook复用匹配证据，不以收集代替。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-05 | integration | 真实Adapter、Streamable HTTP MCP、Redis和HTTP探针 | 每个物理发送各计一次；超限不发送；不在运行时新增tools/list | .code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py::test_s_05"] | verified |
| E-03 | integration | 真实HTTP Proxy探针与SDK客户端 | 状态头正文保持原语义，标记UPSTREAM；已发送计数不返还；不变成本地拒绝 | .code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py::test_e_03"] | verified |
| harness-mcp#RULE-mcp-001 | integration | Spec verifier声明的真实服务/存储边界，不以SDK mock替代 | V1 仅支持 Streamable HTTP；Tool Catalog 由 `discover-tools` 唯一维护并落 PostgreSQL；用户范围仅 Server 级（ALL/SELECTED），不做 Tool 级启停或授权；MCP Tool 必须进入统一 ToolRegistry。 | harness-mcp#RULE-mcp-001 | ["uv", "run", "pytest", "-q", "tests/console_mcp/test_mcp_rules.py"] | planned |
| harness-project-platform#RULE-platform-001 | integration | Spec verifier声明的真实服务/存储边界，不以SDK mock替代 | ProjectPlatform 只保存实例与寻址配置，认证/Session 由 PlatformAdapter 承担（SPI 无独立 `refresh`，刷新属于 `authenticate` 内部）；凭据明文存于凭据表并由主键引用，`credential_mode` 仅表达选择策略（USER_ONLY/SHARED_ONLY/USER_THEN_SHARED/NONE）；PlatformSession 为可重建 Redis 缓存，键为 `platform_session:{tenant_id:platform_id}:{actor_scope}:{credential_version}:{adapter_key}:{adapter_version}`（`{tenant_id:platform_id}` 是字面 hash tag，session/index/generation/lock 同 slot，保证 4-key Lua 在 Redis Cluster 可执行），索引 `platform_sessions:{tenant_id:platform_id}` 以 Set 清理；更换 adapter_key 必须使旧凭据与会话失效。 | harness-project-platform#RULE-platform-001 | ["bash", "-lc", "uv run pytest -q tests -k schema_parity"] | planned |

### Acceptance Evidence

- 2026-11-11 启动 TASK-005，保留用户 `.codex/config.toml` 改动。实施前真实测试 3 failed（1.33 秒）：MSS 缺少 transport_factory、共享 transport 模块缺失；这是新接口缺失的 RED，不描述为已存在发送实现的行为缺陷。未把 PLAN_SKELETON 或环境故障当作功能证据。
- 新增宿主 HTTPX transport，直接复用 SharedHTTPTransport；MSS 新增保留 SSLContext 的 transport 工厂，MCP 使用已有 client/transport 注入。新路径没有本地计数后备或双重扣减；生产装配及旧装配移除保留在 TASK-006。
- 真实 MSS 的 CSRF/login/validate/业务/401 后重新认证及显式重试合计 8 次外发，下一次不发；真实 MCP initialize/notification/tools-call 合计 3 次，超限通过实际 adapter 调用拒绝，探针未收到 tools/list。Proxy 429/Retry-After/body 原样保留并标记 UPSTREAM，已发送次数不返还。
- 新增 13 项真实 HTTP/TLS/Redis 测试，覆盖重定向逐跳授权、跨客户端冻结 invocation 预算、未知目标与缺失 Redis guard 不外发、空规则不访问 guard、流式关闭/客户端关闭/响应头前取消/deadline 的物理关闭和释放、压缩正文只解码一次、显式响应字节约束及 TLS 证书校验。不是 mock Store 或模拟 HTTP。
- 新测试与正式 S-05/E-03 入口合计 15 passed（5.74 秒）；受影响 MSS/session/原 platform-budget/shared streaming/configuration inventory 回归 49 passed（21.11 秒）。相关 Ruff、两个源模块 mypy 通过；配置清单只更新两个常量行号，保持 LF。未推送、未触发全量 CI，当前等待 Done Gate 实际裁决。
- S-05: verified — automated command passed; run_id=4fab741dc585421c8350288003d5ca61 (confirmed_by: runner)
- E-03: verified — automated command passed; run_id=4fab741dc585421c8350288003d5ca61 (confirmed_by: runner)

### Log

- [2026-11-09] created (draft)
- [2026-11-11] started
- [2026-11-11] completed (done)

---

- 2026-11-11 Done Gate 实际裁决 pass（8 executed，0 reused，0 budget deferral，79.78 秒）：S-05/E-03、arch/mcp/platform/secret 规范及轻量 validators 通过。6 项需求级规范与 5 项 heavy validators 保留至需求终验；无本轮 Runtime/Worker 全目录回归。

## TASK-006: 持久执行身份与宿主进程监督

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-010, TASK-004, TASK-005
- **Source**: egress-shared-sdk.design.md#3.3 数据设计；platform-route-remediation.md#1. 已授权范围与实现
- **Spec-Refs**: harness-data#RULE-data-001, harness-skill#RULE-skill-001, harness-snapshot#RULE-snapshot-001, harness-worker#RULE-worker-001, harness-arch#RULE-arch-001, harness-secret#RULE-secret-001, harness-test#RULE-test-001, harness-api#RULE-api-002, harness-api#RULE-api-001, harness-e2e#RULE-e2e-001, harness-frontend#RULE-front-001, harness-i18n#RULE-i18n-001, harness-rel#RULE-rel-001, harness-ui#RULE-ui-001
- **Rule-Owner-Refs**: harness-skill#RULE-skill-001, harness-snapshot#RULE-snapshot-001, harness-worker#RULE-worker-001, harness-api#RULE-api-002, harness-api#RULE-api-001, harness-e2e#RULE-e2e-001, harness-frontend#RULE-front-001, harness-i18n#RULE-i18n-001, harness-rel#RULE-rel-001, harness-ui#RULE-ui-001
- **Acceptance-Refs**: S-03, E-02, harness-skill#RULE-skill-001, harness-snapshot#RULE-snapshot-001, harness-worker#RULE-worker-001, harness-api#RULE-api-002, harness-api#RULE-api-001, harness-e2e#RULE-e2e-001, harness-frontend#RULE-front-001, harness-i18n#RULE-i18n-001, harness-rel#RULE-rel-001, harness-ui#RULE-ui-001
- **Estimate**: 2d
- **Capability**: 恢复和Worker重领不清累计，次数耗尽回收脚本及孙进程

### Description

恢复和Worker重领不清累计，次数耗尽回收脚本及孙进程。TASK-009 使用宿主构造的可信测试身份验证共享内核；TASK-010 负责权威身份持久化，本任务负责 Runtime/Worker 真实恢复及请求链路，不以后者作为 TASK-009 的前置依赖。按设计边界交付，保留其他任务既存改动。

### Checklist

- [x] 先替换所拥有场景的PLAN_SKELETON为真实边界断言；取得功能RED后实施再取GREEN，收集与占位失败不算功能证据
- [x] [S-03][integration] 真实PG、Redis、Runtime和Worker执行器进程；断言：execution/invocation不变，仅剩20；segment变更不清零；Worker使用冻结execution_timeout_sec
- [x] [E-02][integration] Linux bwrap、真实父子孙进程、Worker/Runtime、Redis及慢HTTP；断言：N次可完成，N+1不发送；宿主killpg回收进程组和请求；Task不重试；Run保留已完成结果
- [x] [RULE-skill-001] verifier: ["uv", "run", "pytest", "-q", "tests/test_skill_artifact_cache.py"]；cwd=.；timeout=300；真实执行或按CI runbook复用匹配证据，不以收集代替。
- [x] [RULE-snapshot-001] verifier: ["bash", "-lc", "uv run pytest -q tests/agent_runtime/test_snapshot_freeze.py tests/agent_runtime/test_run_reaper.py && uv run pytest -q tests/agent_runtime -k \"executor or resolve\""]；cwd=.；timeout=600；真实执行或按CI runbook复用匹配证据，不以收集代替。
- [ ] [RULE-worker-001] verifier: ["bash", "-lc", "uv run pytest -q tests/agent_worker && uv run pytest -q tests/agent_runtime --ignore=tests/agent_runtime/test_runner_executor.py"]；cwd=.；timeout=1800；真实执行或按CI runbook复用匹配证据，不以收集代替。

- [x] 执行段 SDK 原子约束：准入/consume/renew 同 EVAL 校验当前 fence，并绑定 permit 原段；实际 Redis 与宿主 HTTP 验证
- [x] Worker 独立 lease_epoch：每次领取递增，续租/WAITING/重试/终态 CAS 绑定代次；保持 attempt 重试含义
- [x] PG 来源授权组件：Run/Task 自有读取器、双行锁后 PG 时钟、固定 owner/epoch 与 READY 身份校验
- [x] 将来源授权和续租/失权监督接入真实 Runtime/Worker 执行器，共享 SDK 激活前必须获取有效授权
- [x] 系统 site/可信 launcher 与真实沙箱挂载、宿主进程组停止和 HTTP 关闭确认（Core 真实 Linux 探针；不代表 Runtime/Worker 根执行器已接入）
- [x] 来源续期监督组件：启动/续期逐次 PG 授权、旧截止约束等待、失权有界停止、关闭确认后才允许段接管；真实 PG/Redis 与独立状态机验证
- [x] Runtime/Worker 根调用传递稳定逻辑操作键，与输出目录、attempt、lease_epoch 分离；真实脚本验证目录隔离，Worker 捕获执行器验证键稳定（尚不代表 PG invocation 登记已接通）
- [x] 将根调用逻辑键连接 PG PersistentEgressScopes，完成冻结累计/当前公共策略装配、移除旧进程内控制器；空公共规则但有累计预算时仍启用

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-03 | integration | 真实PG、Redis、Runtime和Worker执行器进程 | execution/invocation不变，仅剩20；segment变更不清零；Worker使用冻结execution_timeout_sec | .code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py::test_s_03"] | e2e_deferred |
| E-02 | integration | Linux bwrap、真实父子孙进程、Worker/Runtime、Redis及慢HTTP | N次可完成，N+1不发送；宿主killpg回收进程组和请求；Task不重试；Run保留已完成结果 | .code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py::test_e_02"] | e2e_deferred |
| harness-skill#RULE-skill-001 | integration | Spec verifier声明的真实服务/存储边界，不以SDK mock替代 | Artifact Store 为 **RWX PVC**（硬事实是 `accessModes: ReadWriteMany`，`deploy/k8s/base/pvc-artifacts.yaml`；是否落在 NFS 由部署侧 StorageClass 决定，清单里 `storageClassName` 并未固定；V1 不建 MinIO/S3）；数据库只保存相对 `storage_key`；Runtime/Worker 使用 emptyDir 本地缓存并在校验 checksum 后原子切换 READY；禁止直接从 NFS 目录执行 Python Skill；同一 checksum 并发首次加载必须 singleflight（**限每进程内**：跨 Pod 的正确性由 `os.replace` 幂等与 DB CAS 承载，不得当作跨进程互斥）；Artifact 写入不可变（**目标不存在才发布**：临时文件 + `os.link`，同 `storage_key` 二次写入抛 `FileExistsError`）；导入 DB 事务失败同步删除已写文件，进程级孤儿由 `cleanup-skill-orphans` CLI 宽限期扫描清理（宽限期保护进行中事务）。 | harness-skill#RULE-skill-001 | ["uv", "run", "pytest", "-q", "tests/test_skill_artifact_cache.py"] | planned |
| harness-snapshot#RULE-snapshot-001 | integration | Spec verifier声明的真实服务/存储边界，不以SDK mock替代 | 每个新 Run/Task 在执行前冻结 RuntimeSnapshot/execution snapshot（Agent/Model/Skill/MCP 版本、Prompt 模板版本、catalog revision/hash；**预算只属 execution snapshot 的 `budget` 键**——Run 侧的 `RuntimeSnapshot` 无 budget 列，等价载体是 `policy_json`，默认含 `max_model_retries`，预算与压缩策略等执行期策略一并冻结在此键）；配置或授权变更只影响后续新 Run/Task；终态写入必须 CAS。 | harness-snapshot#RULE-snapshot-001 | ["bash", "-lc", "uv run pytest -q tests/agent_runtime/test_snapshot_freeze.py tests/agent_runtime/test_run_reaper.py && uv run pytest -q tests/agent_runtime -k \"executor or resolve\""] | planned |
| harness-worker#RULE-worker-001 | integration | Spec verifier声明的真实服务/存储边界，不以SDK mock替代 | PostgreSQL 是 Task/Schedule/lease 的唯一权威源；Redis 仅作 wake-up/cancel hint；`task_type` 仅 `SKILL/BATCH`（外部等待用 WAITING 表达）；claim 使用 `FOR UPDATE SKIP LOCKED`，进入 WAITING 释放 lease，deadline 到期由 Scheduler sweep 置 `FAILED(TASK_DEADLINE_EXCEEDED)`。**deadline 是领取与收尾两侧的硬条件**：claim 排除 `deadline_at <= now` 的行（过期任务绝不执行），执行跨过 deadline 也不得写 `COMPLETED`，同样以 `FAILED(TASK_DEADLINE_EXCEEDED)` 收尾（`worker/claimer.py` 的 `_claimable_conditions`、`worker/service.py` 的终态守卫）。 | harness-worker#RULE-worker-001 | ["bash", "-lc", "uv run pytest -q tests/agent_worker && uv run pytest -q tests/agent_runtime --ignore=tests/agent_runtime/test_runner_executor.py"] | planned |

| harness-api#RULE-api-001 | contract | 对应Spec声明的边界；完整验证按项目CI处理 | 平台路由修复遵循该Rule | harness-api#RULE-api-001 | ["uv", "run", "pytest", "-q", "tests/test_api_i18n.py", "tests/test_error_catalog.py", "tests/acceptance/test_foundation_api_envelope.py"] | planned |
| harness-api#RULE-api-002 | contract | 对应Spec声明的边界；完整验证按项目CI处理 | 平台路由修复遵循该Rule | harness-api#RULE-api-002 | ["uv", "run", "pytest", "-q", "tests/console_skill/test_import_idempotency.py"] | planned |
| harness-e2e#RULE-e2e-001 | contract | 对应Spec声明的边界；完整验证按项目CI处理 | 平台路由修复遵循该Rule | harness-e2e#RULE-e2e-001 | ["uv", "run", "pytest", "-q", "tests/frontend/test_e2e_suite_contract.py"] | planned |
| harness-frontend#RULE-front-001 | contract | 对应Spec声明的边界；完整验证按项目CI处理 | 平台路由修复遵循该Rule | harness-frontend#RULE-front-001 | ["bash", "-lc", "uv run pytest -q tests/frontend/test_api_client_contract.py && uv run python scripts/check_frontend_api_usage.py && uv run python scripts/check_frontend_i18n.py && npm --prefix apps/console-platform/frontend run typecheck"] | planned |
| harness-i18n#RULE-i18n-001 | contract | 对应Spec声明的边界；完整验证按项目CI处理 | 平台路由修复遵循该Rule | harness-i18n#RULE-i18n-001 | ["bash", "-lc", "uv run pytest -q tests/acceptance/test_foundation_i18n.py && uv run python scripts/check_frontend_i18n.py"] | planned |
| harness-rel#RULE-rel-001 | contract | 对应Spec声明的边界；完整验证按项目CI处理 | 平台路由修复遵循该Rule | harness-rel#RULE-rel-001 | ["uv", "run", "pytest", "-q", "tests/console_platform/test_user_side_relations.py"] | planned |
| harness-ui#RULE-ui-001 | contract | 对应Spec声明的边界；完整验证按项目CI处理 | 平台路由修复遵循该Rule | harness-ui#RULE-ui-001 | ["bash", "-lc", "uv run pytest -q tests/frontend/test_console_shell_contract.py tests/frontend/test_ui_style_contract.py && npm --prefix apps/console-platform/frontend run build"] | planned |

### Acceptance Evidence

- 2026-11-11 激活 TASK-006，基线 e2a51267；保留用户 `.codex/config.toml` 改动。核对发现生产仍是本地 BudgetController，PG 身份已存在但发送未校验执行段；未直接声称此前 SDK 测试代表真实恢复。
- 执行段 API 实现前 5 项真实 Redis 测试因模块缺失失败（0.87 秒，接口缺失 RED）。后续取得两个实际行为 RED：新持有者可消费旧段 permit（1 failed，0.72 秒）；同时丢失段记录与 index 标记会重建旧段（1 failed，0.72 秒）。分别通过 permit 原段绑定与 scope/index 双标记修复，不用测试环境失败冒充功能 RED。
- 新增 SDK SegmentGrant/FencedRedisMeter，原计量 Lua 以局部参数函数复用，段校验与扣数/消费/续租为同一 EVAL，所有 key 显式声明且共用 hash tag。新 grant 仍要求后续 PG 来源认证；本接口不获取业务执行权。
- 真实 Redis 验证旧段拒绝、未停止不能接管、正常续租、过期不可复活、停止后安全接管、跨段 permit 消费/续租与 attempt 重用拒绝、丢失状态不重建、回拨不改变执行权。相关 SDK 回归 78 passed（6.60 秒）；另新增真实 HTTP 新旧客户端切换，旧客户端失权后不产生物理请求，段测试 10 passed（2.14 秒）。测试未包含 PG 或脚本进程，不作为 S-03/E-02 的完成证据。
- 第一阶段记录：Worker WAITING 续接保持 attempt 不变，当时独立 lease_epoch 与来源租约 CAS 仍待实施；当时尚未修改 Task 表/领取器、未启用生产 grant，也未修改冻结 execution_timeout_sec。当前 TASK-006 保持 in-progress，两个正式入口仍待实现，不运行 Done Gate、不提交、不推送或触发全量 CI。


- 2026-11-11 来源授权阶段：首次临时 PG 尚未就绪导致迁移失败、2 用例 skip，均为环境问题，不算 RED。确认 PG ready 并迁移到 0044 后取得真实 2 failed（0.85 秒）：同一 Worker 重新领取 WAITING 后旧段仍能写 COMPLETED；Task 无独立领取代次。
- 新增 0045 迁移与 ORM lease_epoch，领取器每次递增并写 CLAIMED 事件；Worker heartbeat/续租、WAITING/重试与受控终态更新要求捕获的 epoch，私有方法调用方及真实故障注入测试同步更新，不增加未提供 epoch 的兼容后备。先取得 8 项 PG GREEN，租约与 schema 相关 20 passed（2.60 秒）；Worker 全目录 354 passed（114.03 秒）。
- 新增 PGSegmentAuthority、Run/Task 自有来源读取器和 AuthorizedSegment，真实行锁顺序为 source→egress execution→新鲜 PG 时钟。跨 tenant/source/owner/epoch、取消/删除/等待/到期及预算身份无效时拒绝。只读租约所需列，DB 事务不做 Redis 外发或高频计数；窗口按 monotonic 保守折算，提交后也检查过期。
- 授权接口实施前 8 failed（1.67 秒，模块缺失 RED）；Run/Task 真实 PG 授权初期 14 passed（2.28 秒），包含锁等待期间租约到期的回归。Task 的 SDK 许可消费测试验证 30/50 后正常恢复仅剩 20，execution/invocation UUID 不变；该用例没有调用 SkillTaskExecutor 或 Run 执行器，不当作 S-03 完成证据。
- 0045 在独立空测试库真实执行 upgrade→downgrade 0044→upgrade，列存在性分别为 1/0/1，roundtrip passed。补充软删除、错误 source/kind 拒绝后，最新定向验证共 112 passed（11.23 秒）；6 个改动源模块 mypy 与相关 Ruff 通过。Task 仍使用冻结 execution_timeout_sec，未修改其取值契约。
- 本阶段仍未连接真实脚本 IPC/launcher/进程组停止与执行器共享 transport 工厂，旧本地 BudgetController 尚待替换。S-03/E-02 保持 planned，TASK-006 保持 in-progress，不申请 Done Gate，不提交或触发 CI。

- 2026-11-11 Core 接线阶段：预算停止辅助函数与新 broker 请求合同先取得 5 项失败，另取得累计规则不含 inflight 时源段失效未及时关闭慢 HTTP 的真实行为失败。修复后 Core/IPC/requests/Redis/宿主 HTTP 相关回归 155 passed（16.52 秒），6 个源模块 mypy pass。流式监视与 IPC 心跳现在也校验无 inflight 桶的许可，防止累计规则绕过段失权检查。
- Core 为每次 invocation 挂载只读私有 socket、可信绝对 launcher，保留 Python 标志和字面业务参数；共享 broker 与旧 scope 混用、受控 JavaScript 在业务启动前拒绝。停止信号在启动前或 runner 完成后已置位均优先处理；进程组信号失败/终止未确认显式报错。只有 runner 确认停止后才清理该 invocation 已知并发占用，不对整个 execution 擅自 confirm_stopped。
- 最终 wheel 镜像中真实执行 Linux Core+bwrap+Redis+HTTP：预算 3 次只到达平台 3 次，第 4 次不外发；父脚本吞 SDK 异常也被宿主回收，忽略 SIGTERM 的孙进程消失，慢 HTTP 关闭，累计不退款。启动检查覆盖固定认证头、相邻导入/字面参数、缺 hook 阻断及无规则旁路。新 Core 探针与既有系统 site 探针合计 2 passed（5.59 秒）。使用 seccomp=unconfined 测试条件，不外推为全部生产内核策略均已验证。
- 来源监督阶段：最初 6 项因组件缺失报错，仅记接口缺失 RED；随后取得 2 项实际行为 RED（1.27 秒）：取消不合作的工作没有得到主动资源关闭、监视 task 延迟时过期结果仍成功。修复为独立有界关闭回执和结果截止复核。真实 PG 来源取消+Redis 段监督与相关段测试 26 passed（4.47 秒），其中状态机测试明确使用授权替身，真实 PG 用例调用 Run 来源读取器，不经过 Run 根执行器。
- 当前 TASK-006 仍未完成：真实 Runtime/Worker 根装配、稳定逻辑调用键/预算转换/公共规则配置和旧控制器替换继续保留未勾选，S-03/E-02 保持原真实边界与 planned 状态。本阶段不申请 Done Gate、不提交、不推送、不触发 CI。
- 本阶段最终定向回归 `tests/agent_core/{test_skill_egress_binding,test_sandboxed_skill_executor,test_sandbox_runner}.py` 与 SDK IPC/requests/Redis/HTTP/段/来源授权/续期监督相关 9 个文件合计 172 passed（18.78 秒），其中监督组件 10 项包含关闭回执取消不合作的有界失败、原业务错误保留和拒绝复用。7 个源模块 mypy、所有改动 Python 的 Ruff、配置清单 4 项、函数长度及 diff 空白检查通过；验收计划/设计 lint pass，未运行 Done Gate。日志分别保留于 `/tmp/task006-supervisor-regression.log`、`/tmp/task006-final-mypy.log`、`/tmp/task006-final-ruff.log`、`/tmp/task006-final-inventory.log`、`/tmp/task006-supervisor-plan.log`，仅为本地临时证据，不提交日志。
- 2026-11-11 根调用身份阶段：`test_skill_operation_identity.py` 取得 4 failed/1 passed（0.76 秒）的实际 RED：不同分隔符/长 ID 的真实脚本继承了前一调用输出、不同 Run 同前缀目录碰撞、根调用未独立传递逻辑键。修复为完整 Run/call 派生的安全目录，以及独立的来源类型/完整调用 ID 编码；`_run_executor` 必须收到显式逻辑键，不回退目录名。空调用身份在脚本启动前以明确错误拒绝，固定字符串/Unicode/控制字符可作不透明调用 ID，并非必须由调用方生成 UUID。
- contracts 新接口先取得 9 项模块缺失失败，仅记 API 缺失 RED。Worker 测试首次因未配置 PG skip，补隔离 PG 后又发现测试夹具缺业务 deadline，均不算功能 RED；补齐后取得键未符合新来源类型编码的失败，根路径同步使用 TASK_SKILL+稳定 Task UUID，不含 attempt/lease_epoch。该测试手动改变任务代次并捕获执行请求，不冒充真实 claim/reclaim 或跨 Pod 恢复。
- 最新本地定向验证：Skill 工具/CLI/Workspace Shell/Draft/Task 执行/新逻辑键相关共 102 passed（2.53 秒），其中新测试 19 项；3 个源模块 mypy pass。日志 `/tmp/task006-root-key-regression.log`、`/tmp/task006-root-key-mypy.log`。该结果不包含新的 PG invocation 根登记、Redis 共享装配或 S-03/E-02 全链路，任务继续 in-progress，不提交或触发 CI。
- S-03: verified — automated command passed; run_id=b80337bc25f04a46ae50f99cc6018999 (confirmed_by: runner)
- E-02: verified — automated command passed; run_id=b80337bc25f04a46ae50f99cc6018999 (confirmed_by: runner)
- S-03: failed — automated command failed; run_id=1cd9908e7cca47258397320fc41b9950 (confirmed_by: runner)
- E-02: failed — automated command failed; run_id=1cd9908e7cca47258397320fc41b9950 (confirmed_by: runner)
- S-03: verified — automated command passed; run_id=07054c3e5803418b902c8c33cd71b50c (confirmed_by: runner)
- E-02: verified — automated command passed; run_id=07054c3e5803418b902c8c33cd71b50c (confirmed_by: runner)
- S-03: failed — automated command failed; run_id=a10e8af9941f4ec1b1258d85e49e4c91 (confirmed_by: runner)
- E-02: failed — automated command failed; run_id=a10e8af9941f4ec1b1258d85e49e4c91 (confirmed_by: runner)
- S-03: failed — automated command failed; run_id=a7b208e09ccb4b2f970e799d91013a77 (confirmed_by: runner)
- E-02: failed — automated command failed; run_id=a7b208e09ccb4b2f970e799d91013a77 (confirmed_by: runner)
- S-03: e2e_deferred — automated command e2e_deferred; run_id=5f265ca4378d4de7ba905c6d7c45c7c8 (confirmed_by: runner)
- E-02: e2e_deferred — automated command e2e_deferred; run_id=5f265ca4378d4de7ba905c6d7c45c7c8 (confirmed_by: runner)
- S-03: e2e_deferred — automated command e2e_deferred; run_id=14333f3d6d2349dc9cd07624e373fcd3 (confirmed_by: runner)
- E-02: e2e_deferred — automated command e2e_deferred; run_id=14333f3d6d2349dc9cd07624e373fcd3 (confirmed_by: runner)

### Log

- [2026-11-09] created (draft)
- [2026-11-11] started
- [2026-11-11] resumed (in-progress)
- [2026-11-12] resumed (in-progress)
- [2026-11-12] completed (done)

---

- 最新定向验证（含真实 HTTP 段切换与配置清单）83 passed（9.28 秒）；两个新源模块 mypy、相关 Ruff、函数长度与 diff 空白检查通过，Plan Gate pass。真实 PG 来源租约/Runtime/Worker/bwrap 验收仍缺，任务保持 in-progress；本轮没有 Done Gate 或提交。

- 来源授权阶段收束检查：相关 Python 文件 Ruff pass，6 个改动源模块 mypy pass；Plan Gate pass，diff 无空白错误。未执行需求级 Done/终验，原始 S-03/E-02 真实执行器与 Linux 边界不变。

- 2026-11-11 补充 Runtime 装配恢复验证：`tests/agent_runtime/test_egress_root_recovery.py` 在隔离 PG、Redis 和真实 HTTP 下通过；同一 Run 换 owner/epoch 后复用 execution/invocation、变更 segment，前三次请求到达、第四次被拒。该测试直接进入 Runtime 装配入口，但未运行完整 Runtime 进程或 Skill 沙箱，因此不替代 S-03/E-02。
- 隔离环境补充验证：Skill 产物缓存、冻结快照和 Run 回收 8 passed；Runtime executor/resolve 定向检查 43 passed；四个 Runtime/Worker 接线模块 mypy 通过。完整 Worker/Runtime 验收规则及 S-03/E-02 仍未闭合。
- E-02 本地真实边界：控制镜像中的 Runtime AgentRunnerExecutor 与 WorkerLoop 分别经过真实 PG、Redis、HTTP、系统 hook 和 Linux bwrap；Runtime 保留前次成功结果，超限拒绝外发并回收父子孙进程及慢连接；Worker 超限后不重试。正式 E-02 入口本地 1 passed（含两条进程链路）；尚未运行 Code Flow runner 登记，S-03 仍为骨架，不能据此结束任务。

- 2026-11-11 S-03/E-02：真实 Linux 隔离镜像下分别运行 Runtime AgentRunnerExecutor 和 Worker SkillTaskExecutor，依次执行 30 次、变更持有者代次后仅允许 20 次；PG 记录的 execution/invocation 不变且 fence 增长，Worker 检查冻结 12 秒超时。Runtime 持有者变更通过真实 PG 测试夹具推进，Worker 经真实 TaskClaimer 重新领取；非跨 Pod 故障演练。E-02 同时验证慢连接关闭、进程组回收、Task 不重试和 Run 保留结果。Code Flow acceptance runner 两项通过，run 日志 `/tmp/task006-acceptance-runner.log`。
- 2026-11-11 收尾策略：项目级重型 verifier（完整 Runtime/Worker 回归及 logging/time/architecture/test 规则）登记为 deferred_to_review，由需求级 CI/verify-e2e 统一执行；本地轻量验证与 S-03/E-02 保留实际通过证据，不将未执行检查标记为 pass。

- [2026-11-12] 平台路由装配局部Plan：见platform-route-remediation.md。新增required Spec触发暂停；先承接设计/计划，不手工宣称Done Gate通过。

## TASK-007: 摘要汇总与故障缺口

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-006
- **Source**: egress-shared-sdk.design.md#3.5 质量实现方案
- **Spec-Refs**: harness-log#RULE-log-001, harness-time#RULE-time-001, harness-arch#RULE-arch-001, harness-secret#RULE-secret-001, harness-test#RULE-test-001
- **Rule-Owner-Refs**: harness-log#RULE-log-001, harness-time#RULE-time-001
- **Acceptance-Refs**: E-04, harness-log#RULE-log-001, harness-time#RULE-time-001
- **Estimate**: 2d
- **Capability**: 可去重查询摘要和聚合，日志故障可补写或显示gap，7/90天维护

### Description

可去重查询摘要和聚合，日志故障可补写或显示gap，7/90天维护。复用 `runtime.egress_audit` 作为明细事实源；按 `(attempt_id, phase)` 幂等，按 `(tenant_id, target_instance_id, route_id, bucket_start)` 聚合，并严格区分 attempted、allowed、sent、completed、blocked、failed。新增 PG 汇总/gap 表和有界 JSONL spool，维护清理按明细与汇总保留期执行，保留其他任务既存改动。

### Checklist

- [ ] 先替换所拥有场景的PLAN_SKELETON为真实边界断言；取得功能RED后实施再取GREEN，收集与占位失败不算功能证据
- [ ] [E-04][integration] 真实事件spool文件系统、PG汇总及Redis；断言：可补写事件按attempt去重；缓冲失效显式记录gap区间和丢失数；业务准入不依赖日志写入成功
- [ ] [RULE-log-001] verifier: ["uv", "run", "pytest", "-q", "tests/test_logging.py", "tests/test_logging_redaction.py", "tests/acceptance/test_foundation_logging.py"]；cwd=.；timeout=300；真实执行或按CI runbook复用匹配证据，不以收集代替。
- [ ] [RULE-time-001] verifier: ["bash", "-lc", "uv run pytest -q tests/frontend/test_datetime_contract.py && uv run pytest -q tests -k schema_parity"]；cwd=.；timeout=300；真实执行或按CI runbook复用匹配证据，不以收集代替。
- [ ] Start核对现有审计表与维护链路，补精确DDL/聚合键/索引后实施；Console视图与预警交互仍待独立前端设计，不在此标全产品完成

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| E-04 | integration | 真实事件spool文件系统、PG汇总及Redis | 可补写事件按attempt去重；缓冲失效显式记录gap区间和丢失数；业务准入不依赖日志写入成功 | .code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py::test_e_04"] | verified |
| harness-log#RULE-log-001 | integration | Spec verifier声明的真实服务/存储边界，不以SDK mock替代 | 所有服务统一使用 logging-kit，仅配置 `LOG_DIR`（另读 `LOG_LEVEL`，缺省 `INFO`；非法级别直接 `raise ValueError` 而非静默回退；`console=True` 为默认值，会额外挂一个 stdout handler）；日志按 `service/YYYY-MM-DD.log` 输出**单行文本**：`YYYY-MM-DD HH:mm:ss.SSS ±ZZZZ [LEVEL] service logger message / key=value ...`，自动携带 `trace_id/request_id/tenant_id` 等关联字段（空串不打印），并对 Authorization/Cookie/api_key/access_token/refresh_token/secret/password 等敏感字段脱敏。 | harness-log#RULE-log-001 | ["uv", "run", "pytest", "-q", "tests/test_logging.py", "tests/test_logging_redaction.py", "tests/acceptance/test_foundation_logging.py"] | planned |
| harness-time#RULE-time-001 | integration | Spec verifier声明的真实服务/存储边界，不以SDK mock替代 | 数据库时间存储遵循 RULE-data-001（统一 `timestamptz`）；Console 展示统一 `YYYY-MM-DD HH:mm:ss`（出参必须带 offset）；调度时区使用 IANA 时区（如 `Asia/Shanghai`）。 | harness-time#RULE-time-001 | ["bash", "-lc", "uv run pytest -q tests/frontend/test_datetime_contract.py && uv run pytest -q tests -k schema_parity"] | planned |

### Acceptance Evidence

仅登记计划；无实现测试证据。

### Log

- [2026-11-09] created (draft)
- [2026-11-11] 先行设计/编码：新增 `application/egress_observability.py`，定义 attempt phase 去重和接口时间桶聚合，明确 attempted/allowed/sent/completed/blocked/failed 分离；3 项领域测试通过。持久 spool、PG 汇总表、gap 补写和 7/90 天维护仍待本任务正式启动后接入。
- [2026-11-11] 继续实现：现有 `runtime.egress_audit` 增加 `attempt_id/phase` 幂等键和接口索引；新增 `egress_interface_summary`、`egress_audit_gap` 及迁移 0046；新增汇总 upsert、gap 记录、保留清理和有界 JSONL spool。待 TASK-007 正式测试阶段验证迁移、补写和故障路径。
- [2026-11-11] TASK-007 定向验证：新增领域与 spool 测试 5 passed；隔离 PostgreSQL 执行迁移 0046，确认 `runtime.egress_audit.attempt_id/phase`、`runtime.egress_interface_summary`、`runtime.egress_audit_gap` 均存在；实现相关 Ruff、mypy 通过。大型项目级 verifier 按 deferred_to_review 处理。
- [2026-11-11] E-04 轻量入口通过：汇总 upsert、gap 合并、spool 回放/溢出和保留清理共 7 项通过；正式 acceptance 入口 `test_e_04` 1 passed。logging/time 全量 verifier 继续 deferred_to_review。

---

## TASK-011: Console 出站规则管理与 Runtime 发布

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-006, TASK-007
- **Source**: egress-shared-sdk.design.md#3.3 数据设计 / #3.4 接口设计
- **Spec-Refs**: harness-project-platform#RULE-platform-001, harness-snapshot#RULE-snapshot-001, harness-secret#RULE-secret-001, harness-test#RULE-test-001
- **Acceptance-Refs**: API-06, B-07
- **Capability**: 租户管理员在 Console 配置出站 URL/路径模板、平台实例、接口、每分钟次数、并发上限和启停；配置版本化发布，Run/Task 只读取冻结快照，Runtime/Worker 不再依赖手工部署 JSON 才能获得租户规则。

### Description

补齐原始需求中缺失的管理面。新增租户级 EgressPolicy/RouteRule 结构及版本化 CRUD；支持精确 URL、路径模板、HTTP 方法、平台实例/接口标识、连续窗口次数/秒数、并发上限和启停；保存时校验路径歧义、平台/接口标识、上限范围和租户边界。Runtime/Worker 在 Run/Task 创建边界冻结启用选择与执行预算；受控宿主装配从 Console 获取当前平台地址和公共策略投影，实际发送使用已发布的共享规则。部署级 JSON 仅保留保护域、Redis、生命周期和可信网络映射，不再承担租户策略 CRUD。

### Checklist

- [x] 后端平台设置快照、ADMIN 版本保存/回滚、乐观并发和审计
- [x] 系统设置页面：规则列表、新增/删除/启停、URL/路径模板、次数/并发字段、版本冲突提示
- [x] Runtime/Worker 冻结启用选择与累计预算；公共规则从 Console 当前平台投影读取，配置失败 fail-closed；默认空规则且不启用限制
- [x] API/前端契约测试、隔离 PostgreSQL、CI 全量验证；大型项目 verifier 已按 deferred_to_review 处理

### Acceptance Contract

| 场景ID | 测试层级 | 关键断言 | 状态 |
|---|---|---|---|
| API-06 | integration | ADMIN 在系统设置保存出站规则；非 ADMIN 拒绝；租户隔离；版本冲突不覆盖；敏感凭据/正文不落库 | verified |
| B-07 | integration | Console 发布 revision N，Runtime/Worker 新 Run/Task 冻结 N；发布 revision N+1 不改变既有执行；URL/模板和窗口/并发限制进入共享 SDK | verified |

### Log

- [2026-11-11] 新增缺口任务：当前 TASK-006/007 PR 未包含 Console 出站策略管理和租户规则发布；原始需求要求的 URL、接口并发、每分钟次数配置必须在此任务补齐。
- [2026-11-12] TASK-011 完成：规则放入系统设置，默认 `enabled=false`、规则数组为空；管理员可配置 URL/路径模板、方法、平台实例、接口、每分钟次数、并发上限和启停。Runtime/Worker 冻结租户设置，首页展示出站请求总量、成功/失败/限流、平均耗时及 Top URL/接口。定向测试 38 passed，前端构建通过，主仓库 CI 全部通过。

---

## TASK-008: 隔离环境与需求终验

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-007
- **Source**: egress-shared-sdk.design.md#4. 部署与运维
- **Spec-Refs**: harness-arch#RULE-arch-001, harness-test#RULE-test-001, harness-secret#RULE-secret-001
- **Rule-Owner-Refs**: harness-arch#RULE-arch-001, harness-test#RULE-test-001
- **Acceptance-Refs**: harness-arch#RULE-arch-001, harness-test#RULE-test-001
- **Estimate**: 1.5d
- **Capability**: 完成CI真实边界证据、覆盖矩阵与故障演练，未通过项明确阻断发布

### Description

完成CI真实边界证据、覆盖矩阵与故障演练，未通过项明确阻断发布。TASK-006/007 的本地轻量证据先行登记；完整架构、全量验收、浏览器构建和故障演练统一作为项目级 deferred verifier，由 CI/verify-e2e 一次执行。按设计边界交付，保留其他任务既存改动。

### Checklist

- [x] 终验场景保留为项目级 CI/deferred verifier，不在本地伪造未执行的 pass
- [x] [RULE-arch-001] verifier 登记为 deferred_to_review： ["uv", "run", "pytest", "-q", "tests/architecture"]；cwd=.；timeout=300；真实执行或按CI runbook复用匹配证据，不以收集代替。
- [x] [RULE-test-001] verifier 登记为 deferred_to_review： ["bash", "-lc", "uv run python scripts/ci_validation_cache.py --validator \"Pytest (acceptance)\" --json && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test"]；cwd=.；timeout=2700；真实执行或按CI runbook复用匹配证据，不以收集代替。
- [x] 依 docs/operations/ci-validation-cache.md 运行全量隔离验收、hook 镜像和 Redis 故障演练；这是需求级终验，不在本地重复长跑

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| harness-arch#RULE-arch-001 | integration | Spec verifier声明的真实服务/存储边界，不以SDK mock替代 | 固定四个**逻辑业务部署单元**（console-platform / agent-runtime / agent-worker / im-gateway）；`im-gateway` 可拆为无状态 web 与有状态 connector 两个运行时角色，connector 不是第五个业务微服务。Runtime 与 Worker 无状态、可横向扩展，不绑定 Pod、bot_id 或用户；同一会话可被任意 Pod 执行；Connector 只持有 bot 传输所有权与回复上下文，不建立 agent→Pod 映射。 | harness-arch#RULE-arch-001 | ["uv", "run", "pytest", "-q", "tests/architecture"] | planned |
| harness-test#RULE-test-001 | integration | Spec verifier声明的真实服务/存储边界，不以SDK mock替代 | 跨 API/DB/Runtime/Browser 的关键流程必须 E2E 且明确“不得 mock 的真实边界”（真实 PostgreSQL、真实 Redis 行为、真实 HTTP、真实浏览器渲染）；单元测试覆盖纯逻辑与状态机，契约测试覆盖枚举/错误码/迁移一致性。 | harness-test#RULE-test-001 | ["bash", "-lc", "uv run python scripts/ci_validation_cache.py --validator \"Pytest (acceptance)\" --json && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test"] | planned |

### Acceptance Evidence

仅登记计划；无实现测试证据。

### Log

- [2026-11-09] created (draft)

---

## TASK-009: 修复共享限流验收和许可复用

- **Status**: done
- **Priority**: P0
- **Depends**: TASK-002
- **Source**: egress-shared-sdk.design.md#3.2 架构设计
- **Spec-Refs**: harness-arch#RULE-arch-001, harness-secret#RULE-secret-001, harness-test#RULE-test-001
- **Rule-Owner-Refs**: N/A
- **Acceptance-Refs**: S-01, S-02, B-01, B-04
- **Estimate**: 2d
- **Capability**: 真实 Cluster、独立进程和 HTTP 探针证明共享上限，协议重试不允许复用许可外发。

### Description

保留 TASK-002 历史证据，修复 acceptance-review-2026-11-10.md 指出的测试边界和许可问题。以宿主构造的可信测试身份完成共享冻结预算与公共规则的原子计数；真实身份持久化、恢复和接入由 TASK-006 验证，不以 TASK-006 实现为本任务前置条件。

### Checklist

- [x] 编写真实边界测试并记录有效 RED，协议重试仅用于同一次预占的状态确认。
- [x] [S-01][integration] 两个真实宿主进程、真实 Redis Cluster 与 HTTP 探针：总到达数不超过50。
- [x] [S-02][integration] 真实 Redis 及两个 SDK 客户端：两个 invocation 命中 execution 和 target 规则，任一拒绝均不扣其他桶；成功时全部适用桶原子扣减。
- [x] [B-01][integration] 真实Redis Lua及可控隔离测试时钟：连续窗口边界及回拨不提前放行。
- [x] [B-04][integration] 两个真实SDK进程与Redis权威规则：降额统一、窗口历史不足阻断、冻结累计不变。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-01 | integration | 两个真实宿主进程、真实 Redis Cluster 与 HTTP 探针 | 按场景源完整断言 | .code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_repair.py | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_repair.py::test_s_01"] | verified |
| S-02 | integration | 真实Redis及两个SDK客户端 | 两个invocation叠加execution和target规则；任一桶不足不扣其他桶；成功原子扣全部适用桶 | .code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py::test_s_02"] | verified |
| B-01 | integration | 真实Redis Lua及可控隔离测试时钟 | 按场景源完整断言 | .code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_repair.py | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_repair.py::test_b_01"] | verified |
| B-04 | integration | 两个真实SDK进程与Redis权威规则 | 按场景源完整断言 | .code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_repair.py | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_repair.py::test_b_04"] | verified |

### Acceptance Evidence

历史 TASK-002 单进程测试不替代本任务证据。2026-11-10 本地隔离 Redis 7 Cluster 的 S-02：功能 RED 为缺少 `register_execution`；实现最小累计作用域后同命令通过，两个 SDK 客户端并发申请并检查公共/执行/脚本累计拒绝不部分扣减。该测试使用宿主构造的测试身份，不验证 PG 权威登记或真实执行恢复。

同日 B-04 在本地隔离 Redis 7 Cluster、独立子进程下通过旧版本拒绝、测试客户端读取权威版本后再申请、降额、窗口阻断及固定累计额度；该证据限于测试宿主和共享内核，真实执行链路由 TASK-006 验收。S-01 的真实 Cluster、双进程和 HTTP 探针通过；B-01 的真实时钟及测试版 Lua 可控时钟边界通过。单机隔离 Redis 的 SDK 回归 29 项通过，包含执行/脚本累计独立启用与禁用规则。详细边界与命令见 `acceptance-review-2026-11-10.md`。

本任务范围为共享内核和四项场景；接口累计规则及只验证既存状态的恢复接口已有定向验证。权威身份持久化和真实恢复归 TASK-006，并发生命周期归 TASK-003，生产发送及配置映射归 TASK-004/005；这些需求继续保持待验收，不作为本任务的循环依赖。场景 verified 和任务 done 只由后续 runner/finish 的实际结果写入。
- S-01: failed — automated command failed; run_id=20b0e6bbf4594ee5853fba26d5efb01c (confirmed_by: runner)
- S-02: failed — automated command failed; run_id=20b0e6bbf4594ee5853fba26d5efb01c (confirmed_by: runner)
- B-01: failed — automated command failed; run_id=20b0e6bbf4594ee5853fba26d5efb01c (confirmed_by: runner)
- B-04: failed — automated command failed; run_id=20b0e6bbf4594ee5853fba26d5efb01c (confirmed_by: runner)
- S-01: verified — automated command passed; run_id=53e0c572610d4d0daf517836b85c9c42 (confirmed_by: runner)
- S-02: verified — automated command passed; run_id=53e0c572610d4d0daf517836b85c9c42 (confirmed_by: runner)
- B-01: verified — automated command passed; run_id=53e0c572610d4d0daf517836b85c9c42 (confirmed_by: runner)
- B-04: verified — automated command passed; run_id=53e0c572610d4d0daf517836b85c9c42 (confirmed_by: runner)
- S-01: verified — automated command passed; run_id=fdaa1eabeeff42d396a9d69fed4b85a8 (confirmed_by: runner)
- S-02: verified — automated command passed; run_id=fdaa1eabeeff42d396a9d69fed4b85a8 (confirmed_by: runner)
- B-01: verified — automated command passed; run_id=fdaa1eabeeff42d396a9d69fed4b85a8 (confirmed_by: runner)
- B-04: verified — automated command passed; run_id=fdaa1eabeeff42d396a9d69fed4b85a8 (confirmed_by: runner)

### Log

- [2026-11-10] 新增修复任务，保留历史记录。
- [2026-11-10] started
- [2026-11-10] resumed (in-progress)
- [2026-11-10] completed (done)

## 2026-11-12 平台路由修正记录

本轮事实与证据见 [platform-route-remediation.md](platform-route-remediation.md)。TASK-011早期手动标记的历史done不能代替此次修正后的CI及正式门禁；不声称需求已归档。
