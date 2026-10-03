---
id: harness-log
description: Agent Harness 通用平台规则：log
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

- [RULE-log-001] 所有服务统一使用 logging-kit，仅配置 `LOG_DIR`（另读 `LOG_LEVEL`，缺省 `INFO`；非法级别直接 `raise ValueError` 而非静默回退；`console=True` 为默认值，会额外挂一个 stdout handler）；日志按 `service/YYYY-MM-DD.log` 输出 JSON，自动携带 `trace_id/request_id/tenant_id`，并对 Authorization/Cookie/api_key/access_token/refresh_token/secret/password 等敏感字段脱敏。

## Conventions

- **业务代码取 logger 的方式**：统一 stdlib `logging.getLogger(__name__)`。`configure_logging` 已把 handler 与脱敏滤镜装在 **root** logger 上，因此**不要**自建 handler/Formatter。`muad_logging.get_logger` **确实存在且已从 `__init__.py` 导出**（`packages/logging-kit/src/muad_logging/setup.py`、`__init__.py`），只是全仓 0 个调用点——**新增代码不得引入它**作为第二套入口；若要真正封堵，应在 `__init__.py` 移除该导出，而不是只在文档里禁止。
- **trace 关联字段口径唯一**：`trace_id/request_id/run_id/conversation_id/platform_user_id/agent_id/snapshot_id/skill_artifact_id/tool_call_id/task_id/schedule_id`（docs/09 §6.1 共 11 个）。未绑定的字段一律显式空串，**禁止省略或伪造**；`set_trace_context` 遇到未知字段名必须报错，不得静默漂移（`packages/api-kit/src/muad_api/context.py`）。
- **附加结构化字段的唯一通道是 `extra={...}`**：标准库把 `extra` 的每个键**直接挂成 record 属性**（`packages/logging-kit/src/muad_logging/extras.py` 的 `extra_fields()` 用「减去标准属性」把它取回来），formatter 与 RedactionFilter 都读它。全仓既有 14 处调用方（`scheduler/service.py` 的 `schedule_id`、`run_service.py` 的 `run_id`、`memory_tools.py` 的 `memory_key`、`metrics.py` 的 `metric`…）都走这条。
  - ⚠️ **输出与脱敏是同一条通道的两半，必须一起改**：2026-10-03 之前两处都只认 `record.fields`（**全仓 0 个调用方**）⇒ 这 14 处的字段静默消失，而脱敏边界悬空 —— 谁单独把 formatter 那半修好，未脱敏的 `extra` 就直接落盘。改这条通道前先看 `tests/test_logging_redaction.py::test_extra_fields_reach_the_formatter_and_are_redacted`（一条用例同时钉两半）。
  - ❌ 新写 `record.fields = {...}` / `extra={"fields": {...}}`：那是已废弃的第二套入口。
- **脱敏是双通道**：文本 KV（正则键名）与 `extra` 结构化字段两条路径都要过滤，且覆盖 `bearer`/`basic` 凭据前缀（`packages/logging-kit/src/muad_logging/redaction.py`）。
- **脱敏占位符与键表以 logging-kit 为准**：占位符固定 `REDACTED = "***"`；实际键表**比本规范列举的 7 键更宽**——`authorization|cookie|set-cookie|api[_-]?key|access[_-]?token|refresh[_-]?token|id[_-]?token|secret|password|passwd|credential|private[_-]?key`（`packages/logging-kit/src/muad_logging/redaction.py`）。规范列的是最低清单，不是全集；新增敏感键时改代码即可，不必反向裁剪正则。
- **双通道的实现边界（决定哪些日志真会被脱敏）**：`RedactionFilter.filter` 作用于两处 —— ① `record.getMessage()`，命中时把 `record.args` 清空；② `extra={...}` 挂上来的每个 record 属性，**键名命中敏感词就整个置换为 `***`**（不是只处理值），字符串值另过一遍 `redact_text`。
  - ✅ `logger.info("api_key=%s", key)` → 先 `getMessage()` 拼出完整串再按正则脱敏，能被命中。
  - ✅ `logger.info("scheduler_tick", extra={"api_key": key})` → 键名命中 ⇒ `"api_key": "***"`。
  - ❌ 值本身是密钥但键名不含敏感词（如 `logger.info("value=%s", key)` 或 `extra={"blob": key}`）→ 不脱敏；此类只能靠调用方不得记录。
- **JSON 保留键固定集**：`{"timestamp","service","level","logger","message","exception"}`（`packages/logging-kit/src/muad_logging/extras.py` 的 `RESERVED_KEYS`）——log context 与 `extra` 都**不得覆写**这六个键（实现侧直接跳过同名键）。序列化固定 `separators=(",", ":")` + `ensure_ascii=False`。
- **两处时间口径不同，排查时各查一次**：日志文件名按**本地时区**日期滚动（`DailyServiceFileHandler._today()` 用 `datetime.now().astimezone()`，`handler.py`），JSON 里的 `timestamp` 用 **UTC** ISO8601（`formatter.py` 用 `datetime.now(UTC).isoformat()`）。
  - ✅ 跨时区排查：先按本地日期定位文件名，再按 UTC 解析行内 `timestamp`。
  - ❌ 假设两者同一时区 —— UTC+8 下 08:00 前的日志会落进「前一天」的那个文件里。
- **api-kit 中间件是 api-kit 与 logging-kit 两套 contextvar 的唯一桥**：`packages/api-kit/src/muad_api/middleware.py` 在请求入口一次性 `set_log_context(locale=…, tenant_id=…, caller_service=…, **trace_correlation_fields())`，在 `finally` 里 `clear_log_context()`；全仓再无第二处调用点。
  - ✅ 关联字段只在中间件绑一次，业务代码只读。
  - ❌ 在业务代码里手工 `set_log_context` ⇒ 与中间件双写、`clear` 时机错位，日志里出现漂移的 tenant/trace。

## Avoid

- 违反上述任一规则的实现必须修复；与此 Spec 冲突的文档以本 Spec 与 `docs/` V1.4 为准。
