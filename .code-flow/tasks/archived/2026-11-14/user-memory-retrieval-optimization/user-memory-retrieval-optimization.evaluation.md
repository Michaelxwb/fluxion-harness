---
{"description":"用户记忆 v2 评测契约","requirement":"user-memory-retrieval-optimization"}
---
# 用户长期记忆 v2 评测契约

> **版本**: v1.3（与后端/前端设计 v1.3 同步；本版冻结 query-macro 主聚合与配对 ΔCI）
> 本文件冻结“怎么证明 v2 比基线好/不差”，不是生产 SLA，也不是本次已完成的评测结果。
> 设计引用本文件的 EVAL-* 与场景 S-12；Task 的实现必须携带同一版本的评测命令和结果。

## 1. 目标与边界

| 项目 | 内容 |
|------|------|
| 目标 | 在没有生产数据的前提下，可复现地比较基线 IDF/阈值、二值 BM25、slot/alias、scope 覆盖、NOTE 标签门控的真实收益 |
| 不证明 | 不证明跨用户泛化、不证明生产 SLA、不证明所有 lane 已修好 |
| 适用范围 | PREFERENCE / FACT / NOTE 的用户记忆自动注入 |
| 单独报告 | PROCEDURE / FAILURE_PATTERN / VERIFICATION 经验 lane；TASK_STATE/resume；实时业务数据 |
| 基线 | 当前 `select_injection` + `render_memory_block` 的只读副本，同一输入/标签/预算 |
| 公平性 | 同一语料、同一查询、同一标签、同一预算、同一主 Agent 模型/工具；不针对盲测补别名 |

## 2. 数据集

### 2.1 场景分层

| 数据集 | 内容 | 样本类型 |
|--------|------|----------|
| D1 基础偏好/事实 | 输出格式、语言、时区、角色、团队、负责系统 | 明确事实、同义改写、否定、反问 |
| D2 适用条件 | 内部/外部、地区、单笔/批量、时间范围、对象类型 | 匹配、不匹配、缺失、UNKNOWN |
| D3 更新/去重 | 同值重复、明确纠正、来源前缀差异、乱序事件、并发写入 | 应合并、应更新、应隔离 |
| D4 冲突/无答案 | 两个不同值、主体不明、旧值失效、没有相关记忆 | 冲突、拒答、澄清 |
| D5 多值 | SET 成员、NOTE 多值、客户/项目标签 | 成员增删、标签召回、泛词误召回 |
| D6 经验/TASK_STATE | 现有经验、保存记忆确认/resume | 单独计分，不混入 v2 |
| D7 安全/隔离 | 多租户、多用户、恶意指令、敏感字段 | 越权、注入、Secret 排除 |
| D8 干扰 | 相似主题、共享数字、相同词面但不同意图、无 `entity:` 标签的实体 NOTE | 数字碰撞、泛词、实体混淆、标签缺失 |
| D9 端到端 | 多轮条件继承、工具调用、最终回答/动作 | 真实主 Agent 回放 |

### 2.2 标签

每条查询至少包含：

```json
{
  "query_id": "Q-0001",
  "tenant_id": "tenant-01",
  "user_id": "user-001",
  "query": "内部报告默认用什么格式？",
  "conversation_context": [
    {"role": "user", "text": "以后给客户的报告都用 Word", "source_seq": 12}
  ],
  "intent": {"predicate": "report_format", "scope": {"audience": "internal"}},
  "answerability": "ANSWERABLE",
  "required_memory_ids": ["M-001"],
  "acceptable_memory_ids": [],
  "forbidden_memory_ids": ["M-002", "M-003"],
  "expected_value": {"format": "PDF"},
  "live_source_required": false,
  "scenario_family": "preference_scope",
  "group": "user-001:template-A:correction-chain-1"
}
```

- `required`：回答当前问题必须使用。
- `acceptable`：可辅助，不是必需。
- `forbidden`：不适用、过期、冲突、越权，绝不能注入。
- `answerability`：ANSWERABLE / UNANSWERABLE / NEEDS_CLARIFICATION / LIVE_LOOKUP_REQUIRED。
- 否定、内部/外部同时出现、引用旧文本、多维 scope 缺一维必须单独标签。
- 无 `entity:` 标签的实体 NOTE 只作观察项，不纳入 Entity Gate 硬约束；若产品要求关闭该边界，另立需求并补实体提及识别。
- 至少两人裁决 required/acceptable/forbidden、scope、否定；争议样本单独标记，不静默改标签。

### 2.3 划分与防泄漏

1. 先定义 predicate/scope 词表和模板，再按固定种子生成语料。
2. 同一模板的改写、同一主体别名、同一纠正链必须放在同一 group，不能拆到开发/盲测两侧。
3. 按 `user_id / scenario_family / group` 划分开发、验证、盲测；不按单条记忆划分。
4. 盲测集固定后计算 SHA-256；开发期间不得根据盲测结果补别名或改规则。
5. 别名/词表开发集、参数验证集、最终盲测集分离；人工补词不算算法自动泛化。
6. 每次实验保存数据版本、词表版本、算法版本、模型引用、Prompt 版本、预算、随机种子、逐条结果。

### 2.4 规模

| 档位 | 用户数 | 每用户记忆 | 查询数 | 用途 |
|------|-------:|-----------:|-------:|------|
| Smoke | 20 | 50 | 100 | CI/开发快速回归 |
| Standard | 100 | 200 | 500–1000 | 算法对照/发版前 |
| Stress | 1,000 | 200 | 5,000 | PG/Redis/容量压测 |
| Edge | 专项 | 最高 1,000 | 专项 | 上限、并发、长文本、scope 边界 |

Standard 盲测比例 30%；盲测集在实现期间不参与调参。

## 3. 分阶段指标

| 阶段 | 指标 | 说明 |
|------|------|------|
| 候选召回 | Candidate Recall@K | required 中进入候选的比例 |
| 门控后 | Post-gate Recall / Strict Conditional Precision | 只算通过状态/scope/来源/NOTE 实体门控的候选 |
| 预算后 | Final Required Recall / Forbidden Injection | 最终进入 Prompt 的条目 |
| 写入 | Update Correctness / Duplicate Active Slot / Stale Injection | 纠正、去重、删除、清空 |
| 作用域 | Cross-Scope Error / Scope Override Correctness | 全局/特定覆盖、UNKNOWN 不扩大 |
| 安全 | Cross-Tenant/User Leakage / Secret Exclusion | 硬约束 |
| 体验 | Optional 占用、严格有效字节率、预算丢弃 | Prompt 成本 |
| 端到端 | Answer/Action Utility | 固定模型/工具/Prompt 的最终回答或动作正确率 |
| 经验 lane | 单独 Recall/Precision/Forbidden | 不与 v2 指标混算 |
| TASK_STATE | 单独回归 | 确认/resume 不退化 |
| 性能 | Head/缓存/检索/P95、PG 次数、连接池、tokenize、进程内存 | 见 §5 |

**公式（冻结）**

```text
required_injected_q = 查询 q 注入的 required 条目数
required_total_q = 查询 q 标签中的 required 条目总数
r_q = required_injected_q / required_total_q
（required_total_q = 0 → N/A，不计入聚合，报告 N/A 数量）

R = mean{ r_q | q 的 r_q 非 N/A }   # 签收主聚合：query-macro
R_user = mean{ r_q | q∈Q_u 且 r_q 非 N/A }   # 次要：用户内均值
R_user_macro = mean{ R_user | u∈U 且 Q_u 至少有一条非 N/A 查询 }   # 次要：user-macro
R_pooled = Σ_q required_injected_q / Σ_q required_total_q   # 次要：pooled，单独报告

strict_TP_q = 查询 q 注入的 required 条目
strict_FP_q = 查询 q 注入的 forbidden 或未标注条目
acceptable 注入 = 单独计为 acceptable_occupancy，不进入 strict_TP/FP
p_q = strict_TP_q / (strict_TP_q + strict_FP_q)
（分母 0 → N/A）

P = mean{ p_q | q 的 p_q 非 N/A }   # 签收主聚合：query-macro
P_user = mean{ p_q | q∈Q_u 且 p_q 非 N/A }   # 次要：用户内均值
P_user_macro = mean{ P_user | u∈U 且 Q_u 至少有一条非 N/A 查询 }   # 次要：user-macro
P_pooled = Σ_q strict_TP_q / Σ_q (strict_TP_q + strict_FP_q)   # 次要：pooled，单独报告
Inclusive Precision = (required + acceptable 注入) / 全部注入（次要指标）

Forbidden Query Rate = 有 >=1 forbidden 注入的查询数 / 查询总数
Forbidden Item Count = forbidden 注入条目数（单独报告）
Stale Injection Rate = 注入的过期/撤销/被替代条目数 / 注入条目总数
Cross-Scope Error Rate = 注入的 scope 不匹配条目数 / 注入条目总数
Abstention Precision = 正确弃用/澄清数 / 所有弃用/澄清决策数
Update Correctness = 纠正后当前值唯一且正确的纠正查询数 / 纠正查询总数
```

- **主聚合唯一**：签收只使用 `R` / `P` 的 query-macro；user-macro、group 宏平均与 pooled 都只作次要视角，不能替代签收值。同一 fixture 的 `1/1` 与 `0/9` 两个查询，query-macro 是 50%，pooled 是 10%，签收以 50% 为准。
- **分组宏平均（次要）**：`R_group = mean{ r_q | q∈g 且非 N/A }`，再对所有有效 group 取宏平均；`P_group` 同理。group 宏平均、user-macro 与 category 报告只作稳健性/错误分类，不改变签收主聚合。
- 主聚合顺序为 query → overall；user/group 宏平均是次要视角。不按单条记忆当独立用户；N/A 查询单独计数，不按 0/1 处理。
- `acceptable` 的展示占用单独报告；不把 acceptable 当 required 的 TP，也不默认当 FP。
- 未标注注入一律计 strict_FP；不能用“模型可能有用”洗白。
- 空分母必须报告 N/A，不按 0/1 处理。

**输入口径**

- **Oracle 结构化输入**：直接给检索器正确的 predicate/scope/value，只评检索/门控。
- **真实 extractor 输入**：LLM 真实输出，评 extractor + validator + identity + 检索全链路。
- 两者分开计分；不能把标签里的正确 predicate/scope 直接喂给生产检索器当作端到端结果。
- 二值 BM25 的 IDF/长度归一化/字段权重/tie-break 冻结在后端设计 §3.5.3；评测与生产必须同一实现/同一参数，不能评测一套、上线另一套。

## 4. 统计与判定

- 主聚合为 query-macro；同一用户、同一模板、同一纠正链的查询不是独立样本，**重采样单位固定为 user**（同一 user 的 query/group 一起进出），不按 query 或单条记忆独立重采样。
- 配对对象固定为差值：`ΔR = R_v2 − R_base`、`ΔP = P_v2 − P_base`；对 user 做 paired bootstrap（B=10000，同一重采样索引同时计算 v2 与基线），取 Δ 的 2.5/97.5 百分位为 95% CI。
- 点估计在全量冻结集上计算；CI 只针对 Δ，不针对 v2 绝对值。报告 v2 绝对值时只作描述，不能拿“v2 绝对值的 CI 下界”替代 ΔCI。
- 报告整体和分 category（preference/fact/note/scope/update/conflict/security）结果；category/group 宏平均是次要视角。
- “测试集 0 次违规”只说明该集未观察到，不证明总体错误率为 0；安全硬约束仍需独立负例。
- 质量门槛不能只有“不劣于基线”；基线必须使用同一输入/标签/预算下的当前真实实现。
- 盲测不达标时先定位错误类别，不通过修改标签/补测试专属别名制造达标。

**冻结的签收规则**

| 项 | 规则 |
|----|------|
| 硬约束 | 安全/冲突/过期/跨 scope Forbidden Query Rate、跨租户/用户泄漏、UNKNOWN 扩大为全局、无正向实体匹配或候选标签未全部命中时，注入带 entity 标签的候选必须为 0 |
| Required Recall | 主聚合 query-macro：`ΔR = R_v2 − R_base` 点估计 ≥ 0，且 `CI_low(ΔR) ≥ -1pp` |
| Strict Conditional Precision | 主聚合 query-macro：`ΔP = P_v2 − P_base` 点估计 ≥ +5pp，且 `CI_low(ΔP) > 0` |
| Update Correctness | 纠正集必须 100%（有样本才计） |
| Stale Injection | 0 |
| 经验 lane | 单独报告；不参与 v2 签收 |
| 性能 | 按后端 NFR；未实测不能签收为生产 SLA |
| 参数 | calibration 集冻结后一次性评 validation/盲测；盲测后不改参数/别名/阈值 |

- 若质量门槛未过，发布结论只能是“未达标”，不能用“不劣于基线”替代绝对目标。
- 盲测集/标签/参数/基线版本必须写入报告；没有冻结版本的报告只算探索性回放。
- ΔCI 示例：基线 60%、v2 70%、`ΔR` 的 95% CI 为 [6pp,14pp] 时，`CI_low(ΔR)=6pp > 0`，判为非劣/提升；不能拿 v2 绝对值 70% 去和基线 60% 或 Δ 下界比较。

## 5. 性能协议

| 场景 | 数据 | 缓存 | 并发 | 重点 |
|------|------|------|------|------|
| 空用户 | 0 | 冷/热 | 1/20 QPS | 开启 Memory 的固定开销 |
| 普通用户 | 200 | 热 | 1/20 QPS | PG head + payload + 本地检索 |
| 大用户 | 1,000 | 热 | 1/20 QPS | tokenize/序列化/内存 |
| 冷启动 | 200/1,000 | 冷 | 并发 Miss | PG 回源/连接池 |
| 高频更新 | 200 | 持续失效 | 20 QPS | head 变化/缓存回源 |
| Redis 故障 | 200 | 不可用 | 1/20 QPS | PG 降级 |
| 超限 payload | >512KB | bypass | 1/20 QPS | 不缓存/不截断 |
| 持续新 Run | 200 | 热 | 并发 | `MemoryInjectionLog` 审计成本 |

必须报告：

- Memory 分阶段 P50/P95/P99：PG head、Redis GET、反序列化、tokenize、BM25、门控、渲染；
- `/chat` 端到端 P50/P95/P99、TTFT、相对 Memory 关闭的增量；
- PG 查询/写入次数、连接池等待、Redis 命中率/带宽、进程内存；
- `MemoryInjectionLog` + EventWriter 的 PG 写入/提交；
- 经验/TASK_STATE 旧路径成本；
- 上限 payload 的通过/降级结果。

目标值见后端设计 NFR；未实测前不得写成生产 SLA。

## 6. 验收门槛

**硬约束（必须 0）**

- 安全/冲突/过期/跨 scope 类别的 Forbidden Injection = 0；
- 跨租户/跨用户泄漏 = 0；
- 清空/删除后旧候选复活 = 0；
- UNKNOWN scope 被扩大为全局 = 0；
- 无正向实体匹配或候选标签未全部命中时，注入带 entity 标签的候选 = 0。

**质量（相对基线）**

- Required Recall：主聚合 query-macro，`ΔR` 点估计 ≥ 0 且 `CI_low(ΔR) ≥ -1pp`；
- Strict Conditional Precision：主聚合 query-macro，`ΔP` 点估计 ≥ +5pp 且 `CI_low(ΔP) > 0`；
- Abstention Precision：`ΔA = A_v2 − A_base` 点估计 ≥ 0（描述性，不单独设 CI 门槛）；
- Update Correctness 在纠正集 100%；
- Stale Injection = 0。

**统计**

- 主聚合 query-macro；user-macro/pooled/group/category 只作次要描述；
- 以 user 为重采样单位的 paired bootstrap，95% CI 只针对 ΔR/ΔP；
- 分 category 报告；
- Oracle 与真实 extractor 分开；
- 经验 lane 单独报告。

**性能**

- PG head P95、检索 P95、`/chat` 增量、命中率、payload bypass 见后端 NFR；
- 未达标先定位瓶颈，不通过降低评测边界凑结果。

## 7. 产物与冻结

每次评测必须保留：

- `dataset.json` / `labels.json` / `queries.json`（或等价 fixture）；
- 数据版本、词表版本、算法版本、模型引用、Prompt 版本、预算、随机种子；
- 基线逐条结果、v2 逐条结果、分阶段结果；
- 失败样本清单与人工裁决记录；
- 盲测集 SHA-256、manifest；
- 性能报告与环境配置。

未冻结数据/标签/命令的结果，只能作为探索性回放，不能作为选型或上线证据。

---

*文档结束*
