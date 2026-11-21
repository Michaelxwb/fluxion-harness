---
{"version":1,"requirement":"agent-output-and-platform-defaults"}
---

# Tasks: agent-output-and-platform-defaults

<!--
需求：Agent 默认回复语言、失败文案直白描述、IM/Worker locale 一致、定时任务默认时区、配置与 Spec 收口。
设计：agent-output-and-platform-defaults.design.md v0.1。
工作流：full。
-->

## TASK-001: 共享 failure_hint 与失败收尾文案重构

- **Status**: done
- **Depends**:
- **Source**: agent-output-and-platform-defaults.design.md#3.4, #3.5
- **Scenarios**: agent-output-and-platform-defaults:S-03, agent-output-and-platform-defaults:E-02
- **Spec-Refs**: arch/harness-arch, i18n/harness-i18n#RULE-i18n-001, platform/harness-project-platform
- **Paths**: packages/agent-core/src/muad_agent_core/failures.py, packages/agent-core/src/muad_agent_core/__init__.py, apps/agent-runtime/src/muad_agent_runtime/application/finalization/render.py, tests/agent_core/test_failure_hints.py, tests/agent_runtime/test_finalization_render.py, tests/agent_runtime/test_finalization_funnel.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "failure-hints-render",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/agent_core/test_failure_hints.py", "tests/agent_runtime/test_finalization_render.py", "tests/agent_runtime/test_finalization_funnel.py"],
    "timeout": 300,
    "parallel": false
  }
]
```

### Description

新增 `packages/agent-core/src/muad_agent_core/failures.py` 的纯函数 `failure_hint(code, locale, tool=None)`，按码族返回 `title`、`next_step`、`known`，覆盖 `PLATFORM_*`、`CREDENTIAL_*`、`CONTROL_*`、`MODEL_*`、`SANDBOX_*`、`WORKSPACE_*`、`TOOL_*`、`SKILL_*`、`MCP_*`、`RUN_*`、`TASK_*` 与未知码回退。

重构 `finalization/render.py`：

- 正文先给直白原因与下一步，再给完成情况；错误码、工具名、Run ID、截断后的平台原文只进「技术信息」块；
- `NO_PROGRESS` 按 `decisive_failure.retryable` 区分「失败不可重试，已停止」与「同一失败重复达到上限，已停止」，不再对首次确定性失败写「重复失败」；
- 预算/期限失败不被工具错误覆盖；取消不写错误码；
- 保留确定性渲染，不新增模型调用；未知码仍展示原码。

### Checklist

- [x] `failure_hint` 覆盖各码族、未知码、zh-CN/en-US，且为纯函数。
- [x] 失败正文不再以 `RUN_*`/`PLATFORM_*`/`platform_sessions:` 为主句。
- [x] 技术信息包含终止码、工具码、工具名、Run ID；平台原文有界且脱敏。
- [x] 首次确定性失败与阈值失败文案不同；预算/期限/取消文案正确。
- [x] `test_failure_hints.py`、`test_finalization_render.py`、`test_finalization_funnel.py` 通过。

### Spec Delta

```json
[]
```

### Log

- [2026-11-21] created
- [2026-11-21T02:18:19.736Z] started tests=2381a957a618

### Verification

- 尚未执行
- 2026-11-21T02:25:19.391Z PASS commands=1/1 executed=1 deduped=0 elapsed=5644ms tests=a2f7c83986bc head=d71e9cf5af11 dirty=1 tests-changed
  - failure-hints-render PASS exit=0
## TASK-002: Runtime 默认语言冻结、提示语言段与收尾 locale 接线

- **Status**: in-progress
- **Depends**: TASK-001
- **Source**: agent-output-and-platform-defaults.design.md#3.2, #3.3, #3.4, #3.5
- **Scenarios**: agent-output-and-platform-defaults:S-01, agent-output-and-platform-defaults:S-02, agent-output-and-platform-defaults:E-03, agent-output-and-platform-defaults:B-02
- **Spec-Refs**: i18n/harness-i18n#RULE-i18n-001, snapshot/harness-snapshot#RULE-snapshot-001, arch/harness-arch
- **Paths**: apps/agent-runtime/src/muad_agent_runtime/application/executor.py, apps/agent-runtime/src/muad_agent_runtime/application/run_service.py, packages/agent-core/src/muad_agent_core/agent/runner.py, packages/agent-core/src/muad_agent_core/prompt/builder.py, tests/agent_runtime/test_execution_defaults_settings.py, tests/agent_core/test_prompt_builder.py, tests/agent_runtime/test_finalization_funnel.py, tests/agent_runtime/test_run_service.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "runtime-locale",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/agent_runtime/test_execution_defaults_settings.py", "tests/agent_core/test_prompt_builder.py", "tests/agent_runtime/test_finalization_funnel.py", "tests/agent_runtime/test_run_service.py"],
    "timeout": 300,
    "parallel": false
  }
]
```

### Description

- `ExecutionDefaults` 增加 `locale: str | None`；`snapshot_policy` 写入 `locale.default_locale`，`execution_defaults_from_policy` 读取（旧行缺省返回 `None`），`execution_defaults_from_platform` 从平台设置取值。
- `AgentRunRequest` 增加 `reply_locale`；`RunExecutor._build_run_request` 从本 Run 冻结的 `ExecutionDefaults.locale` 传入。
- `DefaultPromptBuilder.build` 增加可选 `reply_locale`，在 instructions 之后插入 `## Reply language`；`None` 不追加，未知值回落 `zh-CN` 并告警。
- `_finalize_failed` / 取消路径用本 Run 的 locale 渲染收尾，并传 `run_id`；旧 Run 缺 locale 时回落 `zh-CN`。
- 语言优先级：当前消息显式要求 > memory `language` > 平台默认；提示文案明确该顺序。

### Checklist

- [ ] 新 Run 的 `policy_json.locale` 含 `default_locale` 与 `default_timezone`；旧 policy 缺 locale 返回 `None`。
- [ ] 系统提示只在 `reply_locale` 非空时追加语言段，并说明覆盖顺序。
- [ ] 失败/取消收尾使用本 Run locale；en-US 输出英文标题与下一步。
- [ ] 语言段不影响 Agent 任务指令与工具目录的装配顺序。
- [ ] 声明测试通过。

### Spec Delta

```json
[]
```

### Log

- [2026-11-21] created
- [2026-11-21T02:25:23.194Z] started tests=a10215633893

### Verification

- 尚未执行

## TASK-003: Worker AGENTIC 默认回复语言

- **Status**: draft
- **Depends**: TASK-002
- **Source**: agent-output-and-platform-defaults.design.md#3.2, #3.4
- **Scenarios**: agent-output-and-platform-defaults:S-06
- **Spec-Refs**: i18n/harness-i18n#RULE-i18n-001, snapshot/harness-snapshot#RULE-snapshot-001, arch/harness-arch
- **Paths**: apps/agent-worker/src/muad_agent_worker/worker/assembly.py, apps/agent-worker/src/muad_agent_worker/application/agentic/run.py, tests/agent_worker/test_scheduled_v2_agentic_adapter.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "worker-agentic-locale",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/agent_worker/test_scheduled_v2_agentic_adapter.py", "tests/agent_worker/test_agentic_no_progress_message.py"],
    "timeout": 300,
    "parallel": false
  }
]
```

### Description

- `AgenticDependencies` 增加 `settings_client`；`build_agentic_executor` 不再 `del settings_client`，把它注入依赖。
- AGENTIC 执行开始时读一次当前平台 `locale.default_locale`，传给 `AgentRunRequest.reply_locale`；不新增 Task 快照字段。
- 设置源不可读/未知 locale 时 warning + `reply_locale=None`，不阻断任务。
- 定时 Agentic 任务与立即 Agentic 任务共用该路径。

### Checklist

- [ ] `settings_client` 从装配根传到 `AgenticDependencies`。
- [ ] AGENTIC 执行只读一次 locale，并写入 `AgentRunRequest.reply_locale`。
- [ ] 设置不可读时任务继续执行，语言段省略且有 warning。
- [ ] 声明测试通过。

### Spec Delta

```json
[]
```

### Log

- [2026-11-21] created

### Verification

- 尚未执行

## TASK-004: IM 固定文案与空正文 locale 兜底

- **Status**: draft
- **Depends**: TASK-002
- **Source**: agent-output-and-platform-defaults.design.md#3.4, #4.2
- **Scenarios**: agent-output-and-platform-defaults:E-03
- **Spec-Refs**: im/harness-im, i18n/harness-i18n#RULE-i18n-001
- **Paths**: config/api-messages.yaml, apps/im-gateway/src/muad_im_gateway/application/progress.py, apps/im-gateway/src/muad_im_gateway/application/stream_renderer.py, apps/im-gateway/src/muad_im_gateway/application/inbound.py, apps/im-gateway/src/muad_im_gateway/api/delivery.py, apps/im-gateway/src/muad_im_gateway/api/deps.py, tests/gateway/test_stream_renderer.py, tests/gateway/test_delivery_api.py, tests/gateway/test_inbound.py, tests/gateway/test_delivery_locale_fallback.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "gateway-locale",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/gateway/test_stream_renderer.py", "tests/gateway/test_delivery_api.py", "tests/gateway/test_inbound.py", "tests/gateway/test_delivery_locale_fallback.py"],
    "timeout": 300,
    "parallel": false
  }
]
```

### Description

- `config/api-messages.yaml` 的 `activity_messages.fixed` 增加 `stopped`/`broken_stream`/`task_accepted` 的 zh-CN/en-US。
- `ActivityMessages` 增加 `fixed(key)`；`StreamRenderer` 增加 `system_text`，移除 `STOPPED_TEXT`/`BROKEN_STREAM_TEXT`/`TASK_ACCEPTED_TEXT` 硬编码中文。
- `inbound.py` 用当前回复 locale 的 `fixed` 目录；`api/delivery.py` 的 CANCELLED/RUN_ABANDONED 空正文同源。
- 仅当 terminal 正文为空时，Gateway 用 `tenant_id` 读一次平台设置取 `locale.default_locale` 渲染目录文案；读取失败 warning + `catalog.default_locale`，不阻断投递。
- 投递契约不新增字段；正常终态仍用 Runtime 已渲染的 `final_text`。

### Checklist

- [ ] 三条固定文案在 YAML 两侧齐全，代码无硬编码中文。
- [ ] `StreamRenderer`/`inbound.py`/`delivery.py` 均按回复 locale 取文案。
- [ ] 空正文 terminal 使用租户 locale；设置读取失败仍能投递并留 warning。
- [ ] 不新增 `DeliveryRequest` 字段；旧调用方行为不变。
- [ ] 声明测试通过。

### Spec Delta

```json
[]
```

### Log

- [2026-11-21] created

### Verification

- 尚未执行

## TASK-005: 新建 Draft 使用平台默认时区

- **Status**: draft
- **Depends**: TASK-002
- **Source**: agent-output-and-platform-defaults.design.md#3.4
- **Scenarios**: agent-output-and-platform-defaults:S-04, agent-output-and-platform-defaults:S-05, agent-output-and-platform-defaults:E-01, agent-output-and-platform-defaults:B-01, agent-output-and-platform-defaults:B-03
- **Spec-Refs**: time/harness-time#RULE-time-001, snapshot/harness-snapshot#RULE-snapshot-001
- **Paths**: apps/agent-runtime/src/muad_agent_runtime/application/schedule_authoring.py, apps/agent-runtime/src/muad_agent_runtime/application/task_tools.py, apps/agent-runtime/src/muad_agent_runtime/application/executor.py, apps/agent-runtime/src/muad_agent_runtime/application/run_service.py, tests/agent_runtime/test_schedule_authoring_compiler.py, tests/agent_runtime/test_scheduled_v2_intent_tools.py, tests/agent_runtime/test_schedule_promotion.py, tests/agent_runtime/test_task_handoff.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "schedule-default-timezone",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/agent_runtime/test_schedule_authoring_compiler.py", "tests/agent_runtime/test_scheduled_v2_intent_tools.py", "tests/agent_runtime/test_schedule_promotion.py", "tests/agent_runtime/test_task_handoff.py"],
    "timeout": 300,
    "parallel": false
  }
]
```

### Description

- `ScheduleAuthoringContext` 增加 `default_timezone`；`_timezone` 在参数缺失且 default 非空时使用 default，仍做 IANA 校验；无 default 时保持原有字段错误。
- `draft_schedule` 的 `timezone` 从 required 移除；`promote_run` 同样可选；`draft_schedule_update` 保持 required（完整配置）。
- 仅当新建 Draft 缺省时区时，`BackgroundTaskToolSet` 用 `settings_client` 读一次当前平台设置；不可读返回 `PLATFORM_SETTINGS_UNAVAILABLE`，不落草稿；未注入客户端（直构/单测）用 Run 冻结值兜底。
- `ExecutorRequest` 增加 `settings_client`，由 `RunService._stream_run` 注入；`build_registry` 透传。
- Draft 仍写入解析后的具体 IANA 时区；已建 Schedule 不变。

### Checklist

- [ ] `draft_schedule`/`promote_run` 缺省时区时使用当前平台默认，并存储具体 IANA 名。
- [ ] 显式时区优先；更新 Draft 未提供时区明确报字段错误，不静默改写。
- [ ] 设置源不可读时明确失败且不落草稿；直构/单测有稳定兜底。
- [ ] 平台时区变更不影响已建 Schedule；新 Draft 使用新值。
- [ ] 声明测试通过。

### Spec Delta

```json
[]
```

### Log

- [2026-11-21] created

### Verification

- 尚未执行

## TASK-006: 失效 env 清理与设置页 effect 文案

- **Status**: draft
- **Depends**: TASK-005
- **Source**: agent-output-and-platform-defaults.design.md#3.4, #3.5
- **Scenarios**: agent-output-and-platform-defaults:S-04
- **Spec-Refs**: i18n/harness-i18n#RULE-i18n-001, ui/harness-ui
- **Paths**: .env, .env.example, apps/console-platform/backend/src/muad_console_platform/application/platform_settings_catalog.py, apps/console-platform/frontend/src/locales/zh-CN.json, apps/console-platform/frontend/src/locales/en-US.json, tests/test_configuration_convergence.py, tests/console_platform/test_platform_settings_schema.py, tests/frontend/test_capacity_webchat_settings_ui.py
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "config-locale-hints",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/test_configuration_convergence.py", "tests/console_platform/test_platform_settings_schema.py", "tests/frontend/test_capacity_webchat_settings_ui.py"],
    "timeout": 300,
    "parallel": false
  }
]
```

### Description

- 删除本地 `.env` 的失效 `DEFAULT_LOCALE`；`.env.example` 已无迁移键，确认不改并保留现有 `test_configuration_convergence.py` 守护。
- `platform_settings_catalog.py` 的 `_FIELD_META` 为 `locale.default_locale` / `locale.default_timezone` 增加 `effect_key`。
- `zh-CN.json` / `en-US.json` 增加 `settings.effect.agent_reply_language` 与 `settings.effect.schedule_default_timezone` 两条词条；不新增组件/状态。
- 文案说明：默认语言用于 Agent 回复与后台任务投递，控制台界面语言不受影响；默认时区用于新建 Draft，不改写已建任务。

### Checklist

- [ ] `.env` 不再有 `DEFAULT_LOCALE`；`.env.example` 键集不变。
- [ ] 两个字段的 `effect_key` 由 API 元数据返回。
- [ ] 中英文词条均存在且与后端 key 一致。
- [ ] 设置页渲染说明文案，无组件回归。
- [ ] 声明测试通过。

### Spec Delta

```json
[]
```

### Log

- [2026-11-21] created

### Verification

- 尚未执行

## TASK-007: 配置盘点、Spec Delta 与规范收口

- **Status**: draft
- **Depends**: TASK-001, TASK-002, TASK-003, TASK-004, TASK-005, TASK-006
- **Source**: agent-output-and-platform-defaults.design.md#Spec Compliance Matrix
- **Scenarios**:
- **Spec-Refs**: arch/harness-arch, time/harness-time#RULE-time-001, i18n/harness-i18n#RULE-i18n-001, platform/harness-project-platform, snapshot/harness-snapshot#RULE-snapshot-001, im/harness-im, test/harness-test
- **Paths**: docs/configuration-inventory.csv, docs/configuration-inventory.md, .code-flow/specs/arch/harness-arch.md, .code-flow/specs/time/harness-time.md, .code-flow/specs/i18n/harness-i18n.md, .code-flow/specs/platform/harness-project-platform.md, .code-flow/specs/snapshot/harness-snapshot.md, .code-flow/specs/im/harness-im.md
- **E2E**:
- **Skip-Tests**: false
- **Skip-Reason**:

### Tests

```json
[
  {
    "id": "inventory-catalog",
    "argv": ["uv", "run", "python", "-m", "pytest", "-q", "tests/test_configuration_inventory.py", "tests/test_error_catalog.py", "tests/test_configuration_convergence.py"],
    "timeout": 300,
    "parallel": false
  }
]
```

### Description

- 运行 `uv run python scripts/regen_config_inventory.py` 更新 `docs/configuration-inventory.csv`；只接受本需求新增常量/行号对齐，不回退已有修改。
- 同步 `docs/configuration-inventory.md` 中与 `locale`/失败文案/默认时区有关的叙述（如新增模块或指标需要登记）。
- 通过 Spec Delta 收口 6 个规范：`harness-arch`、`harness-time`、`harness-i18n`、`harness-project-platform`、`harness-snapshot`、`harness-im`。
- 不新增表/列/迁移；不把实现常量或行号写进 Spec 文案。

### Checklist

- [ ] `configuration-inventory.csv` 与当前 AST 一致；无与本需求无关的漂移。
- [ ] `docs/configuration-inventory.md` 叙事与代码事实一致。
- [ ] 6 个 Spec Delta 的 `target`/`ref`/`before` 与当前 Spec 精确匹配；`add` 规则无重复。
- [ ] `harness-arch` 的确定性收尾口径与代码一致；`harness-time` 的平台默认时区例外明确；`harness-i18n` 覆盖 Agent 语言与失败描述；`harness-project-platform` 明确平台原文只进技术信息；`harness-snapshot` 只新增 `policy_json.default_locale`；`harness-im` 固定文案统一走 locale 目录。
- [ ] 声明测试通过；archive 时 Delta 可应用。

### Spec Delta

```json
[
  {
    "id": "DELTA-001",
    "op": "update",
    "target": "i18n/harness-i18n",
    "ref": "RULE-i18n-001",
    "before": "后端错误消息与前端页面必须支持 zh-CN/en-US；新增业务仅新增配置/词条，不改框架代码；语言经 `X-Locale`/`Accept-Language` 协商。",
    "rule": "后端错误消息与前端页面必须支持 zh-CN/en-US；新增业务仅新增配置/词条，不改框架代码；语言经 `X-Locale`/`Accept-Language` 协商。Agent 的默认回复语言取平台设置 locale.default_locale，当前用户消息的显式语言要求优先，用户 memory 的 language 偏好次之；工具/平台错误码必须同时提供 zh-CN/en-US 直白描述，错误码本身不翻译，未知码必须有通用描述与回退；API/入口错误仍只用 config/api-messages.yaml，Agent 失败收尾的码族描述可由 agent-core 维护。",
    "evidence": "design §3.4/§3.5；scenarios S-01/S-02/S-03/E-02；tests test_prompt_builder.py、test_finalization_render.py"
  },
  {
    "id": "DELTA-002",
    "op": "add",
    "target": "arch/harness-arch",
    "rule": "失败收尾由 Runtime 确定性渲染，不新增模型调用；用户可见正文先给直白原因与下一步，终止码、工具/平台码、Run ID 与脱敏平台原文只进技术信息；首次确定性失败不得渲染成「重复失败」。本条覆盖 Conventions 中「收尾再发一次模型调用」的旧口径。",
    "evidence": "design §3.4/§3.5；scenarios S-03/E-02；tests test_finalization_render.py"
  },
  {
    "id": "DELTA-003",
    "op": "add",
    "target": "time/harness-time",
    "rule": "新建 Schedule Draft 未指定时区时，允许在 Draft 创建边界读取当前平台设置 locale.default_timezone 作为默认值，并在 Draft/Revision 显式存储具体 IANA 时区；用户显式提供的时区优先，更新 Draft 仍要求完整配置；禁止使用服务器本地时区或硬编码 UTC 作为静默兜底。本条是 RULE-time-001 的 authoring 默认值例外。",
    "evidence": "design §3.4；scenarios S-04/S-05/E-01/B-01/B-03；tests test_schedule_authoring_compiler.py、test_scheduled_v2_intent_tools.py"
  },
  {
    "id": "DELTA-004",
    "op": "add",
    "target": "platform/harness-project-platform",
    "rule": "平台失败的 detail 只进入技术信息；用户可见主句使用固定直白描述（平台登录、凭据缺失、业务错误等），错误码与脱敏平台原文保留供支持排查，不得把平台原文或内部平台 key 当作主文案。",
    "evidence": "design §3.4；scenarios S-03/E-02；tests test_failure_hints.py、test_finalization_render.py"
  },
  {
    "id": "DELTA-005",
    "op": "add",
    "target": "snapshot/harness-snapshot",
    "rule": "Run 的 policy_json 冻结 locale.default_locale；旧行缺失时不追加语言段，失败文案回落默认语言。Draft 创建时读取当前 locale.default_timezone 属于 authoring 输入，不替代执行期冻结；Worker AGENTIC 的默认回复语言按执行边界读取，不新增 Task 快照字段。",
    "evidence": "design §3.3/§3.4；scenarios S-01/S-04/S-06；tests test_execution_defaults_settings.py、test_scheduled_v2_agentic_adapter.py"
  },
  {
    "id": "DELTA-006",
    "op": "add",
    "target": "im/harness-im",
    "rule": "所有用户可见固定文案（任务受理、停止、断流、附件回执等）必须按回复 locale 从 config/api-messages.yaml 的 activity_messages 模板取，不得硬编码中文；终态正文为空时才读取租户 locale 兜底，读取失败回落目录默认并留 warning。",
    "evidence": "design §3.4/§4.2；scenarios E-03；tests test_stream_renderer.py、test_delivery_locale_fallback.py"
  }
]
```

### Log

- [2026-11-21] created

### Verification

- 尚未执行
