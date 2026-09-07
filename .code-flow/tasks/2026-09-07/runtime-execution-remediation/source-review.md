# Runtime 执行整改：来源与对齐记录

- 原文：`/Users/jahan/Downloads/Fluxion 最新版本问题清单与整改建议.md`
- 原文 SHA-256：`980ab37b8be11cb29e02db9a5deeebb5703307b7af056cef889107a992ead536`
- 初始核查：`67a25a4`；写入基线：`17acf79`（后续提交为 Console 改版，七项后端证据仍在）。
- 日期：2026-09-07；本文件是仓内来源摘要，非替代权威架构的已批准 ADR。
- 用户已采纳任务方案并指示 TASK-001 挂起；其余按编号写入，不授权本轮启动编码。
- 上轮隔离复现：真实 Gateway/FastAPI + 替换业务 Service 证明 ID/错误契约问题；真实应用流式分支 + 替换依赖证明空流 fallback、cancel/aclose 不调用 finish。不是 PG/Compose/E2E 验收。

## 原文章节索引

| 问题 | 原文章节行号 | TASK |
|---|---|---|
| P0-01 多角色部署 | L34–200 | 001–003 |
| P1-01 执行身份 | L206–390 | 004–007 |
| P1-02 Profile 参数 | L394–658 | 008–012 |
| P1-03 执行生命周期 | L662–822 | 013–018 |
| P1-04 错误契约 | L826–951 | 019–022 |
| P2-01 空流式结果 | L957–1082 | 023 |
| P2-02 一致快照 | L1086–1194 | 024–026 |

## P0-01 多角色部署

- 原文来源：原文章节 L34–200（索引已回读核对）。
- 当前代码：deploy/docker/docker-compose.yml:12；backend/src/fluxion/api/production_bundle.py:120。
- 核查结论：Compose 仅 PostgreSQL/API；API 必需远程 Runtime URL。单镜像不等于单服务。
- 已采纳整改边界：一个镜像支持三角色、实例可扩缩、同 Session 跨实例、持久状态不随 Pod 消失；必须实际观测 service_instance_id，不能只验容器数量。
- 高影响风险：RISK-DEP-01：DNS/连接复用造成假分发；RISK-DEP-02：kill 后透明重放重复副作用。

## P1-01 执行身份

- 原文来源：原文章节 L206–390（索引已回读核对）。
- 当前代码：backend/src/fluxion/services/http_runtime_gateway.py:151；backend/src/fluxion/api/runtime.py:216；backend/src/fluxion/services/context_resolver.py:357。
- 核查结论：request_id 已透传；trace_id/execution_id 在 HTTP 入口重建，Resolver 又生成 execution_id。SnapshotBuilder 只修正 trace_id。
- 已采纳整改边界：唯一身份创建者、契约透传、缓存不能复用旧执行身份；并发 ContextVar 隔离；Logs/Trace/Memory/started/completed 对齐。
- 高影响风险：RISK-ID-01：缓存串身份；RISK-ID-02：并发观测信息串扰或日志泄漏。

## P1-02 Profile 参数

- 原文来源：原文章节 L394–658（索引已回读核对）。
- 当前代码：backend/src/fluxion/resources/resource_specs.py:56；backend/src/fluxion/services/context_resolver.py:232；backend/src/fluxion/runtime/model_providers.py:158。
- 核查结论：Profile 仅 max_rounds 明确接入；模型策略/Provider 已有 timeout/retry，不等于 Profile 同名字段生效。
- 已采纳整改边界：采用参数收缩方案，但先 ADR 明确版本化兼容；新 Schema 不暴露无效项；旧 Published 可读/执行/回滚且不改写；保留 Provider 已有行为。
- 高影响风险：RISK-CFG-01：直接删除类型导致旧版本不可读；RISK-CFG-02：默认工厂继续注入旧字段；RISK-CFG-03：Console虚假承诺生效。

## P1-03 执行生命周期

- 原文来源：原文章节 L662–822（索引已回读核对）。
- 当前代码：backend/src/fluxion/services/execution_session.py:36；backend/src/fluxion/services/runtime_app.py:314；backend/src/fluxion/runtime/memory.py:142。
- 核查结论：run/stream 尚无统一 finalizer；取消/GeneratorExit 绕过 except Exception；prepare 创建 context 后失败也可能失去清理所有权。flush 失败可能阻止字典 pop。
- 已采纳整改边界：明确四类终态、准备失败清理、幂等结算、有界 shield、关闭传播、Memory/Trace 故障策略；真实网络断连验证；SIGKILL 不承诺 finally。
- 高影响风险：RISK-LIFE-01：局部状态泄漏；RISK-LIFE-02：二次flush/Trace；RISK-LIFE-03：清理覆盖原始异常；RISK-LIFE-04：假断连测试。

## P1-04 错误契约

- 原文来源：原文章节 L826–951（索引已回读核对）。
- 当前代码：backend/src/fluxion/api/runtime.py:143；backend/src/fluxion/services/http_runtime_gateway.py:234。
- 核查结论：HTTP slug 拼在 message，Gateway upstream_error 丢失；SSE 单独 error 字段保留。原文示例整数码并非当前源码码值。
- 已采纳整改边界：保留统一四字段 envelope；ADR 确定 slug 位置与兼容窗口；共享 mapper/factory；真实服务错误穿过 Gateway，不能仅 Mock JSON。
- 高影响风险：RISK-ERR-01：message解析脆弱；RISK-ERR-02：堆栈/DSN/Secret泄漏；RISK-ERR-03：旧客户端兼容破坏。

## P2-01 空流式结果

- 原文来源：原文章节 L957–1082（索引已回读核对）。
- 当前代码：backend/src/fluxion/runtime/agent.py:216；backend/src/fluxion/services/runtime_app.py:391。
- 核查结论：Provider 成功空结束后 chunks 为空，应用调用 run，再请求模型。
- 已采纳整改边界：支持但空输出应直接 completed；只有明确 unsupported 才 fallback；保证一次逻辑执行身份和 finalizer 不变，流式不缓冲。
- 高影响风险：RISK-STR-01：重复模型费用与副作用；RISK-STR-02：部分输出失败被重试。

## P2-02 一致快照

- 原文来源：原文章节 L1086–1194（索引已回读核对）。
- 当前代码：backend/src/fluxion/services/context_resolver.py:134；backend/src/fluxion/services/context_resolver.py:418；backend/src/fluxion/registry/sqlalchemy_store.py:447。
- 核查结论：仅起始读取 Revision，结束不复查；多查询未共享一致事务。属于可发生风险，上一轮未做真实并发复现。
- 已采纳整改边界：优先 REPEATABLE READ（backend/database.md）；Store scoped read 不泄漏 ORM 到 Service；事务外做外部 I/O；一致视图/有界失败/缓存不污染/性能验收。
- 高影响风险：RISK-SNAP-01：混合提交视图；RISK-SNAP-02：持续变更无限重试；RISK-SNAP-03：事务内外部IO耗尽连接池。

## 跨任务约束

- 优先级继承问题清单；TASK 编号固定，不因挂起重新编号。除001外均为 draft，依赖未完成时不能启动。
- Profile 字段收缩不意味着立即删除旧 API；新契约版本、兼容入口至少一个发布周期、历史读取与回滚必须先在008确定。
- Error Contract 保留 code/message/data/request_id；slug 是扩展语义，位置由019 ADR确定，不凭原文示例越过核心契约。
- RuntimeInstance 是实际进程/Pod；AgentDefinition 是产品 Agent。runtime-core Spec Guidance 中旧“Agent=Pod”表述由 AGENTS.md 第26条更高优先级术语覆盖，本次不改该Spec。
- 数据库多查询遵循只读 REPEATABLE READ；若要替换成双读 Revision，必须先由024建立ADR并重新对齐TASK。所有相关写入必须纳入一致性分析。
- 不新增Redis/EventBus/服务JWT，不做主架构重构；Worker业务能力缺口不借此扩展，001只接入既有worker角色。
- 每个任务优先1–3个实现文件，测试文件另列。入口盘点若超出范围，先通过原生任务流程细分，不能默默扩展。
- 任务文件为单需求原生任务清单，因26个验收契约超过500行；不适用生产源码单文件职责限制。

## Spec Compliance Matrix

这是 plan-stage 承接矩阵，不伪造已批准 Design 或 code/review 验证。原文无 Design Spec Compliance Matrix，新需求无历史 applied refs；通过原生命令新建 Context，refresh 后绑定 plan。
原有 verifier 类型均保持原定义（包含 manual/project-owner）；下列自动化是明确的行为证据责任，不是对原 verifier 的降级、替换或已经通过的声明。code/review 仍待执行，不能批量标记 verified 或 N/A。

| Spec/Rule 与 verifier_ref（同值） | 唯一责任任务 | 自动化证据场景 | 责任 |
|---|---|---|---|
| fluxion-runtime-core#RULE-fluxion-runtime-001 | TASK-003 | S-DEP-03 | 无状态、不可变快照、Kernel依赖边界 |
| fluxion-resource-registry#RULE-fluxion-resource-001 | TASK-012 | B-CFG-02 | Registry版本、PG单库、tenant与Binding边界 |
| fluxion-dfx#RULE-fluxion-dfx-001 | TASK-018 | B-LIFE-01 | 有界清理、可靠性和框架性能 |
| fluxion-console-api-contract#RULE-fluxion-console-api-001 | TASK-020 | S-ERR-01 | 统一响应与错误映射 |
| backend-logging#RULE-backend-logging-001 | TASK-007 | S-ID-03 | 关联ID和日志脱敏 |
| backend-code-quality-performance#RULE-backend-quality-001 | TASK-015 | E-LIFE-02 | 异常保留、有界外部调用和可靠资源释放 |
| backend-platform-rules#RULE-backend-platform-001 | TASK-001 | S-DEP-01 | 角色部署、探针、Secret注入及远程执行 |
| backend-database#RULE-backend-database-001 | TASK-025 | S-SNAP-01 | 只读一致事务、参数化查询与tenant |
| frontend-quality-standards#RULE-frontend-quality-001 | TASK-011 | S-CFG-03 | 类型与异步三态 |
| frontend-semi-design#RULE-frontend-semi-001 | TASK-011 | S-CFG-03 | Semi与React19 adapter |
| frontend-component-specs#RULE-frontend-component-001 | TASK-011 | S-CFG-03 | 受控表单与组件/API职责 |
| fluxion-console-channel#RULE-fluxion-console-001 | TASK-007 | S-ID-03 | 正式Channel绑定与独立Runtime边界 |

## 风险与场景覆盖

| 风险 | 验收场景 |
|---|---|
| RISK-DEP-01 / RISK-DEP-02 | S-DEP-02 / E-DEP-01 |
| RISK-ID-01 / RISK-ID-02 | B-ID-02 / S-ID-03 |
| RISK-CFG-01 / RISK-CFG-02 / RISK-CFG-03 | B-CFG-01、B-CFG-02 / S-CFG-02 / S-CFG-03 |
| RISK-LIFE-01 / RISK-LIFE-02 / RISK-LIFE-03 / RISK-LIFE-04 | E-LIFE-05、B-LIFE-01 / E-LIFE-01 / E-LIFE-02 / E-LIFE-05 |
| RISK-ERR-01 / RISK-ERR-02 / RISK-ERR-03 | E-ERR-02 / E-ERR-01 / B-ERR-01 |
| RISK-STR-01 / RISK-STR-02 | S-STR-01 / E-STR-01 |
| RISK-SNAP-01 / RISK-SNAP-02 / RISK-SNAP-03 | B-SNAP-01 / E-SNAP-02 / E-SNAP-01 |
