from __future__ import annotations

import hashlib
import io
import json
import re
import tempfile
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_skill_sdk import (
    SkillManifest,
    SkillPackage,
    SkillPackageError,
    declared_entrypoint,
    locate_package_root,
)

ZIP_BYTES_LIMIT = 50 * 1024 * 1024
UNPACKED_BYTES_LIMIT = 200 * 1024 * 1024
ENTRY_LIMIT = 2000
SKILL_FILE_NAME = "SKILL.md"
ARCHIVE_EXTENSIONS = frozenset({".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar"})
ALLOWED_EXTENSIONS = frozenset(
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
TEXT_EXTENSIONS = frozenset(
    {".md", ".py", ".json", ".yaml", ".yml", ".txt", ".csv", ".html", ".css", ".js", ".mjs", ".cjs", ".svg"}
)
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"(?i)\b(api[_-]?key|secret|token|password)\b\s*[:=]\s*['\"][^'\"]{16,}['\"]"),
)
SYMLINK_MODE = 0o120000
ARCHIVE_MAGICS = (
    b"PK\x03\x04",
    b"PK\x05\x06",
    b"PK\x07\x08",
    b"\x1f\x8b",
    b"BZh",
    b"7z\xbc\xaf\x27\x1c",
    b"Rar!\x1a\x07",
    b"\xfd7zXZ\x00",
)


@dataclass(frozen=True, slots=True)
class ValidatedSkillPackage:
    manifest: SkillManifest
    instructions: str
    frontmatter: dict[str, Any]
    files: tuple[dict[str, Any], ...]
    total_size: int
    default_key: str
    author: str | None = None
    #: `muad.skill.json` 声明的入口（包内相对路径）；未声明或声明但文件缺失时为 None。
    entrypoint: str | None = None


@dataclass(frozen=True, slots=True)
class PackageCheck:
    code: str
    status: str
    message: str
    path: str | None = None

    def as_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"code": self.code, "status": self.status, "message": self.message}
        if self.path is not None:
            data["path"] = self.path
        return data


@dataclass(frozen=True, slots=True)
class PackageReview:
    status: str
    checks: tuple[PackageCheck, ...]
    package: ValidatedSkillPackage | None

    def as_dict(self) -> dict[str, Any]:
        errors = [check.as_dict() for check in self.checks if check.status == "error"]
        warnings = [check.as_dict() for check in self.checks if check.status == "warn"]
        return {
            "status": self.status,
            "checks": [check.as_dict() for check in self.checks],
            "errors": errors,
            "warnings": warnings,
        }


def invalid_package(reason: str = "INVALID_ZIP", **detail: Any) -> AppError:
    """Package rejection with a machine-readable reason for the Console.

    The localized message template lives in ``config/api-messages.yaml``; the ``data``
    field only carries the stable reason plus the offending path/limit/detail so the
    frontend can render a precise, bilingual error instead of a generic toast.
    """
    return AppError(ErrorCode.SKILL_PACKAGE_INVALID, data={"reason": reason, **detail})


def _display_path(value: str) -> str:
    cleaned = "".join(char if char.isprintable() else " " for char in value).strip()
    return cleaned if len(cleaned) <= 200 else f"{cleaned[:197]}..."


def _safe_detail(value: str) -> str:
    cleaned = " ".join(value.split())
    return cleaned if len(cleaned) <= 300 else f"{cleaned[:297]}..."


def size_label(size: int) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{value:g} {unit}" if unit == "B" else f"{value:.0f} {unit}"
        value /= 1024
    return f"{value:g} B"


def checksum_of(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug[:128]


def review_package(
    data: bytes,
    *,
    default_script: str | None = None,
) -> PackageReview:
    """只读校验草稿 zip：返回 pass/warn/error 明细，不写 artifact、不碰数据库。"""
    try:
        with validated_package(data) as package:
            checks = [
                PackageCheck("PACKAGE_ARCHIVE", "pass", "zip 结构合法"),
                PackageCheck("MANIFEST", "pass", f"manifest: {package.manifest.name}"),
                PackageCheck("FILE_SAFETY", "pass", f"{len(package.files)} 个文件通过安全检查"),
                PackageCheck("SECRET_SCAN", "pass", "未发现明显密钥"),
            ]
            entrypoint = _entrypoint_check(default_script, package)
            if entrypoint is not None:
                checks.append(entrypoint)
            if package.manifest.platforms:
                checks.append(PackageCheck("PLATFORMS", "pass", "已声明平台登录态"))
            else:
                checks.append(
                    PackageCheck(
                        "PLATFORMS",
                        "warn",
                        "未声明 platforms，草稿 Shell 不会获得平台登录态",
                    )
                )
            status = "error" if any(check.status == "error" for check in checks) else (
                "warn" if any(check.status == "warn" for check in checks) else "pass"
            )
            return PackageReview(status=status, checks=tuple(checks), package=package)
    except AppError as exc:
        if exc.code != ErrorCode.SKILL_PACKAGE_INVALID:
            raise
        detail = exc.data if isinstance(exc.data, dict) else {}
        reason = str(detail.get("reason") or "INVALID_PACKAGE")
        path = detail.get("path")
        detail_path = None if path is None else str(path)
        return PackageReview(
            status="error",
            checks=(PackageCheck(reason, "error", _reason_message(reason), detail_path),),
            package=None,
        )


def _entrypoint_check(
    value: str | None,
    package: ValidatedSkillPackage,
) -> PackageCheck | None:
    """入口校验：显式 default_script 优先，否则回退 manifest 声明的 entrypoint。

    只认调用参数会让「manifest 已声明入口」的包永远显示“未指定默认脚本”——那是误报，
    而且声明本身也就没人校验了。执行侧（`select_entry_script`）本来就会回退 manifest。
    """
    declared = value or package.entrypoint
    if declared is None:
        return PackageCheck("ENTRYPOINT", "warn", "未指定默认脚本，执行时按 SKILL.md 约定选择")
    path = PurePosixPath(declared)
    if path.is_absolute() or ".." in path.parts or path.suffix.lower() != ".py":
        return PackageCheck("ENTRYPOINT", "error", "默认脚本必须是包内相对 .py 路径", declared)
    if not any(
        entry["path"] == declared or entry["path"].endswith(f"/{declared}")
        for entry in package.files
    ):
        return PackageCheck("ENTRYPOINT", "error", "默认脚本不在包内", declared)
    return PackageCheck("ENTRYPOINT", "pass", f"默认脚本: {declared}", declared)


def _declared_entrypoint(package_root: Path) -> str | None:
    """manifest 声明的入口（包内相对路径）；未声明或声明但文件缺失返回 None。"""
    declared = declared_entrypoint(package_root)
    if declared is None:
        return None
    try:
        return declared.resolve().relative_to(package_root.resolve()).as_posix()
    except ValueError:
        return None


def _reason_message(reason: str) -> str:
    return {
        "SKILL_MD_MISSING": "缺少 SKILL.md",
        "MANIFEST_INVALID": "SKILL.md frontmatter 不合法",
        "PATH_INVALID": "包含路径穿越或 symlink",
        "NESTED_ARCHIVE": "包含嵌套压缩包",
        "FILE_TYPE_UNSUPPORTED": "包含不允许的文件类型",
        "SECRET_DETECTED": "发现疑似密钥",
        "PACKAGE_TOO_LARGE": "压缩包过大",
        "UNPACKED_TOO_LARGE": "解压后过大",
        "TOO_MANY_FILES": "文件数过多",
    }.get(reason, "Skill 包校验失败")


@contextmanager
def validated_package(data: bytes) -> Iterator[ValidatedSkillPackage]:
    if not data:
        raise invalid_package()
    if len(data) > ZIP_BYTES_LIMIT:
        raise invalid_package("PACKAGE_TOO_LARGE", limit=size_label(ZIP_BYTES_LIMIT))
    with tempfile.TemporaryDirectory(prefix="skill-import-") as temp_dir:
        yield _validate_package(data, Path(temp_dir))


def _validate_package(data: bytes, temp_root: Path) -> ValidatedSkillPackage:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise invalid_package() from exc
    with archive:
        members = [member for member in archive.infolist() if not member.is_dir()]
        if len(members) > ENTRY_LIMIT:
            raise invalid_package("TOO_MANY_FILES", limit=str(ENTRY_LIMIT))
        total_size = 0
        for member in members:
            _check_member(member)
            total_size += member.file_size
        if total_size > UNPACKED_BYTES_LIMIT:
            raise invalid_package(
                "UNPACKED_TOO_LARGE", limit=size_label(UNPACKED_BYTES_LIMIT)
            )
        _extract(archive, temp_root)
    _scan_nested_archives(temp_root)
    package_root, inner_dir = _locate_package_root(temp_root)
    package = _load_package(package_root)
    _scan_secrets(package_root)
    author = _package_author(package_root)
    frontmatter = _frontmatter(package.manifest)
    if author is not None:
        frontmatter["author"] = author
    return ValidatedSkillPackage(
        manifest=package.manifest,
        instructions=package.instructions,
        frontmatter=frontmatter,
        files=_file_listing(package_root, inner_dir),
        total_size=total_size,
        default_key=_default_key(package.manifest.name, inner_dir),
        author=author,
        entrypoint=_declared_entrypoint(package_root),
    )


def _package_author(root: Path) -> str | None:
    """作者是展示用自由文本，来源固定为 `muad.skill.json`；缺失/非法类型按无作者处理。"""
    manifest_file = root / "muad.skill.json"
    if not manifest_file.is_file():
        return None
    try:
        data = json.loads(manifest_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    author = data.get("author")
    if not isinstance(author, str) or not author.strip():
        return None
    return author.strip()[:128]


def _check_member(member: zipfile.ZipInfo) -> None:
    name = member.filename
    display = _display_path(name)
    if not name or name.startswith("/") or "\\" in name or re.match(r"^[A-Za-z]:", name):
        raise invalid_package("PATH_INVALID", path=display)
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise invalid_package("PATH_INVALID", path=display)
    if _is_symlink(member):
        raise invalid_package("PATH_INVALID", path=display)
    suffix = path.suffix.lower()
    if suffix in ARCHIVE_EXTENSIONS:
        raise invalid_package("NESTED_ARCHIVE", path=display)
    if suffix not in ALLOWED_EXTENSIONS:
        raise invalid_package("FILE_TYPE_UNSUPPORTED", path=display)


def _is_symlink(member: zipfile.ZipInfo) -> bool:
    return (member.external_attr >> 16) & 0o170000 == SYMLINK_MODE


def _extract(archive: zipfile.ZipFile, destination: Path) -> None:
    root = destination.resolve()
    for member in archive.infolist():
        target = (destination / member.filename).resolve()
        if target != root and root not in target.parents:
            raise invalid_package("PATH_INVALID", path=_display_path(member.filename))
    archive.extractall(destination)


def _locate_package_root(temp_root: Path) -> tuple[Path, str]:
    """委托 `muad_skill_sdk.locate_package_root` —— 定位规则**唯一定义在该 SDK**。

    导入侧与运行时侧必须用同一套规则：两边各自实现会漂移成「导入通过、运行时 missing
    SKILL.md」（2026-10-01 实测事故）。本函数只保留「把 SDK 错误翻译成 Console 侧
    `invalid_package()`」这一层适配。
    """
    try:
        return locate_package_root(temp_root)
    except SkillPackageError as exc:
        raise invalid_package("SKILL_MD_MISSING") from exc


def _load_package(root: Path) -> SkillPackage:
    try:
        return SkillPackage.load(root)
    except SkillPackageError as exc:
        raise invalid_package(
            "MANIFEST_INVALID", detail=_safe_detail(exc.message)
        ) from exc


def _scan_secrets(root: Path) -> None:
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in TEXT_EXTENSIONS:
            continue
        if _contains_secret(path):
            raise invalid_package(
                "SECRET_DETECTED", path=_display_path(path.relative_to(root).as_posix())
            )


def _scan_nested_archives(root: Path) -> None:
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = _display_path(path.relative_to(root).as_posix())
        with path.open("rb") as stream:
            header = stream.read(512)
        if any(header.startswith(magic) for magic in ARCHIVE_MAGICS):
            raise invalid_package("NESTED_ARCHIVE", path=relative)
        if len(header) >= 262 and header[257:262] == b"ustar":
            raise invalid_package("NESTED_ARCHIVE", path=relative)


def _contains_secret(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return False
    return any(pattern.search(text) for pattern in SECRET_PATTERNS)


def _file_listing(root: Path, inner_dir: str) -> tuple[dict[str, Any], ...]:
    prefix = f"{inner_dir}/" if inner_dir else ""
    entries: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        entries.append({"path": f"{prefix}{relative}", "size": path.stat().st_size})
    return tuple(entries)


def _frontmatter(manifest: SkillManifest) -> dict[str, Any]:
    result: dict[str, Any] = {
        "name": manifest.name,
        "description": manifest.description,
        "execution": manifest.execution.value,
        "platform_label": manifest.platform_label,
    }
    if manifest.platforms:
        result["platforms"] = list(manifest.platforms)
    return result


def _default_key(name: str, inner_dir: str) -> str:
    return slugify(inner_dir) or slugify(name)
