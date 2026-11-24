---
design_template: full
---
# 会话上下文保留：后端设计

> v0.5 · 2026-11-24 · 待评审 · FEAT-01 至 FEAT-05
>
> v0.5 收敛：删除 `snip`/`micro` 与所有固定组数选择；材料用连续 seq 区间表达；`with_summary` 只替换材料区间；旧摘要失效；Worker 纳入；整轮工具结果外置预算由 `history_budget_tokens` 派生。

## 1. 方案选型

**D1 · 压缩配置只保留 4 个叶子，删除固定组数层。**
`CompactionSettings` 只保留 `history_budget_tokens`、`summary.enabled`、`summary.model_ref`、`tool_result.persist_threshold_bytes`。删除 `snip.*`（4）、`micro.*`（2）、`history_budget_messages`、`max_history_tool_rounds`、`summary.threshold_bytes`、`tool_result.round_budget_bytes`、`tool_result.preview_head_bytes`、`tool_result.preview_tail_bytes`。

`snip` 与 `micro` 都删除：`snip` 是固定组数裁剪；`micro` 只降级已外置结果，却引入第二套内容改写和“保留最近 3 组”的固定选择。压缩只剩**可选摘要 + 请求边界预算裁剪**。

**D1.1 · 整轮工具结果外置预算由历史预算派生。**
工具执行写入时仍按 `tool_result.persist_threshold_bytes` 对单条结果外置；整轮聚合预算取 `history_budget_tokens` 的固定比例（代码常量，不新增配置叶子）。`select_round_persists` 按“超过单条阈值全部外置 + 剩余合计超过整轮预算则从大到小外置”执行，保证一个 assistant 回合的全部工具结果在内联侧有界。预览头尾由 `min(persist_threshold_bytes, PREVIEW_CAP_BYTES)` 按固定比例派生，避免阈值配得很大时预览本身又把请求撑大；同样不新增配置。

**D2 · 预算随 Run/Task 冻结，严格解析。**
`resolve_compaction_settings(平台快照 + Agent 覆盖)` 在 Run/Task 创建事务内得到完整配置并冻进 `policy_json`；new / resume / continuation / Agentic Task 只读冻结值。冻结值使用**严格解析**：要求完整叶子集合，缺键、未知键、旧键一律 `CompactionConfigError`，不回落默认值。Runtime 与 Worker 共用 `muad_contracts.platform_settings` 的同一入口。

**D3 · 装配只读，请求边界只裁一次。**
`load_history` 只按预算分页读取，不裁剪、不生成标记、不推进 coverage。读取分三段且都受预算约束：**尾部窗口**、**材料区间**（从摘要 `covered_seq` 起的连续区间），以及**头部锚点**。裁剪由 `fit_history(plan, budget_tokens)` 在请求构建缝执行一次：保头保尾、覆盖 `older + working`、产出唯一省略标记与审计事实。

材料用 `(covered_seq, material_end_seq]` 的 **seq 区间**表达，不再维护 `material: tuple[Group, ...]` 或 `material_event_ids`。读取侧只记录材料扫描到的 `material_scan_end_seq`；`ContextPlan.material_end_seq` 是**实际命中材料组的最大 seq**，两者语义不同，不能互相替代。需要材料组时从 `plan.older` 按区间筛选；需要 coverage 时取实际筛选组的最大 seq。这样材料、coverage 和替换范围只有一个事实来源。

**D4 · 保头保尾，首个用户目标固定保留。**
装配在未被可信摘要覆盖时显式取“头部锚点”（会话首个 user 组 + 其后首个 assistant 组），并记录 `head_end_seq`。`fit_history` 按 `seq <= head_end_seq` 识别并保护头部；没有头部锚点时（已有摘要）不保护 `older` 前缀。当前用户组由 `run_input` 固定；缺失时回退保护最后一个 USER 组。

**D5 · 摘要只处理连续材料区间，替换时保留头尾。**
摘要材料 = 旧摘要 + `plan.older` 中落在 `(covered_seq, material_end_seq]` 的组。`with_summary` **只移除这些材料组**，再把新摘要放入 `prefix`；头部锚点、尾部最近历史和 `pinned` 保持原样。`covers_up_to_seq` 取实际材料组的最大 seq，不取 `LoadedHistory` 的读端、不取 `max(older.seq)`，也不覆盖当前用户消息。pinned 组不进入材料；若其 seq 落在 coverage 区间内，由 `protected_event_ids` 在重建时保留，这是受保护例外，不是 coverage 空洞。

材料覆盖全部未摘要历史时，尾部本身就在材料区间内，摘要替换后由摘要承载；只有材料被预算截断、存在独立尾部时，才存在“保留原始尾部”的要求。

同一材料区间已摘要过时复用宿主压缩器实例上的缓存摘要（`_summarized_through_seq` + 摘要消息），不重复调用摘要模型；不能把去重状态放在 `ContextPlan` 上，因为 plan 每次从消息列表重建，看不到刚写下的摘要。

**D6 · “继续”不再短路，也不再自动绑定单候选。**
`_offer` 只写 `MEMORY_RESUME_OPTIONS` 与提示，不自动 `SELECTED`；`resume_decision` 返回 `hint`（候选清单 + “未经确认、仅供判断”），执行器照常调模型；只有用户下一条明确选择编号/key/唯一标题时，`_pending` + `choose_resume_option` 才绑定 `resume_ref`。删除 `_stream_run` 的 `SegmentResult(final_text=resume.message)` 分支与 `MEMORY_RESUME_NOTICE`。

**D7 · 估算复用仓库已有的保守口径。**
`history_budget_tokens` 表示“保守估算 token”：1 token ≈ 1 UTF-8 字节 + 每条消息协议开销，与 `executor.conservative_input_estimate` 同口径；不使用 `bytes/3`。`MESSAGE_PROTOCOL_OVERHEAD_BYTES` 上移到 `agent-core` 的共享模块，Runtime 与 Worker 共用。

**D8 · 不兼容数据直接重置，不写兼容解析。**
开发阶段不保留旧压缩键、旧 `policy_json`、旧平台设置和旧摘要：

- 平台设置整份重置（所有分组回到 schema 默认，见 §8）；
- Agent `runtime_config_json.budget.compaction` 整块删除；
- 旧 `CONTEXT_SUMMARY` 全部清理，避免旧单点 coverage 继续被信任；
- 排空在途 Run/Task，旧终态快照只作审计。

旧键残留时由严格解析直接报错。没有数据迁移、没有反向迁移、没有旧格式读取分支。

**被否方案**：

- 保留 `snip`/`micro` 的固定组数选择（与“按内容大小裁剪”冲突，且多出两套内容改写规则）；
- 装配期再裁一次（制造摘要 coverage 空洞）；
- 尾部-only 保留（超预算先丢第 1 轮，和需求目标相反）；
- `bytes/3` 估算（中文会被低估）；
- 模型级 `context_window_tokens`（本需求不做；部署最小窗口先作为守卫）；
- 单候选自动绑定 `resume_ref`（会把当前工作劫持到旧任务）；
- `material` message tuple + `material_event_ids` + 把扫描末端直接当 coverage（重复/错误状态，容易再次出现 coverage 不一致）；
- 给摘要材料单设一个字节阈值（等于把删掉的 `summary.threshold_bytes` 换个名字留下）；
- 摘要链 / 摘要的摘要（本需求不改摘要结构，属过度设计）。

## 2. 契约与数据

### 2.1 压缩配置

`packages/contracts/src/muad_contracts/platform_settings.py`：

| 叶子 | 变更 |
|---|---|
| `history_budget_tokens` | **新增**：正整数，schema 默认 16384；语义是“对话区”的保守估算 token 上限（1 token ≈ 1 UTF-8 字节 + 协议开销） |
| `summary.enabled` | 不变 |
| `summary.model_ref` | 不变 |
| `tool_result.persist_threshold_bytes` | 不变 |
| `history_budget_messages` | **删除** |
| `max_history_tool_rounds` | **删除** |
| `snip`（4 叶） | **删除整组** |
| `micro`（2 叶） | **删除整组** |
| `summary.threshold_bytes` | **删除** |
| `tool_result.round_budget_bytes` | **删除**：由 `history_budget_tokens` 派生固定比例 |
| `tool_result.preview_head_bytes` | **删除**：由 `min(persist_threshold_bytes, PREVIEW_CAP_BYTES)` 按固定比例派生 |
| `tool_result.preview_tail_bytes` | **删除**：同上 |

校验：

- `history_budget_tokens` 在 `[1, HISTORY_BUDGET_TOKENS_MAX]`，默认 `16384`；
- 保存和 Run/Task 准入要求 `history_budget_tokens ≤ model_context_window_min_tokens - HISTORY_BUDGET_RESERVE_TOKENS`；不满足时字段级拒绝；
- `merge_compaction_payload` 的键集随之更新，Agent 覆盖 `runtime_config.budget.compaction.history_budget_tokens` 沿用现有优先级；
- **冻结解析严格**：新增 `parse_frozen_compaction_settings(payload)`，要求恰好包含 `history_budget_tokens`、`summary{enabled, model_ref}`、`tool_result{persist_threshold_bytes}`；缺键/未知键/旧键显式 `CompactionConfigError`。Runtime `compaction_settings_of` 与 Worker `compaction_settings` 都改为调用它，不再返回 `None` 或回落默认。

`ToolResultSettings` 只保留 `persist_threshold_bytes`。装配处计算一次内部 `ToolResultBudget`：

```python
@dataclass(frozen=True, slots=True)
class ToolResultBudget:
    persist_threshold_bytes: int
    round_budget_bytes: int       # history_budget_tokens × 代码常量比例
    preview_head_bytes: int       # min(persist_threshold_bytes, PREVIEW_CAP_BYTES) × 代码常量比例
    preview_tail_bytes: int
```

它不是设置叶子，不进入 Console catalog；Runtime 与 Worker 的 RoundWriter 共用同一派生函数。

### 2.2 历史装配结果

`apps/agent-runtime/.../application/context_builder.py`：

```python
@dataclass(frozen=True, slots=True)
class LoadedHistory:
    messages: tuple[ModelMessage, ...]  # 摘要前缀 + 头部锚点 + 材料 + 尾部
    covered_seq: int = 0                # 可信摘要覆盖到的事件 seq；0 = 无摘要
    material_scan_end_seq: int = 0      # 本次实际扫描到的连续材料末条 seq；0 = 无材料
    head_end_seq: int | None = None     # 头部锚点末条 seq
    earlier_records: int | None = 0     # 材料与尾部之间未读取的记录数；None = 至少 N 条
```

不新增 `material` tuple，不新增 `material_event_ids`。`material` 由 `ContextPlan.older` 中 `(covered_seq, material_end_seq]` 的组推导；`material_end_seq` 在 `plan_context` 中取实际命中的材料组最大 seq（不超过扫描末端），避免把 pinned/当前输入算进材料。

`ContextPlan`：

```python
@dataclass(frozen=True, slots=True)
class ContextPlan:
    prefix: tuple[ModelMessage, ...]
    pinned: tuple[Group, ...]
    older: tuple[Group, ...]
    working: tuple[Group, ...]
    covered_seq: int = 0
    material_end_seq: int = 0
    head_end_seq: int | None = None
    earlier_records: int | None = 0
    run_input: RunInput | None = None
    active_skills: tuple[str, ...] = ()
    pinned_bytes: int = 0
    pinned_omitted: tuple[str, ...] = ()
```

`coverage_seq` 删除；所有摘要 coverage 只读 `material_end_seq`。

`run_service._load_history` / `_restored_history` 返回 `LoadedHistory`；`ExecutorRequest`、`AgentRunRequest`、`AgentRunState` 透传 `covered_seq`、`material_scan_end_seq`、`head_end_seq`、`earlier_records`。`ActiveSkillContext.merge` 只合并消息并保留这些元数据。

Worker 的任务历史没有持久摘要 coverage：`covered_seq=0`、`material_scan_end_seq=max(older.seq)`（全部 older 可摘要）、`earlier_records=0`；`head_end_seq` 与 Runtime 同一规则，取任务历史首个 user 组及其后的首个 assistant/tool 组的末条 seq（没有则 `None`）。`plan_context` 在调用方不传 `material_scan_end_seq` 时取全部 older（Worker 路径）；Runtime 必须显式传值。

### 2.3 审计契约

`LayerOutcome` 增加可选字段：

```python
dropped_records: int = 0
retained_from_seq: int | None = None
```

`as_layer_payload()` 增加两键；`CONTEXT_COMPACTED` 的 `layers` 原样落库。Console `EventCompaction` 增加 `dropped_records`、`retained_from_seq`；Run 事件详情显示“保留起点/丢弃条数”。`head_end_seq` 只作为 `ContextPlan` 内部边界，不进 Console 展示。

`dropped_records` = 被丢弃组的唯一 `source_event_id` 数 + `earlier_records`（已知时）；`earlier_records is None` 时标记写“至少 N 条”，审计保留已知下界。`retained_from_seq` = 保留尾部最小 `source_event_seq`；没有尾部时为空。

## 3. 历史装配（只读）

`load_history(..., budget_tokens: int, current_message_id: str | None)` 算法：

1. 取最新摘要与 `covered_up_to`、`protected_event_ids`；先做会话归属校验。若摘要 payload 无法解析出五字段，视为无摘要（`covered_seq=0`），不静默跳过其后的历史；记 warning + metric。
2. **当前输入**：材料与头部锚点查询排除 `payload.message_id == current_message_id` 的事件；尾部仍包含当前用户消息（它属于当前回合，由 `plan_context` 固定）。
3. **尾部读取**：按 `seq` 倒序 keyset 分页（每页固定条数），用 `estimate_message_tokens` 累加；达到 `budget_tokens × READ_HEADROOM`（代码常量）、或触到 `covered_seq` / `through_seq` 即停。页面边界落在 `TOOL_CALL` 且其 `ASSISTANT_TURN` 不在集合内时，向前补一页；补不齐则丢弃该组并计入缺口。
4. **材料区间**：若尾部读取因预算停下（未触到 `covered_seq` / `through_seq`），从 `covered_seq` 起按 `seq` 正向 keyset 分页读取，累加到 `budget_tokens` 即停，得到连续材料与 `material_scan_end_seq`；否则材料就是全部未摘要历史，`material_scan_end_seq` = 未摘要区间最后一条历史事件的 seq。材料读取必须排除当前输入，并复用尾部的同一组过滤：
   `HISTORY_EVENT_TYPES`、`is_deleted=false`、`through_seq`、`unconsumed_run_id/consumed_event_seq`。
5. **头部锚点**：当 `covered_seq == 0` 时总要确定 `head_end_seq`。若尾部已经覆盖会话开头，直接从已加载组取开头最多 `HEAD_ANCHOR_GROUPS`（代码常量 2）组并记录末条 seq；若尾部没有覆盖开头，另取开头最多两组历史事件，排除当前输入，并复用尾部的同一组过滤（`is_deleted` / `through_seq` / `unconsumed_run_id`），按 `seq` 合并、去重后记录 `head_end_seq`。头部锚点从会话第一个 user 组开始，包含其后的首个 assistant/tool 组；若两组估算超过预算的 50%，只保留首个 user 组；若首个 user 组本身仍超过预算，保留它并记录超预算告警。当前输入就是会话第一条 user 时没有头部锚点，`head_end_seq=None`。
6. **受保护事件**：`protected_event_ids` 单独查询后按 `seq` 合并，不参与 keyset 游标。
7. **缺口计数**：在 `max(material_scan_end_seq, head_end_seq)` 之后、尾部起点之前，统计未读取的历史事件，排除当前输入、protected 事件、`unconsumed_run_id/consumed_event_seq`；失败则 `earlier_records=None`。
8. **成对回放**：沿用 `_paired_tool_results` + `_history_entries`；删除 `_kept_tool_rounds` 与 `_to_messages` 的回合上限参数。只按组边界输出，不产生孤儿 `tool`。
9. 返回摘要前缀 + 头部锚点 + 材料 + 尾部，以及 `covered_seq` / `material_scan_end_seq` / `head_end_seq` / `earlier_records`。

单次请求的读取量上界 = `1.25 × budget`（尾部）+ `1 × budget`（材料）+ 头部锚点（有界），都随预算有界（NFR-PERF-01）；无洞时只有尾部 + 材料。

**索引**：新增 partial index

```sql
CREATE INDEX ix_canonical_event_history
ON runtime.canonical_event (conversation_id, seq)
WHERE is_deleted = false
  AND event_type IN ('USER_MESSAGE','ASSISTANT_MESSAGE','MEMORY_CONFIRMATION_RESOLVED',
                     'TOOL_CALL','ASSISTANT_TURN','TOOL_TASK_ACCEPTED',
                     'BACKGROUND_RESULT','TOOL_SUBMISSION_FAILED');
```

迁移测试比对索引谓词与 `HISTORY_EVENT_TYPES`；ORM `__table_args__` 同步声明该索引。没有该索引时，`event_type` 过滤会在大量 `message.delta`/审计行上扫描，NFR-PERF-01 不成立。

**不在装配层做的事**：不 `trim_history`、不插标记、不写 `CONTEXT_COMPACTED`、不推进 coverage。

**ActiveSkillContext**：`load_skill_context` 仍在 epoch > 1 时按 Run 恢复完整 Skill 指令组；`merge` 只负责去重与按 seq 排序，不改变头部锚点、材料区间与尾部顺序。

## 4. 请求边界唯一裁剪

`packages/agent-core/src/muad_agent_core/context/compactor.py` 新增纯函数：

```python
def fit_history(
    plan: ContextPlan,
    *,
    budget_tokens: int,
) -> tuple[ContextPlan, LayerOutcome | None]:
    ...
```

算法：

1. 移除 `older` / `working` 里既有的省略标记（按 `OMISSION_MARKER_PREFIX` 识别），保证每次重建只有一条。
2. 受保护集合：
   - `prefix`（系统提示、memory、摘要、resume hint）；
   - `pinned`（当前用户组、生效 Skill 组；`run_input` 缺失时补最后一个 USER 组）；
   - `older` 中 `seq <= plan.head_end_seq` 的头部锚点；
   - `working` 中最近一次完整工具往返组及其后的全部消息（没有工具往返时保护最后一个 working 组）。
3. 从新到旧把 `older` 中段与 `working` 中段按组加入预算，直到 `estimate_messages_tokens` 超过 `budget_tokens`；组是最小单位，不拆组。`prefix` 不计入预算；`pinned` 计入体积但不参与丢弃；头部与最近往返计入但永不丢弃。
4. 若仍有组被丢弃、或装配留下缺口（`plan.earlier_records != 0`），生成唯一 `SYSTEM` 省略标记，插在头部锚点之后、保留的尾部之前：

   ```
   [历史裁剪] 本次请求省略了中间的 N 条对话记录（消息与工具调用），它们不在当前上下文中；保留的是会话开头目标与最近对话。需要时请让用户重新提供被省略的内容。
   ```

   `N` = 被丢弃组的唯一 `source_event_id` 数 + `plan.earlier_records`；`earlier_records is None` 时写“至少 N 条”，N 取 `max(1, 已丢弃组的下界)`，避免出现“至少 0 条”。
5. `retained_from_seq` = 保留尾部最小 `source_event_seq`；`dropped_records` 按 §2.3；`bytes_before/after` 用 `history_bytes` 统计对话区。
6. 既没有组被丢弃、也没有缺口时原样返回原 plan 与 `None`，请求中不得出现标记；材料元数据保留但渲染忽略。
7. 发生裁剪或存在缺口时，返回的 plan 清空材料区间：`material_end_seq = 0`；`covered_seq` 保持不变（只有 `with_summary` 成功后推进 coverage）；`head_end_seq` 不由 `fit_history` 改写。

**摘要层**（`layers.py`）：

- 入口先估算整个 `plan` 的对话区 token；**未超过预算且 `earlier_records == 0` 时原样返回**，不跑摘要、不插标记。
- 其余情况（超预算，或存在缺口）：
  - **只有历史区超预算或存在缺口才触发摘要**：`older`（含材料/头部/尾部）估算超过预算，或 `earlier_records != 0`。仅 `working` 超预算时直接 `fit_history`，不调用摘要模型。
  - 摘要条件：`summary.enabled`、存在材料组、且材料末条 `> self._summarized_through_seq`（首次）或缓存摘要不存在。
  - `summary_material(plan)` = `plan.prefix` 里的旧摘要 + `plan.older` 中 `(covered_seq, material_end_seq]` 的组；逐字存档（transcript）用同一份材料。
  - 摘要成功**或复用缓存摘要**：`with_summary` 后调用 `fit_history` 做最终预算检查；`with_summary` 只移除实际材料组，保留头部/尾部/pinned，并设置 `covered_seq = 材料末条`、`material_end_seq = 0`；`head_end_seq` 取仍保留的头部组末条 seq，头部全部被摘要覆盖时置空。
  - 摘要失败/未启用/持久化失败：直接 `fit_history`，不推进 coverage。
- `SummaryAttempt.coverage_seq = material_end_seq`（实际材料末条），不是 `max(older.seq)`。
- 摘要输出受摘要模型冻结的 `max_tokens` 限制；摘要 prefix 不计入历史预算，但必须落在部署最小窗口的 `HISTORY_BUDGET_RESERVE_TOKENS` 内。
- 摘要写成功后更新 `self._summarized_through_seq = material_end_seq` 并缓存摘要消息；Runtime 与 Worker 两个实现各自持有这份状态。
- 同一 Run 的后续模型调用：材料区间相同且已有缓存摘要时直接 `with_summary` + `fit_history`，不重复调用摘要模型。

**删除的层/函数**：`snip`、`micro`、`trim_history`、`trim_older_history`、`compact_history`、`BudgetPolicy`、`_kept_tool_rounds`、`_prepare` 中的 micro 分支，以及 `history_budget_of` / `history_tool_rounds_of` 这类旧冻结读取辅助。`compact_plan` / `compact_plan_layered` 的 `history_budget_messages` 参数全部改为 `budget_tokens`。

**Worker 安装口径**：`TaskContextCompactor` 不再用 `settings.snip.enabled` / `settings.micro.enabled` 决定是否安装；只要本 Task 有冻结压缩配置就安装，以保证 `fit_history` 永远执行。摘要模型缺失或凭据不可用时只关闭摘要层，不关闭预算裁剪。

**估算**：

```python
def estimate_message_tokens(message: ModelMessage) -> int:
    return message_bytes(message) + MESSAGE_PROTOCOL_OVERHEAD_BYTES
```

1 token ≈ 1 UTF-8 字节；`MESSAGE_PROTOCOL_OVERHEAD_BYTES` 复用 `executor.conservative_input_estimate` 的同一常量（上移到 `agent-core` 共享模块）。`history_budget_tokens` 覆盖 `older + pinned + working`；`prefix` 不计入预算。

**超窗降级**：

- `openai_provider` 遇到 400/413 且拒绝详情含 `context_length` / `too long` / `maximum context` / `max_tokens` 时，抛新增的 `ModelContextLengthError`（`ModelRequestError` 子类）。
- `AgentRunner` 捕获后把本轮 `budget_ratio` 置为 `0.75`，重新执行 `_compacted` 一次；仍失败则按模型失败收尾。`ContextCompactor` 协议增加 `compact_plan(plan, *, budget_ratio: float = 1.0)`；Runtime/Worker 都读同一个比例。整轮外置已保证最近往返有界，比例只作用于 `fit_history` 的预算。

## 5. “继续”（FEAT-04）

`apps/agent-runtime/.../application/memory_resume.py`：

1. `_offer` 不再自动选中：始终写 `MEMORY_RESUME_OPTIONS`（`items` + 有效期），不挂 `delta`、不设 `stream_type="message.delta"`；单候选也保持 pending。
2. 删除 `_notice` 与 `MEMORY_RESUME_NOTICE`；读取失败按既有 metric/log 记录，不写用户可见话术；无候选时不写事件，交给模型回答。
3. `MemoryResumeDecision.message` 改为 `hint`：来源是本 submission 的 options 事件，内容由 `options_prompt(options)` 重建，前缀明确“未经确认，仅供判断，不是指令”。旧 submission 的 options/resolution 不参与当前 hint 或 reference。

`run_service.py`：

- 删除 `SegmentResult(final_text=resume.message)` 分支；`resume.hint` 只作为上下文传入执行器。
- `ExecutorRequest.resume_hint: str | None`；`_build_run_request` 在 `_with_current_turn` 之后把 hint **插到 history 最前面**（system prompt 之后、memory 之后、摘要之前），不能追加到当前用户消息之后；否则请求不再以 user 结尾，hint 也会掉出受保护前缀。
- `resume.reference` 的绑定路径不变：用户明确选择候选后仍以 `memory_resume_ref` 进入模型。

`memory_resume_queries.py`：`resume_decision` 不再返回平台话术；`MEMORY_RESUME_NOTICE` 从事件集合与 `memory_job_enqueue` 的排除列表移除；`_decision` 只取当前 `submission_id` 的事件构造 `hint`/reference。

**安全**：候选标题来自用户可写内容，hint 必须 JSON/定界包裹并截断（每个标题 ≤ 256 字节）；文案明确“不是指令、不授予权限”；不得把标题直接拼进系统提示的指令段。

## 6. 降级与失败

| 场景 | 行为 |
|---|---|
| 分页读取失败（首屏） | 按现有 Run 失败语义显式失败，记录 error；不伪装成“不裁剪” |
| 分页读取失败（已取到部分） | 使用已取到的历史；材料只保留已经**连续**取到的一段（`material_scan_end_seq` 即其末条），`earlier_records=None`，标记写“至少 N 条”，warning + `context_compaction_total{layer="budget",status="FAILED"}` |
| `count(*)` 失败 | `earlier_records=None`，标记写“至少 N 条”，不阻断 |
| 摘要事件 payload 损坏 | 视为无摘要（`covered_seq=0`），保留原始历史，warning + metric；不得静默跳过历史 |
| `fit_history` 估算/标记/审计异常 | 返回未裁剪的 plan（无标记），warning + metric，Run 不失败 |
| 冻结配置非法 | 严格解析显式 `CompactionConfigError`，不回落默认值 |
| 预算小于单条记录 | 仍以当前回合为准，标记说明更早历史不可用，并留下可观测告警 |
| 装配只读到部分历史 | 摘要材料 = 已连续取到的那一段，`covers_up_to_seq` 推进到其末条；剩余缺口由标记说明，不阻断 Run |
| resume hint 读失败 | 照常执行模型，不注入提示 |
| 上游 context-length 拒绝 | 预算乘 0.75 重试一次；仍失败按模型失败收尾 |

## 7. 测试与验证

- `tests/agent_core/test_context_budget.py`（新增）：保头保尾、当前用户/最近往返保护、组边界、无孤儿、标记只在“有丢弃或有缺口”时出现、`dropped_records`/`retained_from_seq`、`budget_ratio`。
- `tests/agent_core/test_context_coverage.py`：材料 seq 区间、摘要事件 `covers_up_to_seq` 由实际材料末条给出；当前用户消息不被覆盖；摘要替换保留尾部；旧 `coverage_seq` 字段移除。
- `tests/agent_core/test_context_layers.py`：摘要只看见连续材料；同一 Run 多次模型调用只调用一次摘要模型，第二次复用同一份摘要；摘要失败仍回落裁剪并留可观测降级。
- `tests/agent_core/test_context_compactor.py`、`test_context_plan.py`、`test_context_protection.py`、`test_context_memory.py`：删除 snip/micro/trim/`coverage_seq` 用例，重写为 `fit_history`、seq 区间与头部/尾部保护。
- `tests/agent_runtime/test_context_builder_retention.py`（新增，真实 PostgreSQL）：6 轮工具对话 + 预算内/预算外；第 1 轮头部锚点仍在（包括“全量历史已读但预算略不足”的路径）；缺口计数；分页次数有界；partial index 生效；材料窗口（从 `covered` 起连续、≤ 预算）与 `material_end_seq`；当前输入不进材料；跨轮缺口收敛；旧摘要清理门禁（清理后无旧 coverage）与损坏摘要按无摘要降级。
- `tests/agent_runtime/test_context_compaction_config.py`：4 个叶子的解析、默认值、上限、旧键拒绝、严格冻结解析、Agent 覆盖。
- `tests/agent_runtime/test_run_submission*.py`：new / resume / continuation 三条路径读到同一冻结预算。
- `tests/agent_runtime/test_memory_resume*.py`、`test_memory_redesign_resume.py`：单候选不自动绑定；裸“继续”进入执行段；明确选择才绑定；无候选时模型照常回答；不再有 NOTICE；旧 submission 不影响当前 hint/reference。
- `tests/agent_runtime/test_tool_round_budget.py`、`test_artifact_round_budget.py`、`test_context_compaction_artifacts.py`、`test_tool_result_materialization.py`、`test_archive_tools.py`：整轮外置预算由 `history_budget_tokens` 派生；预览头尾由阈值/上限派生；无 `round_budget_bytes` 配置。
- `tests/agent_runtime/test_context_metrics.py`：摘要只看见连续材料区间；同一 Run 多次模型调用只调用一次摘要模型；摘要失败仍回落裁剪并留可观测降级。
- `tests/agent_worker/test_scheduled_v2_agentic_adapter.py`：Worker 严格冻结解析、始终安装 `fit_history`、Summary 使用全部 older、摘要去重、旧字段删除。
- `tests/console_platform/test_platform_settings_api.py`、`tests/test_platform_settings_schema.py`：设置页字段增删、校验错误、旧文档 fail-closed、全量重置后的 revision 0、严格冻结解析。
- `tests/frontend/` 设置页契约：压缩分组只渲染 4 个字段；`settings.unit.tokens` 词条齐备；Run 事件详情显示保留起点/丢弃条数。
- `tests/acceptance/dfx/test_dfx_journeys.py`、`tests/acceptance/im_gateway/test_context_compaction.py`、`test_memory_redesign_e2e.py`：更新“继续”/NOTICE/旧注释。
- `tests/test_configuration_inventory.py`：重新生成 CSV，删除 `BudgetPolicy`、`snip`/`micro` 与工具结果旧字段行。
- `tests/context_compaction_inventory.py`（或按 `harness-test` 新建的本需求 inventory）：覆盖 TASK、Spec-Refs、Scenarios。

## 8. 发布与清理

- **不写数据迁移**：没有 Alembic data migration，也没有反向迁移脚本；开发阶段直接重置不兼容数据。
- **发布前一次性重置（runbook，不是产品代码路径）**：
  - `DELETE FROM control.platform_setting`：全租户、**全 revision**、**所有分组**回到 schema 默认；同时清理 `control.platform_setting_idempotency` 中平台设置回执，避免重放旧文档。这是整份平台设置重置，不是只清 `compaction`。
  - `control.agent_definition.runtime_config_json` 删除 `budget.compaction` 整个块；其他 runtime_config 原样保留。
  - 删除全部 `CONTEXT_SUMMARY` 事件（旧 coverage 不可信）；旧摘要 transcript 产物不参与重建，可按保留期清理。
  - 排空在途 Run/Task；旧 `policy_json` 不保留兼容解析，旧终态快照只作审计。
- **schema 迁移只有索引**：新增 `ix_canonical_event_history` partial index；这是 DDL，不是数据迁移，随镜像发布即可。
- **回滚**：回退镜像后旧代码同样不认识 `history_budget_tokens`（fail-closed），所以回滚 = 回退镜像 + 再次执行上述重置。
- **部署窗口**：`SharedSettings.model_context_window_min_tokens` 由部署注入（`.env.example`、ConfigMap/Helm/k8s 同步）；发布前核对 `history_budget_tokens + 模型输出预算 + 系统提示/工具目录 < min_window`。
- **回滚镜像**：`CONTEXT_COMPACTED` 新增字段对旧 Console 是未知字段，按现有展示降级；不依赖旧代码理解新键。

## 9. 规范引用

| 规范 | 约束 |
|---|---|
| `snapshot/harness-snapshot#RULE-snapshot-001` | 压缩失败保留原历史并可观测降级 → §6 |
| `snapshot/harness-snapshot` Conventions | 设置在 Run/Task 创建边界冻结一次；历史工具预算旧口径由本需求改写为 token 预算（Spec Delta） |
| `arch/harness-arch` Conventions | 受保护前缀不得被裁；降级必须语义等价 → §3/§4/§6 |
| `persistence/harness-data` Conventions | 摘要 coverage 只覆盖连续材料、旧摘要先失效；平台设置版本表 append-only，开发重置由 runbook 一次性执行 → §4/§8 |
| `memory/harness-memory` Conventions | 记忆确认续跑的 410 语义不变；任务线索只作未确认数据 → §5 |
| `api/harness-api#RULE-api-001` | 设置校验错误走统一封套与错误码 → §2.1 |
| `test/harness-test#RULE-test-001` | 真实 PostgreSQL/真实历史装配的集成测试；人工场景见 `conversation-context-retention.scenarios.md` → §7 |
| `test/harness-test` Conventions | 配置清单重生成、需求 inventory 闭合 → §7/§8 |

---

*文档结束*
