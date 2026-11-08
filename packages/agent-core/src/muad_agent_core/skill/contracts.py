"""Skill 执行的公共值对象与 ``SkillExecutor`` 协议。

这个模块只依赖标准库与共享参数校验：Runtime/Worker 与真实 bwrap 验收探针都直接导入它，
避免因为平台 SDK/数据库驱动的导入副作用而把执行契约绑死在宿主进程里。
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from muad_contracts.skill_invocation import validate_skill_argv

if TYPE_CHECKING:  # pragma: no cover - 仅类型检查导入，运行时不加载平台 SDK
    from muad_platform_sdk.skill_access import SkillPlatformContext

#: stdout/stderr 单流上限（字节）；与 SandboxSpec 的默认上限同口径。
MAX_OUTPUT_BYTES = 64 * 1024
DEFAULT_OUTPUT_MAX_FILES = 100
DEFAULT_OUTPUT_MAX_FILE_BYTES = 16 * 1024 * 1024
DEFAULT_OUTPUT_MAX_TOTAL_BYTES = 64 * 1024 * 1024
class SkillExecutionStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"


class SkillExecutionError(RuntimeError):
    """执行器拒绝执行或沙箱不可用。``message`` 保留给工具层作为错误码/原因。

    ``detail`` 只在**平台非正常响应**（HTTP 非 2xx / 业务 code≠0）时携带可读原因，
    由适配器采集并擦除凭据值后逐层透传到工具结果；成功路径与其它失败恒为 None。
    """

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        detail: dict[str, object] | None = None,
        retryable: bool = False,
    ) -> None:
        self.message = message
        self.code = code or message
        self.detail = detail
        #: 只有**可重试**失败（网络/超时/5xx/429）才为 True；确定性失败第一次即终止 Run。
        self.retryable = retryable
        super().__init__(message)


class SkillOutputLimitError(SkillExecutionError):
    """输出目录超过文件数/单文件/总大小上限；``partial`` 是已扫描到的安全产物。"""

    def __init__(self, message: str, *, partial: tuple[OutputFile, ...] = ()) -> None:
        super().__init__(message, code="SANDBOX_OUTPUT_LIMIT")
        self.partial = partial


class SkillArtifactResolver(Protocol):
    async def ensure(self, *, artifact_id: str, storage_key: str, checksum: str) -> Path: ...


class SkillExecutor(Protocol):
    """唯一执行协议：Runtime/Worker 都只调用 ``execute(request)``。"""

    async def execute(self, request: SkillExecutionRequest) -> SkillExecutionResult: ...


class SkillExecutionObserver(Protocol):
    def __call__(self, event: SkillExecutionEvent) -> None: ...


@dataclass(frozen=True, slots=True)
class OutputLimits:
    max_files: int = DEFAULT_OUTPUT_MAX_FILES
    max_file_bytes: int = DEFAULT_OUTPUT_MAX_FILE_BYTES
    max_total_bytes: int = DEFAULT_OUTPUT_MAX_TOTAL_BYTES

    def __post_init__(self) -> None:
        for name in ("max_files", "max_file_bytes", "max_total_bytes"):
            if getattr(self, name) <= 0:
                raise ValueError(f"output limit {name} must be positive")


@dataclass(frozen=True, slots=True)
class OutputFile:
    """``SKILL_OUTPUT_DIR`` 内一个可交付文件的登记事实。"""

    relative_path: str
    size: int
    checksum: str
    path: Path


@dataclass(frozen=True, slots=True)
class SkillExecutionRequest:
    ready_dir: Path
    input: Mapping[str, Any]
    timeout_sec: float
    #: 服务端记录的唯一来源（SYSTEM/ADMIN/USER）；缺失/非法时执行器拒绝执行。
    source: str | None = None
    #: 绑定到沙箱 ``/workspace`` 的 user+Agent 目录；缺省时创建一次性临时目录。
    workspace_dir: Path | None = None
    #: 本次执行 id：``outputs/<execution_id>`` 与 ``SKILL_OUTPUT_DIR`` 的唯一后缀。
    execution_id: str | None = None
    env: Mapping[str, str] = field(default_factory=dict)
    cancel_event: asyncio.Event | None = None
    budget_exhausted_event: asyncio.Event | None = None
    script: Path | None = None
    platform_context: SkillPlatformContext | None = None
    output_limits: OutputLimits = field(default_factory=OutputLimits)
    #: Business CLI arguments appended after the selected script, never parsed by a shell.
    argv: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "argv", validate_skill_argv(self.argv))



@dataclass(frozen=True, slots=True)
class SkillExecutionResult:
    status: SkillExecutionStatus
    stdout: str = ""
    stderr: str = ""
    result: Mapping[str, Any] | None = None
    duration_ms: int = 0
    exit_code: int | None = None
    outputs: tuple[OutputFile, ...] = ()
    #: 产物被拒绝/截断时的可见标记（如 ``SANDBOX_OUTPUT_LIMIT``）；None 表示全部登记。
    output_notice: str | None = None
    #: 本次执行实际使用的 workspace（临时目录也会返回，调用方负责后续生命周期）。
    workspace_dir: Path | None = None


@dataclass(frozen=True, slots=True)
class SkillExecutionEvent:
    """结构化执行事件：审计/指标只消费这里，不解析 stdout。"""

    source: str
    status: SkillExecutionStatus
    duration_ms: int
    exit_code: int | None = None
    output_count: int = 0
    output_notice: str | None = None


def checksum_file(path: Path, *, chunk_bytes: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(chunk_bytes), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _within(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def collect_outputs(
    output_dir: Path | str | None,
    limits: OutputLimits | None = None,
) -> tuple[OutputFile, ...]:
    """扫描输出目录并登记相对路径/大小/checksum；不跟随 symlink、不越界。

    超过上限时抛 ``SkillOutputLimitError``（携带已扫描的安全产物），由调用方
    决定"整体拒绝"还是"部分登记 + 可见标记"。
    """
    if output_dir is None:
        return ()
    root = Path(output_dir)
    try:
        resolved_root = root.resolve(strict=True)
    except OSError:
        return ()
    if not resolved_root.is_dir():
        raise SkillExecutionError(f"skill output path is not a directory: {root}")
    effective = limits or OutputLimits()
    files: list[OutputFile] = []
    total = 0
    for current, directories, names in os.walk(resolved_root, followlinks=False):
        current_path = Path(current)
        # 显式剪掉 symlink 目录：os.walk 在 followlinks=False 下仍会列出它，但不该被遍历。
        directories[:] = sorted(
            name for name in directories if not (current_path / name).is_symlink()
        )
        for name in sorted(names):
            candidate = current_path / name
            if candidate.is_symlink():
                continue
            try:
                resolved = candidate.resolve(strict=True)
            except OSError:
                continue
            if not _within(resolved_root, resolved) or not resolved.is_file():
                continue
            size = resolved.stat().st_size
            if (
                len(files) >= effective.max_files
                or size > effective.max_file_bytes
                or total + size > effective.max_total_bytes
            ):
                raise SkillOutputLimitError(
                    f"skill outputs exceed limits under {resolved_root}",
                    partial=tuple(files),
                )
            total += size
            files.append(
                OutputFile(
                    relative_path=resolved.relative_to(resolved_root).as_posix(),
                    size=size,
                    checksum=checksum_file(resolved),
                    path=resolved,
                )
            )
    return tuple(files)


async def collect_outputs_async(
    output_dir: Path | str | None,
    limits: OutputLimits | None = None,
) -> tuple[OutputFile, ...]:
    return await asyncio.to_thread(collect_outputs, output_dir, limits)


__all__ = [
    "DEFAULT_OUTPUT_MAX_FILES",
    "DEFAULT_OUTPUT_MAX_FILE_BYTES",
    "DEFAULT_OUTPUT_MAX_TOTAL_BYTES",
    "MAX_OUTPUT_BYTES",
    "OutputFile",
    "OutputLimits",
    "SkillArtifactResolver",
    "SkillExecutionError",
    "SkillExecutionEvent",
    "SkillExecutionObserver",
    "SkillExecutionRequest",
    "SkillExecutionResult",
    "SkillExecutionStatus",
    "SkillExecutor",
    "SkillOutputLimitError",
    "checksum_file",
    "collect_outputs",
    "collect_outputs_async",
]
