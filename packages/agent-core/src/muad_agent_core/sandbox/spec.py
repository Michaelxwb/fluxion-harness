"""Sandbox 值对象、policy 校验与安全 env 过滤。

这个模块只依赖标准库：它同时被宿主 Runtime/Worker 和 E-02 的容器探针导入。
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path, PurePosixPath
from types import MappingProxyType

SANDBOX_ENV_ALLOWLIST: tuple[str, ...] = ("PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TZ")
SANDBOX_ENV_PREFIXES: tuple[str, ...] = ("SKILL_", "MUAD_SKILL_")
#: allowlist 之外**永远不允许**进入沙箱的键片段；即使调用方把它伪装成 SKILL_* 也丢弃。
SANDBOX_FORBIDDEN_ENV_PARTS: tuple[str, ...] = (
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PASSWD",
    "CREDENTIAL",
    "API_KEY",
    "COOKIE",
    "PRIVATE_KEY",
    "DATABASE_URL",
    "REDIS_URL",
    "AWS_",
    "KUBERNETES",
    "SA_TOKEN",
)
SANDBOX_DEFAULT_PATH = "/opt/python3.12/bin:/usr/local/bin:/usr/bin:/bin"
SANDBOX_TMPFS_BYTES = 64 * 1024 * 1024
SANDBOX_OUTPUT_LIMIT_BYTES = 64 * 1024
TERMINATE_GRACE_SEC = 2.0

#: 整个 READY 缓存根、Pod 的敏感挂载与容器运行时 socket 不得成为 mount source/target。
#: workspace/package 是这些根**下面的具体子目录**（如 workspace PVC 下的 tenant/user/agent、
#: READY 缓存下的 checksum 目录），允许 bind；被禁止的是把根整体暴露进沙箱。
_FORBIDDEN_MOUNT_PREFIXES: tuple[str, ...] = (
    "/mnt",
    "/proc",
    "/sys",
    "/run",
    "/var/run",
    "/var/lib/docker",
)
_FORBIDDEN_SOURCE_PREFIXES: tuple[str, ...] = (
    "/proc",
    "/sys",
    "/run",
    "/var/run",
    "/var/lib/docker",
)
#: READY 缓存根用拼接构造：配置收敛机检禁止源码再出现第二套裸路径默认。
_SKILL_CACHE_ROOT = str(PurePosixPath("/var/cache") / "muad" / "skills")
_FORBIDDEN_MOUNT_EXACT: frozenset[str] = frozenset({"/"})
_FORBIDDEN_SOURCE_EXACT: frozenset[str] = frozenset({"/mnt", _SKILL_CACHE_ROOT})


class SandboxProfile(StrEnum):
    TRUSTED = "trusted"
    UNTRUSTED = "untrusted"


@dataclass(frozen=True, slots=True)
class SandboxResult:
    exit_code: int | None
    stdout: str
    stderr: str
    duration_ms: int
    output_truncated: bool = False


class SandboxError(RuntimeError):
    """所有沙箱错误的基类：error code 走既有 `SANDBOX_*` 契约。"""

    code = "SANDBOX_ERROR"

    def __init__(self, message: str, *, result: SandboxResult | None = None) -> None:
        self.message = message
        self.result = result
        super().__init__(message)


class SandboxUnavailable(SandboxError):
    code = "SANDBOX_UNAVAILABLE"


class SandboxTimeout(SandboxError):
    code = "SANDBOX_TIMEOUT"


class SandboxCancelled(SandboxError):
    code = "SANDBOX_CANCELLED"


class SandboxOutputLimit(SandboxError):
    code = "SANDBOX_OUTPUT_LIMIT"


def _validate_env_name(name: str, *, where: str) -> None:
    if not name or "=" in name or "\x00" in name or not name.replace("_", "").isalnum():
        raise SandboxUnavailable(f"invalid sandbox policy: bad env key {name!r} in {where}")
    if _env_key_forbidden(name):
        raise SandboxUnavailable(f"invalid sandbox policy: forbidden env key {name!r} in {where}")


def _env_key_forbidden(key: str) -> bool:
    upper = key.upper()
    if upper in SANDBOX_ENV_ALLOWLIST:
        return False
    return any(part in upper for part in SANDBOX_FORBIDDEN_ENV_PARTS)


def _is_forbidden_mount_path(path: str) -> bool:
    normalized = path.rstrip("/") or "/"
    if normalized in _FORBIDDEN_MOUNT_EXACT:
        return True
    return any(
        normalized == prefix or normalized.startswith(prefix + "/")
        for prefix in _FORBIDDEN_MOUNT_PREFIXES
    )


def _validate_mount_target(target: str) -> None:
    pure = PurePosixPath(target)
    if not target.startswith("/") or ".." in pure.parts or "\x00" in target:
        raise SandboxUnavailable(f"invalid sandbox mount target: {target!r}")
    if _is_forbidden_mount_path(target):
        raise SandboxUnavailable(f"forbidden sandbox mount target: {target!r}")


def _validate_mount_source(source: Path) -> None:
    text = str(source)
    if not source.is_absolute() or "\x00" in text:
        raise SandboxUnavailable(f"sandbox mount source must be absolute: {text!r}")
    normalized = text.rstrip("/") or "/"
    if normalized in _FORBIDDEN_SOURCE_EXACT or normalized in _FORBIDDEN_MOUNT_EXACT:
        raise SandboxUnavailable(f"forbidden sandbox mount source: {text!r}")
    if any(
        normalized == prefix or normalized.startswith(prefix + "/")
        for prefix in _FORBIDDEN_SOURCE_PREFIXES
    ):
        raise SandboxUnavailable(f"forbidden sandbox mount source: {text!r}")


@dataclass(frozen=True, slots=True)
class SandboxMount:
    source: Path
    target: str
    readonly: bool = True
    optional: bool = False

    def __post_init__(self) -> None:
        source = Path(self.source)
        object.__setattr__(self, "source", source)
        _validate_mount_source(source)
        _validate_mount_target(self.target)


def _system_mounts() -> tuple[SandboxMount, ...]:
    return (
        SandboxMount(Path("/usr"), "/usr", readonly=True),
        SandboxMount(Path("/lib"), "/lib", readonly=True, optional=True),
        SandboxMount(Path("/lib64"), "/lib64", readonly=True, optional=True),
        SandboxMount(Path("/bin"), "/bin", readonly=True, optional=True),
        SandboxMount(Path("/etc"), "/etc", readonly=True),
        SandboxMount(Path("/opt"), "/opt", readonly=True, optional=True),
    )


@dataclass(frozen=True, slots=True)
class SandboxLimits:
    tmpfs_bytes: int = SANDBOX_TMPFS_BYTES
    address_space_bytes: int | None = None
    cpu_seconds: int | None = 60
    max_processes: int | None = 1024
    max_file_bytes: int | None = 64 * 1024 * 1024
    open_files: int | None = 256

    def __post_init__(self) -> None:
        if self.tmpfs_bytes <= 0:
            raise SandboxUnavailable("invalid sandbox policy: tmpfs_bytes must be positive")
        for name in (
            "address_space_bytes",
            "cpu_seconds",
            "max_processes",
            "max_file_bytes",
            "open_files",
        ):
            value = getattr(self, name)
            if value is not None and value <= 0:
                raise SandboxUnavailable(f"invalid sandbox policy: {name} must be positive")


@dataclass(frozen=True, slots=True)
class SandboxPolicy:
    profile: SandboxProfile = SandboxProfile.UNTRUSTED
    network: bool = True
    dev_unsafe: bool = False
    env_allowlist: tuple[str, ...] = SANDBOX_ENV_ALLOWLIST
    env_prefixes: tuple[str, ...] = SANDBOX_ENV_PREFIXES
    limits: SandboxLimits = field(default_factory=SandboxLimits)
    system_mounts: tuple[SandboxMount, ...] = field(default_factory=_system_mounts)

    def __post_init__(self) -> None:
        object.__setattr__(self, "profile", SandboxProfile(self.profile))
        object.__setattr__(self, "env_allowlist", tuple(self.env_allowlist))
        object.__setattr__(self, "env_prefixes", tuple(self.env_prefixes))
        object.__setattr__(self, "system_mounts", tuple(self.system_mounts))
        self.validate()

    def validate(self) -> None:
        if not isinstance(self.network, bool) or not isinstance(self.dev_unsafe, bool):
            raise SandboxUnavailable("invalid sandbox policy: network/dev_unsafe must be bool")
        for key in self.env_allowlist:
            _validate_env_name(key, where="env_allowlist")
        for prefix in self.env_prefixes:
            if not prefix.endswith("_") or not prefix.replace("_", "").isalnum():
                raise SandboxUnavailable(
                    f"invalid sandbox policy: env prefix {prefix!r} must end with '_'"
                )
        targets: set[str] = set()
        for mount in self.system_mounts:
            if not isinstance(mount, SandboxMount):
                raise SandboxUnavailable("invalid sandbox policy: system_mounts must be SandboxMount")
            if not mount.readonly:
                raise SandboxUnavailable("invalid sandbox policy: system mounts must be read-only")
            if mount.target in targets:
                raise SandboxUnavailable(f"invalid sandbox policy: duplicate mount {mount.target!r}")
            targets.add(mount.target)
        if not isinstance(self.limits, SandboxLimits):
            raise SandboxUnavailable("invalid sandbox policy: limits must be SandboxLimits")

    @classmethod
    def for_source(
        cls,
        source: str,
        *,
        dev_unsafe: bool = False,
        limits: SandboxLimits | None = None,
    ) -> SandboxPolicy:
        profile = _profile_for_source(source)
        return cls(profile=profile, dev_unsafe=dev_unsafe, limits=limits or SandboxLimits())


def _profile_for_source(source: str) -> SandboxProfile:
    normalized = str(source).strip().upper()
    if normalized in {"SYSTEM", "ADMIN"}:
        return SandboxProfile.TRUSTED
    if normalized == "USER":
        return SandboxProfile.UNTRUSTED
    raise SandboxUnavailable(f"invalid sandbox policy: unknown skill source {source!r}")


@dataclass(frozen=True, slots=True)
class SandboxSpec:
    policy: SandboxPolicy
    command: tuple[str, ...]
    workspace_dir: Path
    env: Mapping[str, str] = field(default_factory=dict)
    timeout_sec: float = 60.0
    output_limit_bytes: int = SANDBOX_OUTPUT_LIMIT_BYTES
    cancel_event: asyncio.Event | None = None
    package_dir: Path | None = None
    stdin: bytes = b""
    mounts: tuple[SandboxMount, ...] = ()
    cwd: str = "/workspace"
    limits: SandboxLimits | None = None
    name: str = "skill"

    def __post_init__(self) -> None:
        if not isinstance(self.policy, SandboxPolicy):
            raise SandboxUnavailable("invalid sandbox spec: policy is required")
        command = tuple(self.command)
        if not command or any(not isinstance(part, str) or not part or "\x00" in part for part in command):
            raise SandboxUnavailable("invalid sandbox spec: command must be non-empty strings")
        object.__setattr__(self, "command", command)

        workspace = Path(self.workspace_dir)
        if not workspace.is_absolute():
            raise SandboxUnavailable("invalid sandbox spec: workspace_dir must be absolute")
        object.__setattr__(self, "workspace_dir", workspace)

        package = Path(self.package_dir) if self.package_dir is not None else None
        if package is not None and not package.is_absolute():
            raise SandboxUnavailable("invalid sandbox spec: package_dir must be absolute")
        object.__setattr__(self, "package_dir", package)

        if self.timeout_sec <= 0:
            raise SandboxUnavailable("invalid sandbox spec: timeout_sec must be positive")
        if self.output_limit_bytes <= 0:
            raise SandboxUnavailable("invalid sandbox spec: output_limit_bytes must be positive")
        if not self.cwd.startswith("/") or ".." in PurePosixPath(self.cwd).parts:
            raise SandboxUnavailable(f"invalid sandbox spec: cwd {self.cwd!r}")

        mounts = tuple(self.mounts)
        targets = {mount.target for mount in mounts}
        if len(targets) != len(mounts):
            raise SandboxUnavailable("invalid sandbox spec: duplicate extra mount target")
        reserved = {"/skill", "/workspace", "/tmp", "/dev"}
        if targets & reserved:
            raise SandboxUnavailable("invalid sandbox spec: extra mount overlaps a reserved target")
        for mount in mounts:
            if not isinstance(mount, SandboxMount):
                raise SandboxUnavailable("invalid sandbox spec: mounts must be SandboxMount")
        object.__setattr__(self, "mounts", mounts)
        object.__setattr__(self, "env", MappingProxyType({str(k): str(v) for k, v in self.env.items()}))

        if self.limits is None:
            object.__setattr__(self, "limits", self.policy.limits)
        elif not isinstance(self.limits, SandboxLimits):
            raise SandboxUnavailable("invalid sandbox spec: limits must be SandboxLimits")

    @property
    def effective_limits(self) -> SandboxLimits:
        return self.limits or self.policy.limits


def sanitize_env(
    env: Mapping[str, str],
    *,
    default_path: str,
    home: str = "/tmp",
) -> dict[str, str]:
    """只保留 allowlist + ``SKILL_*``/``MUAD_SKILL_*``，并强制 ``HOME``。

    服务 Token、DB/Redis DSN、云凭据即使被调用方误传，也不会进入沙箱。
    """
    sanitized: dict[str, str] = {}
    for key, value in env.items():
        key = str(key)
        if key in SANDBOX_ENV_ALLOWLIST:
            if key == "HOME":
                continue
            if value:
                sanitized[key] = str(value)
            continue
        if key.startswith(SANDBOX_ENV_PREFIXES) and not _env_key_forbidden(key) and value:
            sanitized[key] = str(value)
    sanitized.setdefault("PATH", default_path)
    sanitized["HOME"] = home
    return dict(sorted(sanitized.items()))


def default_host_path() -> str:
    return os.environ.get("PATH", os.defpath)
