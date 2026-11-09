"""Render one execution report to JSON/Markdown/CSV/ZIP.

Diagnostic fields stay raw. Only credential-shaped keys/strings are masked; token counts
such as ``input_tokens``/``output_tokens`` are diagnostics, not credentials.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

REDACTED = "***"

#: Credential keys, deliberately excluding diagnostic token-count keys.
_CREDENTIAL_KEY = re.compile(
    r"(^|_)(authorization|cookie|set_cookie|api_key|apikey|access_token|refresh_token|"
    r"id_token|secret|password|passwd|credential|private_key|client_secret|bot_secret|"
    r"mcp_secret|session_token|dsn|database_url|muad_chat_session|muad_chat_csrf)($|_)",
    re.IGNORECASE,
)
_CREDENTIAL_TEXT = re.compile(
    r"(?P<key>authorization|cookie|set-cookie|api[_-]?key|access[_-]?token|"
    r"refresh[_-]?token|id[_-]?token|secret|password|passwd|private[_-]?key|"
    r"client[_-]?secret|bot[_-]?secret|mcp[_-]?secret|session[_-]?token|dsn|database_url)"
    r"(?P<sep>\s*[:=]\s*)(?P<value>[^\s,;&'\"]+)",
    re.IGNORECASE,
)
_BEARER = re.compile(r"\b(?P<scheme>bearer|basic)\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)

ZIP_FILES = (
    "report.json",
    "report.md",
    "events.csv",
    "failures.csv",
    "calls.csv",
    "tasks.csv",
    "README.md",
)


@dataclass(frozen=True)
class RenderedExport:
    content: bytes
    media_type: str
    extension: str

    @property
    def filename(self) -> str:
        return f"execution-export.{self.extension}"


def _looks_credential_key(key: str) -> bool:
    lowered = key.lower()
    if lowered in {"input_tokens", "output_tokens", "max_tokens", "token_coverage"}:
        return False
    return bool(_CREDENTIAL_KEY.search(lowered))


def _masked_value(value: Any) -> str:
    if isinstance(value, str):
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
        return f"{REDACTED} (length={len(value)}, sha256={digest})"
    return REDACTED


def _mask_text(value: str) -> str:
    value = _BEARER.sub(lambda match: f"{match.group('scheme')} {REDACTED}", value)
    return _CREDENTIAL_TEXT.sub(lambda match: f"{match.group('key')}{match.group('sep')}{REDACTED}", value)


def mask_credentials(value: Any) -> Any:
    """Recursively mask credential keys and credential-looking substrings."""
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            text_key = str(key)
            if _looks_credential_key(text_key):
                result[text_key] = _masked_value(item)
            else:
                result[text_key] = mask_credentials(item)
        return result
    if isinstance(value, list):
        return [mask_credentials(item) for item in value]
    if isinstance(value, tuple):
        return [mask_credentials(item) for item in value]
    if isinstance(value, str):
        return _mask_text(value)
    return value


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def render_json(report: Mapping[str, Any]) -> bytes:
    masked = mask_credentials(dict(report))
    return (json.dumps(masked, ensure_ascii=False, indent=2, default=str) + "\n").encode("utf-8")


def _failure_rows(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    diagnosis = _mapping(report.get("diagnosis"))
    rows: list[dict[str, Any]] = []
    primary = diagnosis.get("primary_failure")
    if isinstance(primary, Mapping):
        rows.append({**dict(primary), "is_primary": True})
    for item in _list(diagnosis.get("contributing_failures")):
        if isinstance(item, Mapping):
            rows.append({**dict(item), "is_primary": False})
    return rows


def render_markdown(report: Mapping[str, Any]) -> str:
    report = mask_credentials(dict(report))
    execution = _mapping(report.get("execution"))
    diagnosis = _mapping(report.get("diagnosis"))
    summary = _mapping(report.get("summary"))
    log_lookup = _mapping(diagnosis.get("log_lookup"))
    lines = [
        "# 执行链路分析包",
        "",
        f"- 类型: {execution.get('kind')}",
        f"- ID: `{execution.get('id')}`",
        f"- 状态: {execution.get('status')} / {execution.get('outcome')}",
        f"- 错误码: {execution.get('error_code') or '-'}",
        f"- 错误信息: {execution.get('error_message') or '-'}",
        f"- Trace: {execution.get('trace_id') or '-'}",
        f"- 开始: {execution.get('start_time') or '-'}",
        f"- 结束: {execution.get('end_time') or '-'}",
        "",
        "## 失败定位",
        "",
    ]
    primary = diagnosis.get("primary_failure")
    if isinstance(primary, Mapping):
        lines.extend(
            [
                f"- 首个失败节点: {primary.get('node_type')} / seq={primary.get('seq') or '-'}",
                f"- 服务: {primary.get('service') or '-'}",
                f"- 错误码: {primary.get('error_code') or '-'}",
                f"- 依据: {primary.get('basis') or '-'}",
            ]
        )
    else:
        lines.append("- 未发现失败节点（成功执行不伪造失败）")
    lines.extend(["", "## 失败候选", "", "| 节点 | seq | 状态 | 错误码 | 服务 |", "|---|---|---|---|---|"])
    for item in _failure_rows(report):
        lines.append(
            f"| {item.get('node_type')} | {item.get('seq') or '-'} | {item.get('status') or '-'} | "
            f"{item.get('error_code') or '-'} | {item.get('service') or '-'} |"
        )
    lines.extend(["", "## 耗时", ""])
    timing = _mapping(summary.get("timing"))
    if timing:
        lines.append(f"- {timing}")
    slow = _list(summary.get("slowest_steps"))
    for item in slow:
        if isinstance(item, Mapping):
            lines.append(
                f"- 慢步骤 {item.get('node_type')}: {item.get('label')} / {item.get('duration_ms')}ms"
            )
    lines.extend(["", "## 日志定位", ""])
    keys = _mapping(log_lookup.get("keys"))
    for key, value in keys.items():
        lines.append(f"- {key}: `{value}`")
    for command in _list(log_lookup.get("commands")):
        lines.append(f"```bash\n{command}\n```")
    quality = _mapping(report.get("data_quality"))
    truncation = _mapping(report.get("truncation"))
    lines.extend(["", "## 数据范围", ""])
    lines.append(f"- 完整: {quality.get('complete')}")
    if truncation.get("truncated"):
        lines.append(f"- 已截断: {truncation}")
    warnings = _list(quality.get("warnings"))
    for warning in warnings:
        lines.append(f"- 不可读段: {warning}")
    return "\n".join(lines) + "\n"


def _csv(rows: list[dict[str, Any]], fields: list[str]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def render_events_csv(report: Mapping[str, Any]) -> str:
    report = mask_credentials(dict(report))
    rows = []
    for event in _list(report.get("timeline")):
        if not isinstance(event, Mapping):
            continue
        rows.append(
            {
                **dict(event),
                "payload": _json_text(event.get("payload")) if event.get("payload") is not None else "",
            }
        )
    return _csv(
        rows,
        [
            "seq",
            "event_type",
            "create_time",
            "elapsed_ms",
            "interval_ms",
            "status",
            "error_code",
            "service",
            "tool_call_id",
            "task_id",
            "operation_id",
            "text",
            "payload",
        ],
    )


def render_failures_csv(report: Mapping[str, Any]) -> str:
    report = mask_credentials(dict(report))
    return _csv(
        _failure_rows(report),
        [
            "node_type",
            "node_id",
            "seq",
            "occurred_at",
            "status",
            "error_code",
            "error_message",
            "service",
            "basis",
            "is_primary",
        ],
    )


def render_calls_csv(report: Mapping[str, Any]) -> str:
    report = mask_credentials(dict(report))
    rows: list[dict[str, Any]] = []
    for name in ("model_calls", "tool_calls", "egress_calls"):
        for item in _list(report.get(name)):
            if isinstance(item, Mapping):
                rows.append({"call_type": name, **dict(item)})
    return _csv(
        rows,
        [
            "call_type",
            "audit_id",
            "occurred_at",
            "provider",
            "model",
            "tool_name",
            "tool_call_id",
            "target",
            "status",
            "result_status",
            "error_code",
            "error_message",
            "latency_ms",
        ],
    )


def render_tasks_csv(report: Mapping[str, Any]) -> str:
    report = mask_credentials(dict(report))
    rows = []
    for task in _list(report.get("tasks")):
        if isinstance(task, Mapping):
            rows.append({**dict(task), "events": _json_text(task.get("events"))})
    return _csv(
        rows,
        [
            "task_id",
            "parent_id",
            "status",
            "error_code",
            "error_message",
            "attempt",
            "max_attempts",
            "intent_key",
            "skill_id",
            "source_run_id",
            "deadline_at",
            "started_at",
            "finished_at",
            "delivery_status",
            "events",
        ],
    )


def render_readme(report: Mapping[str, Any]) -> str:
    report = mask_credentials(dict(report))
    policy = _mapping(report.get("data_policy"))
    return (
        "# 执行链路分析包说明\n\n"
        "本包由 Console 的“导出链路”生成，用于单次执行失败/成功复盘。\n\n"
        f"- 诊断字段: {policy.get('diagnostic_fields')}\n"
        f"- 凭据字段: {policy.get('credentials')}\n"
        f"- 对话正文: {policy.get('conversation_text')}\n"
        f"- 仅管理员: {policy.get('admin_only')}\n\n"
        "report.json 是完整结构化数据；report.md 是人读摘要；"
        "events.csv/failures.csv/calls.csv/tasks.csv 供表格分析。\n"
        "Secret、Authorization、Cookie、api_key、access_token、password、私钥和 DSN 不会以原文出现。\n"
    )


def render_zip(report: Mapping[str, Any]) -> bytes:
    masked = mask_credentials(dict(report))
    payloads = {
        "report.json": render_json(masked),
        "report.md": render_markdown(masked).encode("utf-8"),
        "events.csv": render_events_csv(masked).encode("utf-8"),
        "failures.csv": render_failures_csv(masked).encode("utf-8"),
        "calls.csv": render_calls_csv(masked).encode("utf-8"),
        "tasks.csv": render_tasks_csv(masked).encode("utf-8"),
        "README.md": render_readme(masked).encode("utf-8"),
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in ZIP_FILES:
            archive.writestr(name, payloads[name])
    return buffer.getvalue()


def fit_report(report: Mapping[str, Any], max_bytes: int, *, max_events: int | None = None) -> dict[str, Any]:
    """Keep the package bounded while preserving the primary failure and a timeline window."""
    result = dict(report)
    timeline = _list(result.get("timeline"))
    limit = max_events or max(40, len(timeline) // 2)
    if len(timeline) > limit:
        primary = (result.get("diagnosis") or {}).get("primary_failure") or {}
        primary_seq = primary.get("seq") if isinstance(primary, Mapping) else None
        keep: list[Any] = timeline[:20]
        if primary_seq is not None:
            keep.extend(
                item for item in timeline if isinstance(item, Mapping) and item.get("seq") == primary_seq
            )
        keep.extend(timeline[-20:])
        deduped: list[Any] = []
        seen: set[Any] = set()
        for item in keep:
            marker = item.get("seq") if isinstance(item, Mapping) else id(item)
            if marker not in seen:
                seen.add(marker)
                deduped.append(item)
        result["timeline"] = deduped
        truncation = dict(result.get("truncation") or {})
        truncation.update(truncated=True, omitted_events=len(timeline) - len(deduped))
        result["truncation"] = truncation
    while len(render_json(result)) > max_bytes and isinstance(result.get("supplemental"), Mapping):
        supplemental = _mapping(result.get("supplemental"))
        if not supplemental:
            break
        supplemental.pop(next(iter(supplemental)))
        result["supplemental"] = supplemental
        truncation = dict(result.get("truncation") or {})
        truncation["truncated"] = True
        result["truncation"] = truncation
    return result


def render_report(report: Mapping[str, Any], store_format: str, *, max_bytes: int) -> RenderedExport:
    fitted = fit_report(report, max_bytes)
    if store_format.upper() == "JSON":
        return RenderedExport(render_json(fitted), "application/json", "json")
    if store_format.upper() == "ZIP":
        return RenderedExport(render_zip(fitted), "application/zip", "zip")
    raise ValueError(f"unsupported execution export format: {store_format}")
