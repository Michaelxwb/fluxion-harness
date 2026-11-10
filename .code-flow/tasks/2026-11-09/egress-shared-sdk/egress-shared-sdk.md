# Tasks: 共享出口管控 SDK

- **Source**: egress-shared-sdk.design.md
- **Created**: 2026-11-09
- **Updated**: 2026-11-10

## Proposal

以Redis共享计量替代进程内限额，不增加独立服务。旧目录与既存暂存区保持不动；不复用旧verified证据。Plan只收集骨架，实施前先替换为真实测试再记录功能RED/GREEN。

## 执行顺序与 CI 批次

保留历史任务编号，执行依赖以 `Depends` 为准：TASK-001 → TASK-002（历史，验收缺口见 TASK-009）→ TASK-009 → TASK-003 → TASK-004 / TASK-005 → TASK-006 → TASK-007 → TASK-008。TASK-004 与 TASK-005 逻辑上互不依赖，可在 TASK-003 后协调实施；TASK-006 同时依赖二者。不得为缩短路径删除监控、恢复、真实请求接线或验收要求。

TASK-009 验证共享内核，使用可信测试身份；TASK-006 验证 PG、Runtime/Worker 与实际恢复链路。两者的边界、待实现 API 和已知风险见 `task-009-frozen-budget-design.md`；租约和故障设计见 `task-003-lease-failure-design.md`。上述设计是待实现草案，现有 CI 的部分探针通过不能替代场景完整验收。

将互相依赖的实现、回归测试、任务契约和当前 CI 故障修复合并为一个可审查批次；实现期间先进行允许的静态检查，真实边界测试按用户要求在 CI 执行。仅在完整批次准备好后推送触发全量 CI；如果失败，先汇总全部分片和根因，再集中修复并重跑。有效 RED/GREEN、单项通过与全量通过分别记录；不能为减少流水线次数跳过所需验证。

## Acceptance Coverage

| 场景ID | 来源设计 | 测试层级 | 关键真实边界 | 负责任务 | 状态 | 执行命令 | cwd | timeout | 优先级 | 执行方式 | 环境 | 输入 | E2E理由 | 触发路径 | 冒烟 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| S-01 | egress-shared-sdk.design.md#2.5 验收条件 | integration | 两个真实宿主进程、真实 Redis Cluster 与 HTTP 探针 | TASK-009 | planned | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_repair.py::test_s_01"] | . | 300 | P0 | auto | ci | packages/**; apps/agent-runtime/**; apps/agent-worker/**; migrations/** | - | - | no |
| S-02 | egress-shared-sdk.design.md#2.5 验收条件 | integration | 真实Redis及两个SDK客户端 | TASK-009 | planned | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py::test_s_02"] | . | 300 | P0 | auto | ci | packages/**; apps/agent-runtime/**; apps/agent-worker/**; migrations/** | - | - | no |
| S-03 | egress-shared-sdk.design.md#2.5 验收条件 | integration | 真实PG、Redis、Runtime和Worker执行器进程 | TASK-006 | planned | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py::test_s_03"] | . | 300 | P0 | auto | ci | packages/**; apps/agent-runtime/**; apps/agent-worker/**; migrations/** | - | - | no |
| S-04 | egress-shared-sdk.design.md#2.5 验收条件 | integration | Linux受控镜像、系统site hook、真实Skill子进程及HTTP | TASK-004 | planned | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py::test_s_04"] | . | 300 | P0 | auto | ci | packages/**; apps/agent-runtime/**; apps/agent-worker/**; migrations/** | - | - | no |
| S-05 | egress-shared-sdk.design.md#2.5 验收条件 | integration | 真实Adapter、Streamable HTTP MCP、Redis和HTTP探针 | TASK-005 | planned | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py::test_s_05"] | . | 300 | P0 | auto | ci | packages/**; apps/agent-runtime/**; apps/agent-worker/**; migrations/** | - | - | no |
| E-01 | egress-shared-sdk.design.md#2.5 验收条件 | integration | 真实Redis进程、网络故障代理与HTTP探针 | TASK-003 | planned | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py::test_e_01"] | . | 300 | P0 | auto | ci | packages/**; apps/agent-runtime/**; apps/agent-worker/**; migrations/** | - | - | no |
| E-02 | egress-shared-sdk.design.md#2.5 验收条件 | integration | Linux bwrap、真实父子孙进程、Worker/Runtime、Redis及慢HTTP | TASK-006 | planned | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py::test_e_02"] | . | 300 | P0 | auto | ci | packages/**; apps/agent-runtime/**; apps/agent-worker/**; migrations/** | - | - | no |
| E-03 | egress-shared-sdk.design.md#2.5 验收条件 | integration | 真实HTTP Proxy探针与SDK客户端 | TASK-005 | planned | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py::test_e_03"] | . | 300 | P0 | auto | ci | packages/**; apps/agent-runtime/**; apps/agent-worker/**; migrations/** | - | - | no |
| E-04 | egress-shared-sdk.design.md#2.5 验收条件 | integration | 真实事件spool文件系统、PG汇总及Redis | TASK-007 | planned | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py::test_e_04"] | . | 300 | P0 | auto | ci | packages/**; apps/agent-runtime/**; apps/agent-worker/**; migrations/** | - | - | no |
| B-01 | egress-shared-sdk.design.md#2.5 验收条件 | integration | 真实Redis Lua及可控隔离测试时钟 | TASK-009 | planned | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_repair.py::test_b_01"] | . | 300 | P0 | auto | ci | packages/**; apps/agent-runtime/**; apps/agent-worker/**; migrations/** | - | - | no |
| B-02 | egress-shared-sdk.design.md#2.5 验收条件 | integration | 真实requests/urllib3、HTTP重定向及失败探针 | TASK-004 | planned | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py::test_b_02"] | . | 300 | P0 | auto | ci | packages/**; apps/agent-runtime/**; apps/agent-worker/**; migrations/** | - | - | no |
| B-03 | egress-shared-sdk.design.md#2.5 验收条件 | integration | 真实流式HTTP、Redis及宿主deadline监督 | TASK-003 | planned | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py::test_b_03"] | . | 300 | P0 | auto | ci | packages/**; apps/agent-runtime/**; apps/agent-worker/**; migrations/** | - | - | no |
| B-04 | egress-shared-sdk.design.md#2.5 验收条件 | integration | 两个真实SDK进程与Redis权威规则 | TASK-009 | planned | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_repair.py::test_b_04"] | . | 300 | P0 | auto | ci | packages/**; apps/agent-runtime/**; apps/agent-worker/**; migrations/** | - | - | no |
| B-05 | egress-shared-sdk.design.md#2.5 验收条件 | integration | 真实PG、Redis Cluster主从切换与故障演练 | TASK-003 | planned | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py::test_b_05"] | . | 300 | P0 | auto | ci | packages/**; apps/agent-runtime/**; apps/agent-worker/**; migrations/** | - | - | no |
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

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-009
- **Source**: egress-shared-sdk.design.md#3.2 架构设计
- **Spec-Refs**: harness-arch#RULE-arch-001, harness-secret#RULE-secret-001, harness-test#RULE-test-001
- **Rule-Owner-Refs**: N/A
- **Acceptance-Refs**: E-01, B-03, B-05
- **Estimate**: 2d
- **Capability**: 流式占用正确释放，故障不外发，状态缺失不恢复零额度

### Description

流式占用正确释放，故障不外发，状态缺失不恢复零额度。按设计边界交付，保留其他任务既存改动。

### Checklist

- [ ] 先替换所拥有场景的PLAN_SKELETON为真实边界断言；取得功能RED后实施再取GREEN，收集与占位失败不算功能证据
- [ ] [E-01][integration] 真实Redis进程、网络故障代理与HTTP探针；断言：受控新请求不发送；协议重试同attempt不重复扣；错误标记依赖故障而非累计耗尽
- [ ] [B-03][integration] 真实流式HTTP、Redis及宿主deadline监督；断言：读完前保持占用；只释放自身token；续租失败关闭本地传输；租约不早于强制deadline安全过期
- [ ] [B-05][integration] 真实PG、Redis Cluster主从切换与故障演练；断言：可恢复执行不按明细TTL清零；缺key或epoch不确定时阻断；不能静默恢复旧执行；若无法检测切换则该保证不得验收

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| E-01 | integration | 真实Redis进程、网络故障代理与HTTP探针 | 受控新请求不发送；协议重试同attempt不重复扣；错误标记依赖故障而非累计耗尽 | .code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py::test_e_01"] | planned |
| B-03 | integration | 真实流式HTTP、Redis及宿主deadline监督 | 读完前保持占用；只释放自身token；续租失败关闭本地传输；租约不早于强制deadline安全过期 | .code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py::test_b_03"] | planned |
| B-05 | integration | 真实PG、Redis Cluster主从切换与故障演练 | 可恢复执行不按明细TTL清零；缺key或epoch不确定时阻断；不能静默恢复旧执行；若无法检测切换则该保证不得验收 | .code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py::test_b_05"] | planned |

### Acceptance Evidence

仅登记计划；无实现测试证据。

### Log

- [2026-11-09] created (draft)

---

## TASK-004: 旧requests透明hook与私有IPC

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-003
- **Source**: egress-shared-sdk.design.md#3.2 架构设计
- **Spec-Refs**: harness-secret#RULE-secret-001, harness-arch#RULE-arch-001, harness-test#RULE-test-001
- **Rule-Owner-Refs**: harness-secret#RULE-secret-001
- **Acceptance-Refs**: S-04, B-02, harness-secret#RULE-secret-001
- **Estimate**: 2d
- **Capability**: 原requests脚本逐发送受控，CSRF不改写，跳转重试不漏计

### Description

原requests脚本逐发送受控，CSRF不改写，跳转重试不漏计。按设计边界交付，保留其他任务既存改动。

### Checklist

- [ ] 先替换所拥有场景的PLAN_SKELETON为真实边界断言；取得功能RED后实施再取GREEN，收集与占位失败不算功能证据
- [ ] [S-04][integration] Linux受控镜像、系统site hook、真实Skill子进程及HTTP；断言：无需改业务逻辑即受控；Cookie和固定CSRF保持；无hook启动自检明确失败
- [ ] [B-02][integration] 真实requests/urllib3、HTTP重定向及失败探针；断言：服务分别接收2/3次且逐次申请；未经授权跳转拒绝；自定义adapter显式声明支持或报不支持
- [ ] [RULE-secret-001] verifier: ["uv", "run", "pytest", "-q", "tests/test_logging_redaction.py", "tests/acceptance/test_foundation_ops_audit.py"]；cwd=.；timeout=300；真实执行或按CI runbook复用匹配证据，不以收集代替。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-04 | integration | Linux受控镜像、系统site hook、真实Skill子进程及HTTP | 无需改业务逻辑即受控；Cookie和固定CSRF保持；无hook启动自检明确失败 | .code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py::test_s_04"] | planned |
| B-02 | integration | 真实requests/urllib3、HTTP重定向及失败探针 | 服务分别接收2/3次且逐次申请；未经授权跳转拒绝；自定义adapter显式声明支持或报不支持 | .code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py::test_b_02"] | planned |
| harness-secret#RULE-secret-001 | integration | Spec verifier声明的真实服务/存储边界，不以SDK mock替代 | 密钥明文存于各 Owner 表（模型 `control.model_credential.api_key`、Bot `secret`、MCP `auth_secret`、用户/共享凭据 `credential_json`；平台侧只有历史休眠列 `project_platform.auth_secret`，不得读写），跨表以主键引用，不再使用 `secret_ref`/SecretProvider；密钥不得进入日志、`config_audit_log`、Snapshot、LLM Prompt 或 API 响应（对外以 `*_configured` 表达）；Console 凭据编辑弹窗的**按需回显**是下文列出的受控例外，只允许那一条显式路径回明文。 | harness-secret#RULE-secret-001 | ["uv", "run", "pytest", "-q", "tests/test_logging_redaction.py", "tests/acceptance/test_foundation_ops_audit.py"] | planned |

### Acceptance Evidence

仅登记计划；无实现测试证据。

### Log

- [2026-11-09] created (draft)

---

## TASK-005: Adapter和MCP统一发送边界

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-003
- **Source**: egress-shared-sdk.design.md#3.2 架构设计
- **Spec-Refs**: harness-mcp#RULE-mcp-001, harness-project-platform#RULE-platform-001, harness-arch#RULE-arch-001, harness-secret#RULE-secret-001, harness-test#RULE-test-001
- **Rule-Owner-Refs**: harness-mcp#RULE-mcp-001, harness-project-platform#RULE-platform-001
- **Acceptance-Refs**: S-05, E-03, harness-mcp#RULE-mcp-001, harness-project-platform#RULE-platform-001
- **Estimate**: 1.5d
- **Capability**: 认证与MCP逐attempt计量，Proxy响应语义保留

### Description

认证与MCP逐attempt计量，Proxy响应语义保留。按设计边界交付，保留其他任务既存改动。

### Checklist

- [ ] 先替换所拥有场景的PLAN_SKELETON为真实边界断言；取得功能RED后实施再取GREEN，收集与占位失败不算功能证据
- [ ] [S-05][integration] 真实Adapter、Streamable HTTP MCP、Redis和HTTP探针；断言：每个物理发送各计一次；超限不发送；不在运行时新增tools/list
- [ ] [E-03][integration] 真实HTTP Proxy探针与SDK客户端；断言：状态头正文保持原语义，标记UPSTREAM；已发送计数不返还；不变成本地拒绝
- [ ] [RULE-mcp-001] verifier: ["uv", "run", "pytest", "-q", "tests/console_mcp/test_mcp_rules.py"]；cwd=.；timeout=300；真实执行或按CI runbook复用匹配证据，不以收集代替。
- [ ] [RULE-platform-001] verifier: ["bash", "-lc", "uv run pytest -q tests -k schema_parity"]；cwd=.；timeout=300；真实执行或按CI runbook复用匹配证据，不以收集代替。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-05 | integration | 真实Adapter、Streamable HTTP MCP、Redis和HTTP探针 | 每个物理发送各计一次；超限不发送；不在运行时新增tools/list | .code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py::test_s_05"] | planned |
| E-03 | integration | 真实HTTP Proxy探针与SDK客户端 | 状态头正文保持原语义，标记UPSTREAM；已发送计数不返还；不变成本地拒绝 | .code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py::test_e_03"] | planned |
| harness-mcp#RULE-mcp-001 | integration | Spec verifier声明的真实服务/存储边界，不以SDK mock替代 | V1 仅支持 Streamable HTTP；Tool Catalog 由 `discover-tools` 唯一维护并落 PostgreSQL；用户范围仅 Server 级（ALL/SELECTED），不做 Tool 级启停或授权；MCP Tool 必须进入统一 ToolRegistry。 | harness-mcp#RULE-mcp-001 | ["uv", "run", "pytest", "-q", "tests/console_mcp/test_mcp_rules.py"] | planned |
| harness-project-platform#RULE-platform-001 | integration | Spec verifier声明的真实服务/存储边界，不以SDK mock替代 | ProjectPlatform 只保存实例与寻址配置，认证/Session 由 PlatformAdapter 承担（SPI 无独立 `refresh`，刷新属于 `authenticate` 内部）；凭据明文存于凭据表并由主键引用，`credential_mode` 仅表达选择策略（USER_ONLY/SHARED_ONLY/USER_THEN_SHARED/NONE）；PlatformSession 为可重建 Redis 缓存，键为 `platform_session:{tenant_id:platform_id}:{actor_scope}:{credential_version}:{adapter_key}:{adapter_version}`（`{tenant_id:platform_id}` 是字面 hash tag，session/index/generation/lock 同 slot，保证 4-key Lua 在 Redis Cluster 可执行），索引 `platform_sessions:{tenant_id:platform_id}` 以 Set 清理；更换 adapter_key 必须使旧凭据与会话失效。 | harness-project-platform#RULE-platform-001 | ["bash", "-lc", "uv run pytest -q tests -k schema_parity"] | planned |

### Acceptance Evidence

仅登记计划；无实现测试证据。

### Log

- [2026-11-09] created (draft)

---

## TASK-006: 持久执行身份与宿主进程监督

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-004, TASK-005
- **Source**: egress-shared-sdk.design.md#3.3 数据设计
- **Spec-Refs**: harness-data#RULE-data-001, harness-skill#RULE-skill-001, harness-snapshot#RULE-snapshot-001, harness-worker#RULE-worker-001, harness-arch#RULE-arch-001, harness-secret#RULE-secret-001, harness-test#RULE-test-001
- **Rule-Owner-Refs**: harness-data#RULE-data-001, harness-skill#RULE-skill-001, harness-snapshot#RULE-snapshot-001, harness-worker#RULE-worker-001
- **Acceptance-Refs**: S-03, E-02, harness-data#RULE-data-001, harness-skill#RULE-skill-001, harness-snapshot#RULE-snapshot-001, harness-worker#RULE-worker-001
- **Estimate**: 2d
- **Capability**: 恢复和Worker重领不清累计，次数耗尽回收脚本及孙进程

### Description

恢复和Worker重领不清累计，次数耗尽回收脚本及孙进程。TASK-009 使用宿主构造的可信测试身份验证共享内核；本任务负责权威身份持久化、Runtime/Worker 真实恢复及请求链路，不以后者作为 TASK-009 的前置依赖。按设计边界交付，保留其他任务既存改动。

### Checklist

- [ ] 先替换所拥有场景的PLAN_SKELETON为真实边界断言；取得功能RED后实施再取GREEN，收集与占位失败不算功能证据
- [ ] [S-03][integration] 真实PG、Redis、Runtime和Worker执行器进程；断言：execution/invocation不变，仅剩20；segment变更不清零；Worker使用冻结execution_timeout_sec
- [ ] [E-02][integration] Linux bwrap、真实父子孙进程、Worker/Runtime、Redis及慢HTTP；断言：N次可完成，N+1不发送；宿主killpg回收进程组和请求；Task不重试；Run保留已完成结果
- [ ] [RULE-data-001] verifier: ["uv", "run", "pytest", "-q", "tests", "-k", "schema_parity"]；cwd=.；timeout=600；真实执行或按CI runbook复用匹配证据，不以收集代替。
- [ ] [RULE-skill-001] verifier: ["uv", "run", "pytest", "-q", "tests/test_skill_artifact_cache.py"]；cwd=.；timeout=300；真实执行或按CI runbook复用匹配证据，不以收集代替。
- [ ] [RULE-snapshot-001] verifier: ["bash", "-lc", "uv run pytest -q tests/agent_runtime/test_snapshot_freeze.py tests/agent_runtime/test_run_reaper.py && uv run pytest -q tests/agent_runtime -k \"executor or resolve\""]；cwd=.；timeout=600；真实执行或按CI runbook复用匹配证据，不以收集代替。
- [ ] [RULE-worker-001] verifier: ["bash", "-lc", "uv run pytest -q tests/agent_worker && uv run pytest -q tests/agent_runtime --ignore=tests/agent_runtime/test_runner_executor.py"]；cwd=.；timeout=1800；真实执行或按CI runbook复用匹配证据，不以收集代替。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-03 | integration | 真实PG、Redis、Runtime和Worker执行器进程 | execution/invocation不变，仅剩20；segment变更不清零；Worker使用冻结execution_timeout_sec | .code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py::test_s_03"] | planned |
| E-02 | integration | Linux bwrap、真实父子孙进程、Worker/Runtime、Redis及慢HTTP | N次可完成，N+1不发送；宿主killpg回收进程组和请求；Task不重试；Run保留已完成结果 | .code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py::test_e_02"] | planned |
| harness-data#RULE-data-001 | integration | Spec verifier声明的真实服务/存储边界，不以SDK mock替代 | 产品表统一 `id/is_deleted/create_time/update_time`；普通软删除实体用 partial unique `WHERE is_deleted=false`，稳定身份注册表 `runtime.user_memory` / `memory_scope` 的完整唯一键包含 tombstone，append-only history/receipt/job 保持 `is_deleted=false` 且使用完整唯一约束；时间统一 `timestamptz`；同 Owner Schema 的业务归属用物理 FK，跨 Owner Schema 与需跨清理保留的审计来源引用用逻辑 UUID；JSON 配置用 `jsonb`，关键查询字段必须有物理列。 | harness-data#RULE-data-001 | ["uv", "run", "pytest", "-q", "tests", "-k", "schema_parity"] | planned |
| harness-skill#RULE-skill-001 | integration | Spec verifier声明的真实服务/存储边界，不以SDK mock替代 | Artifact Store 为 **RWX PVC**（硬事实是 `accessModes: ReadWriteMany`，`deploy/k8s/base/pvc-artifacts.yaml`；是否落在 NFS 由部署侧 StorageClass 决定，清单里 `storageClassName` 并未固定；V1 不建 MinIO/S3）；数据库只保存相对 `storage_key`；Runtime/Worker 使用 emptyDir 本地缓存并在校验 checksum 后原子切换 READY；禁止直接从 NFS 目录执行 Python Skill；同一 checksum 并发首次加载必须 singleflight（**限每进程内**：跨 Pod 的正确性由 `os.replace` 幂等与 DB CAS 承载，不得当作跨进程互斥）；Artifact 写入不可变（**目标不存在才发布**：临时文件 + `os.link`，同 `storage_key` 二次写入抛 `FileExistsError`）；导入 DB 事务失败同步删除已写文件，进程级孤儿由 `cleanup-skill-orphans` CLI 宽限期扫描清理（宽限期保护进行中事务）。 | harness-skill#RULE-skill-001 | ["uv", "run", "pytest", "-q", "tests/test_skill_artifact_cache.py"] | planned |
| harness-snapshot#RULE-snapshot-001 | integration | Spec verifier声明的真实服务/存储边界，不以SDK mock替代 | 每个新 Run/Task 在执行前冻结 RuntimeSnapshot/execution snapshot（Agent/Model/Skill/MCP 版本、Prompt 模板版本、catalog revision/hash；**预算只属 execution snapshot 的 `budget` 键**——Run 侧的 `RuntimeSnapshot` 无 budget 列，等价载体是 `policy_json`，默认含 `max_model_retries`，预算与压缩策略等执行期策略一并冻结在此键）；配置或授权变更只影响后续新 Run/Task；终态写入必须 CAS。 | harness-snapshot#RULE-snapshot-001 | ["bash", "-lc", "uv run pytest -q tests/agent_runtime/test_snapshot_freeze.py tests/agent_runtime/test_run_reaper.py && uv run pytest -q tests/agent_runtime -k \"executor or resolve\""] | planned |
| harness-worker#RULE-worker-001 | integration | Spec verifier声明的真实服务/存储边界，不以SDK mock替代 | PostgreSQL 是 Task/Schedule/lease 的唯一权威源；Redis 仅作 wake-up/cancel hint；`task_type` 仅 `SKILL/BATCH`（外部等待用 WAITING 表达）；claim 使用 `FOR UPDATE SKIP LOCKED`，进入 WAITING 释放 lease，deadline 到期由 Scheduler sweep 置 `FAILED(TASK_DEADLINE_EXCEEDED)`。**deadline 是领取与收尾两侧的硬条件**：claim 排除 `deadline_at <= now` 的行（过期任务绝不执行），执行跨过 deadline 也不得写 `COMPLETED`，同样以 `FAILED(TASK_DEADLINE_EXCEEDED)` 收尾（`worker/claimer.py` 的 `_claimable_conditions`、`worker/service.py` 的终态守卫）。 | harness-worker#RULE-worker-001 | ["bash", "-lc", "uv run pytest -q tests/agent_worker && uv run pytest -q tests/agent_runtime --ignore=tests/agent_runtime/test_runner_executor.py"] | planned |

### Acceptance Evidence

仅登记计划；无实现测试证据。

### Log

- [2026-11-09] created (draft)

---

## TASK-007: 摘要汇总与故障缺口

- **Status**: draft
- **Priority**: P0
- **Depends**: TASK-006
- **Source**: egress-shared-sdk.design.md#3.5 质量实现方案
- **Spec-Refs**: harness-log#RULE-log-001, harness-time#RULE-time-001, harness-arch#RULE-arch-001, harness-secret#RULE-secret-001, harness-test#RULE-test-001
- **Rule-Owner-Refs**: harness-log#RULE-log-001, harness-time#RULE-time-001
- **Acceptance-Refs**: E-04, harness-log#RULE-log-001, harness-time#RULE-time-001
- **Estimate**: 2d
- **Capability**: 可去重查询摘要和聚合，日志故障可补写或显示gap，7/90天维护

### Description

可去重查询摘要和聚合，日志故障可补写或显示gap，7/90天维护。按设计边界交付，保留其他任务既存改动。

### Checklist

- [ ] 先替换所拥有场景的PLAN_SKELETON为真实边界断言；取得功能RED后实施再取GREEN，收集与占位失败不算功能证据
- [ ] [E-04][integration] 真实事件spool文件系统、PG汇总及Redis；断言：可补写事件按attempt去重；缓冲失效显式记录gap区间和丢失数；业务准入不依赖日志写入成功
- [ ] [RULE-log-001] verifier: ["uv", "run", "pytest", "-q", "tests/test_logging.py", "tests/test_logging_redaction.py", "tests/acceptance/test_foundation_logging.py"]；cwd=.；timeout=300；真实执行或按CI runbook复用匹配证据，不以收集代替。
- [ ] [RULE-time-001] verifier: ["bash", "-lc", "uv run pytest -q tests/frontend/test_datetime_contract.py && uv run pytest -q tests -k schema_parity"]；cwd=.；timeout=300；真实执行或按CI runbook复用匹配证据，不以收集代替。
- [ ] Start核对现有审计表与维护链路，补精确DDL/聚合键/索引后实施；Console视图与预警交互仍待独立前端设计，不在此标全产品完成

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| E-04 | integration | 真实事件spool文件系统、PG汇总及Redis | 可补写事件按attempt去重；缓冲失效显式记录gap区间和丢失数；业务准入不依赖日志写入成功 | .code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py::test_e_04"] | planned |
| harness-log#RULE-log-001 | integration | Spec verifier声明的真实服务/存储边界，不以SDK mock替代 | 所有服务统一使用 logging-kit，仅配置 `LOG_DIR`（另读 `LOG_LEVEL`，缺省 `INFO`；非法级别直接 `raise ValueError` 而非静默回退；`console=True` 为默认值，会额外挂一个 stdout handler）；日志按 `service/YYYY-MM-DD.log` 输出**单行文本**：`YYYY-MM-DD HH:mm:ss.SSS ±ZZZZ [LEVEL] service logger message / key=value ...`，自动携带 `trace_id/request_id/tenant_id` 等关联字段（空串不打印），并对 Authorization/Cookie/api_key/access_token/refresh_token/secret/password 等敏感字段脱敏。 | harness-log#RULE-log-001 | ["uv", "run", "pytest", "-q", "tests/test_logging.py", "tests/test_logging_redaction.py", "tests/acceptance/test_foundation_logging.py"] | planned |
| harness-time#RULE-time-001 | integration | Spec verifier声明的真实服务/存储边界，不以SDK mock替代 | 数据库时间存储遵循 RULE-data-001（统一 `timestamptz`）；Console 展示统一 `YYYY-MM-DD HH:mm:ss`（出参必须带 offset）；调度时区使用 IANA 时区（如 `Asia/Shanghai`）。 | harness-time#RULE-time-001 | ["bash", "-lc", "uv run pytest -q tests/frontend/test_datetime_contract.py && uv run pytest -q tests -k schema_parity"] | planned |

### Acceptance Evidence

仅登记计划；无实现测试证据。

### Log

- [2026-11-09] created (draft)

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

完成CI真实边界证据、覆盖矩阵与故障演练，未通过项明确阻断发布。按设计边界交付，保留其他任务既存改动。

### Checklist

- [ ] 先替换所拥有场景的PLAN_SKELETON为真实边界断言；取得功能RED后实施再取GREEN，收集与占位失败不算功能证据
- [ ] [RULE-arch-001] verifier: ["uv", "run", "pytest", "-q", "tests/architecture"]；cwd=.；timeout=300；真实执行或按CI runbook复用匹配证据，不以收集代替。
- [ ] [RULE-test-001] verifier: ["bash", "-lc", "uv run python scripts/ci_validation_cache.py --validator \"Pytest (acceptance)\" --json && npm --prefix apps/console-platform/frontend run build && npm --prefix e2e test"]；cwd=.；timeout=2700；真实执行或按CI runbook复用匹配证据，不以收集代替。
- [ ] 依docs/operations/ci-validation-cache.md获得全量隔离验收与review；检查hook镜像真实接线及Redis故障演练；所有15个场景执行完毕才能需求终验，不能仅靠规范verifier通过

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

- **Status**: in-progress
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

- [ ] 编写真实边界测试并记录有效 RED，协议重试仅用于同一次预占的状态确认。
- [ ] [S-01][integration] 两个真实宿主进程、真实 Redis Cluster 与 HTTP 探针：总到达数不超过50。
- [ ] [S-02][integration] 真实 Redis 及两个 SDK 客户端：两个 invocation 命中 execution 和 target 规则，任一拒绝均不扣其他桶；成功时全部适用桶原子扣减。
- [ ] [B-01][integration] 真实Redis Lua及可控隔离测试时钟：连续窗口边界及回拨不提前放行。
- [ ] [B-04][integration] 两个真实SDK进程与Redis权威规则：降额统一、窗口历史不足阻断、冻结累计不变。

### Acceptance Contract

| 场景ID | 测试层级 | 不得 Mock 的真实边界 | 关键断言 | 测试文件 / 用例 | 执行命令 | 状态 |
|---|---|---|---|---|---|---|
| S-01 | integration | 两个真实宿主进程、真实 Redis Cluster 与 HTTP 探针 | 按场景源完整断言 | .code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_repair.py | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_repair.py::test_s_01"] | planned |
| S-02 | integration | 真实Redis及两个SDK客户端 | 两个invocation叠加execution和target规则；任一桶不足不扣其他桶；成功原子扣全部适用桶 | .code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_acceptance.py::test_s_02"] | planned |
| B-01 | integration | 真实Redis Lua及可控隔离测试时钟 | 按场景源完整断言 | .code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_repair.py | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_repair.py::test_b_01"] | planned |
| B-04 | integration | 两个真实SDK进程与Redis权威规则 | 按场景源完整断言 | .code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_repair.py | ["uv", "run", "pytest", "-q", ".code-flow/tasks/2026-11-09/egress-shared-sdk/planned_tests/test_egress_shared_repair.py::test_b_04"] | planned |

### Acceptance Evidence

尚未执行；历史 TASK-002 单进程测试不替代本任务证据。本轮已增加 attempt 目标/租约绑定及许可单次消费的回归用例，并实现对应原子操作；尚未取得 RED/GREEN 或真实 Cluster/跨进程/HTTP 证据。按用户要求，通过网页 CI 运行测试。

### Log

- [2026-11-10] 新增修复任务，保留历史记录。
- [2026-11-10] started
