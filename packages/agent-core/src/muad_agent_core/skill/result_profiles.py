"""Host interpretation of the verified default-entrypoint result protocols."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import replace
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, Literal

from muad_contracts.skill_results import SkillBusinessConclusion, SkillCallStatus, SkillResultProfile
from muad_logging.redaction import redact_text
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter, ValidationError

from .contracts import (
    SkillExecutionError,
    SkillExecutionRequest,
    SkillExecutionResult,
    SkillExecutionStatus,
    SkillReviewTarget,
)

REVIEW_ROOT = "/tmp/muad-skill-review"
REVIEW_TARGET = re.compile(r"skill-drafts/([A-Za-z0-9][A-Za-z0-9_.-]{0,127})")
RESULT_INVALID = "SKILL_RESULT_INVALID"


def review_draft_key(argv: tuple[str, ...]) -> str:
    if (
        len(argv) not in (2, 4)
        or argv[0] != "--target"
        or (len(argv) == 4 and argv[2:] != ("--format", "json"))
    ):
        raise SkillExecutionError("Use --target skill-drafts/<key> [--format json].", code=RESULT_INVALID)
    match = REVIEW_TARGET.fullmatch(argv[1])
    if match is None or ".." in match[1]:
        raise SkillExecutionError("A workspace-relative Skill draft is required.", code=RESULT_INVALID)
    return match[1]


def review_target_fingerprint(root: Path) -> tuple[str, int]:
    """Match the v1 checker's fingerprint over the same frozen view."""
    files: list[Path] = []
    for current, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d != "__pycache__"]
        files.extend(Path(current) / name for name in names if not name.startswith("."))
    digest = hashlib.sha256()
    for path in sorted(files, key=lambda item: item.relative_to(root).as_posix()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest(), len(files)


class _ReviewFinding(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    rule: str = Field(min_length=1, max_length=128)
    severity: str
    file: str = Field(min_length=1, max_length=1024)
    line: int = Field(ge=1)
    message: str = Field(min_length=1, max_length=8192)


class _ReviewSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    errors: int = Field(ge=0)
    warnings: int = Field(ge=0)
    files: int = Field(ge=0)
    fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class _ReviewReport(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    target: str
    summary: _ReviewSummary
    findings: list[_ReviewFinding]


class _CustomerOption(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9_.:-]{1,128}$")]
    name: str = Field(min_length=1, max_length=128)


class _CustomerSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    status: Literal["NEEDS_INPUT"]
    candidates: list[_CustomerOption] = Field(min_length=2, max_length=30)


class _DeviceResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    status: Literal["NO_DEVICES", "SUCCEEDED"]
    customer_id: str = Field(min_length=1, max_length=128)
    devices: list[dict[str, Any]] = Field(max_length=1000)


_CUSTOMER_RESULT = TypeAdapter(_CustomerSelection | _DeviceResult)


def _verify_customer(stdout: str) -> tuple[dict[str, Any], SkillBusinessConclusion]:
    result = _CUSTOMER_RESULT.validate_python(json.loads(stdout, object_pairs_hook=_strict_object))
    if isinstance(result, _CustomerSelection):
        if len({candidate.id for candidate in result.candidates}) != len(result.candidates):
            raise ValueError("duplicate customer option")
        payload = {
            "status": result.status,
            "candidates": [
                {"id": candidate.id, "name": redact_text(candidate.name)} for candidate in result.candidates
            ],
        }
    else:
        if (result.status == "NO_DEVICES") != (not result.devices):
            raise ValueError("device count does not match the result")
        payload = result.model_dump(mode="json")
    return payload, SkillBusinessConclusion(result.status)


def _strict_object(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate object property")
        result[key] = value
    return result


def _safe_finding_path(value: str) -> bool:
    path = PurePosixPath(value)
    return (
        not path.is_absolute()
        and path.as_posix() == value
        and ".." not in path.parts
        and value != "."
        and "\\" not in value
        and "\x00" not in value
    )


def _verify_review(
    stdout: str, target: SkillReviewTarget, *, expected_path: str, exit_code: int
) -> tuple[dict[str, Any], SkillBusinessConclusion]:
    report = _ReviewReport.model_validate(json.loads(stdout, object_pairs_hook=_strict_object))
    summary = report.summary
    if report.target != expected_path or summary.fingerprint != target.fingerprint:
        raise ValueError("report identifies a different draft snapshot")
    if summary.files != target.files:
        raise ValueError("report file count differs from snapshot")
    errors = warnings = 0
    for finding in report.findings:
        if not _safe_finding_path(finding.file):
            raise ValueError("finding path is invalid")
        if finding.severity == "error":
            errors += 1
        elif finding.severity == "warning":
            warnings += 1
        else:
            raise ValueError("invalid finding severity")
    if (errors, warnings) != (summary.errors, summary.warnings) or (exit_code == 1) != (errors > 0):
        raise ValueError("findings do not match the exit code or summary")
    return {
        "summary": summary.model_dump(mode="json"),
        "findings": [
            {
                **item.model_dump(mode="json"),
                "file": redact_text(item.file),
                "message": redact_text(item.message),
            }
            for item in report.findings
        ],
        "reviewed_draft": {
            "draft_id": target.draft_id,
            "revision": target.revision,
            "fingerprint": target.fingerprint,
        },
    }, (SkillBusinessConclusion.REVIEW_FINDINGS if errors else SkillBusinessConclusion.REVIEW_PASSED)


def review_argv(request: SkillExecutionRequest, *, native: bool) -> tuple[str, ...]:
    if request.review_target is None:
        return request.argv
    root = request.review_target.root
    target = str(root.resolve()) if native else f"{REVIEW_ROOT}/{root.name}"
    return ("--target", target, "--format", "json")


def validate_result_profile(
    request: SkillExecutionRequest, result: SkillExecutionResult, *, native: bool
) -> SkillExecutionResult:
    contract = request.result_contract
    if contract is None:
        return result
    if result.platform_window_limited:
        return replace(result, execution_status=SkillCallStatus.RATE_LIMITED)
    if result.status in {SkillExecutionStatus.TIMED_OUT, SkillExecutionStatus.CANCELLED}:
        return replace(result, execution_status=SkillCallStatus(result.status.value))
    try:
        payload, conclusion = _verified_payload(request, result, contract.profile, native=native)
    except (ValueError, ValidationError, RecursionError):
        return replace(
            result,
            status=SkillExecutionStatus.FAILED,
            result=None,
            execution_status=SkillCallStatus.FAILED,
            result_error_code=RESULT_INVALID,
        )
    return replace(
        result,
        status=SkillExecutionStatus.SUCCEEDED,
        result=payload,
        stdout="",
        stderr="",
        execution_status=SkillCallStatus.COMPLETED,
        business_conclusion=conclusion,
    )


def _verified_payload(
    request: SkillExecutionRequest,
    result: SkillExecutionResult,
    profile: SkillResultProfile,
    *,
    native: bool,
) -> tuple[dict[str, Any], SkillBusinessConclusion]:
    if result.output_truncated:
        raise ValueError("incomplete result")
    if profile is SkillResultProfile.CUSTOMER_DEVICES:
        if result.exit_code != 0 or result.status is not SkillExecutionStatus.SUCCEEDED:
            raise ValueError("customer query did not complete")
        return _verify_customer(result.stdout)
    target = request.review_target
    if target is None or result.exit_code not in (0, 1):
        raise ValueError("review execution did not produce a complete report")
    return _verify_review(
        result.stdout,
        target,
        expected_path=review_argv(request, native=native)[1],
        exit_code=result.exit_code,
    )


def ensure_result_entrypoint(request: SkillExecutionRequest, *, root: Path, script: Path) -> None:
    contract = request.result_contract
    if contract is None:
        return
    if script.resolve().relative_to(root.resolve()).as_posix() != contract.entrypoint:
        raise SkillExecutionError("Skill entrypoint differs from the frozen contract", code=RESULT_INVALID)
    if contract.profile is SkillResultProfile.SKILL_REVIEW and request.review_target is None:
        raise SkillExecutionError("Review target snapshot is not available", code=RESULT_INVALID)
