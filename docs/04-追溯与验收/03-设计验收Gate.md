# 设计验收 Gate V1.13.1

> 本文件是设计阶段的可判定门禁，也是 `04-追溯与验收/` 下唯一的结构与承接门禁文档。
> **V1.13**：新增「总设框架 Gate A–N 承接映射」与「§5.3 架构 Gate G1–G14 承接映射」，并明确机械比对 Gate（原第三轮 60 条发现的编号索引见 `05-变更记录/09-V1.13-第三轮Review裁决修复.md` 附录）。
> **V1.13.1**：原《06-模块分档与文档拆分验收》并入 Gate 0 §0.2「结构边界」（含 6 条结构回退判定），该文件已删除；闭环台账见 `10-设计合理性Review-2026-09-12.md`。

## Gate 0：文档详细度与结构边界

### 0.1 章节详细度

核心 design-full 模块必须具备：

- 文档控制；
- 背景/痛点；
- 功能清单；
- In/Out Scope；
- RULE；
- 正常/异常场景；
- 方案选型；
- 架构/流程；
- DB；
- API；
- lifecycle；
- DFX；
- deployment/rollback；
- risk/dependency；
- traceability；
- Compliance Matrix。

禁止用“见总体设计”“待实现”替代核心设计内容。Knowledge 规划项例外，但必须写清未决问题和禁止实现边界。

### 0.2 结构边界

> 原《06-模块分档与文档拆分验收》已并入本节（V1.13.1）：文档目录本身就是架构边界，不是文件整理手段。

**后端**

- 模块目录数 **19**：`design-full.md` **18** 个 + `design-lite.md` **1** 个（16-Knowledge规划）；
- 每个模块目录**只存在一个主设计事实源**（`design-full.md` 或 `design-lite.md`），不得另开并行事实源；
- **DB Owner 与 Interface Owner 只能出现在一个模块主设计中**（其他模块只能引用）；
- Full 文档必须保留逐表字段/类型/NULL/默认值/索引/唯一与 CHECK 约束/ER，以及逐接口的请求/响应/错误码/处理逻辑；
- 上述三项由 `tests/architecture/` 的文档结构测试与《机械比对 Gate》（见文末）机械校验，不靠人工抽查。

**前端**

- 页面模块 **12** 个：11 个产品页面 + `00-Console公共框架`，每个模块独立 `design-frontend.md`；
- 每个模块必须写路由、角色、组件、字段、UI 四态、API 映射、风险；
- 公共 Shell / 列表三件套只在 `00-Console公共框架` 实现一次，不在各页面复制；
- 交互事实由 `90-Console交互规格.md` 控制；**V0.8 冻结原型已被部分取代**，凡冲突以 `90-规格` + 后端模块授权列为准，取代范围见 `03-前端设计/README.md` 的「原型被取代范围声明」。

**结构回退判定**（出现即判 Gate 失败）

1. 把全部 Console 页面合回一篇大文档；
2. 为“平均使用模板”把复杂模块降级成 Lite；
3. 同一 DB 表在多个模块重复定义；
4. 同一 API Contract 在多个模块重复定义；
5. 为每个表/每个 API 创建独立设计文件造成碎片化；
6. Knowledge 未冻结时伪造 CRUD/API/DB。

## Gate 1：交互闭环

- 所有菜单来自用户旅程（总设 §6.6 IA 的 11 个菜单一一对应）；
- 新增按钮存在且表单完整；
- 编辑字段明确；
- 详情字段明确；
- Secret 不回显；
- Builder/Admin 角色差异明确，且**按钮可见性与后端授权列一致**（ADR-046）；
- 列表 page size / 排序 / 筛选与后端 Query 一致；
- 时间完整（`YYYY-MM-DD HH:mm:ss`）；
- 详情只读规则一致；
- 路由（含新增/编辑与 Tab 子路由）在 README 与页面文档中登记一致。

## Gate 2：领域闭环

- 一个概念只能有一个事实源；
- Service 是唯一发布对象；
- Skill dependency 来自 manifest；
- identity/route/grant 三关系拆分；
- ProjectPlatform/ProjectIntegration 分离；
- Agent direct capabilities 不等于 Skill deps；
- **Capability Contract 元数据齐全**（`side_effect` 四值 / `risk_level` / `invocation_policy`），且 Chat 直调与 Worker 执行的分流**只由元数据判定**并暴露为派生字段 `direct_invocation`（ADR-057）。

## Gate 3：API/DB 闭环

- 每个 UI 写动作有 API；
- 每个 API 有 DTO/Domain；
- 每个持久字段有用途和约束；
- unique/FK/index/secret ref 明确；
- 不存在万能 JSON/万能 Resource 替代关系；
- 四类 Capability Implementation 的 `config` 必须符合 Owner 判别式 Schema，超时/重试只在 `execution_policy`、分页只在 `data_retrieval_policy`。

## Gate 4：Runtime 边界

- Runtime 无状态；
- Capability paging 不在 Skill；
- Skill 只依赖 SDK，入口是同步 `def run(ctx, input)`，`ctx.capability.call` 返回归一化数据本身（列表即数组）；
- Skill 不提交 Async Capability、不接触大结果外置（ADR-034）；
- 长任务进入 Execution；
- Async Task 属 Step detail；
- Gateway 负责 WebSocket/identity/route/grant，且运行面数据经 agent-runtime 的 `CH-DATA-01..04`（不直连业务库、不依赖 platform-api，Gate H）。

## Gate 5：DFX

### Reliability

- crash/restart；
- **租约 TTL ≥ max(step_timeout) 且长步骤有心跳**（ADR-042）；
- retry/idempotency（错误分类为唯一判定依据）；
- cancellation；
- publish transaction；
- pagination limits；
- 投递 attempt/UNKNOWN 语义（不宣称端到端 exactly-once）。

### Security

- Secret；
- IDOR；
- trusted context（`CORE-LIB-06` 是唯一构造入口）；
- sandbox（隔离子进程、fail-closed）；
- upload path traversal；
- credential isolation；
- `/internal/*` JWT audience/scope 校验。

### Observability

- request/trace/execution/step；
- audit；
- error taxonomy（`10-错误码与错误分类基线`）；
- no sensitive logs；
- `/metrics` 为 Prometheus 文本、不套 Envelope。

## Gate 6：测试

最低 E2E：

1. Service create must primary Agent；
2. Service publish；
3. Agent save immediate；
4. Skill import/new artifact；
5. Skill Mock→Dev Gateway→Production parity（含 Playbook D05 示例原样执行）；
6. Capability 10000/100 paging；
7. Two users different MSS credentials；
8. Two Bot route two Agents；
9. `/bind` identity + no-grant deny；
10. `/new`；
11. `/stop` async；
12. Multi-Pod conversation（`RT-LIB-03` 领取/恢复）；
13. Worker kill recovery（长步骤跨 TTL）；
14. large result artifact（`artifact_id` 交付与重取）；
15. Secret no readback；
16. **提案签发→跨 Pod 确认→唯一 Execution**（S-SVC-07）；
17. **Draft 测试快照（无 current release）**（S-SVC-08）；
18. **人工等待通知与 RESUME 闭环**（S-SVC-09 / S-CHAN-09）；
19. **投递重试与 UNKNOWN 对账**（S-WORK-08 / S-WORK-12）与**重新投递不重跑步骤**（S-SVC-12，`EXE-API-07`）；
20. **Redis 停机降级**（S-WORK-07）；
21. **Sandbox 强制隔离（跨 workspace、宿主路径、网络出站）**（S-WS-06 / S-WS-07）。
22. **第四轮策略与资源门禁**：步骤 `human_policy` 与 `failure_policy` 正交（B11，S-02-08）、字段级 `FIELD_ADMIN_ONLY` 原子拒绝（D2，E-07-01 / E-08-01）、`browser`/`external-scan`/`large-report` 走 PG 信号量（B4，S-WORK-13）、保留与清理按《11-数据保留与清理策略》（B3）。

## Gate 7：编码开工条件

只有以下全部成立才能拆 coding tasks：

- interaction frozen；
- design-full updated；
- API DTO frozen enough；
- logical DB frozen enough；
- migration impact known；
- test scenarios executable；
- no unresolved P0 architecture question；
- **各轮全库 Review 的 P1 项已闭合或有明确裁决**（`T-01..T-60` 编号索引见 `05-变更记录/09-V1.13-第三轮Review裁决修复.md` 附录，`R01..R25`/`N01` 见 `05-变更记录/08-V1.12.1-复审修复与收尾.md` 附录，第四轮 D/B/Q/Y/Z 的闭环台账见 `10-设计合理性Review-2026-09-12.md` 第六节）。

## Gate 8：变更规则

编码中发现设计问题时：

```text
issue
→ update interaction/design
→ review
→ update API/DB trace
→ update task
→ code
```

禁止：

```text
code workaround
→ later document what code happened to do
```

---

## Gate A–N：总设框架验收 Gate 的承接映射

> 来源：总设 §6.2（`00-总体设计/01-…完整总体设计说明书.md` 的 Gate A–N）。**每个框架 Gate 必须有负责模块、设计落点与可执行场景**；「部分」表示设计已承接但验收场景仍待补。

| 框架 Gate | 要求 | 负责模块 | 设计落点 | 验证场景 | 状态 |
|---|---|---|---|---|---|
| Gate A Identity / AuthProvider | Channel userid→Binding→PlatformUser→AuthProvider→外部 RBAC；假 user_id 无效；Session 过期可 refresh；Secret 不入 trace/log | 10 / 18 / 09 / 07 | `18` USR-LIB-01、`09` AUTH-LIB-01/02、`10` CH-DATA-02、`07` CAP-LIB-01 | S-AUTH-01/02/06、E-USER-02、E-AUTH-03 | 已承接 |
| Gate B Stateless Runtime | 两个 Runtime，kill A 后 B 续跑，User/Memory/Conversation/版本/能力一致 | 03 / 11 | `03` RT-LIB-03、`11` conversation_run + CheckpointIdentity | S-RT-01、S-RT-05、S-RT-06 | 已承接 |
| Gate C Execution Snapshot | 发布 v2 后 Execution-1 仍用 v1 | 01 / 05 / 08 / 19 | `01` CORE-LIB-05 ExecutionProjection、`05` snapshot、`08`/`19` 冻结优先 | S-SVC-03、S-SVC-07 | 已承接 |
| Gate D Worker Crash Recovery | external submit 后 SIGKILL，另一 Worker 不重复 create | 06 / 07 / 05 | `06` WORK-LIB-01/03/05、`05` async_task_run（`operation_id` 幂等） | S-WORK-02、S-WORK-08、S-WORK-11 | 已承接 |
| Gate E Long Wait | 10/30 分钟等待不占请求/协程/粘性 Worker | 06 / 05 | `06` WAIT 分支、`05` `next_run_at`/`wait_until` | S-WORK-03、B-SVC-03 | 已承接 |
| Gate F Redis Down | 停 Redis 后正确性不丢，退化为 PG polling | 06 / 14 | `06` claim 轮询 + WORK-LIB-06、`14` INFRA-LIB-05 best-effort | S-WORK-07、S-INFRA-02 | 已承接 |
| Gate G Channel Delivery | Worker 完成后按 DeliveryRoute 主动推送；重连后重试只由 Worker 触发 | 10 / 06 | `10` CH-INT-01 + channel_delivery、`06` WORK-LIB-06 | S-CHAN-05、S-WORK-12 | 已承接 |
| Gate H Control Plane Down | 停管理面后在途执行继续 | 02 / 03 / 10 / 09 | `10` CH-DATA-01..04（不经过 platform-api）、`03` 装配、`02` RULE-API-01 | S-API-05、S-CHAN-09 | 已承接 |
| Gate I Memory 与 Channel 解耦 | 跨会话/跨渠道读同一 UserMemory；key 无渠道语义 | 11 | `11` user_memory（仅 tenant+user）、MEM-LIB-01、`03` 注入 | S-MEM-02、S-MEM-03、S-MEM-06 | 已承接 |
| Gate J Capability Contract / Project Adapter | 同一 Contract 同时被 Agent 直查与 Worker Step 复用，同一 Auth/Risk 语义 | 07 / 04 / 06 | `07` CAP-LIB-01/02、`04` AGCORE-LIB-02、`01` CORE-LIB-05 | S-CAP-01/02/07、S-SVC-06 | 已承接 |
| Gate K Core Purity | Core 不依赖 MSS/WeCom；MSS + Demo 两个 Integration 共用同一 Runtime/Worker/DB | 12 / 07 | `12` INT-LIB-01/02/03、tests/architecture `test_core_purity` | S-INT-04、E-INT-01 | 已承接 |
| Gate L Knowledge 规划 | Provider 可替换、ExecutionStep 记录 retrieval evidence | 16 | 规划态，不冻结 DB/API（ADR-022） | — | 明确后置 |
| Gate M AuthProvider Replaceability | 两种认证实现（NoAuth/API-Key + MSS Session）可切换，Capability Runtime 无项目认证分支 | 12 / 09 / 07 | `12` ProviderKind=AUTH、`09` AUTH-LIB-01/02 | S-AUTH-06、S-INT-05 | 已承接 |
| Gate N Workspace / Sandbox Boundary | 绝对路径/穿越/跨 workspace 拒绝，Local 默认禁 shell，超时强制，Runtime 不直接 subprocess | 13 / 17 / 08 | `13` RULE-WS-01..05 + WS-LIB-01..03 | S-WS-04/05/06/07、E-WS-01/02 | 已承接 |

---

## §5.3 架构 Gate G1–G14 的承接映射

> 来源：总设 §5.3「Architecture Gates」。这些必须**自动化**（CI/静态扫描/故障注入），不能只写在文档里。

| 架构 Gate | 落地方式 | 当前状态 |
|---|---|---|
| G1 Stateless Runtime | `03` 无状态约束 + S-RT-01/05/06；集成测试双 Runtime | 设计已承接；测试待实现 |
| G2 Execution Snapshot | `01` CORE-LIB-05 + `05` snapshot 条件约束 | 设计已承接 |
| G3 Worker Crash Recovery | `06` WORK-LIB-01/02 + S-WORK-02/05/11；SIGKILL 故障注入 | 设计已承接；注入待实现 |
| G4 Idempotent Side Effect | `05` `effect:{operation_id}`、`07` CAP-LIB-03、`06` 幂等重放 | 设计已承接 |
| G5 Permission / On-Behalf-Of | `18` USR-LIB-01 + `09` CRED/AUTH + `01` CORE-LIB-06（身份只能来自可信 ctx） | 设计已承接 |
| G6 Credential Isolation | `05` snapshot 禁 Secret、`14` SecretProvider、`04` 前端不回显 | 设计已承接（`tests/unit/test_redaction.py` 部分覆盖） |
| G7 Control Plane Outage | Gate H 映射（上表）+ S-API-05、S-CHAN-09 | 设计已承接 |
| G8 Channel Reconnect / Delivery | `10` CH-INT-01 状态迁移 + `06` 唯一重试 owner | 设计已承接 |
| G9 Memory Cross-Channel | Gate I 映射 + S-MEM-02 | 设计已承接 |
| G10 Capability Contract | `07` Contract 元数据 + `direct_invocation` 派生结论（ADR-057）+ `04` 分流 | 设计已承接 |
| G11 Redis Non-SoT | `06`/`14` Redis 仅唤醒 + S-WORK-07 | 设计已承接 |
| G12 Local State Audit | `01`/`03` 无本地权威状态；静态扫描 | `tests/architecture/test_core_purity.py` 覆盖部分 |
| G13 Simplified Publish Model | 仅 Service 两态发布（ADR-002）+ `05` Publish | 设计已承接 |
| G14 Database ER / Constraint Gate | 表所有权/唯一键/claim 索引与 Snapshot FK 一致 | **机械比对 Gate（见下）** |

### 机械比对 Gate（G14 落地方式）

以下比对必须可在 CI 中重跑，任一差集非空即失败：

1. 模块 §3.3 的物理表集合 == `01-架构与规范/08-数据库表所有权与字段索引.md` 登记集合 == `02-模块设计/README.md` §2 集合；
2. 模块 §3.4.1 的接口集合 == `09-接口所有权与详细设计索引.md` == `02-模块设计/README.md` §3；
3. 每个模块的 Spec Compliance Matrix「验证场景」列必须写**具体场景 ID**，且该 ID 在本模块 §2.5 有定义、在 §6 追溯矩阵中被引用；
4. §2.5 场景表不得插入规则表；规则表恒为 4 列；表格内不得有空行；
5. 每条 RULE 的 verifier（场景 ID）必须存在且语义匹配。

> 第三轮 Review 的 T-55/T-13/T-15/T-16/T-17 正是这些比对失败的结果；把它们变成 CI Gate 才能防止再次漂移。
