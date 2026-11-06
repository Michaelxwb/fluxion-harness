"""草稿 Shell：Console 草稿 zip → 可写副本 → 沙箱 → 变更校验 → revision CAS 回写。

设计定位（backend design §3.2/§3.4 API-10）：

- Runtime ``skill_draft_shell`` 从 Console 取回**当前用户当前草稿**的 zip 与 revision；
- 在宿主临时目录解包成独立草稿副本，只把这个副本交给沙箱；不挂生产 PVC、其他草稿或
  READY 缓存；``data/`` 与 ``outputs/`` 与草稿目录分离，运行产物不参与回写；
- 草稿声明 ``platforms`` 时复用 TASK-004 的 ``skill_platform_environment`` 注入
  ``session-manager``；允许集合仍由 Console 按受信身份计算；
- 执行后只接受普通文件、白名单扩展名、单文件/总量/文件数上限内的变更；路径穿越、
  symlink、可执行位、超限文件显式拒绝；
- 合法变更以**抓取时 revision** 一次性 CAS 回写；冲突返回 ``DRAFT_REVISION_CONFLICT``。

这里不实现 HTTP，也不直接写 Console 数据库：调用方通过 ``DraftShellStore`` 注入取件/
回写边界，便于 Runtime 与验收使用同一套沙箱/校验逻辑。
"""

from __future__ import annotations

import hashlib
import io
import logging
import os
import shutil
import tempfile
import zipfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol

from muad_platform_sdk.adapter.mss_config import PlatformAuthError
from muad_platform_sdk.skill_access import SkillPlatformContext
from muad_platform_sdk.skill_bridge import SessionMount, skill_platform_environment
from muad_skill_sdk.skill_package import SkillPackage, SkillPackageError, locate_package_root
from redis.exceptions import RedisClusterException, RedisError

from ..sandbox import (
    SANDBOX_DEFAULT_PATH,
    SandboxCancelled,
    SandboxError,
    SandboxMount,
    SandboxOutputLimit,
    SandboxPolicy,
    SandboxProfile,
    SandboxResult,
    SandboxRunner,
    SandboxSpec,
    SandboxTimeout,
    SandboxUnavailable,
)
from .sandboxed import SESSION_SANDBOX_ROOT

logger = logging.getLogger("muad_agent_core.skill.draft_shell")

DRAFT_SHELL_ALLOWED_EXTENSIONS = frozenset(
    {
        ".md",
        ".py",
        ".json",
        ".yaml",
        ".yml",
        ".txt",
        ".csv",
        ".html",
        ".css",
        ".js",
        ".mjs",
        ".cjs",
        ".png",
        ".jpg",
        ".svg",
    }
)
#: 只存在于执行期、不参与回写的目录/后缀（py_compile、node_modules 等工具副产物）。
DRAFT_SHELL_IGNORED_DIRS = frozenset(
    {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".venv", "node_modules"}
)
DRAFT_SHELL_IGNORED_SUFFIXES = frozenset({".pyc", ".pyo"})
DRAFT_SHELL_MOUNT_ROOT = "/workspace/draft"
DRAFT_SHELL_TEMP_PREFIX = "muad-draft-shell-"
DRAFT_SHELL_PATH_LIMIT = 1024
DRAFT_SHELL_DEFAULT_TIMEOUT_SEC = 60.0
ZIP_SYMLINK_MODE = 0o120000


class DraftShellError(RuntimeError):
    """草稿 Shell 的封闭错误码；``result`` 保留超时/输出超限前的有界输出。"""

    def __init__(self, code: str, message: str, *, result: SandboxResult | None = None) -> None:
        self.code = code
        self.message = message
        self.result = result
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class DraftShellRequest:
    tenant_id: str
    owner_user_id: str
    draft_id: str
    command: str
    cwd: str = "."
    timeout_sec: float = DRAFT_SHELL_DEFAULT_TIMEOUT_SEC
    execution_id: str = ""

    def __post_init__(self) -> None:
        for name in ("tenant_id", "owner_user_id", "draft_id", "command"):
            value = str(getattr(self, name)).strip()
            if not value:
                raise ValueError(f"draft shell {name} must not be empty")
        if self.timeout_sec <= 0:
            raise ValueError("draft shell timeout_sec must be positive")


@dataclass(frozen=True, slots=True)
class DraftShellArchive:
    draft_id: str
    revision: int
    zip_bytes: bytes


@dataclass(frozen=True, slots=True)
class DraftShellChange:
    path: str
    content: bytes


@dataclass(frozen=True, slots=True)
class DraftShellApplyResult:
    revision: int
    changed_files: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DraftShellResult:
    stdout: str
    stderr: str
    exit_code: int | None
    duration_ms: int
    changed_files: tuple[str, ...]
    revision: int


class DraftShellStore(Protocol):
    """Console 内部 API 的取件/回写边界；Runtime 用真实 HTTP 实现，验收可注入内存实现。"""

    async def fetch_archive(self, request: DraftShellRequest) -> DraftShellArchive: ...

    async def apply_changes(
        self,
        request: DraftShellRequest,
        *,
        expected_revision: int,
        changes: Sequence[DraftShellChange],
        deletions: Sequence[str],
    ) -> DraftShellApplyResult: ...


PlatformContextFactory = Callable[[DraftShellRequest, tuple[str, ...]], SkillPlatformContext | None]


class DraftShellRunner:
    """一次调用 = 一条命令：独立临时草稿副本 + 独立 workspace + 一次 CAS 回写。"""

    def __init__(
        self,
        *,
        store: DraftShellStore,
        runner_factory: Callable[[], SandboxRunner],
        max_file_bytes: int,
        max_total_bytes: int,
        max_files: int,
        output_limit_bytes: int,
        platform_context_factory: PlatformContextFactory | None = None,
        temp_root: str | None = None,
    ) -> None:
        for name, value in (
            ("max_file_bytes", max_file_bytes),
            ("max_total_bytes", max_total_bytes),
            ("max_files", max_files),
            ("output_limit_bytes", output_limit_bytes),
        ):
            if value <= 0:
                raise ValueError(f"draft shell {name} must be positive")
        self._store = store
        self._runner_factory = runner_factory
        self._max_file_bytes = max_file_bytes
        self._max_total_bytes = max_total_bytes
        self._max_files = max_files
        self._output_limit_bytes = output_limit_bytes
        self._platform_context_factory = platform_context_factory
        self._temp_root = temp_root

    async def run(self, request: DraftShellRequest) -> DraftShellResult:
        archive = await self._store.fetch_archive(request)
        workspace = Path(tempfile.mkdtemp(prefix=DRAFT_SHELL_TEMP_PREFIX, dir=self._temp_root))
        try:
            draft_root = workspace / "draft"
            draft_root.mkdir()
            extract_draft_archive(
                archive,
                draft_root,
                max_files=self._max_files,
                max_total_bytes=self._max_total_bytes,
                max_file_bytes=self._max_file_bytes,
            )
            package_root = _draft_package_root(draft_root)
            baseline = _snapshot_tree(draft_root)
            result = await self._execute(
                request,
                workspace=workspace,
                draft_root=draft_root,
                package_root=package_root,
            )
            changes, deletions = _collect_changes(
                draft_root,
                baseline,
                max_file_bytes=self._max_file_bytes,
                max_total_bytes=self._max_total_bytes,
                max_files=self._max_files,
            )
            if changes or deletions:
                applied = await self._store.apply_changes(
                    request,
                    expected_revision=archive.revision,
                    changes=changes,
                    deletions=deletions,
                )
                changed_paths = tuple([change.path for change in changes] + list(deletions))
                revision = applied.revision
            else:
                changed_paths = ()
                revision = archive.revision
            return DraftShellResult(
                stdout=result.stdout,
                stderr=result.stderr,
                exit_code=result.exit_code,
                duration_ms=result.duration_ms,
                changed_files=changed_paths,
                revision=revision,
            )
        finally:
            shutil.rmtree(workspace, ignore_errors=True)

    async def _execute(
        self,
        request: DraftShellRequest,
        *,
        workspace: Path,
        draft_root: Path,
        package_root: Path,
    ) -> SandboxResult:
        package_rel = _relative_package(package_root, draft_root)
        cwd_rel = _resolve_cwd(request.cwd, package_rel, draft_root)
        data_dir = workspace / "data"
        output_dir = workspace / "outputs" / (request.execution_id or "shell")
        data_dir.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)
        declared = _declared_platforms(package_root)
        skill_name = _skill_name(package_root)
        context = (
            self._platform_context_factory(request, declared)
            if self._platform_context_factory is not None and declared
            else None
        )
        try:
            runner = self._runner_factory()
            dev_unsafe = getattr(runner, "name", "") == "dev-unsafe"
            async with skill_platform_environment(
                context,
                skill_name,
                sandbox_root=None if dev_unsafe else SESSION_SANDBOX_ROOT,
            ) as session:
                spec = _sandbox_spec(
                    request,
                    workspace=workspace,
                    draft_root=draft_root,
                    cwd_rel=cwd_rel,
                    data_dir=data_dir,
                    output_dir=output_dir,
                    dev_unsafe=dev_unsafe,
                    session_env=session.env,
                    session_mounts=session.mounts,
                    output_limit_bytes=self._output_limit_bytes,
                )
                return await runner.run(spec)
        except PlatformAuthError as exc:
            raise DraftShellError(exc.code, str(exc)) from exc
        except (RedisError, RedisClusterException):
            raise DraftShellError(
                "PLATFORM_SESSION_STORE_UNAVAILABLE",
                "platform session store is unavailable",
            ) from None
        except SandboxTimeout as exc:
            raise DraftShellError("SANDBOX_TIMEOUT", exc.message, result=exc.result) from exc
        except SandboxOutputLimit as exc:
            raise DraftShellError("SANDBOX_OUTPUT_LIMIT", exc.message, result=exc.result) from exc
        except SandboxCancelled as exc:
            raise DraftShellError("SANDBOX_CANCELLED", exc.message, result=exc.result) from exc
        except SandboxUnavailable as exc:
            raise DraftShellError("SANDBOX_UNAVAILABLE", exc.message) from exc
        except SandboxError as exc:
            raise DraftShellError("SANDBOX_ERROR", str(exc), result=exc.result) from exc


def extract_draft_archive(
    archive: DraftShellArchive,
    target: Path,
    *,
    max_files: int,
    max_total_bytes: int,
    max_file_bytes: int,
) -> Path:
    """安全解包草稿 zip：拒绝穿越/绝对路径/symlink/加密条目与超限包。"""
    target.mkdir(parents=True, exist_ok=True)
    try:
        package = zipfile.ZipFile(io.BytesIO(archive.zip_bytes))
    except zipfile.BadZipFile as exc:
        raise DraftShellError("SKILL_PACKAGE_INVALID", "draft archive is not a valid zip") from exc
    with package:
        entries = [info for info in package.infolist() if not info.is_dir()]
        if len(entries) > max_files:
            raise DraftShellError(
                "DRAFT_QUOTA_EXCEEDED", f"draft has more than {max_files} files"
            )
        total = sum(info.file_size for info in entries)
        if total > max_total_bytes:
            raise DraftShellError(
                "DRAFT_QUOTA_EXCEEDED", f"draft exceeds {max_total_bytes} bytes"
            )
        seen: set[str] = set()
        for info in entries:
            relative = _archive_member_path(info)
            if relative in seen:
                raise DraftShellError("DRAFT_PATH_INVALID", f"duplicate draft path: {relative}")
            seen.add(relative)
            if info.flag_bits & 0x1:
                raise DraftShellError("SKILL_PACKAGE_INVALID", "encrypted draft entries are not allowed")
            if info.file_size > max_file_bytes:
                raise DraftShellError(
                    "DRAFT_QUOTA_EXCEEDED", f"draft file exceeds {max_file_bytes} bytes: {relative}"
                )
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(package.read(info))
    return target


def _archive_member_path(info: zipfile.ZipInfo) -> str:
    raw = info.filename
    if "\x00" in raw or "\\" in raw:
        raise DraftShellError("DRAFT_PATH_INVALID", "draft entry contains an invalid character")
    pure = PurePosixPath(raw)
    if pure.is_absolute() or ".." in pure.parts or not pure.parts:
        raise DraftShellError("DRAFT_PATH_INVALID", f"draft entry escapes the draft: {raw}")
    if _is_zip_symlink(info):
        raise DraftShellError("DRAFT_PATH_INVALID", f"draft entry is a symlink: {raw}")
    relative = pure.as_posix()
    if len(relative) > DRAFT_SHELL_PATH_LIMIT:
        raise DraftShellError("DRAFT_PATH_INVALID", "draft entry path is too long")
    return relative


def _is_zip_symlink(info: zipfile.ZipInfo) -> bool:
    return (info.external_attr >> 16) & 0o170000 == ZIP_SYMLINK_MODE


def _draft_package_root(draft_root: Path) -> Path:
    if (draft_root / "SKILL.md").is_file():
        return draft_root
    try:
        package_root, _ = locate_package_root(draft_root)
    except SkillPackageError:
        return draft_root
    return package_root


def _relative_package(package_root: Path, draft_root: Path) -> str:
    relative = package_root.relative_to(draft_root)
    return relative.as_posix() if relative.parts else "."


def _declared_platforms(package_root: Path) -> tuple[str, ...]:
    skill_md = package_root / "SKILL.md"
    if not skill_md.is_file():
        return ()
    try:
        return tuple(SkillPackage.load(package_root).manifest.platforms)
    except SkillPackageError as exc:
        # Shell 本身仍可运行；platforms 解析失败时不注入 session（fail closed 到无登录态）。
        logger.warning("draft_shell_manifest_invalid error=%s", type(exc).__name__)
        return ()


def _skill_name(package_root: Path) -> str:
    skill_md = package_root / "SKILL.md"
    if not skill_md.is_file():
        return ""
    try:
        return SkillPackage.load(package_root).manifest.name
    except SkillPackageError:
        return ""


def _resolve_cwd(raw: str, package_rel: str, draft_root: Path) -> str:
    value = (raw or ".").strip()
    for prefix in (f"{DRAFT_SHELL_MOUNT_ROOT}/", "/draft/", DRAFT_SHELL_MOUNT_ROOT, "/draft"):
        if value == prefix.rstrip("/") or value.startswith(prefix):
            value = value[len(prefix) :]
            break
    value = value.lstrip("/") or "."
    pure = PurePosixPath(value)
    if ".." in pure.parts or "\x00" in value or "\\" in value:
        raise DraftShellError("COMMON_VALIDATION_ERROR", f"cwd escapes the draft: {raw!r}")
    relative = PurePosixPath(package_rel) / pure if package_rel != "." else pure
    if not (draft_root / relative).is_dir():
        raise DraftShellError("COMMON_VALIDATION_ERROR", f"cwd does not exist: {raw!r}")
    return relative.as_posix()


def _sandbox_spec(
    request: DraftShellRequest,
    *,
    workspace: Path,
    draft_root: Path,
    cwd_rel: str,
    data_dir: Path,
    output_dir: Path,
    dev_unsafe: bool,
    session_env: Mapping[str, str],
    session_mounts: Sequence[SessionMount],
    output_limit_bytes: int,
) -> SandboxSpec:
    if dev_unsafe:
        env = {
            **dict(session_env),
            "SKILL_DATA_DIR": str(data_dir),
            "SKILL_OUTPUT_DIR": str(output_dir),
        }
        env["PATH"] = session_env.get("PATH", os.environ.get("PATH", os.defpath))
        dev_unsafe_cwd = draft_root / cwd_rel
    else:
        env = {
            **dict(session_env),
            "SKILL_DATA_DIR": "/workspace/data",
            "SKILL_OUTPUT_DIR": f"/workspace/outputs/{request.execution_id or 'shell'}",
            "PATH": _session_path(session_env),
        }
        dev_unsafe_cwd = None
    cwd = f"{DRAFT_SHELL_MOUNT_ROOT}/{cwd_rel}"
    mounts = tuple(
        SandboxMount(source=mount.source, target=mount.target, readonly=mount.readonly)
        for mount in session_mounts
    )
    return SandboxSpec(
        policy=SandboxPolicy(
            profile=SandboxProfile.UNTRUSTED,
            network=True,
            dev_unsafe=dev_unsafe,
            env_inject=("MUAD_SESSION_SOCKET",) if session_env else (),
        ),
        command=("sh", "-c", request.command),
        workspace_dir=workspace,
        env=env,
        timeout_sec=request.timeout_sec,
        output_limit_bytes=output_limit_bytes,
        cwd=cwd,
        dev_unsafe_cwd=dev_unsafe_cwd,
        mounts=mounts,
        name="draft_shell",
    )


def _session_path(session_env: Mapping[str, str]) -> str:
    socket = session_env.get("MUAD_SESSION_SOCKET", "")
    command_dir = PurePosixPath(socket).parent.as_posix() if socket else ""
    if command_dir and command_dir != ".":
        return f"{command_dir}:{SANDBOX_DEFAULT_PATH}"
    return SANDBOX_DEFAULT_PATH


@dataclass(frozen=True, slots=True)
class _FileStamp:
    size: int
    mode: int
    checksum: str


def _snapshot_tree(root: Path) -> dict[str, _FileStamp]:
    stamps: dict[str, _FileStamp] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if _ignored(relative):
            continue
        if path.is_symlink():
            raise DraftShellError("DRAFT_PATH_INVALID", f"draft contains a symlink: {relative}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise DraftShellError("DRAFT_PATH_INVALID", f"draft contains a special file: {relative}")
        stat = path.stat()
        stamps[relative] = _FileStamp(
            size=stat.st_size,
            mode=stat.st_mode & 0o777,
            checksum=_checksum_file(path),
        )
    return stamps


def _collect_changes(
    root: Path,
    baseline: Mapping[str, _FileStamp],
    *,
    max_file_bytes: int,
    max_total_bytes: int,
    max_files: int,
) -> tuple[list[DraftShellChange], list[str]]:
    after = _snapshot_tree(root)
    if len(after) > max_files:
        raise DraftShellError("DRAFT_QUOTA_EXCEEDED", f"draft has more than {max_files} files")
    if sum(stamp.size for stamp in after.values()) > max_total_bytes:
        raise DraftShellError("DRAFT_QUOTA_EXCEEDED", "draft exceeds the total size limit")
    changes: list[DraftShellChange] = []
    for relative, stamp in after.items():
        if baseline.get(relative) == stamp:
            continue
        _validate_change_path(relative)
        if stamp.mode & 0o111:
            raise DraftShellError(
                "DRAFT_PATH_INVALID", f"draft shell cannot write executable files: {relative}"
            )
        if PurePosixPath(relative).suffix.lower() not in DRAFT_SHELL_ALLOWED_EXTENSIONS:
            raise DraftShellError(
                "DRAFT_PATH_INVALID", f"draft shell cannot write this file type: {relative}"
            )
        if stamp.size > max_file_bytes:
            raise DraftShellError(
                "DRAFT_QUOTA_EXCEEDED", f"draft file exceeds {max_file_bytes} bytes: {relative}"
            )
        changes.append(DraftShellChange(path=relative, content=(root / relative).read_bytes()))
    deletions = sorted(set(baseline) - set(after))
    for relative in deletions:
        _validate_change_path(relative)
    return changes, deletions


def _validate_change_path(relative: str) -> None:
    if len(relative) > DRAFT_SHELL_PATH_LIMIT or "\x00" in relative or "\\" in relative:
        raise DraftShellError("DRAFT_PATH_INVALID", f"invalid draft path: {relative}")
    pure = PurePosixPath(relative)
    if pure.is_absolute() or ".." in pure.parts or not pure.parts:
        raise DraftShellError("DRAFT_PATH_INVALID", f"invalid draft path: {relative}")


def _ignored(relative: str) -> bool:
    pure = PurePosixPath(relative)
    if any(part in DRAFT_SHELL_IGNORED_DIRS for part in pure.parts):
        return True
    return pure.suffix.lower() in DRAFT_SHELL_IGNORED_SUFFIXES


def _checksum_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = [
    "DRAFT_SHELL_ALLOWED_EXTENSIONS",
    "DRAFT_SHELL_DEFAULT_TIMEOUT_SEC",
    "DRAFT_SHELL_IGNORED_DIRS",
    "DRAFT_SHELL_IGNORED_SUFFIXES",
    "DRAFT_SHELL_MOUNT_ROOT",
    "DRAFT_SHELL_PATH_LIMIT",
    "DRAFT_SHELL_TEMP_PREFIX",
    "DraftShellApplyResult",
    "DraftShellArchive",
    "DraftShellChange",
    "DraftShellError",
    "DraftShellRequest",
    "DraftShellResult",
    "DraftShellRunner",
    "DraftShellStore",
    "extract_draft_archive",
]
