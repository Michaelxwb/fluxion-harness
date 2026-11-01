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

## 2. 当前真实接线状态（2026-11-01 补完）

| 环节 | 实现 | 运行时接线 | 端到端可观察 |
|---|---|---|---|
| 任务族派生 | ✅ | ✅ `_finalize_run` | ✅ |
| 族传播（agent/skill） | ✅ | ✅ executor 初始 `agent:<id>` + skill 调用后切换 | ✅ |
| learning 设置冻结 | ✅ | ✅ `policy_json.learning` + executor 读取 | ✅ |
| 注入归因 / 隐式信号 | ✅ | ✅ 每请求 / consolidation 入口 | ✅ |
| Golden Set 抽取 | ✅ | ✅ learning 调度循环（每 60s） | ✅ |
| 经验候选 | ✅ | ✅ 同上 | ✅ |
| 两臂回放 | ✅ | ✅ 生产 CaseRunner（Snapshot + 模型凭据） | ✅ |
| TRIAL / 晋升 / 退休 | ✅ | ✅ 调度循环 + 审计 | ✅ |
| learning_admin CLI | ✅ | ✅ `scripts/learning_admin.py` | ✅ |
| 接线盘点 | ✅ | ✅ `tests/architecture/test_learning_wiring_inventory.py` | ✅ |

前提：租户的 `learning_enabled=true`。dev 的 `default` 租户在 Console「系统设置 → 学习与记忆」打开「启用学习闭环」即可（或按第 4 节用 CLI 触发）。

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

## 4. 自进化闭环场景（现已可端到端验证）

### 场景 D：经验沉淀 → 回放 → 激活 → 下一次 Run 生效

1. 同一任务族执行 ≥3 次成功任务（同 agent、同 skill 或同会话连续任务）。
2. 等待 learning 调度循环（默认 60s）一轮，或手动触发：
```
.venv/bin/python scripts/learning_admin.py extract --tenant-id default --user-id <uuid>
.venv/bin/python scripts/learning_admin.py candidates --tenant-id default --user-id <uuid>
```
3. 调度自动执行两臂回放（基线臂不携带经验、候选臂携带），PASSED 后经验进入 TRIAL 并写审计。
4. 再等一轮调度：TRIAL 期无劣化信号自动晋升 ACTIVE。
5. 第 4 次同类任务：`MEMORY_INJECTED.items` 出现该经验；回答体现 PROCEDURE。

观察 SQL 见第 6 节。

## 5. 已完成的接线工作包（learning-pipeline-wiring 需求）

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
