---
{"version":1,"requirement":"platform-session-reporting"}
---

# Tasks: platform-session-reporting

<!--
需求：用户问“可以列一下平台 session”，第一次回复应列清本次范围内各平台结果及失败原因。
设计：platform-session-reporting.design.md v1.4（增强 list-platforms + 一个最后报告对象 + 有界摘要进现有错误/证据）。
工作流：full。
-->

## TASK-001: 增强 list-platforms 为实际登录态查询

- **Status**: done
- **Depends**:
- **Source**: platform-session-reporting.design.md#3.2, #3.3, #3.4, #3.5
- **Scenarios**: platform-session-reporting:S-01, platform-session-reporting:S-02, platform-session-reporting:E-01, platform-session-reporting:E-02, platform-session-reporting:B-04
- **Spec-Refs**: platform/harness-project-platform#RULE-platform-001, secret/harness-secret#RULE-002, test/harness-test
- **Paths**: packages/platform-sdk/src/muad_platform_sdk/skill_access.py, packages/platform-sdk/src/muad_platform_sdk/skill_bridge.py, packages/platform-sdk/src/muad_platform_sdk/session_cli.py, tests/platform_sdk/test_skill_bridge.py, tests/platform_sdk/test_unified_session_contract.py, tests/platform_sdk/test_platform_session_report.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "platform-sdk-list",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/platform_sdk/test_skill_bridge.py", "tests/platform_sdk/test_unified_session_contract.py", "tests/platform_sdk/test_platform_session_report.py"],
    "timeout": 300,
    "parallel": false
  }
]
```

### Description

把 `session-manager list-platforms` 从“只回 Console 目录”增强为“按本次范围逐平台实际检查登录态”：

- `SkillPlatformAccess.collect_batch(keys)` 顺序解析 + `acquire`，返回有序条目；平台错误进报告，基础设施错误按原规则失败；
- `list-platforms` stdout 保留 `version: 2`、`key`、`name`、`authentication`，`available` 改为实际可用性；失败带 `error.code/detail`，未检查带 `available: null` + `reason`；
- 一个执行只保留“最后报告”对象；不建历史、不落库、不跨执行；
- `--help` 同步说明 `list-platforms` 会实际检查；不新增命令或 CLI 参数；
- 输出结构支持 `available: null` + `reason`；无会话/REQUEST_SIGNING 平台的具体判定与完整适配器支持由 TASK-005 落地（本任务只保证结构可表达“未检查”）。

### Checklist

- [x] `collect_batch` 顺序采集；报告按请求顺序；`available` 反映实际检查结果。
- [x] `list-platforms` 一成一败、全失败、缺凭据、空目录都有稳定输出；单个/全部失败不改变“能产出报告即成功退出”。
- [x] `version: 2`、`key`/`name`/`authentication` 保留；`available: null` + `reason` 结构可表达“未检查/不适用”。
- [x] 最后报告对象只存在 broker 内存；无新表/新字段/新缓存。
- [x] `--help` 与输出语义一致；相关单测通过。

### Spec Delta

```json
[]
```

### Log

- [2026-11-20] created
- [2026-11-20T03:53:36.356Z] started tests=45164bc8e5d5

### Verification

- 尚未执行
- 2026-11-20T04:00:54.972Z PASS commands=1/1 executed=1 deduped=0 elapsed=3717ms tests=45164bc8e5d5 head=1334ae717995 dirty=1
  - platform-sdk-list PASS exit=0
## TASK-002: start/get-state 按请求集采集并附失败报告

- **Status**: done
- **Depends**: TASK-001
- **Source**: platform-session-reporting.design.md#3.2, #3.3, #3.4, #3.5
- **Scenarios**: platform-session-reporting:S-02, platform-session-reporting:E-02, platform-session-reporting:E-03, platform-session-reporting:B-05
- **Spec-Refs**: platform/harness-project-platform#RULE-platform-001, secret/harness-secret#RULE-002, test/harness-test
- **Paths**: packages/platform-sdk/src/muad_platform_sdk/adapter/mss_config.py, packages/platform-sdk/src/muad_platform_sdk/skill_access.py, packages/platform-sdk/src/muad_platform_sdk/skill_bridge.py, packages/platform-sdk/src/muad_platform_sdk/session_cli.py, tests/platform_sdk/test_skill_bridge.py, tests/acceptance/user_skill_sandbox/test_session_manager.py, tests/acceptance/user_skill_sandbox/test_s_03.py, tests/sdk/test_platform_sliding_session.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "platform-sdk-state",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/platform_sdk/test_skill_bridge.py", "tests/acceptance/user_skill_sandbox/test_session_manager.py", "tests/acceptance/user_skill_sandbox/test_s_03.py", "tests/sdk/test_platform_sliding_session.py"],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

让现有 `start()` 与 `get-state` 共用 TASK-001 的采集与最后报告，但不改变它们的成功/失败语义：

- `start()` 使用请求集（Skill 声明 / 显式选择），不用 `allowed_keys()` 过滤结果；首个平台失败后仍检查其他请求平台，最后把报告附在异常上；
- `PlatformSessionEnvironment` 暴露最后报告（`SkillPlatformAccess.report`），供 TASK-003 的 executor 消费；broker 结束即释放，不持久化；
- `_write_state()` 使用请求集；成功平台写状态文件；失败仍非零退出、不交付部分状态文件，报告随错误返回；
- `get-state` 失败 stderr 增加 `platform_sessions`；原错误码、detail、retryable 保持；
- `allowed_keys()` 只服务授权闸门；`_discover_keys()` 只服务目录，不替代查询范围；
- 显式 `platforms` 的预登录失败（B-05）仍走原失败出口，报告交给上层。

### Checklist

- [x] `start()` 对请求集采集；单平台失败不阻止其他平台；异常带最后报告。
- [x] `PlatformSessionEnvironment.report` 暴露最后报告；无持久化/跨执行共享。
- [x] `_write_state()` 失败不写状态文件；成功输出形状、权限、清理不变。
- [x] `get-state` 失败 stderr 的 `platform_sessions` 包含所有请求平台；原错误字段保持。
- [x] `allowed_keys()` 只做授权；缺凭据平台不被静默过滤。
- [x] 既有 `get-state`/`refresh` 成功路径与 `test_platform_sliding_session` 回归通过。

### Spec Delta

```json
[]
```

### Log

- [2026-11-20] created
- [2026-11-20T04:03:31.521Z] started tests=1b12294888da

### Verification

- 尚未执行
- 2026-11-20T04:12:41.905Z PASS commands=1/1 executed=1 deduped=0 elapsed=24758ms tests=1b12294888da head=f169a6982d14 dirty=1
  - platform-sdk-state PASS exit=0
## TASK-003: 执行器暴露最后报告并附加有界摘要

- **Status**: done
- **Depends**: TASK-001, TASK-002
- **Source**: platform-session-reporting.design.md#3.2, #3.3, #3.4, #3.5
- **Scenarios**: platform-session-reporting:S-02, platform-session-reporting:S-03, platform-session-reporting:S-04, platform-session-reporting:E-03
- **Spec-Refs**: platform/harness-project-platform#RULE-platform-001, secret/harness-secret#RULE-002, test/harness-test
- **Paths**: packages/agent-core/src/muad_agent_core/skill/contracts.py, packages/agent-core/src/muad_agent_core/skill/executor.py, packages/agent-core/src/muad_agent_core/skill/sandboxed.py, packages/agent-core/src/muad_agent_core/skill/draft_shell.py, packages/agent-core/src/muad_agent_core/sandbox/workspace_shell.py, apps/agent-runtime/src/muad_agent_runtime/application/skill_tools.py, tests/agent_core/test_skill_executor.py, tests/agent_core/test_sandboxed_skill_executor.py, tests/agent_runtime/test_skill_tools.py, tests/agent_runtime/test_workspace_shell_tool.py, tests/acceptance/user_skill_sandbox/test_s_03.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "executor-report",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/agent_core/test_skill_executor.py", "tests/agent_core/test_sandboxed_skill_executor.py", "tests/agent_runtime/test_skill_tools.py", "tests/agent_runtime/test_workspace_shell_tool.py", "tests/acceptance/user_skill_sandbox/test_s_03.py"],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

把 broker 的最后报告送到模型第一次能看到的工具结果，同时保持成功结果不变：

- 读取 TASK-002 的 `PlatformSessionEnvironment.report`（`SkillPlatformAccess.report` 是唯一最后报告）；
- `SkillExecutionResult`、draft 失败结果、`SkillExecutionError`/`SkillToolError`/draft 错误增加可选 `platform_sessions`；成功路径不新增字段；
- executor 在失败结果 `stderr` 前部插入单行有界摘要（`platform_sessions: mssp=ok; mssw=PLATFORM_AUTH_REJECTED(http=401) …`），复用现有 1 KiB evidence 与 300 字失败收尾预算；不新增审计列/迁移/artifact 强制外置；
- `skill_tools._workspace_shell_with_platforms()` 预登录失败时返回带 `platform_sessions` 的现有错误载荷；draft/workspace 执行协议不变；
- `execute` 工具描述改为：列 session 用 `list-platforms`；执行 Skill 直接用 `get-state`，不要先 list；`platforms` 是 fail-closed 收窄。

### Checklist

- [x] executor/沙箱在失败时读取 TASK-002 的 `PlatformSessionEnvironment.report`。
- [x] `SkillExecutionResult`/错误/draft 失败结果带 `platform_sessions`；成功结果无新增字段。
- [x] 摘要单行、有界、无敏感值；2 平台在现有 evidence/预览预算内可读。
- [x] `execute` 描述包含“list 用于列 session / Skill 直接用 get-state / 不要先 list”。
- [x] `_workspace_shell_with_platforms` 预登录失败带报告；draft/workspace 协议不变；B-05 由 TASK-004 验收。
- [x] 相关单测/集成测试通过；无新审计字段或迁移。

### Spec Delta

```json
[]
```

### Log

- [2026-11-20] created
- [2026-11-20T04:25:53.038Z] started tests=225f031c240d

### Verification

- 尚未执行
- 2026-11-20T04:26:18.043Z PASS commands=1/1 executed=1 deduped=0 elapsed=19652ms tests=225f031c240d head=7c05dbbbe16b dirty=1
  - executor-report PASS exit=0
## TASK-004: 失败收尾与 Worker 消费最后报告

- **Status**: done
- **Depends**: TASK-003
- **Source**: platform-session-reporting.design.md#3.2, #3.4, #3.5
- **Scenarios**: platform-session-reporting:S-03, platform-session-reporting:S-04, platform-session-reporting:E-03, platform-session-reporting:B-05
- **Spec-Refs**: platform/harness-project-platform#RULE-platform-001, worker/harness-worker, test/harness-test
- **Paths**: apps/agent-runtime/src/muad_agent_runtime/application/run_service.py, apps/agent-runtime/src/muad_agent_runtime/application/finalization/**, apps/agent-worker/src/muad_agent_worker/worker/**, tests/agent_runtime/test_finalization_facts.py, tests/agent_runtime/test_finalization_render.py, tests/agent_runtime/test_finalization_funnel.py, tests/agent_runtime/test_workspace_shell_tool.py, tests/agent_worker/test_async_background_error_delivery.py, tests/agent_worker/test_execution_outcomes.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "finalization-report",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/agent_runtime/test_finalization_facts.py", "tests/agent_runtime/test_finalization_render.py", "tests/agent_runtime/test_finalization_funnel.py", "tests/agent_runtime/test_workspace_shell_tool.py"],
    "timeout": 600,
    "parallel": false
  },
  {
    "id": "worker-diagnostic",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/agent_worker/test_async_background_error_delivery.py", "tests/agent_worker/test_execution_outcomes.py"],
    "timeout": 600,
    "parallel": true
  }
]
```

### Description

让现有失败出口真正读到 TASK-003 的报告，不新增字段或迁移：

- `run_service` / finalization 从失败结果的 `stderr` 前部读取有界摘要（现有 `evidence_preview`）；逐平台告知；结构化 `platform_sessions` 供模型；
- 覆盖 B-05：显式 `execute platforms` 的预登录失败，命令未执行，本次回复仍能看到 MSSP/MSSW 结果；不能声称 CLI 已执行；
- agent-worker 同类失败转任务错误时保留 `platform_sessions`/摘要，复用现有失败展示/投递；不改状态、重试决策或成功结果；
- 不新增审计列、不强制外置、不改变 Runner 终止规则。

### Checklist

- [x] finalization 从 `stderr` 前部摘要逐平台告知；2 平台在 1 KiB/300 字预算内可读。
- [x] B-05 预登录失败：命令未执行、报告仍在本次回复；不声称 CLI 已执行。
- [x] Worker 任务失败消息保留诊断；成功结果不变。
- [x] 无新审计字段/迁移；Runner 终止规则不变。
- [x] 相关 finalization/Worker/workspace 测试通过。

### Spec Delta

```json
[]
```

### Log

- [2026-11-20] created
- [2026-11-20T04:27:34.519Z] started tests=346ea897415d

### Verification

- 尚未执行
- 2026-11-20T04:32:12.906Z PASS commands=2/2 executed=2 deduped=0 elapsed=23806ms tests=346ea897415d head=5c071efe7084 dirty=1
  - finalization-report PASS exit=0
  - worker-diagnostic PASS exit=0
## TASK-005: 敏感值擦除与无会话平台报告

- **Status**: draft
- **Depends**: TASK-001, TASK-002
- **Source**: platform-session-reporting.design.md#3.3, #3.5
- **Scenarios**: platform-session-reporting:S-02, platform-session-reporting:S-04, platform-session-reporting:E-02, platform-session-reporting:B-04
- **Spec-Refs**: secret/harness-secret#RULE-002, platform/harness-project-platform, test/harness-test
- **Paths**: packages/platform-sdk/src/muad_platform_sdk/adapter/mss.py, packages/platform-sdk/src/muad_platform_sdk/skill_access.py, packages/platform-sdk/src/muad_platform_sdk/skill_bridge.py, tests/sdk/test_mss_sessions.py, tests/platform_sdk/test_skill_bridge.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "secret-scrub",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/sdk/test_mss_sessions.py", "tests/platform_sdk/test_skill_bridge.py"],
    "timeout": 600,
    "parallel": false
  }
]
```

### Description

补齐报告/失败摘要的安全边界，并让无会话平台不被误报：

- `_scrub` 在现有 `ak`/`sk`/`headers` 外，增加本次 CSRF token 与登录响应中可取得的 Cookie 值；
- 报告、工具错误 `platform_sessions`、有界摘要、日志只保留 `http_status`/`business_code`/`message`，不出现原值；
- `list-platforms` 对 `SessionMode.NONE` / `RequestSigning` 平台返回 `available: null` + `reason`，不报成认证失败；不扩展完整适配器支持；
- 日志只记平台 key 与错误码。

### Checklist

- [ ] CSRF/登录 Cookie canary 不出现在报告、摘要、日志、工具错误。
- [ ] 无会话/REQUEST_SIGNING 平台 `available: null` + `reason`，不误报认证失败。
- [ ] 现有 `ak`/`sk`/`headers` 擦除与 `test_mss_sessions` 回归保持。
- [ ] `list-platforms` 缺凭据/认证拒绝/基础设施故障不互相冒充。

### Spec Delta

```json
[]
```

### Log

- [2026-11-20] created

### Verification

- 尚未执行

## TASK-006: 模型说明、作者指南与 Spec Delta

- **Status**: draft
- **Depends**: TASK-001, TASK-002, TASK-003, TASK-004, TASK-005
- **Source**: platform-session-reporting.design.md#2.4, #3.4, #3.5
- **Scenarios**: platform-session-reporting:S-03, platform-session-reporting:S-02, platform-session-reporting:B-03
- **Spec-Refs**: platform/harness-project-platform, secret/harness-secret, test/harness-test
- **Paths**: skills/self-skill-creator/references/session-manager.md, skills/self-skill-creator/references/skill-md.md, skills/_templates/business-skill-python/scripts/run.py, packages/platform-sdk/MSSP-INTEGRATION.md, docs/operations/legacy-skill-migration.md, docs/designs/2026-11-17-unified-platform-session.md, .code-flow/specs/platform/harness-project-platform.md, .code-flow/specs/secret/harness-secret.md
- **E2E**:
- **Skip-Tests**: true
- **Skip-Reason**: docs/spec only; runtime behavior is covered by TASK-001..005; Spec Delta is applied at archive

### Tests

```json
[]
```

### Description

同步模型可见说明与作者指南，并声明本轮 Spec Delta：

- `execute` 说明（TASK-003 已实现）与 `list-platforms --help`（TASK-001）保持一致：列 session 用 `list-platforms`；执行 Skill 直接用 `get-state`，不要先 list；`platforms` 是 fail-closed 收窄；
- `self-skill-creator` 说明 `list-platforms` 现在会实际登录/续期/消耗预算；Skill 直接 `get-state`；删除 `session_get_state` 陈旧段落；
- `business-skill-python` 模板的失败诊断转交保留 `platform_sessions`，不再只取 200 字；
- `MSSP-INTEGRATION`、`legacy-skill-migration` 与 2026-11-17 设计同步 `list-platforms`/`available` 新语义；
- 声明 Spec Delta：新增 `platform/harness-project-platform#RULE-platform-002` 与 `secret/harness-secret#RULE-003`。目标 platform spec 的 `next_rule` 当前为 1，与 `RULE-platform-001` 不一致；archive 应用时按“当前最大数字 + 1”分配 `RULE-platform-002`，并同步 `next_rule = 3`；secret 的 `next_rule` 3 → 4。

### Checklist

- [ ] `execute`/`--help`/作者指南同文；无“先 list”的旧指导。
- [ ] `session_get_state` 陈旧段落删除或修正；模板保留完整诊断。
- [ ] 2026-11-17 设计与 `MSSP-INTEGRATION`/`legacy-skill-migration` 同步 `list-platforms` 新语义。
- [ ] Spec Delta 已声明；`next_rule` 不一致已记录。

### Spec Delta

```json
[
  {
    "id": "DELTA-001",
    "op": "add",
    "target": "platform/harness-project-platform",
    "rule": "[RULE-platform-002] 执行期 `session-manager list-platforms` 按本次查询范围逐平台检查实际登录态：返回实际 `available`、有效期或失败原因；CLI `available` 不再只表示有凭据配置；Console 内部目录仍只提供候选范围；`get-state`/`refresh` 的交付与失败出口保持。",
    "evidence": "platform-session-reporting design v1.4 §3.3/§3.4/§3.5；S-01/S-02/E-02/B-05"
  },
  {
    "id": "DELTA-002",
    "op": "add",
    "target": "secret/harness-secret",
    "rule": "[RULE-003] 平台失败原因在离开适配器前，除 `ak`/`sk`/`headers` 外还须擦除本次 CSRF token 与登录响应中可取得的 Cookie 值；进入 Skill stderr、工具错误 `platform_sessions`、有界摘要或日志前完成替换，日志只记 code 与状态。",
    "evidence": "platform-session-reporting design v1.4 §3.5；S-02/S-04"
  }
]
```

### Log

- [2026-11-20] created

### Verification

- 尚未执行
