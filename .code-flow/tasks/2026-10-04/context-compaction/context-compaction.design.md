# 上下文压缩（Context Compaction）设计与需求一体化文档

- **需求目录**: `.code-flow/tasks/2026-10-04/context-compaction/`
- **PRD**: `context-compaction.prd.md`（本会话逐条对齐的结论已固化在 PRD §4.3）
- **模板**: Full（中大型、跨 agent-core / runtime / worker / 数据面）

## 1. 文档控制

### 1.1 责任人

| 角色 | 姓名 | 职责范围 |
|---|---|---|
| 产品经理 | 用户 | 需求定义、业务验收 |
| 开发负责人 | Claude | 技术方案、代码实现 |

### 1.2 修订历史

| 版本 | 日期 | 作者 | 变更描述 |
|---|---|---|---|
| v0.1 | 2026-10-04 | Claude | 初始设计（来源：本会话对齐结论 + PRD v0.1） |

## 2. 需求分析

### 2.1 需求概述 [必填]

| 项目 | 内容 |
|---|---|
| **模块名称** | 上下文压缩 |
| **模块ID** | MOD-CTXCOMPACT |
| **所属系统/产品线** | Agent Harness（agent-core / agent-runtime / agent-worker） |
| **需求类型** | 功能优化（长会话可靠性） |
| **业务背景** | 长会话上下文膨胀，当前只有"保留尾部 40 条"的硬裁：开头诉求被丢、旧工具结果长期占位、超预算只能丢不能压 |
| **核心目标** | 分级压缩（先便宜后昂贵），压缩结果**可由库确定性重建、可审计** |

### 2.2 痛点与价值 [必填]

| 维度 | 内容 |
|---|---|
| **目标用户** | IM 终端用户（长会话/多工具回合）；平台审计人员；平台管理员 |
| **当前问题** | ① `HISTORY_BUDGET_MESSAGES=40` 只保尾部 ⇒ 原始诉求最先消失；② 工具结果只有 8 KiB 的**单项**外置阈值，**整轮合计**超限无处理；③ 无任何降级/摘要 ⇒ 信息净损失且不留痕 |
| **业务影响** | 长会话回答质量塌方且不可解释——"模型为什么忘了"在审计面查不到 |
| **预期价值** | 长会话不丢原始约束；同等预算装下更多有效信息；压缩可解释、可回放 |

| 编号 | 用户故事 | 优先级 |
|---|---|---|
| US-01 | 作为 IM 用户，我希望长会话里系统仍记得我最初的要求 | P0 |
| US-02 | 作为审计人员，我希望看到某轮压缩掉了什么、用了哪份摘要 | P0 |
| US-03 | 作为管理员，我希望按租户调压缩策略，改动对后续新 Run 生效而无需重启 | P1 |
| US-04 | 作为运维，我希望压缩在 Pod 之间一致（换 Pod 后同一会话看到的历史相同） | P0 |

### 2.3 功能方案 [必填]

| 功能ID | 功能名称 | 功能描述 | 优先级 | 来源 |
|---|---|---|---|---|
| FEAT-01 | 保留头部 + 省略标记 | 裁剪改为"保留最前 `keep_head_groups` 组 + 最近 `keep_tail_groups` 组"，中间替换为一条省略标记；标记按**消息组**报数 | P0 | US-01 |
| FEAT-02 | 整轮批次预算 | 工具回合结束时按**整轮**判定：单条 > `persist_threshold_bytes` 落盘；未落盘合计 > `round_budget_bytes` 则按字节**从大到小**逐条落盘，进预算即停 | P0 | US-01 |
| FEAT-03 | 旧工具结果降级（micro） | 只保留最近 `keep_recent_tool_groups` 个工具交换组的原文，更早的**结果内容**换占位符；`tool_calls` 与参数**原样保留**；占位符含 `artifact_id` 与工具名 | P0 | US-01 |
| FEAT-04 | 摘要 | 前三层后仍 > `threshold_bytes` 才调用摘要模型；五字段摘要（`user_goal` / `constraints` / `progress` / `open_items` / `artifacts`）+ 字段集合精确校验；失败**保持原历史**；摘要**作为权威历史落库**（事件），被压缩掉的逐字原文（transcript）落**共享产物存储** | P0 | US-01 |
| FEAT-05 | 压缩审计事件 | 压缩**真的发生**时落一行 `canonical_event`（层级、省下字节/组数、摘要引用） | P0 | US-02 |
| FEAT-06 | 压缩指标 | 各层触发次数、省下字节、摘要调用与 token 数进各服务 metric catalog | P1 | US-02 |
| FEAT-07 | 压缩配置 | 配置进 execution snapshot 的 `budget.compaction`（Run 侧等价载体 `policy_json`），仅影响后续新 Run；进程内缓存 + 短 TTL | P1 | US-03 |
| FEAT-08 | 重建一致 | 压缩后的历史可由库**确定性重建**，与当时真正发给模型的那份**逐字节一致** | P0 | US-04 |
| FEAT-09 | memory 预算 | memory 注入计入上下文预算；注入段不参与 micro 降级 | P1 | US-01 |

**字段约束（配置项）**

| 字段 | 类型 | 默认 | 约束 |
|---|---|---|---|
| `snip.enabled` | bool | `true` | — |
| `snip.max_groups` | int | `50` | **触发阈值**（组数严格大于它才 snip）；≥ `keep_head_groups + keep_tail_groups + 1` |
| `snip.keep_head_groups` | int | `3` | ≥ 1（保证开头诉求与最初约束） |
| `snip.keep_tail_groups` | int | `20` | ≥ 1（保留最近若干组原文） |
| `tool_result.persist_threshold_bytes` | int | `8192` | 严格大于才落盘（沿用现值） |
| `tool_result.round_budget_bytes` | int | `200000` | ≥ `persist_threshold_bytes` |
| `tool_result.preview_head_bytes` / `preview_tail_bytes` | int | `2000` / `2000` | ≥ 0 |
| `micro.enabled` | bool | `false` | — |
| `micro.keep_recent_tool_groups` | int | `3` | ≥ 1 |
| `summary.enabled` | bool | `false` | 需同时给出 `summary.model_ref` |
| `summary.threshold_bytes` | int | `50000` | 严格大于才摘要 |
| `summary.model_ref` | str \| null | `null` | 引用既有 `model_definition`（**不承载 api_key**） |
| `history_budget_messages` | int | `40` | ≥ 1（原常量转为配置项） |
| `memory.budget_ratio` | float | `0.2` | (0,1] |

### 2.4 范围与边界 [必填]

| 类别 | 内容 |
|---|---|
| **范围（In Scope）** | 压缩四层（FEAT-01..04）、审计事件（FEAT-05）、指标（FEAT-06）、配置冻结（FEAT-07）、重建一致（FEAT-08）、memory 预算（FEAT-09） |
| **非范围（Out of Scope）** | Console 系统设置页（需求二）；上下文膨胀看板；非 OpenAI 兼容模型的精确 token 计数；跨会话记忆压缩；"prompt too long" 响应式恢复；**Worker 侧压缩**（Worker 只执行 Skill 脚本、不构建模型请求，本需求不新增 worker 代码） |

### 2.5 验收条件 [必填]

#### 2.5.1 业务规则

| 编号 | 规则 |
|---|---|
| RULE-01 | 压缩后不得出现孤儿 `tool` 消息；带 `tool_calls` 的 assistant 回合与其工具结果同进同出 |
| RULE-02 | 阈值判定一律用 **UTF-8 字节**（中文 1 字 = 3 字节），边界为**严格大于** |
| RULE-03 | 摘要输出必须字段集合**精确相等**；任何不符 ⇒ 本次摘要作废、历史不变 |
| RULE-04 | 压缩失败（落盘/摘要）一律**退化到不压缩**，不得让 Run 失败 |
| RULE-05 | 摘要文本是权威历史（落库）；transcript 是存档（落共享产物），二者不得互换 |
| RULE-06 | transcript 不新增任何对外明文出口（Console 无读取/下载入口） |

#### 2.5.2 验收场景

| 场景ID | 功能ID | 测试层级 | 关键真实边界 | 预期结果 |
|---|---|---|---|---|
| B-01 | FEAT-01 | unit | 纯逻辑：消息组切分 | 保留头 3 组 + 尾 N 组；标记按组报数；无孤儿 TOOL |
| B-02 | FEAT-02 | unit | 纯逻辑：整轮批次预算 | 单条超阈值落盘；合计超预算时按字节从大到小、进预算即停 |
| B-03 | FEAT-03 | unit | 纯逻辑：micro 降级 | 旧工具组内容换占位（含 artifact_id + 工具名）；结构不变；占位不短于原文则跳过 |
| B-04 | FEAT-04 | unit | 纯逻辑：摘要校验 | 五字段精确匹配才接受；多/少字段、非 JSON、带 tool_calls、finish_reason≠stop 逐条拒绝 |
| E-01 | FEAT-08 | integration | 真实 PostgreSQL：从 canonical_event 重建 | 压缩后的请求与当时真正发给模型的**逐字节一致** |
| E-02 | FEAT-02/04 | integration | 真实 PG + 共享产物存储 | 整轮超预算时大结果落盘且预览头尾都在；transcript 可被运维按 id 直读、Console 无入口 |
| E-03 | FEAT-05 | integration | 真实 PG：canonical_event | 压缩发生时恰好多一行压缩事件，字段（层级/省下字节/摘要引用）齐 |
| E-04 | FEAT-07 | integration | 真实 PG：execution snapshot | 配置改动只影响后续新 Run；在跑的 Run 用冻结值 |
| E-05 | FEAT-06 | integration | 真实 agent-runtime `/metrics`（api-kit 目录） | 无流量也暴露四级计数器目录；label 低基数（layer/outcome）；触发后计数与省下字节递增 |
| E-06 | FEAT-09 | integration | 真实 agent-runtime 请求装配 → 模型 HTTP 探针 | memory 注入段计入上下文预算（超限时参与裁剪）；注入段不参与 micro 降级、内容原样保留 |
| S-01 | FEAT-01..05 | E2E | 真实 WS → Gateway → Runtime → PG → 模型 HTTP 探针 | 长会话 + 大工具结果 + 多工具回合后：开头诉求仍在、无孤儿 TOOL、模型收到的 prompt 含省略标记或摘要、压缩事件落库 |

非功能指标：压缩对单次请求的额外开销 O(历史条数) 单遍、无额外查库（配置走缓存）；摘要调用仅在开启且超阈值时发生。

## 3. 技术设计

### 3.1 方案选型 [必填]

**备选与取舍**

| 方案 | 做法 | 结论 |
|---|---|---|
| A. 各调用方自己压 | runtime 的 `context_builder` 与其它调用方各写一套 | ❌ 两处漂移面（现状是第二处尚不存在，但一旦将来出现 agent 回合就会静默漏） |
| B. **在共享的请求构建缝统一压**（选中） | 压缩纯逻辑放 `packages/agent_core/context/`，在 `AgentRunner` 调 provider **之前**统一执行；落盘/摘要等副作用经**端口**注入 | ✅ 压在整个模型请求的**唯一组装点**（system prompt + 历史 + tools 都在这里成形），"重建的那份 == 真正发出去的那份"才有锚；纯函数可单测 |
| C. 只在运行时压 | 只改 `context_builder` | ❌ 该处只装配**历史**；system prompt / tools / 采样参数由 `AgentRunner` 组装，逐字节重建要跨两个模块拼口径 |

**代码事实（2026-10-04 核查，纠正本文档早先的表述）**：`AgentRunner` 全仓只有 `apps/agent-runtime/src/muad_agent_runtime/application/executor.py:771` 一处构造；`apps/agent-worker` 只 import `muad_agent_core.skill`（`worker/executor.py:7`），**不构建模型请求、没有历史装配**。故本需求实际只影响 **Runtime 同步 Run** 路径；`harness-worker` 绑定保留，但由收口任务以「Worker 路径不引入压缩」的**对照断言**承接（不新增 worker 代码）。

**ADR-01：压缩放在"请求构建缝"而不是"历史查询缝"**
- 依据：`AgentRunner` 的 `_call_model` 是 `ModelRequest` 的**唯一组装点**（`packages/agent-core/src/muad_agent_core/agent/runner.py:302-318`：messages + tools + 采样参数在此成形），压在这里才能保证"重建出来的那份 == 当时真正发给模型的那份"（FEAT-08/US-04）。
- 代码事实：当前 Worker 不构建模型请求，压缩链路只覆盖 Runtime；跨 Pod 一致（US-04）由"前三层纯函数确定性重算 + 摘要落库"承载，与执行形态无关。
- 放弃：方案 A/C（A 是漂移面；C 让逐字节重建跨模块拼口径）。

**ADR-02：摘要落库、transcript 落产物**
- 依据：`cleanup-artifacts` 按 30 天**删文件 + 删行**（`artifact_cleanup_service`）。摘要若只是文件，过期后"被压缩掉的历史"彻底消失；而摘要必须参与历史重建。
- 放弃：摘要也落产物（过期即失忆）；transcript 也落库（把逐字原文塞进事件表，体积与隐私都不可接受）。

**ADR-03：前三层确定性重算，不做缓存**
- 依据：前三层是纯函数（同输入同输出），每次重建成本 O(n) 单遍；缓存反而引入一致性面。
- 代价：每轮重建要做一次字节统计（可接受，见 §3.5）。

### 3.2 架构设计 [必填]

```
                    ┌─────────────── agent-core（共享）───────────────┐
  请求构建 ───────▶ │ AgentRunner  ──▶ RequestCompactor（纯逻辑）      │
（历史由 Runtime 的  │   │  1) 字节统计（UTF-8）                        │
 context_builder 装配；│   │  2) micro：旧工具结果 → 占位（含 artifact_id）│
 Worker 不构建模型    │   │  3) snip：头 N 组 + 尾 M 组 + 省略标记       │
 请求，不在本链路）   │   │  4) 超阈值 ⇒ 经 Port 调摘要                  │
                    └───┬───────────────────────┬────────────────────┘
                        │ CompactionPort        │ SummaryPort
             （落盘/读回/写事件）        （调摘要模型）
                        ▼                       ▼
              runtime 适配器              model provider（OPENAI 兼容）
                        │
        ┌───────────────┴────────────────┐
        ▼                                ▼
  共享产物存储（RWX PVC）           PostgreSQL
  transcript / 工具结果           canonical_event（摘要事件 + 压缩审计事件）
```

- **两个集成缝**：① 工具回合结束时（批次预算，扩既有 `ArtifactResultWriter`）；② 模型请求前（micro/snip/摘要，新增 `RequestCompactor`）。
- **不新增部署单元**；不新增表；迁移为空（新事件类型与产物类型都是字符串枚举）。

### 3.3 数据设计 [必填]

**不新增表**，复用两处既有载体：

| 载体 | 用途 | 关键字段 |
|---|---|---|
| `runtime.canonical_event` | 摘要事件 + 压缩审计事件 | `event_type='CONTEXT_SUMMARY'`：`payload_json = {covers_up_to_seq, summary{user_goal, constraints, progress, open_items, artifacts}, transcript_artifact_id, bytes_before, bytes_after}`；`event_type='CONTEXT_COMPACTED'`：`payload_json = {layers{layer: {fired, groups, bytes_saved}}, summary_event_seq?}` |
| `runtime.artifact` | transcript / 工具结果 | 新增类型 `TRANSCRIPT`（既有 `TOOL_RESULT` 不动）；`storage_key` 相对路径 |

- **重建语义**：`cover_up_to_seq` 表示"seq ≤ 该值的原始事件已被摘要覆盖"；重建时取**最新**一份覆盖事件作为前缀，其后再按 seq 顺序应用后续事件，最后跑前三层压缩 ⇒ 确定性。
- 索引：沿用 `canonical_event(run_id, seq)` 既有索引，无需新增。

### 3.4 接口设计 [必填]

**配置（`budget.compaction`，Run 侧 `policy_json` 同结构）**

```json
{
  "compaction": {
    "snip": {"enabled": true, "max_groups": 50, "keep_head_groups": 3, "keep_tail_groups": 20},
    "tool_result": {"persist_threshold_bytes": 8192, "round_budget_bytes": 200000,
                    "preview_head_bytes": 2000, "preview_tail_bytes": 2000},
    "micro": {"enabled": false, "keep_recent_tool_groups": 3},
    "summary": {"enabled": false, "threshold_bytes": 50000, "model_ref": null},
    "history_budget_messages": 40,
    "memory": {"budget_ratio": 0.2}
  }
}
```

**核心接口（agent-core 内部）**

| 签名 | 说明 |
|---|---|
| `RequestCompactor.compact(request, *, config) -> CompactionOutcome` | 纯逻辑；返回新请求 + `CompactionOutcome{layers, bytes_before, bytes_after, summary_request?}` |
| `CompactionPort.persist_tool_results(round_results, config) -> None` | 副作用端口：整轮落盘（适配器实现） |
| `CompactionPort.record(event) -> None` | 审计事件落库（适配器实现） |
| `SummaryPort.summarize(messages, config) -> SummaryFields \| None` | 摘要模型调用（失败返回 `None`，调用方保持原历史） |

**env（部署级，改后重启生效）**：`CONTEXT_SETTINGS_CACHE_TTL_SEC`（默认 10）。

### 3.5 质量实现方案 [必填]

- **性能**：字节统计单遍 O(n)（一次 `encode('utf-8')`，不做逐条重复编码）；配置从进程内缓存读（TTL，无每轮查库）；落盘只发生在工具回合结束时（非每轮）；摘要仅在开启且超阈值时调用一次。**无 N+1、无循环内 IO**。
- **可靠性**：落盘用既有不可变写（temp + `os.replace`）；**整批中途失败回滚已写产物**（不留半批引用）；摘要任何异常 → 记录指标、保持原历史；压缩整体包在 try 里，异常退化到"不压缩"。
- **安全/隐私**：transcript 只写共享产物、**不新增任何对外读取端点**；摘要与事件 payload 不含密钥（与 `RULE-secret-001` 一致）；日志只记字节数与层级，不记内容。
- **可观测性**：`context_compaction_total{layer,outcome}`、`context_compaction_bytes_saved_total{layer}`、`context_summary_total{outcome}`、`context_summary_tokens_total`（低基数 label，无资源 ID）；压缩发生时的审计事件见 §3.3。
- **回放一致（FEAT-08）**：E-01 用"同一份 canonical_event 重建两次 + 与真实发出的请求对比"逐字节断言。

## 4. 部署与运维

- 无新部署单元、无迁移；配置项默认值随代码发布。
- 需注意：`summary.enabled=false` 是**默认**——上线后先只观察 FEAT-01/02 的效果（指标），再决定开 micro/摘要。
- `.env` 侧新增仅 `CONTEXT_SETTINGS_CACHE_TTL_SEC`；k8s 走 ConfigMap（非敏感）。

## 5. 风险与依赖 [必填]

| 风险 | 影响 | 应对 |
|---|---|---|
| 压缩改变模型可见历史 ⇒ 行为回归 | 中 | 后两层默认关；四层独立开关；S-01 覆盖 |
| 摘要成本/延迟 | 中 | 默认关 + 阈值可配 + 失败不替换 |
| micro 占位比原文长 | 低 | B-03 断言"不短于原文则跳过" |
| 重建不一致（换 Pod 结果不同） | 高 | 前三层纯函数 + 摘要落库 + E-01 逐字节断言 |
| transcript 成为新的对话原文出口 | 高（隐私） | RULE-06：无对外入口；仅运维按 id 直读共享存储 |

依赖：`execution snapshot` 的 `policy_json`（既有）、共享产物存储（RWX PVC，既有）、`canonical_event` 重建链（既有）。

## 6. 需求追溯矩阵 [必填]

| US | FEAT | 接口 | 场景 |
|---|---|---|---|
| US-01 | FEAT-01/02/03/04/09 | `RequestCompactor.compact` / `CompactionPort.persist_tool_results` | B-01/02/03/04、E-02、E-06、S-01 |
| US-02 | FEAT-05/06 | `CompactionPort.record` / 各服务 metric catalog | E-03、E-05、S-01 |
| US-03 | FEAT-07 | `budget.compaction` 配置 | E-04 |
| US-04 | FEAT-08 | `compact` 确定性 | E-01 |

## Spec Compliance Matrix

| Spec/Rule | enforcement | 设计影响 | 设计落点 | 验证场景/verifier | 状态 |
|---|---|---|---|---|---|
| harness-snapshot#RULE-snapshot-001 | required | 压缩配置随 execution snapshot 冻结，仅影响后续新 Run | 3.4 接口设计（`budget.compaction`） | E-04 | applied |
| harness-arch#RULE-arch-001 | required | 压缩结果可按库重建、跨 Pod 一致；transcript 落共享存储 | 3.2 架构设计、3.3 数据设计 | E-01、E-02 | applied |
| harness-data#RULE-data-001 | required | 复用既有表；时间戳/jsonb 口径不变；软删唯一约束不涉及 | 3.3 数据设计 | E-03 | applied |
| harness-skill#RULE-skill-001 | required | transcript/工具结果沿用共享产物不可变写与原子发布 | 3.5 质量实现方案（可靠性） | E-02 | applied |
| harness-worker#RULE-worker-001 | required | Worker 只执行 Skill 脚本、不构建模型请求 ⇒ 本需求不新增 worker 代码；绑定保留，以「Worker 路径不引入压缩」对照断言承接 | 3.1 方案选型（ADR-01 的代码事实段） | E-01（对照断言） | applied |
| harness-model#RULE-model-001 | required | 摘要只走 OPENAI 兼容协议，引用既有 `model_definition`，无默认模型回退 | 3.4 接口设计（`summary.model_ref`） | B-04 | applied |
| harness-secret#RULE-secret-001 | required | 摘要/事件/日志不含密钥；transcript 不新增对外出口 | 3.5 质量实现方案（安全/隐私） | E-02、S-01 | applied |
| harness-test#RULE-test-001 | required | 分层验收：纯逻辑单测 + 真实 PG 一致性 + 真实链路 E2E | 2.5.2 验收场景 | S-01、E-01、B-01..04 | applied |
