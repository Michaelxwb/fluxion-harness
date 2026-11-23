"""业务执行的结果契约（design §4.2）。

一次业务执行只在这里判定一次：适配器把结构化执行事实映射成 `ExecutionOutcome`，pipeline
只消费、不解析任意文本。**只有 `target=skill` 的业务执行**会产生 `CONTENT` 失败；
`workspace_shell` / `skill_draft_shell` 的退出码只是结果证据。

模块保持纯函数、无 IO。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from muad_logging.redaction import redact_text

TOOL_FAILED_CODE = "TOOL_FAILED"
SKILL_FAILED_CODE = "SKILL_EXECUTION_FAILED"
#: 证据 preview 的默认上限：头尾各保留一段，尾部优先（失败原因通常在最后）。
DEFAULT_EVIDENCE_PREVIEW_BYTES = 1024
#: 属于"环境/平台"的失败前缀：这类失败再试也不会成功，Run 第一次即停。
ENVIRONMENT_CODE_PREFIXES = ("PLATFORM_", "CREDENTIAL_", "CONTROL_", "SANDBOX_", "MODEL_")
ARTICLE_MARKER = "…（中间省略）…"
#: 逐平台报告的一行摘要上限；详情只保留短原因，完整结构仍在工具错误载荷里。
PLATFORM_SUMMARY_LIMIT = 300


class BusinessStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"
    DENIED = "DENIED"
    #: 该调用不是业务执行（读取/诊断/草稿/workspace），或业务状态未知。
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNKNOWN = "UNKNOWN"


class FailureLayer(StrEnum):
    NONE = "NONE"
    TOOL = "TOOL"
    CONTENT = "CONTENT"


FAILURE_STATUSES = frozenset(
    (BusinessStatus.FAILED, BusinessStatus.TIMED_OUT, BusinessStatus.DENIED)
)
#: 不改变失败计数的业务状态：成功/不适用/未知都代表"这次调用没有确认失败"。
NON_FAILURE_STATUSES = frozenset(
    (BusinessStatus.SUCCEEDED, BusinessStatus.NOT_APPLICABLE, BusinessStatus.UNKNOWN)
)


@dataclass(frozen=True, slots=True)
class ExecutionEvidence:
    """一次调用的证据预览：有界、脱敏；原始 stdout/stderr 不进入审计表。"""

    preview: str = ""
    output_truncated: bool = False
    artifact_id: str | None = None


@dataclass(frozen=True, slots=True)
class ExecutionOutcome:
    call_id: str = ""
    tool_name: str = ""
    transport_status: str = "OK"
    business_status: BusinessStatus = BusinessStatus.NOT_APPLICABLE
    failure_layer: FailureLayer = FailureLayer.NONE
    error_code: str | None = None
    error_message: str = ""
    exit_code: int | None = None
    retryable: bool = False
    evidence: ExecutionEvidence = field(default_factory=ExecutionEvidence)

    @property
    def is_failure(self) -> bool:
        return self.business_status in FAILURE_STATUSES and self.failure_layer is not FailureLayer.NONE

    def with_identity(self, *, call_id: str, tool_name: str, transport_status: str) -> ExecutionOutcome:
        """pipeline 填充调用身份；解析器只负责业务判定。"""
        return ExecutionOutcome(
            call_id=call_id,
            tool_name=tool_name,
            transport_status=transport_status,
            business_status=self.business_status,
            failure_layer=self.failure_layer,
            error_code=self.error_code,
            error_message=self.error_message,
            exit_code=self.exit_code,
            retryable=self.retryable,
            evidence=self.evidence,
        )


def bounded_preview(
    text: str,
    *,
    limit_bytes: int = DEFAULT_EVIDENCE_PREVIEW_BYTES,
) -> str:
    """头尾保留的脱敏预览；截断标记可见，不切断多字节字符。"""
    if limit_bytes <= 0:
        raise ValueError("preview limit must be positive")
    redacted = redact_text(text).strip()
    data = redacted.encode("utf-8")
    if len(data) <= limit_bytes:
        return redacted
    head_bytes = limit_bytes // 3
    tail_bytes = limit_bytes - head_bytes
    head = data[:head_bytes].decode("utf-8", errors="ignore")
    tail = data[len(data) - tail_bytes :].decode("utf-8", errors="ignore")
    return f"{head}{ARTICLE_MARKER}{tail}"


def evidence_for(
    *texts: str,
    output_truncated: bool = False,
    artifact_id: str | None = None,
    limit_bytes: int = DEFAULT_EVIDENCE_PREVIEW_BYTES,
) -> ExecutionEvidence:
    """按给定顺序取第一段非空文本作为预览（失败优先 stderr，其次 stdout）。"""
    preview = ""
    for text in texts:
        if isinstance(text, str) and text.strip():
            preview = bounded_preview(text, limit_bytes=limit_bytes)
            break
    return ExecutionEvidence(
        preview=preview,
        output_truncated=output_truncated,
        artifact_id=artifact_id,
    )


def _retryable_for(code: str, retryable: bool | None) -> bool:
    if retryable is not None:
        return retryable
    return not code.startswith(ENVIRONMENT_CODE_PREFIXES)


def platform_sessions_summary(payload: object) -> str:
    """把工具错误载荷里的逐平台报告压成一行有界摘要。

    报告已经由平台层脱敏；这里只读取安全投影字段，供现有 `evidence_preview` 展示。
    不是逐平台报告时返回空串。
    """
    if not isinstance(payload, Mapping):
        return ""
    platforms = payload.get("platforms")
    if not isinstance(platforms, list):
        return ""
    parts: list[str] = []
    for item in platforms:
        if not isinstance(item, Mapping):
            continue
        key = str(item.get("key") or "")
        available = item.get("available")
        if available is True:
            parts.append(f"{key}=ok")
            continue
        if available is False:
            error = item.get("error")
            err = error if isinstance(error, Mapping) else {}
            detail = err.get("detail")
            det = detail if isinstance(detail, Mapping) else {}
            status = det.get("http_status")
            message = det.get("message")
            suffix = f"(http={status})" if status is not None else ""
            if isinstance(message, str) and message:
                suffix += f" {message[:80]}"
            parts.append(f"{key}={err.get('code') or 'ERROR'}{suffix}")
            continue
        reason = item.get("reason")
        parts.append(f"{key}=not_checked" + (f"({reason})" if reason else ""))
    if not parts:
        return ""
    return ("platform_sessions: " + "; ".join(parts))[:PLATFORM_SUMMARY_LIMIT]


def tool_exception_outcome(
    *,
    code: str | None,
    message: str,
    retryable: bool | None = None,
    evidence_text: str | None = None,
) -> ExecutionOutcome:
    """处理器宣告的失败 → TOOL 失败；错误码沿用异常自带的 `.code`（缺失为内部错误）。

    `retryable=None` 表示异常未声明：按既有策略（平台/环境前缀确定性、其余可纠正）。
    `evidence_text` 允许宿主把已脱敏的附加事实（如逐平台报告）并入证据预览。
    """
    resolved = code or "COMMON_INTERNAL_ERROR"
    return ExecutionOutcome(
        transport_status="ERROR",
        business_status=BusinessStatus.FAILED,
        failure_layer=FailureLayer.TOOL,
        error_code=resolved,
        error_message=message,
        retryable=_retryable_for(resolved, retryable),
        evidence=evidence_for(evidence_text or message),
    )


def policy_denied_outcome(code: str, message: str) -> ExecutionOutcome:
    return ExecutionOutcome(
        transport_status="BLOCKED",
        business_status=BusinessStatus.DENIED,
        failure_layer=FailureLayer.TOOL,
        error_code=code,
        error_message=message,
        retryable=_retryable_for(code, None),
        evidence=evidence_for(message),
    )


def not_applicable_outcome(*, transport_status: str = "OK") -> ExecutionOutcome:
    return ExecutionOutcome(
        transport_status=transport_status,
        business_status=BusinessStatus.NOT_APPLICABLE,
    )


def shell_outcome(
    *,
    stdout: str = "",
    stderr: str = "",
    exit_code: int | None = None,
    output_truncated: bool = False,
) -> ExecutionOutcome:
    """非业务执行（workspace/draft/诊断）只记录退出码与证据，不判定业务成败。

    入参是宿主持有的**类型化执行事实**，不是模型可见正文；不得再从 JSON 文本反推。
    """
    return ExecutionOutcome(
        business_status=BusinessStatus.NOT_APPLICABLE,
        failure_layer=FailureLayer.NONE,
        exit_code=exit_code,
        evidence=evidence_for(
            stderr,
            stdout,
            output_truncated=output_truncated,
        ),
    )


#: Skill 执行载荷的状态词表（与 `SkillExecutionStatus` 同值；此处不 import 以免成环）。
_SKILL_STATUSES = frozenset(("SUCCEEDED", "FAILED", "TIMED_OUT", "CANCELLED"))


def _last_json_object(text: str) -> dict[str, Any] | None:
    """stderr 的**最后一个** JSON 对象才是失败信封（前面可能是日志行）。"""
    for line in reversed([line.strip() for line in text.splitlines() if line.strip()]):
        try:
            payload = json.loads(line)
        except ValueError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def _failure_envelope(stderr: str) -> tuple[str | None, str, bool | None]:
    envelope = _last_json_object(stderr)
    if envelope is None:
        return None, "", None
    if envelope.get("ok") is not False and "error" not in envelope and "code" not in envelope:
        return None, "", None
    nested = envelope.get("error")
    source: Mapping[str, Any] = nested if isinstance(nested, Mapping) else envelope
    raw_code = source.get("code")
    code = raw_code if isinstance(raw_code, str) and raw_code else None
    message = source.get("message")
    retryable = source.get("retryable")
    return (
        code,
        message if isinstance(message, str) else "",
        retryable if isinstance(retryable, bool) else None,
    )


def skill_result_outcome(
    *,
    status: str,
    stdout: str = "",
    stderr: str = "",
    result: object = None,
    exit_code: int | None = None,
    output_truncated: bool = False,
) -> ExecutionOutcome:
    """`execute(target=skill)` 的**类型化执行事实** → `ExecutionOutcome`。

    只消费执行器已经给出的 `status/exit_code/result` 与脚本协议要求的 stderr 失败信封；
    stdout 只作证据。
    """
    if status == "CANCELLED":
        return ExecutionOutcome(
            business_status=BusinessStatus.CANCELLED,
            failure_layer=FailureLayer.NONE,
            exit_code=exit_code,
            evidence=evidence_for(stderr, stdout, output_truncated=output_truncated),
        )
    if status in ("FAILED", "TIMED_OUT"):
        code, message, envelope_retryable = _failure_envelope(stderr)
        retryable = True if status == "TIMED_OUT" else envelope_retryable is True
        return ExecutionOutcome(
            business_status=(
                BusinessStatus.TIMED_OUT if status == "TIMED_OUT" else BusinessStatus.FAILED
            ),
            failure_layer=FailureLayer.CONTENT,
            error_code=code or SKILL_FAILED_CODE,
            error_message=message or (stderr.strip()[:500] if stderr.strip() else ""),
            exit_code=exit_code,
            retryable=retryable,
            evidence=evidence_for(
                stderr,
                stdout,
                output_truncated=output_truncated,
            ),
        )

    # SUCCEEDED：只有结构化结果才能确认业务成功；纯文本/缺失按 UNKNOWN（只作证据）。
    if not isinstance(result, Mapping) or result.get("ok") is False or "error" in result:
        return ExecutionOutcome(
            business_status=BusinessStatus.UNKNOWN,
            failure_layer=FailureLayer.NONE,
            exit_code=exit_code,
            evidence=evidence_for(stdout, output_truncated=output_truncated),
        )
    return ExecutionOutcome(
        business_status=BusinessStatus.SUCCEEDED,
        failure_layer=FailureLayer.NONE,
        exit_code=exit_code,
        # 成功调用不落正文预览：证据只服务失败/诊断（design §4.7）。
        evidence=ExecutionEvidence(output_truncated=output_truncated),
    )


def skill_execution_outcome(payload: Mapping[str, Any]) -> ExecutionOutcome | None:
    """Worker 执行信封（已解析的 Mapping）→ `ExecutionOutcome`。

    只认带 Skill 状态词的执行载荷；draft/workspace 载荷返回 `None`。这是 Worker 任务路径
    消费执行器结构的事实，不是对模型可见正文的推断。
    """
    if not isinstance(payload, Mapping):
        return None
    status = payload.get("status")
    if not isinstance(status, str) or status not in _SKILL_STATUSES:
        return None
    stdout = payload.get("stdout")
    stderr = payload.get("stderr")
    exit_code = payload.get("exit_code")
    return skill_result_outcome(
        status=status,
        stdout=stdout if isinstance(stdout, str) else "",
        stderr=stderr if isinstance(stderr, str) else "",
        result=payload.get("result"),
        exit_code=exit_code if isinstance(exit_code, int) and not isinstance(exit_code, bool) else None,
        output_truncated=payload.get("output_truncated") is True,
    )
