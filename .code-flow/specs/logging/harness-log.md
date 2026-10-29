---
id: harness-log
description: 日志、trace 与脱敏
stages:
- design
- plan
- code
- review
enforcement: required
verifiers:
- rule: RULE-log-001
  type: command
  config:
    argv:
    - uv
    - run
    - pytest
    - -q
    - tests/test_logging.py
    - tests/test_logging_redaction.py
    - tests/acceptance/test_foundation_logging.py
    cwd: .
    timeout: 300
---

# harness-log

## Rules

- [RULE-log-001] 所有服务统一使用 logging-kit，仅配置 `LOG_DIR`（另读 `LOG_LEVEL`，缺省 `INFO`；非法级别直接 `raise ValueError` 而非静默回退；`console=True` 为默认值，会额外挂一个 stdout handler）；日志按 `service/YYYY-MM-DD.log` 输出**单行文本**：`YYYY-MM-DD HH:mm:ss.SSS ±ZZZZ [LEVEL] service logger message | key=value ...`，自动携带 `trace_id/request_id/tenant_id` 等关联字段（空串不打印），并对 Authorization/Cookie/api_key/access_token/refresh_token/secret/password 等敏感字段脱敏。

## Conventions

- **记忆内容在审计边界使用安全投影**：remember的value/source_quote/完整候选/prompt不进入tool_call_audit；Recorder在通用预览之前仅生成memory_key/category/args_hash/status，RuntimeAuditWriter再按同一投影兜底，并以当次内存凭据canary遮蔽非法key。schema失败回执只报告安全错误，不序列化validator原实例。✅ hash与固定状态/合法key；❌ 因字段名是value而把用户内容写入审计，或把ValidationError正文写入结果。
- **记忆成功指标跟随真实外层提交**：session事务收集低基数计数，嵌套提交只移交父事务，rollback丢弃对应样本；最外层commit后才发布写入/确认/清扫/淘汰成功。失败日志只用extra的ID/错误类型，ERROR即时计数，不要求不可写DB落额外失败事件。✅ 原子效果与成功计数同一提交事实；❌ execute_in_session刚flush便计成功、随后历史/事件/CAS失败却保留成功指标。
- **记忆指标目录无流量也声明**：confirmation/consolidation_enqueue/consolidation/quota_evictions/expiry/write_conflicts均由Runtime catalog声明；memory_observability仅允许固定outcome/reason值域，不接受user/key/UUID等label。✅ 数量是counter增量；❌ 把具体key、用户或自由错误文本作为标签。

- **业务代码取 logger 的方式**：统一 stdlib `logging.getLogger(__name__)`。`configure_logging` 已把 handler 与脱敏滤镜装在 **root** logger 上，因此**不要**自建 handler/Formatter。`muad_logging.get_logger` **确实存在且已从 `__init__.py` 导出**（`packages/logging-kit/src/muad_logging/setup.py`、`__init__.py`），现网仅 `overview_query_service.py` 一处使用——**新增代码不得引入它**作为第二套入口；若要真正封堵，应在 `__init__.py` 移除该导出，而不是只在文档里禁止。
- **trace 关联字段口径唯一**：`trace_id/request_id/run_id/conversation_id/platform_user_id/agent_id/snapshot_id/skill_artifact_id/tool_call_id/task_id/schedule_id`（docs/09 §6.1 共 11 个）。未绑定的字段一律显式空串，**禁止省略或伪造**；`set_trace_context` 遇到未知字段名必须报错，不得静默漂移（`packages/api-kit/src/muad_api/context.py`）。formatter 输出时跳过空串，避免日志行被空字段刷屏。
- **附加结构化字段的唯一通道是 `extra={...}`**：标准库把 `extra` 的每个键**直接挂成 record 属性**（`packages/logging-kit/src/muad_logging/extras.py` 的 `extra_fields()` 用「减去标准属性」把它取回来），formatter 与 RedactionFilter 都读它。全仓生产代码已有多处调用方（`scheduler/service.py` 的 `schedule_id`、`run_service.py` 的 `run_id`、`memory_tools.py` 的 `memory_key`、`metrics.py` 的 `metric`…）都走这条。
  - ⚠️ **输出与脱敏是同一条通道的两半，必须一起改**：2026-10-03 之前两处都只认 `record.fields`（**全仓 0 个调用方**）⇒ 这些字段静默消失，而脱敏边界悬空 —— 谁单独把 formatter 那半修好，未脱敏的 `extra` 就直接落盘。改这条通道前先看 `tests/test_logging_redaction.py::test_extra_fields_reach_the_formatter_and_are_redacted`（一条用例同时钉两半）。
  - ❌ 新写 `record.fields = {...}` / `extra={"fields": {...}}`：那是已废弃的第二套入口。
- **脱敏是双通道**：文本 KV（正则键名）与 `extra` 结构化字段两条路径都要过滤，且覆盖 `bearer`/`basic` 凭据前缀（`packages/logging-kit/src/muad_logging/redaction.py`）。
- **脱敏占位符与键表以 logging-kit 为准**：占位符固定 `REDACTED = "***"`；实际键表**比本规范列举的更宽**——`authorization|cookie|set-cookie|api[_-]?key|access[_-]?token|refresh[_-]?token|id[_-]?token|secret|password|passwd|credential|private[_-]?key|token|muad[_-]?chat[_-]?session|muad[_-]?chat[_-]?csrf`（`packages/logging-kit/src/muad_logging/redaction.py`）。规范列的是最低清单，不是全集；新增敏感键时改代码即可，不必反向裁剪正则。
- **双通道的实现边界（决定哪些日志真会被脱敏）**：`RedactionFilter.filter` 作用于两处 —— ① `record.getMessage()`，命中时把 `record.args` 清空；② `extra={...}` 挂上来的每个 record 属性，**键名命中敏感词就整个置换为 `***`**（不是只处理值），字符串值另过一遍 `redact_text`。
  - ✅ `logger.info("api_key=%s", key)` → 先 `getMessage()` 拼出完整串再按正则脱敏，能被命中。
  - ✅ `logger.info("scheduler_tick", extra={"api_key": key})` → 键名命中 ⇒ `"api_key": "***"`。
  - ❌ 值本身是密钥但键名不含敏感词（如 `logger.info("value=%s", key)` 或 `extra={"blob": key}`）→ 不脱敏；此类只能靠调用方不得记录。
- **文本保留键固定集**：`{"timestamp","service","level","logger","message","exception"}`（`packages/logging-kit/src/muad_logging/extras.py` 的 `RESERVED_KEYS`）——log context 与 `extra` 都**不得覆写**这六个键（实现侧直接跳过同名键）。整行**不是 JSON**；消息后的字段是 `key=value`，含空白/竖线/引号的字符串加双引号，嵌套容器用紧凑 JSON 值，空值不打印。
- **时间口径统一为本地时区**：日志文件名按本地日期滚动（`DailyServiceFileHandler._today()`），行内时间也用本地时区并带 `±ZZZZ` 偏移（`TextLogFormatter._timestamp` 基于 `record.created`）；排查时文件名日期与行内日期同属一个时区。
  - ✅ 跨时区排查：先按本地日期定位文件名，再按 UTC 解析行内 `timestamp`。
  - ❌ 假设两者同一时区 —— UTC+8 下 08:00 前的日志会落进「前一天」的那个文件里。
- **api-kit `context_scope` 是 API 与 logging-kit contextvar 的唯一桥**：HTTP 中间件、后台 Run 接续与工具控制发件边界都调用 `packages/api-kit/src/muad_api/context.py` 的 scope；退出时用各自 token 恢复进入前的值，嵌套与并发互不覆盖。后台只绑定已持久化的关联事实，缺少的 request/call/task 等字段显式空串。
  - ✅ `with context_scope(tenant_id=tenant, trace_id=run.trace_id, run_id=str(run.id)):`；边界内取下游头和 logger，退出恢复外层上下文。
  - ❌ 在业务代码里手工 `set_log_context`，或退出时全局 `clear_log_context()` ⇒ 两套 context 漂移，嵌套外层上下文丢失。

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
