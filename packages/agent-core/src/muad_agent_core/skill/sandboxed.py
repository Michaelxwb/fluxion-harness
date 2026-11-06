"""统一沙箱执行器：只读包、独立 workspace、有界输出与产物登记。

所有 Skill 来源（SYSTEM/ADMIN/USER）都走这里。执行器本身不写 READY 缓存，
只把已校验的包目录只读挂到 ``/skill``，把本次执行的 workspace 可写挂到
``/workspace``，并从 ``SKILL_OUTPUT_DIR`` 收集有界产物。
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from muad_skill_sdk.skill_package import SkillPackageError, locate_package_root

from ..sandbox import (
    SandboxCancelled,
    SandboxError,
    SandboxOutputLimit,
    SandboxPolicy,
    SandboxResult,
    SandboxRunner,
    SandboxSpec,
    SandboxTimeout,
    SandboxUnavailable,
    build_sandbox_runner_from_settings,
)
from .contracts import (
    MAX_OUTPUT_BYTES,
    OutputFile,
    OutputLimits,
    SkillExecutionError,
    SkillExecutionEvent,
    SkillExecutionObserver,
    SkillExecutionRequest,
    SkillExecutionResult,
    SkillExecutionStatus,
    SkillOutputLimitError,
    collect_outputs_async,
)
from .executor import parse_result
from .selection import SCRIPTS_DIR, SKILL_FILE, sandbox_script_path, script_command, select_script

logger = logging.getLogger("muad_agent_core.skill.sandbox")

VALID_SOURCES = frozenset({"SYSTEM", "ADMIN", "USER"})
_SAFE_COMPONENT_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_WORKSPACE_DATA = "data"
_WORKSPACE_OUTPUTS = "outputs"
_OUTPUT_LIMIT_NOTICE = "SANDBOX_OUTPUT_LIMIT"


class SandboxedSkillExecutor:
    """``SkillExecutor`` 的沙箱实现；runner 由装配侧显式提供或按 settings 构造。"""

    def __init__(
        self,
        *,
        runner: SandboxRunner | None = None,
        runner_factory: Callable[[], SandboxRunner] | None = None,
        dev_unsafe: bool = False,
        output_limits: OutputLimits | None = None,
        stdout_limit_bytes: int = MAX_OUTPUT_BYTES,
        observer: SkillExecutionObserver | None = None,
    ) -> None:
        if runner is not None and runner_factory is not None:
            raise ValueError("pass either runner or runner_factory, not both")
        if stdout_limit_bytes <= 0:
            raise ValueError("stdout_limit_bytes must be positive")
        self._runner = runner
        self._runner_factory = runner_factory
        self._dev_unsafe = dev_unsafe
        self._output_limits = output_limits or OutputLimits()
        self._stdout_limit_bytes = stdout_limit_bytes
        self._observer = observer

    async def execute(self, request: SkillExecutionRequest) -> SkillExecutionResult:
        source = validate_source(request.source)
        package_root = resolve_package_root(request.ready_dir)
        script = select_script(package_root, request.script)
        workspace, output_dir, execution_id = _prepare_workspace(request, package_root)
        try:
            runner = self._require_runner()
        except SandboxUnavailable as exc:
            raise SkillExecutionError(str(exc), code="SANDBOX_UNAVAILABLE") from exc
        dev_unsafe = getattr(runner, "name", "") == "dev-unsafe"
        host_path = os.environ.get("PATH", os.defpath)
        command = script_command(
            script if dev_unsafe else sandbox_script_path(package_root, script),
            {"PATH": host_path if dev_unsafe else "/opt/python3.12/bin:/usr/local/bin:/usr/bin:/bin"},
        )
        data_dir = workspace / _WORKSPACE_DATA
        env = {
            **dict(request.env),
            "SKILL_DATA_DIR": str(data_dir) if dev_unsafe else f"/workspace/{_WORKSPACE_DATA}",
            "SKILL_OUTPUT_DIR": str(output_dir)
            if dev_unsafe
            else f"/workspace/{_WORKSPACE_OUTPUTS}/{execution_id}",
        }
        spec = SandboxSpec(
            policy=SandboxPolicy.for_source(source, dev_unsafe=self._dev_unsafe),
            command=command,
            workspace_dir=workspace,
            package_dir=package_root,
            env=env,
            timeout_sec=request.timeout_sec,
            output_limit_bytes=self._stdout_limit_bytes,
            cancel_event=request.cancel_event,
            stdin=json.dumps(dict(request.input), ensure_ascii=False).encode("utf-8"),
            cwd=str(workspace) if dev_unsafe else "/skill",
            name=f"skill:{source}:{execution_id}",
        )
        started = time.monotonic()
        try:
            sandbox_result = await runner.run(spec)
            status = (
                SkillExecutionStatus.SUCCEEDED
                if sandbox_result.exit_code == 0
                else SkillExecutionStatus.FAILED
            )
            output_notice = None
        except SandboxOutputLimit as exc:
            sandbox_result = exc.result or SandboxResult(
                exit_code=None, stdout="", stderr="", duration_ms=0, output_truncated=True
            )
            status = SkillExecutionStatus.FAILED
            output_notice = _OUTPUT_LIMIT_NOTICE
        except SandboxTimeout as exc:
            sandbox_result = exc.result or SandboxResult(
                exit_code=None, stdout="", stderr="", duration_ms=0
            )
            status = SkillExecutionStatus.TIMED_OUT
            output_notice = None
        except SandboxCancelled as exc:
            sandbox_result = exc.result or SandboxResult(
                exit_code=None, stdout="", stderr="", duration_ms=0
            )
            status = SkillExecutionStatus.CANCELLED
            output_notice = None
        except SandboxUnavailable as exc:
            self._emit(SkillExecutionEvent(
                source=source,
                status=SkillExecutionStatus.FAILED,
                duration_ms=int((time.monotonic() - started) * 1000),
            ))
            raise SkillExecutionError(str(exc), code="SANDBOX_UNAVAILABLE") from exc
        except SandboxError as exc:
            sandbox_result = exc.result or SandboxResult(
                exit_code=None, stdout="", stderr=str(exc), duration_ms=0
            )
            status = SkillExecutionStatus.FAILED
            output_notice = None

        outputs: tuple[OutputFile, ...] = ()
        try:
            outputs = await collect_outputs_async(output_dir, request.output_limits)
        except SkillOutputLimitError as exc:
            outputs = exc.partial
            output_notice = exc.code
        duration_ms = sandbox_result.duration_ms or int((time.monotonic() - started) * 1000)
        result = SkillExecutionResult(
            status=status,
            stdout=sandbox_result.stdout,
            stderr=sandbox_result.stderr,
            result=parse_result(sandbox_result.stdout)
            if status is SkillExecutionStatus.SUCCEEDED
            else None,
            duration_ms=duration_ms,
            exit_code=sandbox_result.exit_code,
            outputs=outputs,
            output_notice=output_notice,
            workspace_dir=workspace,
        )
        self._emit(
            SkillExecutionEvent(
                source=source,
                status=status,
                duration_ms=duration_ms,
                exit_code=sandbox_result.exit_code,
                output_count=len(outputs),
                output_notice=output_notice,
            )
        )
        return result

    async def collect_outputs(
        self, output_dir: Path | str | None, limits: OutputLimits | None = None
    ) -> tuple[OutputFile, ...]:
        return await collect_outputs_async(output_dir, limits or self._output_limits)

    def _require_runner(self) -> SandboxRunner:
        if self._runner is None:
            if self._runner_factory is None:
                raise SandboxUnavailable(
                    "sandbox runner is not configured; refusing to execute skill on the host"
                )
            self._runner = self._runner_factory()
        return self._runner

    def _emit(self, event: SkillExecutionEvent) -> None:
        logger.info(
            "skill_execution source=%s status=%s duration_ms=%s exit_code=%s outputs=%s notice=%s",
            event.source,
            event.status.value,
            event.duration_ms,
            event.exit_code,
            event.output_count,
            event.output_notice,
        )
        if self._observer is not None:
            self._observer(event)


def validate_source(source: str | None) -> str:
    normalized = str(source).strip().upper() if source is not None else ""
    if normalized not in VALID_SOURCES:
        raise SkillExecutionError(
            f"skill source is missing or invalid: {source!r}", code="SKILL_SOURCE_INVALID"
        )
    return normalized


def resolve_package_root(ready_dir: Path) -> Path:
    root = Path(ready_dir).resolve()
    if not root.is_dir():
        raise SkillExecutionError(f"skill ready directory is unavailable: {root}")
    if (root / SKILL_FILE).is_file() or (root / SCRIPTS_DIR).is_dir():
        return root
    try:
        located, _ = locate_package_root(root)
    except SkillPackageError as exc:
        raise SkillExecutionError(exc.message, code="SKILL_PACKAGE_INVALID") from exc
    return located.resolve()


def _safe_component(value: str, label: str) -> str:
    text = str(value)
    if not text or text in {".", ".."} or not _SAFE_COMPONENT_RE.fullmatch(text):
        raise SkillExecutionError(
            f"invalid skill execution {label}: {value!r}", code="SKILL_EXECUTION_INVALID"
        )
    return text


def _prepare_workspace(
    request: SkillExecutionRequest, package_root: Path
) -> tuple[Path, Path, str]:
    execution_id = _safe_component(request.execution_id or uuid.uuid4().hex, "execution_id")
    if request.workspace_dir is None:
        workspace = Path(tempfile.mkdtemp(prefix="muad-skill-workspace-")).resolve()
    else:
        workspace = Path(request.workspace_dir).resolve()
    if workspace == package_root or package_root in workspace.parents:
        raise SkillExecutionError(
            "skill workspace must not be inside the read-only skill package",
            code="SKILL_EXECUTION_INVALID",
        )
    data_dir = workspace / _WORKSPACE_DATA
    output_dir = workspace / _WORKSPACE_OUTPUTS / execution_id
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SkillExecutionError(
            f"skill workspace is unavailable: {workspace}", code="WORKSPACE_UNAVAILABLE"
        ) from exc
    return workspace, output_dir, execution_id


def build_sandboxed_skill_executor(
    settings: object,
    *,
    observer: SkillExecutionObserver | None = None,
) -> SandboxedSkillExecutor:
    """按启动配置装配（Runtime/Worker 共用）；runner 在首次执行时才探测 bwrap。"""
    output_limits = OutputLimits(
        max_files=int(getattr(settings, "skill_output_max_files", 100)),
        max_file_bytes=int(getattr(settings, "skill_output_max_file_mib", 16)) * 1024 * 1024,
        max_total_bytes=int(getattr(settings, "skill_output_max_total_mib", 64)) * 1024 * 1024,
    )
    return SandboxedSkillExecutor(
        runner_factory=lambda: build_sandbox_runner_from_settings(settings),
        dev_unsafe=str(getattr(settings, "sandbox_mode", "bwrap")) == "dev-unsafe",
        output_limits=output_limits,
        stdout_limit_bytes=int(getattr(settings, "sandbox_output_limit_bytes", MAX_OUTPUT_BYTES)),
        observer=observer,
    )


__all__ = [
    "SandboxedSkillExecutor",
    "VALID_SOURCES",
    "build_sandboxed_skill_executor",
    "resolve_package_root",
    "validate_source",
]
