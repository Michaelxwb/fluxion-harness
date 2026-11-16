---
{"description":"用户长期记忆 v2 需求基线","requirement":"user-memory-retrieval-optimization"}
---
# 用户长期记忆 v2（准确率优先）PRD

> **版本**: v1.3
> **日期**: 2026-11-14
> **状态**: 需求基线，待与设计/场景一起评审
> **设计**: `user-memory-retrieval-optimization.backend.design.md`、`user-memory-retrieval-optimization.frontend.design.md`
> **场景**: `user-memory-retrieval-optimization.scenarios.md`
> **评测**: `user-memory-retrieval-optimization.evaluation.md`
> **v1.3 同步**: 修正 UNKNOWN 后台处理表述；补齐实体门控、SET tombstone 恢复；单条删除不递增 `forget_epoch`；验收指标引用评测契约的 query-macro/配对 ΔCI 口径。

## 1. 背景

现有用户长期记忆已具备 PG 持久化、确认、版本和基础检索，但对话中仍存在：

- 同一事实因不同 `memory_key`/来源前缀并存；
- Task Family 命中可直接绕过文本门槛；
- 内部/外部等适用条件混用；
- 旧值、推断值、过期值进入 Prompt；
- 管理员看不懂自由 key/category，无法有效治理；
- 缺少可复现的检索质量评测。

本需求是开发阶段的 **Memory v2 原地重构**，不做兼容层、双写或历史数据迁移，不新建记忆微服务。

## 2. 目标

### 2.1 核心目标

在对话中只向 Agent 提供与当前请求相关、来源可信、条件匹配、时间有效且无未解决冲突的用户记忆；优先提升 PREFERENCE / FACT / NOTE 的注入准确率。

### 2.2 非目标

- 不重建 PROCEDURE/EPISODE 经验体系；
- 不改变 TASK_STATE/resume/确认的协议语义；
- 不做客户/项目全局知识库；
- 不引入 Embedding/向量库；
- 不承诺普通用户自助只读记忆；
- 不做零 PG 的 Redis 头协议。

## 3. 用户与角色

| 角色 | 诉求 |
|------|------|
| 普通用户 | 明确保存的偏好/事实在相关任务中被准确使用；纠正后只用新值；不同 scope 不混用 |
| 管理员 | 在用户详情查看/编辑/启用禁用/删除/恢复/清空记忆；处理待确认候选；维护别名/显示名但不能改身份 schema |
| 开发者/测试 | 用固定合成数据集复现基线、对照 IDF/BM25/slot/alias，防止回归 |

## 4. 功能需求

| ID | 需求 | 优先级 |
|----|------|--------|
| FR-01 | LLM 从显式保存/Run 结果中提炼结构化候选，保留来源、条件、否定、单位、标签 | P0 |
| FR-02 | 受控 taxonomy：predicate/kind/cardinality/scope code/value schema/别名/priority | P0 |
| FR-03 | SINGLE/SET/NOTE 稳定身份；LLM 不自造 key；v1 禁止编辑身份字段 | P0 |
| FR-04 | 显式最新值优先；推断不覆盖；不同推断进 PENDING；同内容权威变化升版本 | P0 |
| FR-05 | scope 三态 ABSENT/UNKNOWN/EXPLICIT；未知不扩大；特定覆盖全局 | P0 |
| FR-06 | 检索：偏好 lane + 事实 slot/别名 + 二值 BM25 + NOTE 标签/实体门控 + 经验/TASK_STATE 原逻辑 | P0 |
| FR-07 | PG head 权威新鲜度；Redis 只缓存 PREF/FACT/NOTE payload；Redis 不可用回退 PG | P0 |
| FR-08 | Console 用户详情记忆治理：列表/创建/编辑/启用禁用/删除/恢复/清空/回收站/历史/待确认 | P1 |
| FR-09 | 系统设置：三档预设 + 推断开关；taxonomy 受控维护 | P1 |
| FR-10 | 合成评测：分阶段指标、盲测、基线、经验 lane 单独报告 | P0 |

## 5. 关键规则

1. 所有读写限定在可信 `tenant_id + user_id`。
2. POST 只允许创建不存在的身份；已存在必须用 PATCH + `expected_version` 更新，已删除的同值身份走恢复（restore），不能重建。
3. 单条删除保留 tombstone/version、递增 revision、撤销该 key 的 PENDING，不递增 `forget_epoch`；清空递增 `forget_epoch` 并撤销所有 PENDING。
4. 未知 scope 不降级为全局；显式保存拒绝，后台拒绝并记录，不生成 PENDING。
5. 全局默认与特定 scope 不同时注入；多个无法比较的特定值冲突时不注入。
6. 同步检索不新增独立 LLM 调用；LLM 只在写入侧提炼/清洗。
7. 用户记忆 lane 不允许 Task Family 直通；经验 lane 保持现状并单独评测。
8. v1 治理面仅 ADMIN；普通用户只读另立需求。
9. taxonomy code/schema 不可由管理员修改；save/restore/seed 必须走统一校验。
10. 不 drop 被 `model_call_lease` 引用的 `memory_consolidation_job`。
11. 带 entity 标签的候选必须有正向实体匹配；候选全部 entity 标签都要出现在 query 中；无匹配时带 entity 标签的候选不注入。

## 6. 验收

- 场景：`user-memory-retrieval-optimization.scenarios.md` 的 S-01…S-13、E-01…E-12、B-01…B-05。
- 评测：`user-memory-retrieval-optimization.evaluation.md`。
- 硬约束：安全/冲突/过期/跨 scope 的 Forbidden Injection 为 0；跨租户/用户泄漏为 0；UNKNOWN 扩大为全局为 0；无正向实体匹配或候选标签未全部命中时，仍注入带 entity 标签的候选 = 0。
- 质量：按评测契约的 query-macro 主聚合与配对差值：Required Recall 的 ΔR 点估计 ≥ 0 且 CI 下界 ≥ -1pp；Strict Conditional Precision 的 ΔP 点估计 ≥ +5pp 且 CI 下界 > 0；Update Correctness 纠正集 100%。
- 性能：PG head、检索、`/chat` 增量、命中率、payload bypass 按设计 NFR 压测确认；未实测不写成生产 SLA。

## 7. 依赖与风险

| 依赖/风险 | 说明 |
|-----------|------|
| 现有 Runtime/Console/PG/Redis/模型池 | 不新建服务/集群 |
| taxonomy 质量 | 需要产品/研发评审；管理员只维护别名/显示名 |
| LLM 提炼质量 | 结构化失败降级 NOTE；用户确认保留 |
| 合成数据 | 不能证明生产泛化；盲测和后续真实样本补齐 |
| 共享 Redis 容量 | payload ≤512KB 或 bypass；监控 OOM/淘汰 |
| 迁移 | 保留 job/scope/receipt；从当前 Alembic head 升级测试 |

---

*文档结束*
