# ADR-A013 RuntimeProfile 参数版本化：v1 冻结 + v2 收缩

**引用**：source-review P1-02、TASK-008、ADR-A010、规则 25。

**背景**（2026-09-08 盘点，`resources/resource_specs.py`）：

- `RuntimeProfile` 现有 7 字段：`request_timeout_ms`、`max_retries`、`max_rounds`、`concurrency`、`memory_budget_mb`、`bootstrapped_from`、`default`。
- 执行期仅 `max_rounds` 明确接入（经 `ModelPolicy` 快照驱动 agent 工具循环）；其余 4 个（`request_timeout_ms`、`max_retries`、`concurrency`、`memory_budget_mb`）在执行路径零读取——`grep profile.<字段>` 无命中。模型 Provider 的超时/重试是其自有行为，不等于 Profile 同名字段生效（P1-02 核查结论）。
- 历史 Published 无版本号，全部视为隐式 v1。

**决策**：

1. **版本标识**：`RuntimeProfile.schema_version`，`v1` 缺省（历史数据无字段即 v1，可读）；未知版本 fail-closed（`profile_schema_version_unknown`），不猜。
2. **v1 冻结**：已发布的 v1（含 4 个未接入字段）可读、可执行、可回滚，存储 JSON/hash 不改写。v1 写入（旧 API）至少保留一个发布周期，之后才可拒绝。
3. **v2 收缩**（实际移除由 TASK-009/010 落，本 ADR 只定方向）：新写入只暴露有效参数；4 个未接入字段去向——`request_timeout_ms`/`max_retries` 由 Provider 自有超时重试覆盖（保留 Provider 现有行为，不迁移语义）；`concurrency`/`memory_budget_mb` 暂不承载（容量/预算另行设计）；v1 读出保留原值，v2 写入遇此 4 字段必须拒绝并指错字段，不静默丢弃。
4. **红线**：不把单次 timeout 改成总 deadline；不原地修改历史 Published；导入/回滚产生新 Draft/Version（规则 5）；回滚可执行旧版本。
5. 本 ADR 在 ADR-A010 之上：默认解析链（租户默认 + platform-default）语义不变，版本化只约束"解析到什么字段生效"。

**后果**：TASK-008 在 `resource_specs.py` 加版本标识与契约声明 + contract 测试；TASK-009 分离历史读兼容与新写校验；TASK-010 清理生产入口。
