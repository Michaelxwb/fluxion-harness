# ADR-A010 RuntimeProfile 默认解析链：租户默认 + platform-default，废弃同名回退

**引用**：ADR-A003、REQ-RUN-002、REQ-RUN-003、ARCH-02、ARCH-03、规则 25（Contract 变更须经 ADR）。

**背景**：

`AgentDefinition.runtime_profile_ref` 允许为空，schema 注释声明「留空由解析层取租户默认」（`agents/definitions.py`）。但当前实现（2026-09-02 核实）：

- 解析层在为空时回退 `RuntimeProfile.id == Agent.id` 同名约定（`services/context_resolver.py:175-191`），租户默认零实现；
- 同名回退另散布 `services/agents_app.py:112-120`、`services/channel_app.py:259-271`、`api/studio.py:178-186`、`runtime/context.py:26-27`，多处注释自认「迁移/fixture 约定」；
- dev 自举 `ensure_runtime_profile` 只保证 profile → 同名 agent 方向，反向不存在；
- Console 新建 Agent（不配 ref）发布执行即 `runtime_profile_not_found`，Golden Path 断裂。

**候选**：

1. 保留同名回退，仅文档化为显式约定；
2. 引入租户默认解析链：未配置 → Tenant Default RuntimeProfile → `platform-default`；删除同名回退；
3. 强制 `runtime_profile_ref` 必填，把默认选择交给 Console 前端。

**决策**：选 2（Golden Path 要求「从空租户建 Agent 即可运行」，选 1 保留隐式耦合，选 3 把复杂度转嫁给管理员）。

解析链定型：

```text
AgentDefinition.runtime_profile_ref
        │
        ├── 已配置 → exact RuntimeProfile（现状语义不变）
        │
        └── 未配置
              ↓
        Tenant Default RuntimeProfile（同租户 RUNTIME_PROFILE 中 default=true 的唯一版本）
              ↓（不存在时）
        platform-default（系统内置 RuntimeProfile，registry bootstrap 保证存在）
```

细则：

- **Tenant Default 标记**：`RuntimeProfileDefinition` spec 新增 `default: bool = False`；同租户同 kind 至多一个 `default=true`，Registry 层校验（新增 default 时自动取消旧 default，或拒绝并存——实现取拒绝并存，fail-closed 语义一致）；
- **版本更替不延续 default**：同资源发布新版本后，默认身份按**当前 published 版本**的 spec 判断——新版本不带 `default=true` 即视为放弃租户默认（与 Published Resource 不可原地修改一致，fail-closed）；唯一性校验同 resource_id 版本更替不受限；
- **platform-default**：bootstrap/seed 负责创建系统级 `platform-default` RuntimeProfile（资源 ID 固定，租户 scope 内置资源），缺失视为部署异常；dev 自举 `ensure_runtime_profile` 幂等确保其存在；
- **同名回退删除**：上述五处 `profile_id = agent_id` 回退全部移除，不保留兼容层（同类迁移先例：ADR-A008 废弃 `MODEL`）；
- **dev 自举调整**：`ensure_runtime_profile` 改为创建/标记 tenant default（CLI `--bootstrap` 请求带 `default=true`），不再依赖「profile+agent 同名成对」；
- **Console 投影**：普通管理员的 Agent 创建/编辑不暴露 `runtime_profile_id` 选择（§4.1 整改要求），高级运行设置仅对显式配置开放；
- **发布校验**：显式 ref 时校验指向的 RuntimeProfile 存在且 published；未配置时校验租户默认可解析。

**代价**：

- 存量同名 fixture/e2e 数据需迁移（显式 ref 或 tenant default）；
- 五处代码删除 + `RuntimeProfileDefinition` 契约变更（规则 25，经本 ADR 授权）；
- 需要新增 tenant default 的唯一性校验与幂等 bootstrap。

**失败模式**：

- 无 tenant default 且 `platform-default` 缺失 → fail-closed，错误码 `runtime_profile_default_missing`（与单版本 `runtime_profile_not_found` 区分，运维可直接定位是「默认链断链」而非「版本缺失」）；
- 同租户出现多个 `default=true` → Registry 写入即拒绝，不静默取首个。

**验收**：

- Console 新建 Agent（`runtime_profile_ref=None`）→ 发布 → 执行 Resolve 成功（空租户、无同名 seed，B-S-01）；
- 无租户默认且无 platform-default → `runtime_profile_default_missing` fail-closed（B-E-01）；
- SQLite/PG 同 Contract 测试覆盖 default 唯一性。

**重新评估条件**：

- 若出现租户内分级默认（按 Agent 类型/负载等级选不同默认 Profile）的治理需求，再评估解析链扩展；否则维持两级回退。
