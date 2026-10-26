# code-flow 0.8.0 工具链缺陷明细

> 调查日期：2026-10-26
> 触发场景：`high-qps-remediation` 需求（12 个 TASK、31 个验收场景）连续执行期间，
> 工具链从旧 0.8.0 构建升级到新 0.8.0 构建后暴露的问题。
> 结论来源：全部为**在本仓真实复现**的现象（归档需求同样可复现），不是推测。
>
> 本仓在 `1e617464` 对下列问题打了本地补丁（`.code-flow/scripts/` 7 个文件）。
> 上游修复后，这些补丁应撤销，改为跟随上游。

## 汇总

| ID | 级别 | 问题 | 影响面 |
|---|---|---|---|
| B1 | P0 | 工件引用解析基准不一致（需求目录 vs 项目根） | 所有既有需求的门禁恒红 |
| B2 | P1 | 引用 item_id 要求字面出现，既有引用格式全部失效 | 同上 |
| B3 | P0 | 真值校验要求「全需求规则 verified」，与 Done Gate 的 deferral 冲突 | 多任务需求永远无法 collect/merge |
| B4 | P0 | Start 门禁把 `exec: deferred` 的 smoke E2E 排进**每个**任务 | 一个延后场景阻塞所有任务启动 |
| B5 | P0 | 批次 smoke 按整个需求执行，不按本批 TASK 过滤 | 需求进行中的任何批次都失败 |
| B6 | P1 | stop hook 只看测试层级，忽略「执行方式=deferred」 | 计划声明延后的场景被当未闭环 |
| B7 | P1 | 0.8.0 计划 schema 硬断裂，无迁移路径 | 升级后所有既有需求 `task start` 失败 |
| B8 | P1 | registration 要求 `collect` 配置，但工具从不生成 | 新计划的 browser/smoke check 永远 `check_collection_missing` |
| B9 | P1 | manifest 重锁静默丢失运行态（status/evidence/runs） | 已 verified 的场景被回退成 planned |
| B10 | P2 | `task finish` 在提交前跑，证据 input_hash 与提交后树不一致 | collect/merge 报 `input_hash_mismatch`，只能重跑验收 |
| B11 | P2 | `spec refresh` 与 `--verify-plan` 的顺序陷阱 | 改任务文件后 19 条规则报 `rule_artifact_stale` |
| B12 | P2 | `cf_stop_hook.py` 并发执行会互相等锁、长时间不退出 | 观察到一个实例挂起 5 小时以上 |
| B13 | P0 | 无 active TASK 时，path-mapped 单个 spec 超 1700 token 直接阻断编辑 | 本仓 `harness-test.md` 38KB ⇒ 任何 `tests/` 文件都改不了 |

---

## B1 工件引用解析基准不一致

**级别**：P0（阻断所有既有需求）

**现象**

任取一份归档需求执行计划门禁：

```bash
code-flow spec gate --task-dir .code-flow/tasks/archived/2026-10-16/agent-capacity-webchat --stage plan --json
```

19 条 required 规则全部返回 `rule_artifact_missing`。该需求此前是通过全部门禁归档的。

**根因**

- 写入侧：`cf_spec_context.py:1420 _application_ref(task_dir, …)` 要求引用路径落在**需求目录**内；
  `cf_spec_context.py:1182 _refresh_artifacts(…, artifact_root)` 由 `refresh … --task-dir` 传入需求目录。
- 读取侧：`cf_rule_readiness.py:58 artifact_problem(root, artifact, …)` 用 `Path(root)`（**项目根**）拼接。

同一份 `spec-context.yml`，两处用不同基准解析同一个相对路径。把基准换成需求目录后，
同一批引用全部通过。

**建议修复**

`applied_findings` 接受 `task_dir` 参数（调用方 `validate_stage` 已经从 task_dir 推导 root），
或 `artifact_problem` 在项目根找不到时按 `.code-flow/tasks/*/*/<artifact>` 回落（仅唯一命中时）。

---

## B2 引用 item_id 必须字面出现

**级别**：P1

**现象**

B1 修复路径后，所有引用转为 `rule_artifact_reference_missing`。既有引用形如：

```yaml
section_id: TASK-008
item_id: task-008
```

而 `TASK-008` 段落里并不存在字面量 `task-008`；设计文档里的 `rule-api-002` 同理。
`cf_rule_readiness.referenced_item` 只在**包含 item_id 子串的行**里取引用。

**影响**

schema 0.8.0 之前 `spec apply` 生成的引用**全部**失效，包括已归档需求；
等于新门禁与历史数据格式不兼容，且没有任何迁移工具。

**本仓处理**

把引用改指向真实字面锚点：设计侧指向 `Spec Compliance Matrix` 的规则行
（`harness-api#RULE-api-002`），计划侧指向各 TASK 的 `[spec#RULE][verifier]` 行。

**建议修复**

- 明确 item_id 的约定并在 `spec apply` 时校验；
- 或对 `<spec>#<RULE>` 形式自动解析到该规则的同名行；
- 至少提供一次性迁移命令。

---

## B3 真值校验越界：done 任务要求全需求规则 verified

**级别**：P0（多任务需求的 collect/merge 死锁）

**现象**

两条并行任务都 finish 通过后：

```bash
code-flow task parallel collect --run-id <id>
# {"ok": false, "code": "task_status_truth_mismatch",
#  "message": "TASK-008: 文档状态与 spec-context 冲突: harness-auth#RULE-auth-001: unverified, …"}
```

列出的 7–10 条规则**都不属于该任务**。

**根因**

`cf_workflow_service.py:70 task_truth_issues()` 遍历 `context.bindings` 的全部 required 规则；
而 `cf_task_runtime._run_done_gate` 按任务范围验证，把范围外规则统一写 `deferred_to_review`。
⇒ 只要同需求还有 draft 任务，任何批次都无法 collect/merge。

**建议修复**

按任务自身的 `Spec-Refs` ∪ 命中路径的 specs 收敛校验范围（Done Gate 已经有这份 scope）。

---

## B4 Start 门禁把 deferred 场景排进每个任务

**级别**：P0

**现象**

```bash
code-flow task start --task TASK-008 …
# {"ok": false, "error": {"code": "e2e_plan_invalid",
#  "message": "TASK-008: S-06:无法验证测试收集与套件登记"}}
```

S-06 是 `exec: deferred`、`owner: TASK-009` 的 smoke E2E，需要真实浏览器栈，
计划里写明留给需求级 verify-e2e。

**根因**

`cf_acceptance_plan.py:100 _group_scenarios()` 把**任何** `smoke: true` 的 E2E 放进
每个任务的 `scheduled`；`validate_registration` 随即要求它完成 Playwright 收集。
与「deferred 不在编码阶段调度」的语义直接矛盾。

**建议修复**

构造 `scheduled` 时排除 `exec == "deferred"`（`owned` 保留，仍提示该任务要编写该 E2E）。

---

## B5 批次 smoke 按整个需求执行

**级别**：P0

**现象**

两个任务（TASK-008、TASK-011）合并后，批次 smoke 把 6 个**仍属 draft 任务**的场景
（S-08、E-08、S-01、S-02、S-06、S-16）一起执行并判红：

```
batch_smoke_failed: e2e_failed; failed=[E-08,S-01,S-02,S-06,S-08,S-16]
```

批次因此永远无法通过，worktree 无法回收。

**根因**

`cf_task_workflow.py:907 _e2e_layer()` 调 `run_manifest(...)` 时没有传 owner；
`collect` 传下来的 `only_tasks` 只用于筛选任务状态，没有影响场景选择。

**建议修复**

把本批 TASK id 作为 owner 过滤传给 `run_manifest`（`_owned` 需支持多 owner）。

---

## B6 stop hook 忽略「执行方式=deferred」

**级别**：P1

**现象**

`cf-stop` 报：

```
TASK-001 的 S-16 状态未闭环（…仅实现阶段 E2E 可 e2e_deferred…）
```

而 S-16 在计划里是 `exec: deferred`（`defer_reason` / `target_phase: review` 齐全），
Start 门禁按 B4 的期望也不应调度它。

**根因**

`cf_stop_hook.py:149 _acceptance_gap()` 用 `_kind(cells[2])`（测试层级）决定允许集合；
`integration + deferred` 的行于是被要求 verified。

**建议修复**

覆盖表「执行方式」列为 `deferred` 的行，与 E2E 行同样允许 `e2e_deferred`。

---

## B7 计划 schema 硬断裂且无迁移

**级别**：P1

**现象**

工具链升级后，所有既有需求 `task start` 直接失败：

- `e2e_plan_invalid`：每个 expected 必须挂 steps 或声明的 assertions；
- `scenario_defer_metadata_missing`：deferred 场景缺 `defer_reason/owner/target_phase`；
- `check_collection_missing`：browser/smoke check 缺 `collect`。

**问题**

这三类是新增质量门禁（合理），但发布时**没有迁移命令**，也没有兼容窗口；
一个正常演进的仓库升级后立刻全局不可用。

**建议修复**

提供 `code-flow scenarios migrate --add-expected-refs/--defer-metadata`，
或按 `e2e_gate` 版本对旧 manifest 走 warning 路径。

---

## B8 registration 要求工具不生成的配置

**级别**：P1

**现象**

从任务文件 Coverage 表提取出来的 check 天然没有 `collect` / `suite` / `test_ids` / `markers`；
`acceptance manifest` 重锁也不会补。结果：

```
B-04: check_collection_missing
S-06: check_collection_missing
```

**问题**

错误信息要求「无法验证测试收集与套件登记」，但 CLI 没有提供写入这些字段的命令；
唯一通路是手工编辑 `.acceptance-manifest.json`。

**建议修复**

- 从 check 的 `command` 推导默认 collect（pytest `--collect-only -q` / playwright `--list --reporter=json`）；
- 或提供 `code-flow acceptance register --check-id … --collect …`。

---

## B9 manifest 重锁静默丢失运行态

**级别**：P1

**现象**

每次任务文件变更后重锁 manifest，6 个已 `verified` 的场景全部退回 `planned`，
`evidence`/`runs` 被清空；没有任何提示。

**根因**

`cf_acceptance_manifest.py:644 _preserve_runtime_state()` 只在「旧 manifest 仍能通过
`validate_manifest`」时才复制 `status/revision/evidence/runs/stale_checks`；
而任务文件一改，`task_sha256` 必然漂移，该条件几乎永远不成立。

**建议修复**

按 scenario id 无条件保留运行态字段（不可变字段已由哈希校验覆盖），
或在丢弃时显式输出 `dropped_runtime_state=[…]`。

---

## B10 finish 与提交的顺序导致证据哈希失配

**级别**：P2

**现象**

```bash
code-flow task finish …   # pass
git commit …
code-flow task parallel collect …
# required_evidence_missing: S-07: input_hash_mismatch
```

**根因**

`worktree_fingerprint(include_untracked=False)` 只对已跟踪文件取指纹；
`finish` 时新测试文件还是 untracked，提交后它们进入指纹 ⇒ 证据与当前树不匹配。
工具没有和解路径，只能重跑验收。

**建议修复**

finish 后自行提交回写时同步重算指纹；或 collect 在检测到「仅文件跟踪状态变化」时
用提交后的指纹重验一次。

---

## B11 `spec refresh` 与 `--verify-plan` 的顺序陷阱

**级别**：P2

**现象**

编辑任务文件后：

```bash
code-flow acceptance manifest … --verify-plan
# 19 × rule_artifact_stale
code-flow spec refresh --task-dir …    # 之后才 pass
```

`task start` 内部先 refresh，`--verify-plan` 不刷新，于是同样的改动在两条路径上结论相反。

**建议修复**

`--verify-plan` 在内存中先刷新（只读，不落盘），或输出明确提示「先运行 spec refresh」。

---

## B12 `cf_stop_hook.py` 并发执行长时间挂起

**级别**：P2（可靠性）

**现象**

同一工作区并发跑两个 `cf_stop_hook.py` 时，两个进程都卡住不退出；实测有实例持续
5 小时以上，只能手工 kill。没有超时、没有锁提示。

**建议修复**

加文件锁 + 超时（如 120s）并在等待时输出持有者 PID；或拒绝并发执行。

---

## B13 path-mapped 单 spec 超预算时永久阻断编辑

**级别**：P0（无 active TASK 时工作区不可编辑测试）

**现象**

需求归档/全部任务 done 之后，任何 `tests/` 文件编辑都被 PreToolUse hook 拦下：

```
SPEC_WORKFLOW_BLOCKED [spec_budget_exceeded]: Matched Specs exceed 1700 estimated tokens;
create a focused TASK Context or split the editing scope.
```

**根因**

`cf_spec_router.py:229 _read_candidates()` 把 path 命中的 spec 正文装配后与
`min(budget.total=2500, budget.l1_max=1700)` 比较，超了直接 `raise`。旧构建
（`08da3386`）的同一函数**没有**这段预算检查。

本仓 `tests/**` 只命中一个 spec：`test/harness-test.md`（38KB）。它自己就远超 1700，
于是「无 active TASK ⇒ 只能走 path-mapped」这条唯一通路上永远超预算——**没有任何配置
能绕开**（`l1_max` 拉满到 `total=2500` 仍然不够，且 `maximum` 取两者最小值）。

**本仓处理**

超预算时不再抛错：跳过注入、返回一段显式说明（`## Path-mapped Specs over budget`）
并放行编辑。多 spec 命中时校验照旧（真正的上下文膨胀风险在那里）。

**建议修复**

预算超限应当是「不注入 + 警告」，而不是阻断编辑；或在单一 canonical spec 命中时
不再适用 l1 预算。

---

## 附：本仓补丁清单（`1e617464`）
| 文件 | 补丁 |
|---|---|
| `cf_rule_readiness.py` | B1：工件按需求目录回落解析 |
| `cf_acceptance_plan.py` | B4：deferred 不进 scheduled |
| `cf_workflow_service.py` / `cf_task_parallel.py` | B3：真值校验按任务 Spec 范围 |
| `cf_acceptance_runner.py` / `cf_task_workflow.py` | B5：批次 smoke 按本批 TASK 过滤 |
| `cf_stop_hook.py` | B6：deferred 行允许 `e2e_deferred` |
| `cf_spec_router.py` | B13：path-mapped 超预算改为「不注入 + 放行」（`0ac9176d` 之后另行提交） |

B2/B7/B8/B9/B10/B11/B12 未在代码中修，按下列方式规避：引用改为字面锚点、
计划字段补齐、重锁后手工恢复运行态、提交后重跑验收写证据、改文件后先 refresh、避免并发执行。
