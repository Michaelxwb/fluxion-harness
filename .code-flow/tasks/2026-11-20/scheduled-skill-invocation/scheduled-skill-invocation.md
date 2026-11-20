---
{"version":1,"requirement":"scheduled-skill-invocation"}
---
# Tasks: Skill 调度与对话调用可调用性

- **Created**: 2026-11-20
- **Workflow**: full
- **Inputs**: [PRD](scheduled-skill-invocation.prd.md)、[后端增量设计](scheduled-skill-invocation.backend.design.md)、[人工验收场景](scheduled-skill-invocation.scenarios.md)
- **Plan Status**: 待任务边界、Spec-Refs、Scenarios、Tests 复核；所有 TASK 为 draft，未开始实现。

## 目标与范围

在已实现的 `scheduled-task-v2` 基线上补齐：

- Skill artifact 的 `invocation_contract`（版本、`invocation: argv|stdin`、参数 schema）；
- 导入链路保留 contract，非法契约显式拒绝；
- Draft 创建/更新时执行调度预检：解析、渲染、schema、Snapshot，不执行 Skill；
- Confirm 用当前依赖与下一次触发重跑预检，失败不建 Schedule；
- Fire 用当前 Skill 版本重校验，不兼容明确失败；
- 对话直接调用在进程启动前按同一 contract 校验，失败回模型修复；
- Authoring 按契约生成 `SKILL_PLAN`，不静默降级 `AGENT_TURN`；
- `SKILL:<uuid>:*` 拒绝 + `allowed_values`；
- self-creator 指导与存量 Skill 改造；
- 删除真实试跑 `/test-run`、trial 执行/投递/卡片。

不重写调度器，不新增 Run Ledger/表，不校验业务数据准确性，不做 Console-side contract overlay，不锁 artifact 版本。

## 执行与验证约定

- 一个需求一份任务文档。按本文件路径和明确 TASK ID 绑定，不从状态猜当前任务。
- 每个 TASK 增加有意义的行为/契约回归；Tests 只跑相关 pytest，不跑全仓/acceptance/E2E。
- 数据库/Worker 测试沿用现有临时 PG、Redis 和真实 Skill 子进程夹具；不共享 dev 库。
- `task finish` 只执行本 TASK 声明的测试；失败时不写完成。
- `Spec-Refs` 相对 `.code-flow/specs`，不带扩展名；实施前读当前 Spec 和本 TASK 的 Delta。
- 最后一个 TASK finish 后停下，等待用户完整功能验收与代码审查；确认前不 archive。

## Task Index

| TASK | 可验证能力 | Depends |
|---|---|---|
| [TASK-001](#task-001) | Skill invocation_contract 模型与导入保留 | — |
| [TASK-002](#task-002) | 调用通道校验、工具通配拒绝 | TASK-001 |
| [TASK-003](#task-003) | Draft 调度预检，零执行/投递 | TASK-002 |
| [TASK-004](#task-004) | Confirm 重跑预检门禁 | TASK-003 |
| [TASK-005](#task-005) | Fire 重校验与显式失败 | TASK-002 |
| [TASK-006](#task-006) | Authoring、self-creator 与存量 Skill 契约 | TASK-002, TASK-003 |
| [TASK-007](#task-007) | 观测、删除 Trial、端到端兼容 | TASK-004, TASK-005, TASK-006 |
| [TASK-008](#task-008) | 对话直接调用校验 | TASK-002 |

<a id="task-001"></a>

## TASK-001: Skill invocation_contract 模型与导入保留

- **Status**: done
- **Depends**:
- **Source**: scheduled-skill-invocation.backend.design.md §2.2、§2.3 FEAT-01/02、§3.3
- **Scenarios**: E-03, B-04
- **Spec-Refs**: artifact/harness-skill, api/harness-api, test/harness-test
- **Paths**: packages/contracts/src/muad_contracts/skill_invocation.py, packages/skill-sdk/src/muad_skill_sdk/skill_package.py, apps/console-platform/backend/src/muad_console_platform/infrastructure/skill_validator.py, tests/agent_core/test_skill_package.py, tests/contracts/test_scheduled_v2_contracts.py, tests/console_skill/test_import_api.py, tests/console_skill/test_validator_boundaries.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "skill-contract-import",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/agent_core/test_skill_package.py",
      "tests/contracts/test_scheduled_v2_contracts.py",
      "tests/console_skill/test_import_api.py",
      "tests/console_skill/test_validator_boundaries.py"
    ],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

- 定义 `SkillInvocationContract`：`version: Literal[1]`、`invocation: Literal["argv", "stdin"]`、`schema`；定时任务 preflight 与对话直接调用共用。
- `schema` 必须是 `type: object`、`properties` 为对象、`additionalProperties: false`；`required` 必须是已声明字段。
- `argv` 的属性名是长选项名（不含 `--`）；`help` / `version` 是保留名，不允许声明。
- 契约绑定默认入口（`muad.skill.json` 的 `entrypoint` / `select_entry_script`）；辅助脚本不在契约范围。
- `SkillManifest` 增加 `invocation_contract: SkillInvocationContract | None`；`_build_manifest` 严格解析，非法契约导入失败。
- Console `_frontmatter()` 白名单保留 `invocation_contract`，持久化到 `artifact.frontmatter_json`。
- 缺失 contract 的 Skill 仍可导入；定时任务新 Draft 拒绝，对话调用保持现有行为。
- 不新增数据库表/迁移。

### Checklist

- [x] 合法 argv/stdin contract 可解析并 round-trip。
- [x] 非法 schema（非 object、未知 invocation、required 指向未声明字段、保留参数名）显式拒绝。
- [x] `SkillManifest` / `_build_manifest` 不静默丢弃 contract。
- [x] Console `_frontmatter()` 真实保留 contract；真实 zip 导入 → `frontmatter_json` 可读。
- [x] 没有 contract 的 Skill 仍可导入；preflight 后续返回 `SCHEDULE_INVOCATION_CONTRACT_MISSING`。
- [x] 契约绑定默认入口；辅助脚本不要求契约。
- [x] 测试覆盖合法、缺失、非法、导入 round-trip。

### Spec Delta

```json
[
  {
    "id": "DELTA-001",
    "op": "add",
    "target": "artifact/harness-skill",
    "rule": "可执行 Skill 可在 SKILL.md frontmatter 声明 invocation_contract（version、invocation=argv|stdin、参数 schema）；定时任务新 Draft 必须声明，对话直接调用有声明时校验；契约随不可变 artifact 版本冻结，导入链路必须保留，缺失或非法时不得进入调度。",
    "evidence": "scheduled-skill-invocation：Skill 只声明 description，平台无法知道 run.py 需要 argv；Console _frontmatter 白名单会丢弃未知字段"
  }
]
```

### Log

- [2026-11-20] created
- [2026-11-20T07:28:30.883Z] started tests=d54d87b0ede0

### Verification

- 尚未执行

<a id="task-002"></a>
- 2026-11-20T07:33:35.781Z PASS commands=1/1 executed=1 deduped=0 elapsed=13794ms tests=d54d87b0ede0 head=0c609b129ace dirty=1
  - skill-contract-import PASS exit=0
## TASK-002: 调用通道校验与工具通配拒绝

- **Status**: done
- **Depends**: TASK-001
- **Source**: scheduled-skill-invocation.backend.design.md §3.3.2、§3.5.3、§3.5.4
- **Scenarios**: E-01, E-02, E-04, E-05, E-06, B-01, B-06, B-07
- **Spec-Refs**: api/harness-api, worker/harness-worker, test/harness-test
- **Paths**: packages/contracts/src/muad_contracts/skill_invocation.py, packages/agent-core/src/muad_agent_core/skill/invocation.py, packages/api-kit/src/muad_api/error_codes.py, config/api-messages.yaml, apps/agent-runtime/src/muad_agent_runtime/application/schedule_authoring.py, apps/console-platform/backend/src/muad_console_platform/application/resolve_service.py, apps/agent-worker/src/muad_agent_worker/application/schedule_drafts.py, tests/agent_core/test_skill_invocation.py, tests/agent_runtime/test_schedule_authoring_compiler.py, tests/agent_worker/test_scheduled_skill_plan.py, tests/agent_worker/test_schedule_tool_policy.py, tests/contracts/test_scheduled_v2_contracts.py, tests/console_internal/test_scheduled_v2_admission_materials.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "invocation-tools",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/agent_core/test_skill_invocation.py",
      "tests/agent_runtime/test_schedule_authoring_compiler.py",
      "tests/agent_worker/test_scheduled_skill_plan.py",
      "tests/agent_worker/test_schedule_tool_policy.py",
      "tests/contracts/test_scheduled_v2_contracts.py",
      "tests/console_internal/test_scheduled_v2_admission_materials.py"
    ],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

- 在 contracts 提供共享的 argv 解析与 schema 校验：
  - 只支持长选项 `--key value` / `--key=value`；值以 `-` 开头时用 `--key=value`；
  - `type: boolean` 是存在型开关：true 传裸 `--flag`，false 省略；拒绝 `--flag=true|false`；
  - 重复选项、短选项、裸位置参数、`--`、`--help`、`--version`、未知选项拒绝；
  - 模板变量在结构阶段允许，渲染后再校验具体值；
  - schema 必须 `additionalProperties: false`；能否为空由 `required` 决定，`argv=[]` / `input={}` 可以合法。
- `invocation=argv` 时按 schema 校验 `argv`、`input` 必须为空；`invocation=stdin` 时按 schema 校验 `input`、`argv` 必须为空。
- Authoring compiler 在生成 Draft 前做结构校验；缺必填项返回 `ScheduleFieldError`（field/reason/repair/allowed_values）。
- `resolve_service._resolve_tool` 对 `SKILL:<uuid>:*` 返回 `SCHEDULE_TOOL_WILDCARD_UNSUPPORTED`，带 `allowed_values`；裸 `tools_allow=["*"]` 保持合法。
- 未知显式工具名（`SKILL:<uuid>:foo`）不在 P0 范围；作为同类 follow-up，不新增工具注册表。
- 不在 Pydantic 边界拒绝通配，保持结构化 field error。
- 同一校验函数供 authoring、Draft/Confirm/Fire 与对话调用（TASK-008）共用，避免两套口径。
- 错误 `data` 带 `invocation`、`required`、`allowed_values`、`entry`。

### Checklist

- [x] argv 结构校验覆盖必填、可选、boolean、未知、重复、`--help`/`--version`、短选项/位置参数。
- [x] boolean 只接受裸 flag；`=true` / `=false` 拒绝；校验值与实际执行值一致。
- [x] 零参数 `argv=[]` / `input={}` 在 schema 无 required 时合法；另一通道仍必须为空。
- [x] 模板值在结构阶段通过，渲染后非法值被拒绝。
- [x] `invocation=argv` + 非空 input、`invocation=stdin` + 非空 argv 返回通道错配。
- [x] `SKILL:<uuid>:*` 拒绝并返回完整 allowed_values；裸 `*` 不回归。
- [x] Authoring 在出 Draft 前就能返回同样的字段错误。
- [x] 测试使用真实 `ResolvedSkill`/`ScheduleDraftService`，不 mock 校验边界。

### Spec Delta

```json
[
  {
    "id": "DELTA-002",
    "op": "add",
    "target": "api/harness-api",
    "rule": "SKILL_PLAN 必须按 invocation_contract.invocation 校验调用通道与参数：argv 只支持长选项（boolean 为存在型开关），argv 与 stdin 只能使用声明的通道，未知/重复/--help/--version/缺必填必须拒绝；能否为空由 schema.required 决定；SKILL:<uuid>:* 必须返回 allowed_values，裸 tools_allow=* 保持合法。",
    "evidence": "scheduled-skill-invocation：agent 只给 input、run.py 只读 argv，运行期才报 required --company-id；SKILL:<uuid>:* 被当作普通工具名冻结"
  }
]
```

### Log

- [2026-11-20] created
- [2026-11-20T07:40:43.635Z] started tests=be51c430409c

### Verification

- 尚未执行

<a id="task-003"></a>
- 2026-11-20T07:43:02.041Z PASS commands=1/1 executed=1 deduped=0 elapsed=6149ms tests=be51c430409c head=815a4301ac5a dirty=1
  - invocation-tools PASS exit=0
## TASK-003: Draft 调度预检与零副作用

- **Status**: done
- **Depends**: TASK-002
- **Source**: scheduled-skill-invocation.backend.design.md §3.5.1、§3.3.3
- **Scenarios**: S-01, S-05, S-06, E-03
- **Spec-Refs**: api/harness-api, snapshot/harness-snapshot, worker/harness-worker, test/harness-test
- **Paths**: apps/agent-worker/src/muad_agent_worker/application/schedule_drafts.py, apps/agent-worker/src/muad_agent_worker/api/schedule_drafts.py, packages/contracts/src/muad_contracts/scheduling.py, tests/agent_worker/test_scheduled_v2_drafts.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "draft-preflight",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/agent_worker/test_scheduled_v2_drafts.py"
    ],
    "timeout": 900,
    "parallel": false
  }
]
```

### Description

- 在 `ScheduleDraftPreflight` 内完成：解析依赖 → 校验 contract/通道 → 按 `first_fire_at` 渲染 → 完整 schema 校验 → 构建 Snapshot；不新增第二条预检路径。
- Draft 创建/更新成功后只落 PENDING Draft；`preview.dispatch_check` 包含 status/checked_at/occurrence_at/invocation/contract_version/dependencies/rendered/snapshot_hash。
- 预检失败不落 Draft，返回结构化错误；不创建 TaskExecution/Fire/Revision/message_delivery。
- 停止自动 trial；不再调用 `ScheduleTrialRunService`。
- `REMINDER` 保留现有依赖/投递校验，不做 Skill 契约校验。
- `AGENT_TURN` 校验 model/credential/tools，不做执行。
- 幂等键与现有 Draft 重放保持。

### Checklist

- [x] Draft 创建成功只写 `task_schedule_draft`，执行/调度/投递表计数不变。
- [x] `preview.dispatch_check` 可读，且与 Confirm 时的最新检查一致。
- [x] 预检失败返回 `SCHEDULE_ARGV_INVALID` / `SCHEDULE_INPUT_INVALID` / `SCHEDULE_INVOCATION_CONTRACT_MISSING` / `SCHEDULE_ADMISSION_FAILED`。
- [x] 零参数 Skill（`argv=[]` / `input={}` 且 schema 无 required）可创建 Draft；`--flag=true` 被拒绝。
- [x] 同一 Draft 重放不重复预检/不重复落库。
- [x] `REMINDER` 不受 Skill 契约影响。
- [x] 预检不启动 Skill 子进程、不产生投递。

### Spec Delta

```json
[
  {
    "id": "DELTA-003",
    "op": "add",
    "target": "api/harness-api",
    "rule": "Draft 创建/更新必须同步执行调度预检（依赖、通道、渲染后 schema、Snapshot）；预检不得执行 Skill、不得创建 Execution/Fire/Revision/投递，失败不得落 Draft。",
    "evidence": "scheduled-skill-invocation：真实试跑会把确认前执行/投递/产物带进流程，不能作为门禁"
  }
]
```

### Log

- [2026-11-20] created
- [2026-11-20T07:51:34.781Z] started tests=e26ccf885f86

### Verification

- 尚未执行

<a id="task-004"></a>
- 2026-11-20T07:52:30.902Z PASS commands=1/1 executed=1 deduped=0 elapsed=19196ms tests=e26ccf885f86 head=d713f46c9575 dirty=1
  - draft-preflight PASS exit=0
## TASK-004: Confirm 重跑预检门禁

- **Status**: done
- **Depends**: TASK-003
- **Source**: scheduled-skill-invocation.backend.design.md §3.5.2
- **Scenarios**: S-02, E-07, E-09, B-03
- **Spec-Refs**: api/harness-api, worker/harness-worker, test/harness-test
- **Paths**: apps/agent-worker/src/muad_agent_worker/application/schedule_confirm.py, apps/agent-worker/src/muad_agent_worker/api/schedule_drafts.py, apps/agent-worker/src/muad_agent_worker/application/schedule_dispatch.py, packages/api-kit/src/muad_api/error_codes.py, config/api-messages.yaml, tests/agent_worker/test_scheduled_v2_confirm.py, tests/agent_worker/test_scheduled_v2_drafts.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "confirm-preflight",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/agent_worker/test_scheduled_v2_confirm.py",
      "tests/agent_worker/test_scheduled_v2_drafts.py"
    ],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

- Confirm 在进入 Draft 行锁前，用当前依赖 + 当前下一次触发重跑预检；失败返回 422，不建 Schedule。
- 成功后在现有事务内复核 draft_hash/PENDING/expiry/confirmation ref，创建/更新 Schedule + Revision。
- 把最新 `dispatch_check` 写入 Revision 的 `preflight_json`，保证确认时的依赖事实可追溯。
- `REMINDER` 跳过 Skill 契约预检；保留现有 recurrence/delivery 校验。
- 并发 confirm 仍由现有行锁/幂等台账裁决；预检在锁外。
- 不新增 `SCHEDULE_TRIAL_*` 错误码；复用依赖/参数错误。

### Checklist

- [x] 创建 Draft 后撤销授权/发布不兼容版本，Confirm 稳定失败且不建 Schedule。
- [x] Confirm 成功时 Revision 的 `preflight_json` 是本次最新检查。
- [x] `REMINDER` 不受 Skill 契约影响。
- [x] 并发 confirm 只有一个成功。
- [x] 预检失败后 Draft 仍 PENDING，可修正后重试。
- [x] 错误封套/`data` 与 Draft 预检一致。

### Spec Delta

```json
[
  {
    "id": "DELTA-004",
    "op": "add",
    "target": "api/harness-api",
    "rule": "Draft Confirm 必须用当前依赖与下一次触发重跑调度预检；失败不得创建或更新 Schedule；REMINDER 不参与 Skill 契约预检；并发 confirm 仍只允许一次成功。",
    "evidence": "scheduled-skill-invocation：创建到确认之间依赖/版本可能变化，创建时的预检不能代表确认时事实"
  }
]
```

### Log

- [2026-11-20] created
- [2026-11-20T07:59:03.735Z] started tests=93c392d3f018

### Verification

- 尚未执行

<a id="task-005"></a>
- 2026-11-20T08:00:03.450Z PASS commands=1/1 executed=1 deduped=0 elapsed=25616ms tests=93c392d3f018 head=51009cf3ab1e dirty=1
  - confirm-preflight PASS exit=0
## TASK-005: Fire 重校验与显式失败

- **Status**: done
- **Depends**: TASK-002
- **Source**: scheduled-skill-invocation.backend.design.md §3.5.4
- **Scenarios**: S-03, E-08, B-02
- **Spec-Refs**: worker/harness-worker, snapshot/harness-snapshot, test/harness-test
- **Paths**: apps/agent-worker/src/muad_agent_worker/application/schedule_admission.py, apps/agent-worker/src/muad_agent_worker/scheduler/service.py, apps/agent-worker/src/muad_agent_worker/scheduler/templates.py, packages/contracts/src/muad_contracts/skill_invocation.py, tests/agent_worker/test_scheduled_v2_fire.py, tests/agent_worker/test_scheduled_v2_snapshot.py, tests/agent_worker/test_scheduler_misfire.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "fire-revalidate",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/agent_worker/test_scheduled_v2_fire.py",
      "tests/agent_worker/test_scheduled_v2_snapshot.py",
      "tests/agent_worker/test_scheduler_misfire.py"
    ],
    "timeout": 900,
    "parallel": false
  }
]
```

### Description

- Fire 的 `admission_request_from_revision` 渲染 `input`/`argv` 后，按当前 artifact 的 contract 做完整校验，再构建 Snapshot。
- 失败码：`SKILL_CONTRACT_MISSING`、`SKILL_ARGS_INVALID`、`WILDCARD_NOT_SUPPORTED`；全部加入永久 admission 失败集合。
- 失败写 Fire `FAILED_ADMISSION`；CRON → `NEEDS_ATTENTION`，ONCE → `ENDED`；不建 Execution。
- 存量 Revision 带 `*` 时同样明确失败，不把 `*` 带进快照/执行。
- 成功路径保持现有 Snapshot/Execution/Worker 行为；预算/记忆/投递不变。

### Checklist

- [x] Fire 用当前 artifact contract 校验渲染后的参数。
- [x] 三个失败码都是永久失败，带 repair；不建 Execution。
- [x] CRON/ONCE 的状态迁移符合现有 `record_admission_failure`。
- [x] 存量 `*` 不会进入 Snapshot。
- [x] 成功 Fire 的 Snapshot 与执行输入不变。
- [x] 测试覆盖契约缺失、参数不兼容、版本漂移、`*`。

### Spec Delta

```json
[
  {
    "id": "DELTA-005",
    "op": "add",
    "target": "worker/harness-worker",
    "rule": "每次 Fire 必须用当前 Skill artifact 的 invocation_contract 重新校验渲染后的参数；契约缺失、参数不兼容或工具通配必须作为永久 admission 失败处理，不得创建 Execution，CRON 进入 NEEDS_ATTENTION、ONCE 进入 ENDED。",
    "evidence": "scheduled-skill-invocation：确认后 Skill 版本变化可能让旧参数不再合法，不能静默执行"
  }
]
```

### Log

- [2026-11-20] created
- [2026-11-20T08:04:46.999Z] started tests=03a764d86e24

### Verification

- 尚未执行

<a id="task-006"></a>
- 2026-11-20T08:05:48.198Z PASS commands=1/1 executed=1 deduped=0 elapsed=23005ms tests=03a764d86e24 head=789cde2a0a3b dirty=1
  - fire-revalidate PASS exit=0
## TASK-006: Authoring、self-creator 与存量 Skill 契约

- **Status**: done
- **Depends**: TASK-002, TASK-003
- **Source**: scheduled-skill-invocation.backend.design.md §3.5.3、§3.6
- **Scenarios**: S-04, E-06, B-04
- **Spec-Refs**: api/harness-api, artifact/harness-skill, test/harness-test
- **Paths**: apps/agent-runtime/src/muad_agent_runtime/application/schedule_authoring.py, skills/self-skill-creator/SKILL.md, skills/self-skill-creator/references/invocation-contract.md, skills/self-skill-creator/references/skill-md.md, skills/daily-duty-report-mssp-local/SKILL.md, tests/agent_runtime/test_schedule_authoring_compiler.py, tests/agent_worker/test_schedule_intent_golden.py, tests/agent_worker/test_schedule_authoring_e2e.py, tests/agent_worker/test_local_report_skill.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "authoring-self-creator",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/agent_runtime/test_schedule_authoring_compiler.py",
      "tests/agent_worker/test_schedule_intent_golden.py",
      "tests/agent_worker/test_schedule_authoring_e2e.py",
      "tests/agent_worker/test_local_report_skill.py"
    ],
    "timeout": 900,
    "parallel": false
  }
]
```

### Description

- Authoring compiler 读取 `ResolvedSkill.frontmatter.invocation_contract`：
  - `invocation=argv` → 生成 argv，input 为空；
  - `invocation=stdin` → 生成 input，argv 为空；
  - 缺必填业务值 → 一个业务问题，不生成 `AGENT_TURN`；
  - 不允许因 SKILL_PLAN 校验失败而静默降级。
- `SKILL:<uuid>:*` 在 authoring 返回 `TOOL_WILDCARD_UNSUPPORTED` + allowed_values；裸 `*` 保持合法。
- 预算仍来自 `PlatformSettings`；不新增 per-draft 数值。
- self-creator：
  - Step 1 确认脚本入口通道（argv/stdin）；所有可执行 Skill 写 contract，纯指令/MCP-only 不必；
  - 契约只绑默认入口；辅助脚本不写主入口契约；
  - 从入口代码判断 argv/stdin，歧义问作者；
  - Step 4 frontmatter 模板增加 contract；
  - Step 6 自检增加契约检查（boolean 存在型开关、零参数由 schema 决定、`additionalProperties: false`）；
  - 新增 `references/invocation-contract.md`；
  - `references/skill-md.md` 删除“frontmatter 只写 name/description”。
- 存量 `skills/daily-duty-report-mssp-local` 增加 `invocation: argv` + `schema`；生产 MSSP Skill 按实际入口补契约并发新版本。

### Checklist

- [x] 确定性 Skill 意图稳定生成 SKILL_PLAN；不因模型措辞变成 AGENT_TURN。
- [x] `invocation` 决定 argv/input 通道；缺值只问业务问题。
- [x] `SKILL:<uuid>:*` 在 authoring 拒绝；裸 `*` 不回归。
- [x] self-creator 文档说明所有可执行 Skill 写 contract，纯指令/MCP-only 不必；包含格式、通道判断、自检清单。
- [x] self-creator 文档明确契约只绑默认入口，辅助脚本不写主入口契约；boolean 存在型开关、零参数由 schema 决定。
- [x] 正向/反向 fixture 覆盖导入 + preflight。
- [x] MSSP 本地 Skill 的 contract 与实际 `run.py` 参数一致。

### Spec Delta

```json
[
  {
    "id": "DELTA-006",
    "op": "add",
    "target": "api/harness-api",
    "rule": "Authoring 必须按 Skill invocation_contract 生成 SKILL_PLAN 并选择 argv/stdin 通道；缺必填业务值只向用户提一个业务问题，禁止静默降级为 AGENT_TURN；技术参数错误必须作为结构化字段错误返回给 agent。",
    "evidence": "scheduled-skill-invocation：agent 把确定任务改成 AGENTIC 后出现 TASK_NO_PROGRESS，用户被迫参与参数调试"
  }
]
```

### Log

- [2026-11-20] created
- [2026-11-20T08:11:17.326Z] started tests=87795f7cc14c

### Verification

- 尚未执行

<a id="task-007"></a>
- 2026-11-20T08:11:55.453Z PASS commands=1/1 executed=1 deduped=0 elapsed=13787ms tests=87795f7cc14c head=f97801657f69 dirty=1
  - authoring-self-creator PASS exit=0
## TASK-007: 观测、删除 Trial、端到端兼容

- **Status**: done
- **Depends**: TASK-004, TASK-005, TASK-006
- **Source**: scheduled-skill-invocation.backend.design.md §3.7、§4
- **Scenarios**: S-06, E-03, B-02, B-04
- **Spec-Refs**: test/harness-test, api/harness-api, logging/harness-log
- **Paths**: apps/agent-worker/src/muad_agent_worker/metrics.py, apps/agent-worker/src/muad_agent_worker/application/schedule_drafts.py, apps/agent-worker/src/muad_agent_worker/api/schedule_drafts.py, apps/agent-worker/src/muad_agent_worker/application/schedule_trial_run.py, packages/contracts/src/muad_contracts/scheduling.py, packages/contracts/src/muad_contracts/__init__.py, apps/console-platform/frontend/src/locales/zh-CN.json, apps/console-platform/frontend/src/locales/en-US.json, apps/console-platform/frontend/src/modules/webchat/types.ts, apps/console-platform/frontend/src/modules/webchat/components/DraftCard.tsx, tests/agent_worker/test_scheduled_v2_observability.py, tests/agent_worker/test_schedule_authoring_e2e.py, tests/agent_worker/test_scheduled_v2_drafts.py, tests/agent_worker/test_scheduled_skill_plan_e2e.py, tests/contracts/test_scheduled_v2_contracts.py, tests/frontend/test_schedule_preview_contract.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "observability-cleanup",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/agent_worker/test_scheduled_v2_observability.py",
      "tests/agent_worker/test_schedule_authoring_e2e.py",
      "tests/agent_worker/test_scheduled_v2_drafts.py",
      "tests/agent_worker/test_scheduled_skill_plan_e2e.py",
      "tests/contracts/test_scheduled_v2_contracts.py",
      "tests/frontend/test_schedule_preview_contract.py"
    ],
    "timeout": 900,
    "parallel": false
  }
]
```

### Description

- 指标：`schedule_preflight_total{status,mode,code}`、`schedule_confirm_blocked_total{code}`、`schedule_admission_failure_total{code}`、`skill_invocation_guard_total{status,code}`；删除 trial 指标。
- 审计：draft create/preflight/confirm/blocked；记录 draft_hash、contract version、artifact checksum、invocation、error_code；不记录 argv/input 正文。
- 删除 `ScheduleTrialRunService`、`POST/GET /test-run`、`ScheduleTrialFact`、`SchedulePreview.trial`、trial 投递/执行分支。
- 前端 `chat.draft.trial.*` 改为调度预检文案；卡片展示 dispatch_check，不展示 trial execution/artifact。
- 端到端：Draft → 预检 → Confirm → Fire；失败/blocked 路径不建 Schedule/Execution。
- 存量兼容：已有 ACTIVE Schedule 不重建；补 contract 后按 Fire 规则校验。

### Checklist

- [x] 成功/失败/blocked 路径指标递增，标签有界。
- [x] 审计不泄露 argv/input/业务正文；可追溯 contract version/artifact checksum。
- [x] `/test-run`、trial service/fact/preview 字段无残留引用。
- [x] 前端卡片只展示调度预检；无“真实试跑”文案。
- [x] E2E 覆盖成功、契约缺失、参数不兼容、版本漂移。
- [x] 存量 Schedule 行为符合 B-02。
- [x] 文档/操作说明明确“预检不执行、不投递”。

### Spec Delta

```json
[
  {
    "id": "DELTA-007",
    "op": "add",
    "target": "test/harness-test",
    "rule": "调度可调用性验收必须覆盖 Draft 预检零执行/零投递、Confirm 重跑预检、Fire 版本漂移显式失败、契约导入 round-trip 与 REMINDER 兼容；不得用真实业务 Skill 的试跑替代预检。",
    "evidence": "scheduled-skill-invocation：真实试跑有副作用且不能作为门禁；必须证明预检本身能拦截参数/通道错误"
  }
]
```

### Log

- [2026-11-20] created
- [2026-11-20T08:12:54.665Z] started tests=3a2652a3250b

### Verification

- 尚未执行

<a id="task-008"></a>
- 2026-11-20T08:28:54.656Z PASS commands=1/1 executed=1 deduped=0 elapsed=47016ms tests=bf63d582a46d head=af4743ceda94 dirty=1 tests-changed
  - observability-cleanup PASS exit=0
## TASK-008: 对话直接调用校验

- **Status**: done
- **Depends**: TASK-002
- **Source**: scheduled-skill-invocation.backend.design.md §3.5.5、FEAT-11、RULE-13
- **Scenarios**: S-07, E-10, E-11, B-05
- **Spec-Refs**: artifact/harness-skill, test/harness-test
- **Paths**: apps/agent-runtime/src/muad_agent_runtime/application/skill_tools.py, apps/agent-runtime/src/muad_agent_runtime/metrics.py, packages/contracts/src/muad_contracts/skill_invocation.py, tests/agent_runtime/test_skill_tools.py, tests/agent_runtime/test_skill_argv.py, tests/agent_core/test_runner.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "chat-invocation-guard",
    "argv": [
      "uv",
      "run",
      "python",
      "-m",
      "pytest",
      "-q",
      "tests/agent_runtime/test_skill_tools.py",
      "tests/agent_runtime/test_skill_argv.py",
      "tests/agent_core/test_runner.py"
    ],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

- 对话中 `execute(target=skill)` / `execute_skill` / `run_skill_script` 在启动子进程或提交异步操作前，读取默认入口的 `invocation_contract`。
- 契约只绑默认入口（`select_entry_script` / `muad.skill.json` 的 `entrypoint`）：
  - `execute_skill` 总是校验；
  - `run_skill_script` / `execute(target=skill, script=...)` 只有选中的脚本等于默认入口才校验；
  - 辅助脚本（如 `check_status.py`）保持现有规则，不套用主入口契约。
- `load_skill.manifest` 返回 `entrypoint` + `invocation_contract`，模型能按需读取调用依据；不扩技能目录。
- 有 contract：按同一校验函数检查通道与参数；失败返回 `_error_result("SKILL_INVOCATION_INVALID", ...)`，`error.detail{field,reason,repair,allowed_values,invocation}`，**`retryable=true`**，不启动进程。
- 只有可修复参数错误 retryable；skill key/权限/契约缺失等保持不可重试；复用 `max_identical_failures`。
- 无 contract：保持现有行为，不阻断对话调用；记录 `contract_missing` 观测。
- 对话调用不接受 `{{fire_date}}` / `{{previous_day}}` / `{{fire_time}}` 调度模板；模型必须给具体值。
- 参数守卫阶段不写 Draft/Schedule/Fire/Execution/Snapshot；校验通过后继续现有执行链路，ASYNC 仍按现有逻辑提交 operation/Execution。
- 与定时任务共用 `skill_invocation` 校验函数，避免两套口径。

### Checklist

- [x] 有 contract 的 argv Skill：合法 argv 正常执行。
- [x] `invocation=argv` 但只给 input：返回 `SKILL_INVOCATION_INVALID` + `retryable=true`，不启动子进程。
- [x] 缺必填/格式错误：返回 field/reason/repair/allowed_values；模型改参后第二次成功。
- [x] 只有 `SKILL_INVOCATION_INVALID` retryable；其他错误保持不可重试。
- [x] 辅助脚本 `check_status.py` 不被主入口契约拦截；主入口缺参仍被拒绝。
- [x] `load_skill.manifest` 返回 `entrypoint` + `invocation_contract`。
- [x] 无 contract 的 Skill：保持现有行为，不阻断对话。
- [x] 参数含 `{{fire_date}}` / `{{previous_day}}` / `{{fire_time}}`：拒绝并提示给具体值。
- [x] ASYNC 非法参数不创建 operation；合法参数仍按现有逻辑提交 operation/Execution。
- [x] 测试覆盖同步/异步、成功/失败、无契约、aux script、retryable 链路。

### Spec Delta

```json
[
  {
    "id": "DELTA-008",
    "op": "add",
    "target": "artifact/harness-skill",
    "rule": "对话直接调用 Skill 时，若默认入口声明 invocation_contract，必须在启动子进程或提交异步操作前按同一通道/参数 schema 校验；失败返回 SKILL_INVOCATION_INVALID（retryable=true）且不启动执行；辅助脚本不套用主入口契约；load_skill.manifest 返回 entrypoint 与 invocation_contract；无契约时保持现有行为。",
    "evidence": "scheduled-skill-invocation：同一通道错配也会发生在对话直接调用，不能只在定时任务路径校验"
  }
]
```

### Log

- [2026-11-20] created
- [2026-11-20T08:35:12.364Z] started tests=165864e69cb9

### Verification

- 尚未执行
- 2026-11-20T08:36:22.073Z PASS commands=1/1 executed=1 deduped=0 elapsed=5639ms tests=165864e69cb9 head=5cef35ead847 dirty=1
  - chat-invocation-guard PASS exit=0
