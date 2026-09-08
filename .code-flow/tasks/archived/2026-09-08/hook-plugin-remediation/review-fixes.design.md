# Hook/Plugin review 修复局部设计

## 1 来源与范围

来源：2026-09-08 本会话 review 的三个复现缺陷；用户随后要求「进行代码修复」。延续原任务 TASK-001/002/003 的语义，修复关闭失败后的资源遗失、Post Hook 消耗模型 deadline、取消出口缺少模型结果记录。保留无状态 Runtime、固定 Snapshot 和既有 Plugin SPI。

## 2 技术设计

- 关闭：Loader 逐一尝试 shutdown，成功项移除、失败项保留；汇总错误而非提前退出。Service 总是尝试 Hook、MCP、Store 清理，仅在 Loader 清空后释放引用，全部尝试后仍有失败则抛聚合错（调用方可观测）。再次 close 只重试失败插件。初始化 rollback 保留原始初始化异常，附带可观测清理错误（`__notes__`）；每次关闭尝试有界（单插件 shutdown 默认 5000ms，与 FEAT-07 启动预算同量级），不自动无限重试。
- 模型预算：保持整个模型/Tool 循环的累计 deadline，显式扣除 Hook observer 的等待耗时；Hook 仍受 TypedEventBus 自身 timeout/fail policy 约束。预算状态属于单次执行局部变量，不存 Runtime 实例。流式同样排除观察回调耗时。
- 结果收尾：每个已经进入 Provider 调用的 attempt 在成功、异常、deadline 或单次取消时恰好报告一次结果；取消后继续传播 CancelledError。deadline 报 timeout，外部取消以现有 error 结果语义报告，避免扩大公开状态字段。双取消（上报 await 中途再取消）仅 best-effort，不为保成对而启动脱离执行的后台任务。before 阻断不制造实际 Provider 调用（预算耗尽时连 before 也不触发）。异步生成器关闭也必须完成清理，不启动脱离执行的后台任务。
- 维护性：把预算/attempt 收尾拆为 Runtime 内部模块，避免继续复制流式/非流式结果构造代码。Kernel 仍仅提供 Contract。

## 3 验收条件

| 场景 | 层级 | 真实边界 | 断言 |
|---|---|---|---|
| S-CL-01 | integration | Service.close → PluginLoader → 测试插件，PG/MCP close | 一个 shutdown 失败仍关闭其他插件与依赖，失败插件可重试，成功插件不重复关闭；全部尝试后抛聚合错 |
| E-CL-01 | integration | Service.initialize rollback → PluginLoader | 清理异常不覆盖初始化异常（notes 附带），失败插件保留可重试；单插件关闭超时上限默认 5000ms 也不跳过后续插件 |
| S-MB-01 | integration | AgentRuntime → Service Hook bridge → TypedEventBus → stub Provider | 慢 FAIL_OPEN post hook 不把模型成功变成 deadline 失败；多轮/流式保持同义 |
| S-MB-02 | integration | AgentRuntime deadline/cancel → Provider → Hook bridge | 单次取消/超时/流关闭成对记录且原取消/超时保持；双取消（上报中途再取消）仅 best-effort；无遗留任务 |
| E-MB-01 | integration | AgentRuntime 多轮/流式真实预算 | 扣除 Hook 等待仍保留累计业务 deadline，不重置每轮预算；per-attempt 超时语义不变 |

## 4 Spec Compliance Matrix

所有规则沿用已绑定 verifier，以下为逐项适用范围，非 waiver/N/A。

| Rule | 设计承接与验证方式 |
|---|---|
| RULE-backend-quality-001 | 类型完整、异常汇总不吞掉、关闭调用有界；S-01/E-01，ruff/mypy |
| RULE-backend-database-001 | 继续使用 TEST_POSTGRES_DSN；关闭仅调用 Store 抽象，不增加 SQL/事务；PG 集成回归 |
| RULE-backend-directory-001 | 预算模块留在 runtime，生命周期留在 loader/service；AST 架构测试 |
| RULE-backend-logging-001 | 复用 context.emit 与 Hook trace sink，传播原异常，不写 Secret/原始请求；S-03 |
| RULE-backend-platform-001 | 不改变 HTTP API/部署配置，dev/production 共用执行逻辑；Service 回归 |
| RULE-fluxion-console-api-001 | 通过已有异常/响应基础设施向上返回，修复不创建新 HTTP Handler 或日志字段；Service 回归与 diff 审查 |
| RULE-fluxion-console-001 | 修复仅 Runtime 执行边界，不改变 Channel 身份绑定或 Console 部署边界；架构回归与 diff 审查 |
| RULE-fluxion-dfx-001 | RED 先行、关闭故障恢复、模型预算与成对 Trace；S-01/E-01/S-02/S-03/E-02 |
| RULE-fluxion-resource-001 | Snapshot/Registry/Binding Contract 不变，PG 真实集成；Snapshot 回归 |
| RULE-fluxion-runtime-001 | 执行预算局部持有、observer 不存 Runtime、固定 Snapshot；原 TASK-001 继续唯一负责该 Rule，本轮补回归 |
| RULE-fluxion-workflow-001 | Tool 仍经既有 handler/Capability 调用，预算变更不引入 durable state；AgentLoop 回归 |
