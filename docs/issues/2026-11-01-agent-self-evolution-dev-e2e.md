# agent-self-evolution 开发环境 E2E 测试计划

> 日期：2026-11-01
> 环境：dev（tenant `default`，agent `demo-01`，model `deep`，PG `muad` @ 0030 head）
> 入口：IM Gateway（企微 bot）或 WebChat

## 1. 环境事实

| 项 | 值 |
|---|---|
| 租户 | `default` |
| Agent | `demo-01`（`f069722a-3752-4316-9613-d1e4e606393c`） |
| 模型 | `deep`（deepseek，provider `dev`） |
| 入口 | 企微 bot（`aiblnVCKe3oM4XPUTeuwEwfAYU06O15Komp`）、WebChat `webchat-demo01` |
| 技能 | `greeting`、`skill-vetter` 启用；`weather-query` 禁用 |
| 迁移 | `0030 (head)` |
| 测试库 | 已删除（`muad_acc_*`、`muad_unit_iso`），仅保留 `muad`(dev)、`isf`、`isf_mattermost`、`postgres` |

## 2. 当前真实接线状态（2026-11-01）

| 环节 | 实现 | 运行时接线 | 可端到端观察 |
|---|---|---|---|
| 任务族派生（Run 完成写 `run_record.task_family`） | ✅ | ✅ `_finalize_run` | ✅ |
| 注入归因 `MEMORY_INJECTED` | ✅ | ✅ `MemoryContextLoader` | ✅ |
| 隐式信号 `LEARNING_SIGNAL` | ✅ | ✅ consolidation job 入口 | ✅ |
| Golden Set 抽取 `GoldenSetExtractor` | ✅ | ❌ 无调用方/调度 | ❌ 需脚本手动触发 |
| 经验候选 `ExperienceCandidates` | ✅ | ❌ 无调用方/调度 | ❌ 需脚本手动触发 |
| 两臂回放 `LearningReplay` | ✅（编排） | ❌ 无生产 `CaseRunner`（真实模型执行端未接） | ❌ |
| 激活/晋升 `LearningActivation` | ✅ | ❌ 无 CLI/流水线 | ❌ |
| 任务族注入（`set_task_family`） | ✅ | ❌ 执行层未调用：skill 执行后不会切换族 | ❌ |
| `learning` 设置读取（阈值/白名单/开关） | schema ✅ | ❌ 五处读取点均未接 | ❌（当前使用代码默认值） |
| 任务内 verifier | ✅ | ❌ `answer_verifier` 从未注入 | ❌ |

结论：**通过 IM 直接发 prompt 可以验证「采集侧」（任务族、归因、信号），但无法完成「激活并影响下一次 Run」的闭环**。要让「越用越智能」可端到端观察，需要先补运行时接线。

## 3. 现在就能验证的场景（无需接线）

### 场景 A：任务族与注入归因
1. 通过 IM 发一条普通任务（例如「把下面三条待办整理成表格：…」），等 Run 完成。
2. 查询：
```sql
select id, status, task_family from runtime.run_record
 where tenant_id='default' order by create_time desc limit 1;
-- 期望：task_family = agent:f069722a-... （无 skill）或 skill:<key>

select event_type, payload_json from runtime.canonical_event
 where tenant_id='default' and event_type in ('MEMORY_INJECTED','LEARNING_SIGNAL')
 order by seq desc limit 5;
-- 期望：MEMORY_INJECTED 含 items(score/decision)/dropped/selection_hash
```

### 场景 B：隐式纠正信号
1. 同一会话先问 A（得到回答），紧接着回复「不对，改成表格」。
2. 等 consolidation job 执行（`memory_consolidation_job` 状态 DONE）。
3. 查询上一条 SQL 的 `LEARNING_SIGNAL`：期望 `signal_type=NEGATION`、`source_run_id=上一 Run`。

### 场景 C：记忆门控（噪声下降）
1. 通过 Console 给测试用户写入 3 条记忆：1 条与当前问题相关、2 条无关。
2. 发一条只与相关记忆沾边的问题。
3. 查询 `MEMORY_INJECTED.items`：期望只注入相关条目，无关条目不出现在 items 中（阈值门控），并带 score/decision。

## 4. 需要先接线才能验证的场景（自进化闭环）

### 场景 D：经验沉淀 → 回放 → 激活 → 下一次 Run 生效
1. 同一任务族执行 ≥3 次成功任务（`experience_min_success_runs=3`）。
2. `GoldenSetExtractor.extract(tenant)` 生成 `eval_case`。
3. `ExperienceCandidates.generate(tenant)` 生成 `user_memory`（category=PROCEDURE，activation_state=PROPOSED）。
4. `LearningReplay` 两臂回放（真实模型）：基线臂 vs 候选臂，判定 PASSED/REJECTED。
5. `LearningActivation.activate_manually`（或自动 TRIAL→ACTIVE）写审计。
6. 第 4 次同类任务：`MEMORY_INJECTED.items` 出现该经验；回答体现 PROCEDURE。

**当前阻塞**：第 2–6 步没有调用方；且即使手动调用，Run 内的经验注入还需要执行层把任务族传给 `MemoryContextLoader`（`set_task_family` 无调用者），目标文本通道又因 `trigger_text` 目前只写 family 串而难以命中。

## 5. 补齐接线的最小工作包（建议作为下一个需求）

1. **执行层族传播**：executor 在 Run 启动时设置 `agent:<id>`，`execute_skill`/`run_skill_script` 成功后切换为 `skill:<key>`；`learning` 设置从冻结 `policy_json` 读取并传入选择器。
2. **调度**：worker 周期性/按 Run 完成后触发 Golden Set 抽取 → 候选生成 → 自动 TRIAL。
3. **生产 CaseRunner**：从 `eval_case` 的 Snapshot + Run 输入重建请求，用模型池（BACKGROUND）重跑两臂，verifier 判逐项成功。
4. **`learning_admin` CLI**：list/replay/activate/retire/explain，激活需回放证据。
5. **验收**：一条真实模型的两臂回放 + 激活 + 下一次 Run 注入的 E2E 场景。

预计 2–3 个 capability 任务（约 4–6 天）。

## 6. 观察用 SQL

```sql
-- 最近 Run 的任务族
select id, status, task_family, end_time from runtime.run_record
 where tenant_id='default' order by create_time desc limit 10;

-- 学习事件
select seq, event_type, payload_json from runtime.canonical_event
 where tenant_id='default'
   and event_type in ('MEMORY_INJECTED','LEARNING_SIGNAL','EXPERIENCE_STATE_CHANGED')
 order by seq desc limit 20;

-- Golden Set 与经验候选
select id, task_family, item_count, criteria_json from runtime.eval_case
 where tenant_id='default' order by create_time desc limit 10;
select memory_key, category, activation_state, task_signature, trigger_text, evidence_json
  from runtime.user_memory
 where tenant_id='default' and category in ('PROCEDURE','FAILURE_PATTERN','VERIFICATION');

-- 回放报告与审计
select task_family, experience_key, version, decision_json from runtime.eval_report
 where tenant_id='default' order by create_time desc limit 10;
select action, after_json from control.config_audit_log
 where tenant_id='default' and resource_type='MEMORY_EXPERIENCE' order by create_time desc limit 10;
```
