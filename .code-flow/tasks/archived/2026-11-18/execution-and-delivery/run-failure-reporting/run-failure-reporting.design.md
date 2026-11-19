---
{"design_template": "full"}
---

# 执行失败收尾与定时任务执行模型 设计

> **文档编号**: MOD-RUN-FAILURE-1.0
> **文档版本**: v0.5
> **创建日期**: 2026-11-18
> **文档状态**: 设计评审中
> **设计输入**: `run-failure-reporting.prd.md` v0.6 + 内网 Run `70c31545` 链路分析包 + 定时任务失败实例 + 执行内核/压缩/恢复/导出/调度代码核对 + 两轮复查结论
> **修订**: v0.1 初稿：执行结果契约 + 收尾事实 + 受保护收尾 envelope；v0.2 补 stdout/stderr 证据边界；v0.3 收敛：平台直出、`outcome_parser`、仅业务执行有内容层失败、结构化 retryable、preview-only 证据、completion 只用现有契约、统一终止快照；v0.4 纳入定时任务执行链路（argv/input、时间、预算、草稿诊断、TASK 导出、Fake MSSP 场景）；v0.5 定时任务重构为 Job/Payload/Session/Delivery/Policy，authoring 编译/试跑/提升，与 artifact delivery 定义边界

**评审边界说明**:
- **需求评审**: PRD 第 2-8 章 → 通过后锁定需求基线。
- **设计评审**: 第 3-5 章 → 通过后锁定设计基线。
- **交接契约**: PRD 2.5 验收条件；场景唯一源为 `run-failure-reporting.scenarios.md`。

**ID 体系**: FEAT、NFR、RISK；场景使用 S-/E-/B-。

**本版关键结论（先读）**:

1. **一次执行只判定一次**：业务执行工具通过 `ToolDefinition.outcome_parser` 输出 `ExecutionOutcome`；pipeline 消费该契约，runner 不再解析工具文本。
2. **只有 `target=skill` 的业务执行有内容层失败**：`workspace_shell` / `skill_draft_shell` 的退出码是结果证据，不是 Run 止损条件。
3. **收尾由平台直出**：失败后不调用收尾模型、不追加 USER 指令、不依赖压缩后的历史；`FinalizationFacts` + `FinalizationRenderer` 生成 `ASSISTANT_MESSAGE` 与 `RUN_FAILED`。
4. **终止原因与事实分离**：终止原因回答“为什么停”，业务失败证据回答“哪里失败”，`last_failure` 只是历史证据，`decisive_failure` 才与停止原因关联。
5. **证据只做预览**：stdout/stderr 有界采集、头尾保留、`output_truncated` 可见；审计只存脱敏后的 `evidence_preview`，不做 4xx/5xx 模式扫描。
6. **所有终止出口走同一漏斗**：turn/tool budget、deadline、no_progress、cancel、model/verification/empty/shutdown 都由 Runtime 统一建 facts、渲染、持久化；挂起不是终止。
7. **完成状态只用现有契约**：普通 Run 没有交付物/输出契约时一律 `UNKNOWN`；只有已有 `output_contract` 结果时才写 `CONTRACT_VERIFIED/FAILED`，不新增交付物计划器。
8. **不做过度设计**：不新增服务/表/终态，不改预算默认值，不做模型收尾前言，不做 advisory 扫描，不保留第二套失败判定。

---

## 2. 需求分析

### 2.1 需求概述

| 项目 | 内容 |
|------|------|
| **模块名称** | 运行失败收尾与预算止损 |
| **模块ID** | MOD-RUN-FAILURE |
| **需求类型** | 缺陷修复 + 执行语义重构 + 可观测性补齐 |
| **业务背景** | 内网 Run `70c31545` 在轮次预算耗尽后，收尾消息声称“没有任何工具调用记录、错误码或平台返回内容”，但同一 Run 的审计事实包含 20 次工具调用、XDR HTTP 500（openresty）与 mssw 401 `PLATFORM_AUTH_REJECTED`。平台自己的结论与自己的记录相反。 |
| **核心目标** | 一次执行失败时，平台以自身事实向用户交代“哪一步失败、平台返回什么、哪些无法确认”；确定性失败首次即停或有界重试；收尾结论与审计同源。 |

### 2.2 痛点与价值

| 维度 | 内容 |
|------|------|
| **目标用户** | WebChat/企微终端用户；Console 运行审计与链路分析包排障人员；Skill 作者 |
| **当前问题** | 内容层失败未进入失败跟踪；预算耗尽复用失败提示词；收尾指令作为普通 USER 参与压缩，导致整段工具历史被裁掉；审计/导出只记录 transport `OK`，无法复盘 |
| **业务影响** | 用户被要求自己贴报错；模型把预算耗在无效排查；排障只能 grep 日志；同一 Run 的结论与审计矛盾 |
| **预期价值** | 失败原因可见、可回归、可导出；长任务不在确定性失败上反复消耗；平台事实成为唯一结论来源 |

### 2.3 功能方案

| 功能ID | 功能名称 | 设计落点 |
|--------|---------|----------|
| FEAT-01 | 业务执行失败纳入失败判定 | `ToolDefinition.outcome_parser` + `ExecutionOutcome`；仅 `target=skill` 的业务执行产生 `CONTENT` 失败 |
| FEAT-02 | 终止原因感知的收尾 | `TerminationSnapshot` + `TerminationReason`；Runtime 统一 finalization funnel |
| FEAT-03 | 收尾事实块 | `FinalizationFacts`：终止/用量/工具结果/业务失败证据/证据状态/待执行调用/完成状态 |
| FEAT-04 | 收尾一致性约束 | 平台确定性渲染，失败后不调用模型；事实块不可删除/改写 |
| FEAT-05 | 收尾不依赖压缩后的历史 | 删除 `_conclude` USER 指令；失败收尾不进入模型请求，普通压缩逻辑不变 |
| FEAT-06 | 审计与导出可见 | `tool_call_audit` 业务字段 + `RUN_FAILED.finalization` + failure evidence 保留 |

### 2.4 范围与边界

| 类别 | 内容 |
|------|------|
| **范围** | 业务执行工具的结果契约与失败跟踪；终止快照/原因；收尾事实/渲染/持久化；工具审计/链路导出；checkpoint 最小失败状态；Skill 失败结果契约与模板 |
| **非范围** | Skill 自身业务实现与平台可用性修复；调整 max_turns/工具/时间预算默认值；新增 Console 页面；新增独立事实表/消息队列；新增交付物计划或“部分完成”终态；模型收尾前言；stdout/stderr 模式扫描；历史 Run 事实回填 |
| **前置假设** | 业务执行适配器能输出结构化结果；Runtime 可在收尾时读取本 Run 的 `tool_call_audit`；现有 `FAILED` 终态与 `RUN_NO_PROGRESS`/`RUN_BUDGET_EXCEEDED`/`RUN_DEADLINE_EXCEEDED` 等错误码保留 |

### 2.5 验收条件

1. 工具层全 OK、存在业务执行失败、轮次预算耗尽时，收尾必须分别表达终止原因、工具结果、业务失败证据；不得只写“无工具失败/仅预算耗尽”。
2. `target=skill` 的 `status=FAILED` / 非零退出进入失败跟踪；`retryable` 由 skill 结构化失败信封提供，缺省按确定性处理；5xx/429/网络按冻结策略有界重试；鉴权/凭据错误立即停止。
3. `workspace_shell` / `skill_draft_shell` 的非零退出是结果证据，不触发 Run 止损；`grep`/`test` 等诊断命令退出 1 不能终止 Run。
4. turn budget、tool budget、deadline、cancel、no_progress、model/verification/empty/shutdown 都有确定性收尾；未执行工具调用只记 pending，不进入失败计数。
5. 失败收尾不向历史追加 USER 指令、不调用收尾模型；即使此前经历多次压缩，收尾事实仍完整。
6. 模型空输出或编造“报告已生成”时，平台事实块仍然输出；关键事实不依赖模型措辞。
7. 恢复后的收尾可从 checkpoint 失败状态 + 已持久化审计重建；没有交付物依据时明确“完成情况无法确认”，不猜测。
8. 导出包能区分工具层/内容层失败，列出终止原因、用量、最后失败与完成状态；timeline 截断不影响 `failure_evidence`。

---

## 3. 现状与根因

### 3.1 实例事实链

| 时间 / seq | 事实 |
|---|---|
| 06:11:09 / 1319 | 用户请求生成 `xdr-app55-business-zzx` 昨天的 MSSP 外部威胁报告 |
| 06:11:22 / 1335 | `online-customers-mssp` 成功，解析 `company_id=22921947` |
| 06:11:32 后 | 真正执行任务的 `daily-duty-report-mssp` 调用；随后 16 轮手工排查 |
| 06:12:36 / 1434 | 第 1 次 `budget` 压缩，节省 43,931 字节 |
| 06:12:43 / 1440 | 第 2 次 `budget` 压缩，节省 45,603 字节 |
| 06:12:45 / 1444 | 第 3 次 `budget` 压缩，节省 108,418 字节 |
| 06:12:49 / 1447 | 收尾消息：“我这边没有收到具体的失败信息……没有任何工具调用记录、错误码或平台返回内容” |
| 06:12:49 / 1448 | `RUN_FAILED`，`RUN_BUDGET_EXCEEDED` |

运行账目：模型调用 21 次、工具调用 20 次（全部 transport `OK`）、轮次 20/20、工具 20/30、耗时 99.963s/3600s；另有 1 次工具调用因轮次打满未执行。模型推理在 seq 1431/1437 已看到 XDR 500 与 mssw 401。

### 3.2 工具结果语义分裂

- `apps/agent-runtime/.../skill_tools.py::_execution_payload` 返回 `status/exit_code/stdout/stderr`，没有顶层 `error`。
- `packages/agent-core/.../tools/pipeline.py::_content_error` 只识别顶层 `error.code`，因此 `status=FAILED/exit_code != 0` 仍记为 `SUCCEEDED`。
- `packages/agent-core/.../agent/runner.py::_failure_info` 同样只认顶层 `error` 或 `status == ERROR`；`_track_failure` 在“成功”时只清 `identical_failures`，**不清 `last_failure`**。
- 结果：业务执行失败被当成进展，模型继续排查直到轮次预算耗尽；`last_failure` 也不能直接作为本次停止原因。

### 3.3 收尾提示词与收尾事实缺失

- `runner.py::_conclude` 硬编码“工具执行已因失败停止”，但本次是 `budget_exhausted=True`、`no_progress=False`。
- `_conclude` 先追加 `ModelRole.USER` 收尾指令，再走 `_prepare_model_request` → `compact_history`；`trim_history` 从最近一条 USER 切分，收尾指令成为最新 USER 后，整段工具历史消失。
- `AgentRunState` 没有终止原因、完整用量、业务失败证据列表或完成状态；`last_failure` 仅覆盖工具失败。
- `RunnerCheckpoint` 只有计数与操作水位，没有失败跟踪信息；恢复后无法重建失败事实。

### 3.4 终止出口不统一

- `_route_after_tools` 在 `budget_exhausted` 时直接 `NODE_FINALIZE`，工具预算耗尽路径可能没有正文。
- `RunnerDeadlineExceeded` / `RunnerCancelled` / `RunnerVerificationExhausted` / `RunnerEmptyReply` / `RunnerModelError` 以异常退出，没有统一事实。
- 取消走 `RUN_COMPLETED(CANCELLED)`，不是 `RUN_FAILED`；挂起 `WAITING_TOOL/WAITING_INPUT` 不是终止。
- `budget_exhausted` 只是单个 bool，不能区分 turn budget 与 tool budget。

### 3.5 审计与导出盲区

- `tool_call_audit` 只有 transport `status/error_code`，没有 `business_status`/`exit_code`/`failure_layer`/`output_truncated`。
- `execution_export_view.py::_tool_calls` 不导出结果状态；`calls.csv` 的 `result_status`/`target` 为空。
- `execution_diagnosis.py::_tool_candidates` 只收 `status=FAILED`，内容层全 OK 不会成为失败候选。
- `execution_export_render.py::fit_report` 只保留前 20 + 后 20 事件，原始报告调用的中间事件被截掉；`failures.csv` 只有 RUN/EVENT。
- `report.md` 出现“完整: True”与“已截断: omitted_events=85”并存，容易误读。

### 3.6 Skill retryable 契约缺口

- 模板 `skills/_templates/business-skill-python/scripts/run.py` 的 `except Exception` 统一 `sys.exit(1)`，`PlatformAuthError.retryable` 被丢掉。
- 适配器只看到退出码，无法区分 5xx/429 与鉴权失败；“5xx 有界重试”目前没有实现路径。
- 必须在 skill 失败信封中显式携带 `code/retryable`，否则只能按确定性处理。

---

## 4. 设计

### 4.1 分层与数据流

```text
Tool Handler（adapter）
   │  业务执行返回结构化 envelope
   ▼
ToolDefinition.outcome_parser ──► ExecutionOutcome
   │
   ├──► tool_call_audit（business_status / failure_layer / exit_code /
   │                     output_truncated / evidence_preview）
   ▼
AgentRunner（失败累积 / TerminationSnapshot）
   │  status + counters + failure_state + pending calls
   ▼
Runtime FinalizationFunnel
   │  facts = build_facts(snapshot, audit, completion)
   ▼
FinalizationRenderer（平台直出，不调用模型）
   │
   ├──► ASSISTANT_MESSAGE
   └──► RUN_FAILED（或 RUN_COMPLETED(CANCELLED)）
```

职责：

| 层 | 只负责 |
|---|---|
| Tool 层 | 判定一次业务执行是 `SUCCEEDED/FAILED/TIMED_OUT/CANCELLED/NOT_APPLICABLE/UNKNOWN`，输出 error/exit_code/evidence |
| Agent 层 | 失败累积、失败状态、终止快照；不拼最终用户文案 |
| Runtime 层 | 构建 facts、平台确定性渲染、持久化、投递 |
| Console/导出 | 只读事实，不重新判断失败 |

### 4.2 `ExecutionOutcome` 与工具边界

不在 handler 里直接写审计，也不让内核解析任意文本。`ToolDefinition` 增加 **适配器拥有的纯函数**：

```text
outcome_parser: Callable[[str], ExecutionOutcome | None] | None
```

- 只有业务执行工具设置：`execute`（`target=skill`）、`execute_skill`、`run_skill_script`。
- `workspace_shell` / `skill_draft_shell` 不设置；它们的退出码是结果内容，不是业务失败。
- 其它内容/读取工具不设置。
- 不要求 handler 返回新的 `outcome` envelope；`outcome_parser` 从现有 `_execution_payload`（`status/stdout/stderr/exit_code/result` + `output_truncated`）提取失败事实，避免在处理链里再复制一份 outcome。
- `execute` 的 parser 只在结果是 skill 执行 payload 时返回 outcome；`target=draft/workspace` 返回 `None`，pipeline 记 `NOT_APPLICABLE`。
- `outcome_parser` 是纯函数，无 IO；pipeline 在 POST_TOOL_USE 之后、写审计之前调用一次，判定输入是 hook 处理后的最终结果。
- 删除 runner 侧的 `_failure_info` 文本解析；pipeline 只保留一处结构化 `error` envelope 识别（tool 层错误）。
- `outcome_parser` 自身异常按 `TOOL` 失败/`COMMON_INTERNAL_ERROR` 处理，不得让 Run 崩在解析器上。

```text
ExecutionOutcome
  call_id, tool_name          # pipeline 填充
  transport_status            # OK / ERROR / BLOCKED / NOT_FOUND
  business_status             # SUCCEEDED / FAILED / TIMED_OUT / CANCELLED /
                              # DENIED / NOT_APPLICABLE / UNKNOWN
  failure_layer               # NONE / TOOL / CONTENT
  error_code, error_message
  exit_code
  retryable
  evidence:
    preview                   # 脱敏、有界；失败/诊断摘要
    output_truncated          # 采集是否被上限截断
    artifact_id               # 已有工具结果产物；不新建 evidence_artifact_id
```

适配器输入是现有执行结果（新增 `output_truncated`），`outcome_parser` 据此产出 `ExecutionOutcome`：

```json
{
  "status": "FAILED",
  "stdout": "...",
  "stderr": "{\"ok\": false, \"code\": \"PLATFORM_AUTH_REJECTED\", \"message\": \"mssw session rejected: HTTP 401\", \"retryable\": false}",
  "result": null,
  "exit_code": 3,
  "output_truncated": false
}
```

```text
outcome_parser:
  business_status=FAILED
  failure_layer=CONTENT
  error_code=PLATFORM_AUTH_REJECTED
  error_message=mssw session rejected: HTTP 401
  exit_code=3
  retryable=false
  evidence.preview="mssw session rejected: HTTP 401"
```

分类矩阵：

| 场景 | business_status | failure_layer | retryable |
|---|---|---|---|
| `target=skill` `SUCCEEDED` + 结构化结果 | SUCCEEDED | NONE | false |
| `target=skill` `FAILED` / 非零退出 | FAILED | CONTENT | 由 skill 失败信封给出；缺省 false |
| `target=skill` `TIMED_OUT` | TIMED_OUT | CONTENT | true，受冻结阈值约束 |
| `target=skill` `CANCELLED` | CANCELLED | NONE | false；不算失败 |
| `target=skill` `SUCCEEDED` 但无结构化结果 | UNKNOWN | NONE | false；不判失败，facts 标 UNKNOWN |
| `target=skill` `SUCCEEDED` 但结构化结果明确 `ok=false`/`error` | UNKNOWN | NONE | false；只作证据，不猜业务失败 |
| `workspace_shell` / `skill_draft_shell` 任意退出码 | NOT_APPLICABLE | NONE | false；退出码只进证据 |
| `execute target=draft/workspace` 执行成功 | NOT_APPLICABLE | NONE | false |
| `execute` 校验/沙箱/平台会话错误 | FAILED | TOOL | 按错误码/`retryable` |
| pipeline 策略拒绝 | DENIED | TOOL | 按现有策略（可纠正则重试） |
| handler 抛异常 | FAILED | TOOL | 按异常码 |
| 其它 handler 返回 `{"error": ...}` | FAILED | TOOL | 按 error code |
| MCP `isError` | FAILED | TOOL | 按协议/错误码 |

关键规则：

- **只有 `target=skill` 的业务执行有 `CONTENT` 失败**；诊断 shell 的非零退出不得触发 `no_progress`。
- 判定只消费 `status/exit_code/结构化 error`；stdout/stderr 只作为 evidence。
- 鉴权/凭据错误（401/403、`PLATFORM_AUTH_REJECTED`）确定性停；5xx/429/网络按冻结策略有界重试。
- 非零退出证明业务执行失败，不能单独证明不可重试；retryable 由 skill 失败信封给出，缺省确定性。
- 无法判断业务结果时输出 `UNKNOWN`，不得伪装成功。
- tool 层 `retryable` 沿用现有语义：显式 `retryable: true`、平台/环境前缀确定性、其余按现有策略；该判定移到 pipeline，runner 不再解析文本。
- `evidence.preview` 必须脱敏；原始 stdout/stderr 不进入审计表。
- 采集有界保留头部 + 尾部（失败证据优先保留尾部），并记录 `output_truncated`；只留头部会让尾部错误和结果 JSON 不可见。
- workspace/draft 触发 `SandboxOutputLimit` 时，错误体必须带 `output_truncated=true` 和有界、脱敏的输出 preview，不能只返回错误码。

失败跟踪（`AgentRunner` 消费 outcome）：

- `SUCCEEDED` / `NOT_APPLICABLE` / `UNKNOWN`：清 `identical_failures`；保留历史失败证据，但不改变停止原因。
- `FAILED`/`TIMED_OUT`/`DENIED` 且 `retryable=false`：第一次 `no_progress`。
- `FAILED`/`TIMED_OUT`/`DENIED` 且 `retryable=true`：按 `(tool, error_code, failure_layer)` 计数到冻结阈值。
- `CANCELLED`：不算失败；由取消路径收口。
- `last_failure` 只记“最近观察到的失败”；`decisive_failure` 在 finalize 时按终止原因推导。

Skill 失败信封（P0）：

- 成功：stdout 只输出一个机器可读 JSON（现有模板契约）。
- 失败：stderr 输出**最后一个 JSON 对象**，至少包含 `{"ok": false, "code": "...", "message": "...", "retryable": true|false}`，并返回非零退出码。
- 适配器解析该 JSON；缺失时按 `retryable=false` 处理，并在 facts 中标记 retryability unknown。
- 模板 `skills/_templates/business-skill-python/scripts/run.py` 同步：从异常对象读取 `code/retryable`（`PlatformAuthError`/`SkillExecutionError` 等），写 stderr JSON。
- 不要求所有存量 skill 立即重写；不符合契约的 skill 只产生证据，不猜业务失败。

### 4.3 失败状态与终止原因

`AgentRunState` 的失败状态：

```text
last_failure: FailureEvidence | null
identical_failures: { "tool:code": count }
no_progress: bool
```

`FailureEvidence`：

```text
tool, tool_call_id, status, code, message, failure_layer,
exit_code, retryable, evidence_preview, occurred_at
```

`TerminationReason`：

```text
TURN_BUDGET
TOOL_BUDGET
DEADLINE
NO_PROGRESS
CANCELLED
RUN_ERROR
```

映射到现有错误码：

| 触发 | reason | error_code |
|---|---|---|
| `turns >= max_turns` | TURN_BUDGET | `RUN_BUDGET_EXCEEDED` |
| `tool_calls_used >= max_tool_calls` | TOOL_BUDGET | `RUN_BUDGET_EXCEEDED` |
| `RunnerDeadlineExceeded` | DEADLINE | `RUN_DEADLINE_EXCEEDED` |
| `no_progress` | NO_PROGRESS | `RUN_NO_PROGRESS` |
| 用户取消 / `RunnerCancelled` | CANCELLED | 无；写 `RUN_COMPLETED(CANCELLED)` |
| `RunnerModelError` | RUN_ERROR | `MODEL_UNAVAILABLE` |
| `RunnerVerificationExhausted` | RUN_ERROR | `VERIFICATION_EXHAUSTED` |
| `RunnerEmptyReply` | RUN_ERROR | `MODEL_EMPTY_REPLY` |
| `RunnerShutdown` | RUN_ERROR | `RUN_SHUTDOWN` |
| 其它异常 | RUN_ERROR | `COMMON_INTERNAL_ERROR` |

规则：

- `termination.reason` 取**实际导致停止的条件**；其它已观察条件进 `secondary_reasons`。
- `NO_PROGRESS` 优先于 turn/tool budget（当前 `_finalize` 的口径）。
- 硬 deadline / 取消以实际异常为主；若此前已有失败，失败只作 secondary，不伪装成主因。
- `TURN_BUDGET` / `TOOL_BUDGET` 在 finalize 时从计数器推导，不依赖单个 `budget_exhausted` bool。
- `WAITING_TOOL` / `WAITING_INPUT` 不是终止；不生成 failure facts。
- `decisive_failure` 推导：
  - `NO_PROGRESS` → `last_failure`
  - TURN_BUDGET / TOOL_BUDGET / DEADLINE / CANCELLED → `null`
  - RUN_ERROR → `null`（除非错误本身就是最后一次工具失败）
- 历史 `last_failure` 不自动成为本次停止原因。

`TerminationSnapshot`（runner → runtime）：

```text
status                 # COMPLETED / BUDGET_EXCEEDED / NO_PROGRESS / WAITING_*
budget_kind            # TURN / TOOL / NONE
turns_used, turns_limit
tool_calls_used, tool_calls_limit
elapsed_ms, deadline_ms
failure_state: { last_failure, identical_failures, no_progress }
pending_tool_calls: [{ call_id, tool_name }]
```

- 正常终态由 `AgentRunResult` 携带。
- `RunnerError` 子类在 runner 顶层边界挂同一个 snapshot 后再抛出。
- `runner.outcome` / `SegmentResult` 携带 snapshot；Runtime 不依赖进程内 state。

`RunnerCheckpoint.failure_state`（可选）：

```text
last_failure
identical_failures
no_progress
```

旧 checkpoint 默认空；恢复时用 checkpoint + `tool_call_audit` 重建 facts。checkpoint 不保存完整 facts，避免双份事实。

### 4.4 `FinalizationFacts`

由 Runtime 的 `FinalizationFactsBuilder` 构建，输入 `TerminationSnapshot` + 本 Run 的 `tool_call_audit` + 已有完成契约结果：

```text
FinalizationFacts
  schema_version
  termination:
    reason, secondary_reasons, error_code
  usage:
    turns_used/turns_limit
    tool_calls_used/tool_calls_limit
    elapsed_ms/deadline_ms
  tool_outcomes:
    total/succeeded/failed/timed_out/denied/cancelled/not_applicable/unknown
    failure_layers: {TOOL: n, CONTENT: n}
  business_failure_evidence:
    failures[]              # 仅 confirmed 失败
    last_failure
    decisive_failure
  evidence_status:
    CONFIRMED_FAILURE | CONFIRMED_NONE | NOT_OBSERVED | UNKNOWN
  evidence_incomplete       # 任一 output_truncated
  pending_tool_calls[]      # 仅 call_id/tool_name，不展开参数
  completion:
    status: UNKNOWN | CONTRACT_VERIFIED | CONTRACT_FAILED
    source: string | null
```

`evidence_status` 判定：

| 条件 | 状态 |
|---|---|
| `failures[]` 非空 | `CONFIRMED_FAILURE` |
| 本 Run 没有业务执行工具（只有读取/诊断工具） | `NOT_OBSERVED` |
| 所有业务执行确认成功，且无截断/无 UNKNOWN | `CONFIRMED_NONE` |
| 业务执行成功但无结构化结果、结果非法、或输出被截断 | `UNKNOWN` |
| 没有任何工具调用 | `NOT_OBSERVED` |

语义：

- `CONFIRMED_FAILURE` 与 `CONFIRMED_NONE` 都必须是“契约确认”的结果，不能靠文本猜测。
- “无工具层失败”只描述 transport/tool 层，不代表没有内容层失败。
- `failures[]` 来自审计中 `business_status in (FAILED, TIMED_OUT, DENIED)` 且 `failure_layer != NONE` 的 confirmed 失败；`last_failure` 是最近一条；`decisive_failure` 只与 `NO_PROGRESS` 关联。
- `output_truncated=true` 时不得写 `CONFIRMED_NONE`；除非已有 confirmed failure，否则 `UNKNOWN`，并置 `evidence_incomplete=true`。
- `completion` 只使用已有契约结果：普通交互 Run 没有交付物/输出契约，写 `UNKNOWN`；Agentic 等已有 `output_contract` 验证结果时写 `CONTRACT_VERIFIED/CONTRACT_FAILED`。本期不新增 `expected_deliverables`/交付物计划。
- `failures[]` 只保留脱敏摘要，按证据预算有界（首个、最后、最近若干条）；完整记录仍在 `tool_call_audit`，facts 不复制完整输出。
- `pending_tool_calls` 只表示“未执行”，不进入 failed 计数。

### 4.5 收尾渲染（平台直出）

失败收尾**不调用模型**：

- 删除 `runner.py::_conclude` 的收尾模型调用；不向 `messages` 追加 USER 指令。
- 不引入 `protected_suffix` / `FinalizationRequest` / 收尾压缩保护。
- 普通模型回合的压缩逻辑保持不变；失败收尾不依赖压缩后的历史。
- 若未来产品确实需要“模型前言”，单独用 **facts-only** 请求实现（只输入 facts，不输入历史），本期不做。

`FinalizationRenderer`（Runtime）：

- 输入 `FinalizationFacts` + locale；确定性生成：
  - 终止原因与用量
  - 工具结果汇总
  - 业务失败证据（工具、错误码、退出码、脱敏摘要）
  - 证据状态 / 是否截断
  - 待执行调用
  - 完成状态（无法确认时明说）
  - 下一步建议（按错误类别）
- 模板由 Runtime 拥有，走现有 i18n 词条；不放进模型 prompt。
- 不渲染原始 stdout/stderr，只渲染有界、脱敏的 preview。
- 文案不得出现“没有任何工具调用/没有任何失败信息”这类与 facts 矛盾的固定句。

所有终止路径统一漏斗（`_finish_segment` / `_finalize_failed`）：

1. 从 `AgentRunResult` 或异常取 `TerminationSnapshot`。
2. 读取本 Run 的 `tool_call_audit`。
3. `FinalizationFactsBuilder` 构建 facts。
4. `FinalizationRenderer` 生成 `final_text`。
5. 写 `ASSISTANT_MESSAGE` + `RUN_FAILED`；取消走简化路径：`CANCEL_EVENT` + `RUN_COMPLETED(CANCELLED)`，不构建完整 failure facts。

取消：

- 取消不是失败：`TerminationReason=CANCELLED`，`error_code` 为空，写 `RUN_COMPLETED(CANCELLED)`。
- `final_text` 明确“用户已停止”；可以把停止前的最后事实作为次要说明，但不编造失败原因。
- 取消文案必须可见：`CANCEL_EVENT` payload 带 `final_text`（或按现有 UI 契约写 `ASSISTANT_MESSAGE`），不能只写 `reason=cancelled` 让用户看不到说明。
- 取消时未完成的工具调用不得记为 confirmed failure：能标记 `CANCELLED` 就标记，不能则 `UNKNOWN`，只作证据，不进入 `failures[]`。
- 取消只持久化 `final_text` + `reason=cancelled`；不写 `RUN_FAILED`，也不持久化完整 `finalization`（取消不是失败）。

降级：

- facts 构建失败：用 `error_code + last_failure` 生成最小 facts，记录 warning/metric，不阻塞 Run。
- 渲染失败：写 `error_code` + 最后失败摘要；仍写 `ASSISTANT_MESSAGE`，避免空正文。
- 审计读取失败：facts 标记 `evidence_status=UNKNOWN`，不得伪造 `CONFIRMED_NONE`。

### 4.6 持久化与恢复

`RUN_FAILED` payload 增加 `finalization`：

```json
{
  "status": "FAILED",
  "error_code": "RUN_BUDGET_EXCEEDED",
  "finalization": {
    "schema_version": 1,
    "termination": {"reason": "TURN_BUDGET", "secondary_reasons": [], "error_code": "RUN_BUDGET_EXCEEDED"},
    "usage": {},
    "tool_outcomes": {},
    "business_failure_evidence": {},
    "evidence_status": "NOT_OBSERVED",
    "evidence_incomplete": false,
    "pending_tool_calls": [],
    "completion": {"status": "UNKNOWN", "source": null}
  },
  "final_text": "平台渲染的最终文案"
}
```

- 同时写 `ASSISTANT_MESSAGE`，历史与失败终态看到同一段文本。
- `_finalize_failed` 接受 facts/final_text；不再只写错误码。
- 普通成功路径不变；取消路径写 `RUN_COMPLETED(CANCELLED)` + `final_text`/`reason`，不写 failure finalization。
- `AgentRunResult` 增加 `termination_snapshot`；`RunnerError` 携带同一 snapshot。
- `SegmentResult` 增加 `snapshot`（从 outcome 或异常取）。

恢复：

- 终态：直接读 `RUN_FAILED.finalization`；取消读 `RUN_COMPLETED(CANCELLED)` 的 `final_text`/`reason`，不读 failure facts。
- 非终态：`RunnerCheckpoint.failure_state` + 已持久化 `tool_call_audit` 重建 snapshot/facts。
- `no_progress=true` 时 `decisive_failure=last_failure`；否则为 null。
- 不新建事实表；不把完整 facts 写入 checkpoint 造成双份事实。
- 旧 checkpoint / 旧事件缺少 facts 时按最小事实降级，不伪造历史结论。

### 4.7 审计与导出

`tool_call_audit` 新增：

```text
business_status
failure_layer
exit_code
output_truncated
evidence_preview
```

- 复用现有 `artifact_id` 指向工具结果产物；不新增 `evidence_artifact_id`。
- `evidence_preview` 只对失败/诊断调用写入，脱敏、有界；原始 stdout/stderr 不落审计表。
- `evidence_preview` 使用与审计参数相同的脱敏策略（`redact_sensitive`/credential canaries）；skill stdout 当前没有脱敏，必须在写入前处理。
- `output_truncated=true` 时导出必须标注证据可能不完整。

迁移回填：

- 老行 `business_status=UNKNOWN`（不得把 transport `OK` 回填成业务 `SUCCEEDED`）。
- 老行 `failure_layer=NULL`（不得回填 `NONE`，否则等于宣称“确认无内容失败”）。
- 老行 `output_truncated=NULL`（未知，不得写成 false）；`evidence_preview=NULL`。
- 新行由 `ExecutionOutcome` 写入。

Console 诊断/导出：

- `execution_diagnosis` 的 tool candidates 同时检查 `status in FAILURE_STATUSES` **或** `business_status in (FAILED, TIMED_OUT, DENIED)`；同一行只计一次。
- `calls.csv`：新增 `business_status/exit_code/failure_layer/output_truncated`，保留 transport `status`。
- `failures.csv`：只列 confirmed 失败（含 `failure_layer=CONTENT`）；不列 `UNKNOWN`/`NOT_APPLICABLE`。
- `report.md`：终止原因/用量/最后失败/完成状态来自 `finalization`。
- `report.json`：`execution.finalization` + `tool_calls` 业务字段 + 独立 `failure_evidence`。
- `fit_report`：timeline 可截断，`failure_evidence` 不截断；至少保留首个失败、最后失败、所有 confirmed 失败调用、收尾事实；失败记录只带脱敏摘要，不复制完整输出。
- `data_quality`：`db_complete` 与 `export_truncated` 分开表达；`evidence_incomplete` 单独标注。

### 4.8 Skill 结果契约

- `SkillExecutionStatus` 是业务状态权威来源。
- 成功：stdout 只输出一个机器可读 JSON。
- 失败：stderr 最后一个 JSON 对象携带 `ok=false`、`code`、`message`、`retryable`，并返回非零退出码。
- retryable 映射：5xx/429/网络超时 → true；401/403/业务校验/未知 → false。模板按 HTTP 状态/异常类型给出，不把裸文本留给平台猜。
- `outcome_parser` 由适配器拥有，从 `execute`/`execute_skill`/`run_skill_script` 的现有结果中提取 outcome；不要求在工具结果里再嵌一层 `outcome`。
- 失败时不得“捕获全部异常后 exit 0”；返回非成功 status 或非零退出。
- 不符合契约、exit 0 但输出疑似错误的 skill 只能进入 `UNKNOWN`/证据预览，平台不猜业务失败。
- 普通诊断命令 stdout 只作 evidence；`workspace_shell`/`skill_draft_shell` 的退出码不触发止损。
- `self-skill-creator` 文档/模板同步更新 `code/retryable` 契约；不要求所有 skill 立即重写。

---

## 5. 关键流程

### 5.1 业务执行失败

```text
target=skill 返回 FAILED/非零退出
→ outcome_parser 产出 ExecutionOutcome(CONTENT, retryable)
→ pipeline 写 audit business_status/failure_layer/exit_code/evidence_preview
→ runner 更新 failure_state
→ 确定性失败首次 no_progress；可重试按阈值
→ Runtime finalization funnel
→ 平台渲染事实 + ASSISTANT_MESSAGE + RUN_FAILED
```

### 5.2 工具全 OK + 轮次预算耗尽

```text
tool_outcomes: all SUCCEEDED/NOT_APPLICABLE
business_failure_evidence: 空/历史失败
evidence_status: CONFIRMED_NONE / NOT_OBSERVED
termination: TURN_BUDGET
→ 文案分别表达终止原因、工具结果、业务失败证据
→ 不写“仅预算耗尽/没有失败”
```

### 5.3 工具预算 / Deadline

```text
TOOL_BUDGET / DEADLINE
→ snapshot + audit
→ 平台确定性文本
→ pending_tool_calls 只记录未执行，不计失败
```

### 5.4 取消

```text
cancel_requested / RunnerCancelled
→ CANCELLED
→ 平台文本“用户已停止”
→ CANCEL_EVENT + RUN_COMPLETED(CANCELLED)
→ 不写 RUN_FAILED，不编造失败原因
```

### 5.5 模型/验证/空回复/关机

```text
RunnerModelError / RunnerVerificationExhausted /
RunnerEmptyReply / RunnerShutdown
→ RUN_ERROR + 现有 error_code
→ 同一 facts/renderer 漏斗
→ 有 snapshot 用 snapshot；无 snapshot 用最小 facts
```

### 5.6 恢复

```text
checkpoint.failure_state
+ persisted tool_call_audit
→ snapshot/facts
→ 终态直接读 RUN_FAILED.finalization
```

---

## 6. 迁移与兼容

1. `tool_call_audit` 新增列 + 回填 `UNKNOWN/NULL`（一次迁移）。
2. `RunnerCheckpoint` 新增可选 `failure_state`（JSON，无表迁移）。
3. `RUN_FAILED` 新增 `finalization` payload（无表迁移，`schema_version`）。
4. 导出 schema 版本递增；旧事件缺少 facts 时按最小事实降级，不伪造。
5. 不双写、不保留第二套失败判定；`FAILED` 终态和预算默认值不变。
6. 删除 `_conclude` 收尾模型调用与 `concluded` 状态；不保留 `protected_suffix`。

---

## 7. 错误码、文案与安全

- 不新增 Run 终态错误码；沿用 `RUN_NO_PROGRESS`/`RUN_BUDGET_EXCEEDED`/`RUN_DEADLINE_EXCEEDED`/`VERIFICATION_EXHAUSTED`/`MODEL_EMPTY_REPLY`/`RUN_SHUTDOWN` 等。
- `TerminationReason` 是事实字段，不替代 `error_code`；API/Console 仍读 `error_code`。
- 平台文案按 locale 渲染；使用现有 i18n 词条，不新增框架。
- 事实/证据经过既有脱敏与截断；不输出凭据、Cookie、Token、内部 URL。
- skill stdout 不默认可信；写入审计/渲染前必须脱敏。
- 证据容量有上限：失败证据保留预览/引用，不保护所有原文；按完整调用组处理。

---

## 8. 测试与验收

### 单元

- `outcome_parser` 分类矩阵：skill status/exit code、结构化 stderr retryable、draft/workspace 非零退出不判失败、error envelope、UNKNOWN。
- `workspace_shell` / `skill_draft_shell` 退出 1 不设置 `no_progress`。
- `ExecutionOutcome` 的 `evidence.preview` 脱敏、`output_truncated` 保留。
- `TerminationReason` 映射：turn/tool budget、deadline、no_progress、cancel、model/verification/empty/shutdown。
- budget subtype 从计数器推导。
- `decisive_failure` 规则：NO_PROGRESS → last_failure；budget/deadline → null。
- `FinalizationFacts` 的 `evidence_status` 判定与序列化；旧值兼容。
- `FinalizationRenderer` 各 reason 文案；locale。
- `RunnerCheckpoint.failure_state` 序列化与旧 checkpoint。

### Runner

- 内容层失败首次 `NO_PROGRESS`，不继续 16 轮。
- 可重试内容失败按阈值；retryable 缺失按确定性。
- 工具全 OK + 轮次耗尽：三组事实，不写“无失败/仅预算”。
- 失败+预算：失败为主，预算 secondary。
- 失败收尾不调用模型、不追加 USER、不触发压缩；压缩历史后 facts 仍完整。
- 工具预算/DEADLINE/CANCELLED/RUN_ERROR：确定性收尾；pending 不算失败。
- 取消写 `RUN_COMPLETED(CANCELLED)`，不写 `RUN_FAILED`。
- 模型空/编造：平台事实块仍输出。
- resume 后收尾：checkpoint+audit 重建。

### 集成/导出

- 20 次工具调用 + 3 次压缩：最终文案含终止原因、用量、证据、完成状态。
- `calls.csv` 显示 business_status/exit_code/failure_layer/output_truncated/evidence_preview。
- `failures.csv` 含 confirmed 内容层失败，不含 UNKNOWN/NOT_APPLICABLE。
- `execution_diagnosis` 能发现 `status=OK + business_status in (FAILED, TIMED_OUT, DENIED)`。
- timeline 截断不影响 failure_evidence；`evidence_incomplete` 明确标注。
- `report.md` 不再把 `RUN_BUDGET_EXCEEDED` 当作唯一失败。

---

## 9. 备选方案与取舍

| 方案 | 结论 |
|---|---|
| 只修 `_conclude` 提示词 | 拒绝：压缩仍会删掉事实 |
| 模型收尾前言 / protected suffix | 拒绝：过度设计，重新引入压缩与不确定表述 |
| 扫描 stdout 4xx/5xx | 拒绝：误伤诊断命令/正常数据，无法区分工具语义 |
| ADVISORY 模式扫描 | 拒绝：第二套解释层；preview + `output_truncated` 已足够 |
| 把 workspace/draft 退出码当内容失败 | 拒绝：`grep`/`test` 正常退出 1 会误停 Run |
| 新增 `expected_deliverables`/交付物计划 | 拒绝：RunRequest 没有该契约，属于新子系统 |
| 新增“部分完成”终态 | 拒绝：跨系统状态扩散；用 `FAILED + termination_reason` |
| 新增事实表/服务/消息队列 | 拒绝：现有 events + tool audit + checkpoint 足够 |
| `ToolDefinition.outcome_parser` | 采用：适配器拥有判定，pipeline 只消费，避免 47 个 handler 全改 |
| 平台直出事实 | 采用：可回归、无模型前言、无压缩依赖 |
| 工具审计业务字段 + failure evidence | 采用：一次迁移，审计/导出/UI 同源 |

---

## 10. 定时任务执行模型（v0.5）

### 10.1 现状与根因

| 事实 | 代码位置 | 影响 |
|---|---|---|
| 三种模式由模型在 draft 工具里直接选 | `apps/agent-runtime/src/muad_agent_runtime/application/task_tools.py:100` | 用户被迫理解 SKILL/AGENTIC；选错就失败 |
| SKILL 的 spec 只有 `input_template`，没有 argv | `packages/contracts/src/muad_contracts/scheduling.py:145` | 旧 CLI Skill 无法被 SKILL 模式驱动 |
| Fire 只写 `{mode, skill_id, input, context}` | `apps/agent-worker/src/muad_agent_worker/scheduler/fire.py:703` | `SkillTaskExecutor` 读 `execution_snapshot_json["argv"]` 永远为空 |
| SKILL 的 stdin 收到整个 Fire 信封 | `apps/agent-worker/src/muad_agent_worker/worker/executor.py:284` | 控制字段混入业务 input |
| worker AGENTIC skill 工具只传 input | `apps/agent-worker/src/muad_agent_worker/worker/agentic_skills.py:119` | 模型传的 argv 全进 stdin |
| AGENTIC prompt 没有时间 | `apps/agent-worker/src/muad_agent_worker/application/agentic/run.py:149` | 模型不知道"昨天"，只能问用户 |
| worker 没有 `current_time` | `apps/agent-worker/src/muad_agent_worker/application/agentic/tools.py:110` | 与交互式 Run 能力漂移 |
| 预算由 draft 的模型参数提供 | `apps/agent-runtime/src/muad_agent_runtime/application/task_tools.py:400` | 模型自拟 20/8、12/4/200000 |
| 没有 session/tool policy/quiet result | 调度契约 | 定时任务无法像交互式 Run 一样工作 |

**结论**：不是 Skill 的问题；是 Job 模型缺少 payload/session/policy，authoring 缺少编译/试跑，runtime 缺少时间/工具/静默。

### 10.2 Job 模型

```text
ScheduleJob
  id, tenant, owner, agent
  schedule: cron/once + timezone
  payload:
    REMINDER   { text }
    SKILL_PLAN { skill_id, input, argv, window }
    AGENT_TURN { prompt, model_ref?, tools_allow, budget_ref }
  session_target: isolated | current | session:<id> | main
  delivery_intent: NONE | PUSH (WECOM)
  delivery_route_id / route snapshot  # 独立于创建入口；Web pull 不是 delivery_intent
  failure_policy: retry/backoff/notify
  state: next_fire_at, last_run, last_meaningful_result, scratch
```

- `payload.kind` 是内部派生字段，不是用户选项；
- `session_target` 决定上下文，`delivery` 决定结果去哪，两者分离；
- 旧 `SKILL`/`AGENTIC` 在开发期直接迁移为 `SKILL_PLAN`/`AGENT_TURN`，不保留双读。

### 10.3 Payload 契约

#### REMINDER

```json
{"kind": "REMINDER", "text": "..."}
```

无模型、无工具；直接写结果。

#### SKILL_PLAN

```json
{
  "kind": "SKILL_PLAN",
  "skill_id": "f314c19f-...",
  "input": {"company_id": "22921947"},
  "argv": ["--company-id", "22921947", "--date", "{{previous_day}}", "--no-wecom-push"],
  "window": {"kind": "DAY", "offset": -1}
}
```

- `input` 只写 stdin；`argv` 追加到脚本；
- 时间变量在 fire 时渲染并冻结；
- 无模型；失败按现有 Skill 失败契约处理。

#### AGENT_TURN

```json
{
  "kind": "AGENT_TURN",
  "prompt": "汇总 {{previous_day}} 的定时任务执行...",
  "model_ref": null,
  "tools_allow": ["SKILL:...", "PLATFORM:current_time", "PLATFORM:list_tasks"],
  "budget_ref": "platform"
}
```

- prompt 由 authoring 编译，用户确认；
- 运行时注入时间行、窗口、无人值守契约、上次结果、scratch；
- 工具面是完整的，不因"定时"而裁剪。

### 10.4 Authoring 编译器与意图识别

```text
用户意图
  → agent 输出 SchedulePlan（结构化）
  → 平台校验（skill/entrypoint/input/argv/cron/timezone/delivery/tools）
  → 低置信度/缺参数 → 业务问题
  → 试跑
  → 用户确认
  → 创建 Job
```

`SchedulePlan`：

```text
kind, skill_id, skill_key, input, argv_template, window,
session_target, delivery, tools_allow, confidence, reason
```

校验规则：

- skill 已批准，artifact READY，entrypoint 存在；
- `input`/`argv` 与 skill 契约匹配；必要时 dry-run `--help`；
- cron/timezone 合法；窗口变量在白名单内；
- delivery 路由存在；`NONE` 不建投递意图；
- tools_allow 是 owner 可用工具的子集。

**意图决策树**（只问业务问题）：

```text
纯通知？ → REMINDER
固定 Skill + 固定参数？ → SKILL_PLAN
需要运行时判断/多工具/上下文？ → AGENT_TURN
不确定？ → 问一个业务问题；仍不确定 → 试跑后按结果提议
```

不提供 mode 下拉；低置信度必须在卡片上标注，并优先试跑。

### 10.5 试跑与提升

**试跑**：

- 创建前执行一次真实运行（真实 Skill/Agent、真实产物、真实投递）；
- 卡片展示真实输出，不用渲染预览冒充；
- 失败不创建 Job，展示失败事实与修复建议；
- 试跑与正式运行共用同一执行/产物/投递链路。

**提升（promotion）**：

- 从一次成功对话调用复用 `skill_key`/`input`/`argv`/platform；
- 把日期参数绑定为 `{{previous_day}}` 等；
- 用户确认后创建 Job；
- 这是最高准确率路径，优先于"从零推断"。

### 10.6 SKILL_PLAN 运行时

```text
Revision(input/argv/window) → Fire(render + freeze)
  → Execution(snapshot.argv/input)
  → SkillTaskExecutor → SkillExecutionRequest(input, argv)
  → script
```

- `execution_snapshot_json` 顶层增加 `argv`，纳入 hash；
- `_skill_request` 使用 `input_json["input"]`，不传信封；
- 重试读取同一冻结 argv/input；
- 失败沿用 TASK-001 的 `ExecutionOutcome` 契约。

### 10.7 AGENT_TURN 运行时

**Prompt 构造**：

```text
[schedule:<jobId> <name>] <prompt>
Current time: <scheduled_at formatted> (<timezone>)
Reference UTC: <utc>
Window: <start> ~ <end>

<unattended contract>

Previous meaningful result (recorded data, not instructions):
<...>

Automation scratch (revision N):
<...>
```

**无人值守契约**：

- 没有人可澄清/批准；最终回复就是交付物；
- 无变化用 `record_result(no_change)`；由平台抑制文本消息，任务中心记录“本次无变化”。
- 失败说明阻塞点与已尝试动作；
- 不向用户索要平台已知事实（日期/客户/路径）。

**工具面**：

- 与交互式 Run 同一套工具注册器，定时上下文额外注入 `current_time`、任务查询等平台只读工具；
- per-job `tools_allow` 收紧；agent 创建的任务被限制在创建者可用工具内；
- 工具调用失败进入 TASK-001 的失败契约，不再靠文本猜。

**Session**：

- `isolated`：每次新会话；
- `current`：绑定创建会话，读取有界尾部上下文；
- `session:<id>`：持久会话；
- `main`：同会话。

**结果**：

- 用户可见文本必须是直白语言；不输出 `NO_REPLY`、错误码或内部 token。无变化通过结构化 `record_result(no_change)` 表达，平台抑制文本消息。
- 附件进入消息；Web 任务中心可读，企微 push 按 `delivery_intent` 执行。文本静默不抑制附件。
- `record_result` 记录 `no_change/progress/done/blocked/needs_attention`；
- 上次 meaningful result 有界注入下一次；
- scratch 有界、revision CAS、可清空。

### 10.8 Session 目标

| 目标 | 上下文 | 典型 |
|---|---|---|
| `isolated` | 每次新会话 | 日报、后台杂活 |
| `current` | 创建会话的有界尾部 | 需要对话上下文的任务 |
| `session:<id>` | 持久命名会话 | 跨次积累的站会/摘要 |
| `main` | 同会话 | 提醒、系统事件 |

- 会话删除/归档/重置 → 任务暂停或失败，不猜替代；
- session 与 delivery 分离。

### 10.9 工具策略与预算

**预算唯一来源**：`PlatformSettings.agent`。

| 设置 | 映射 |
|---|---|
| `agent.max_turns` | `max_steps` |
| `agent.max_tool_calls` | `max_tool_calls` |
| `agent.deadline_ms` | agentic 期限（与 task deadline 取早） |
| `agent.max_model_retries` | 模型重试 |
| `agent.max_identical_failures` | 同错阈值 |

- `max_tokens` 执行级累计上限；`0` = 不限制；单次请求仍取模型 params；
- admission 解析并冻结；旧 budget 只读不消费；
- `agent_policy(frozen)` 只读快照，不读 agent runtime_config；
- 卡片显示"系统默认（来源：平台设置）"。

**工具策略**：

- `tools_allow` per-job；默认 `*` 跟随 owner；
- 平台只读工具（`current_time`、`list_tasks`、`get_task`）是一等工具；
- agent 创建的任务不能超过创建者工具集；
- 撤权/策略变更在每次执行前复核。

### 10.10 静默结果、失败与导出

- `record_result(no_change)`；平台抑制文本，无变化不刷屏；附件仍进入消息（Web pull / 企微 push 独立）；用户可见文本必须直白。
- 上次 meaningful result 有界注入；
- 失败通知与业务投递分离；重试/退避/自动停用；
- 失败事实沿用 TASK-001/002/003 的 finalization funnel。
- TASK 导出的 timeline/events 来自 `task.task_event`；`tasks[].events` 按 `entity_id` 归组；TASK 用 `started_at`/`finished_at`；有 `task_id` 就采集 audits。
- `TASK_NO_PROGRESS` 带 `last_failure`；`TASK_RESULT_UNVERIFIED` 保留 cause；CANCELLED 不写“成功”。

### 10.11 API/工具契约

- `draft_schedule` 不再要求用户/模型选 `mode`；接受 `SchedulePlan`（kind 可内部派生）。
- 卡片返回 `resolved_payload`、`resolved_plan`、`budget_source`、`tools_allow`、`session_target`、`delivery`、`next_fire_at`。
- `confirm_schedule` 创建 Job；`run_schedule(force)` 试跑；`promote_run(run_id)` 从成功对话提升。
- `get_schedule` 展示 plan/最近结果/失败原因；`cancel_schedule(scope)` 保持现有语义。

### 10.12 数据迁移

- 开发期直接迁移：`mode=SKILL → payload=SKILL_PLAN`；`mode=AGENTIC → payload=AGENT_TURN`；`mode=REMINDER → payload=REMINDER`。
- 旧 `budget` 字段只读；新 Job 不写。
- 不保留双读/双写；不做历史回填。

### 10.13 与 `unified-artifact-delivery` 的边界

`unified-artifact-delivery` v2.0 的定稿模型：

```text
MessagePayload { text, attachments[] }
AttachmentRef { artifact_id, filename, media_type, size, checksum }
SurfacePlan { pull: bool, push_targets: [PushTarget] }
MessageDelivery (WECOM only) { message_key, kind, target, payload, status }
```

| 交界 | 归属 |
|---|---|
| `SKILL_OUTPUT_DIR` 登记、Artifact 版本、`OUTPUT`、`logical_file_id + version` | `unified-artifact-delivery` |
| `MessagePayload`/`AttachmentRef`/`SurfacePlan`/`MessageDelivery` | `unified-artifact-delivery` |
| Web pull（WebChat/MSSWWEB） | `unified-artifact-delivery`；没有投递意图 |
| 企微 push（WECOM） | `unified-artifact-delivery`；消息级状态机 |
| Job 的 `delivery_intent`/`delivery_route_id` | 本需求声明；对方校验/冻结/实现 |
| `write_artifact`/`deliver_artifact` | `unified-artifact-delivery`；`AGENT_TURN` 工具策略包含 |
| 任务中心 `deliveries[]`/`delivery_intent` | `unified-artifact-delivery` |
| 试跑产物 | 两者共用同一消息/附件/push 链路 |
| `MEDIA:` | 对方 P1 兼容层；本需求不依赖 |

**重叠与 owner**：

- 对方 TASK「Schedule 路由独立 + 目标选择」拥有 `schedule_drafts.py`/`schedule_confirm.py`/目标列表；本需求不重复修改。
- 本需求 TASK-005/007/011 消费 `delivery_intent`/`delivery_route_id`；投递通道是平台默认行为，确认卡片不展示通道或 `push_target_label`，只显示“完成后会回到当前会话 / 完成后可在任务中心查看”。
- 本需求不建第二套投递状态机；无变化用结构化 `record_result(no_change)` 抑制文本，附件仍进入消息并参与 pull/push；不向用户发送内部 token。

**不合并 PRD**；共享契约 + 联合 E2E；排期先定契约。

### 10.13.1 已确认的跨需求契约决策

| # | 决策 | 结论 |
|---|---|---|
| 1 | `logical_file_id` 作用域 | `tenant + owner(run/task id) + attempt + 产出调用身份 + 规范路径`；每一次执行尝试 = 新逻辑文件；只有同一 attempt 内显式 `append_artifact` 才是 version；跨天/跨执行/跨重试不聚合。 |
| 2 | 无变化静默 + 附件 | 用 `record_result(no_change)` 抑制文本；允许 `text` 空、`attachments` 非空；Web pull 可见；企微 push 按 `delivery_intent`；用户可见文本必须直白，不出现内部 token。 |
| 3 | Schedule 路由 TASK | 归 `unified-artifact-delivery`；本需求只消费 `delivery_intent`/`delivery_route_id`，不改 `schedule_drafts.py`/`schedule_confirm.py`。 |
| 4 | `deliveries[]` | `[]` 仅 NONE；push 请求未激活 = PENDING；outbox 失败 = FAILED；UI 不能显示成“仅 Web”。 |
| 5 | 交付方式 | 两个目录、两份任务文档、统一发布；共享契约 + 联合 E2E；不合并 PRD。 |

### 10.14 测试### 10.14 测试

| 层 | 用例 |
|---|---|
| 契约 | Job/payload schema；旧 mode 迁移；session/delivery 分离 |
| Authoring | 黄金集：日报→SKILL_PLAN；汇总→AGENT_TURN；提醒→REMINDER；歧义→业务问题；校验失败→修正/降级 |
| 试跑/提升 | 真实试跑；失败不建 Job；promotion 复用真实调用 |
| SKILL_PLAN | argv/input/time；stdin 只含 input；重试幂等 |
| AGENT_TURN | prompt 时间行/窗口/无人值守；工具策略；session；`record_result(no_change)`；文件产物 |
| 预算 | 设置映射/冻结/旧 budget 不消费/0=不限制 |
| 诊断 | TASK 导出；NO_PROGRESS last_failure |
| Fake MSSP | 全资产/非全资产/空数据/401/500/威胁实体/事件/XDR |
| E2E | SKILL_PLAN/AGENT_TURN/试跑/提升四条路径 |

### 10.15 关键流程

**从零创建**：

```text
用户意图 → agent SchedulePlan → 平台校验 → 业务问题/试跑
  → 用户确认 → Job(enabled) → 下次触发 → SKILL_PLAN/AGENT_TURN
```

**从成功对话提升**：

```text
成功 Run 的 execute(target=skill) 调用
  → 复用 skill/input/argv/platform
  → 绑定时间变量 → 试跑 → 确认 → Job
```

**执行**：

```text
Fire → SKILL_PLAN(no model) / AGENT_TURN(prompt+tools+time+session)
  → 结果/文件 → Artifact/Delivery → 任务记录/通道
```

### 10.16 备选方案与取舍

| 方案 | 取舍 |
|---|---|
| 保留 SKILL/AGENTIC 双模式，只修 argv/time | 用户仍要选 mode；开放性任务仍被裁剪；不采用 |
| 所有任务都 AGENT_TURN | 成本和不确定性高；固定日报不需要模型；不采用 |
| 只做 authoring 推断，不做试跑 | 错误计划会直接上线；不采用 |
| 合并 unified-artifact-delivery PRD | 范围/owner/迁移面不同；合并集成点即可 |
| wrapper skill | 沙箱只挂当前包、session/egress 绑定当前 skill；不采用 |

### 10.17 PRD 决策映射（定时任务）

| PRD | 设计结论 |
|---|---|
| FEAT-13 | Job = schedule + payload + session + delivery + policy；旧 mode 开发期迁移 |
| FEAT-14 | SchedulePlan + 平台校验 + 业务问题 + 黄金集 |
| FEAT-15 | 真实试跑 + promotion；失败不建 Job |
| FEAT-16 | SKILL_PLAN：input/argv/time 冻结，无模型 |
| FEAT-17 | AGENT_TURN：完整 prompt/工具/时间/session/静默 |
| FEAT-18 | isolated/current/session/main；session 与 delivery 分离 |
| FEAT-19 | tools_allow + PlatformSettings.agent 预算 |
| FEAT-20 | record_result(no_change)/上次结果/scratch/失败通知；用户可见文本直白 |
| FEAT-21 | TASK 导出 + NO_PROGRESS + Fake MSSP/本地 Skill |
| 非范围 | 系统提示词/平台 guide；文件投递存储/状态机 |

---

## 11. PRD 决策映射

| PRD | 设计结论 |
|---|---|
| OPEN-01 | B：平台确定性渲染；失败后不调用模型、不做可选前言 |
| OPEN-02 | 保留 `FAILED`，增加 `termination_reason` 和完成状态；不新增终态 |
| OPEN-03 | 结构化 `status/exit_code/error`；stdout/stderr 只作 preview，不扫描判定 |
| OPEN-04 | 不改预算默认值 |
| OPEN-05 | 失败收尾不进入模型历史；不做 protected suffix；普通压缩不变 |
| FEAT-01 | `outcome_parser` + `ExecutionOutcome` + skill-only `CONTENT` + 结构化 retryable |
| FEAT-02 | 统一 finalization funnel，覆盖所有终止出口；取消单独事件 |
| FEAT-03 | `FinalizationFacts`，区分 confirmed failure/confirmed none/not observed/unknown |
| FEAT-04 | 平台事实块；无模型收尾前言 |
| FEAT-05 | 删除 `_conclude` USER 指令；失败收尾不依赖压缩历史 |
| FEAT-06 | tool audit 业务字段 + finalization payload + failure evidence 不截断 |

---

*文档结束*
