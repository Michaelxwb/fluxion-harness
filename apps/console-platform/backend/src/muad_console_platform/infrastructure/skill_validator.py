from __future__ import annotations

import hashlib
import io
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
from muad_skill_sdk import SkillManifest, SkillPackage, SkillPackageError

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
        ".png",
        ".jpg",
        ".svg",
    }
)
TEXT_EXTENSIONS = frozenset(
    {".md", ".py", ".json", ".yaml", ".yml", ".txt", ".csv", ".html", ".css", ".js", ".svg"}
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


@dataclass(frozen=True, slots=True)
class ValidatedSkillPackage:
    manifest: SkillManifest
    instructions: str
    frontmatter: dict[str, Any]
    files: tuple[dict[str, Any], ...]
    total_size: int
    default_key: str


def invalid_package() -> AppError:
    return AppError(ErrorCode.SKILL_PACKAGE_INVALID)


def checksum_of(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug[:128]


@contextmanager
def validated_package(data: bytes) -> Iterator[ValidatedSkillPackage]:
    if not data or len(data) > ZIP_BYTES_LIMIT:
        raise invalid_package()
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
            raise invalid_package()
        total_size = 0
        for member in members:
            _check_member(member)
            total_size += member.file_size
        if total_size > UNPACKED_BYTES_LIMIT:
            raise invalid_package()
        _extract(archive, temp_root)
    package_root, inner_dir = _locate_package_root(temp_root)
    package = _load_package(package_root)
    _scan_secrets(package_root)
    return ValidatedSkillPackage(
        manifest=package.manifest,
        instructions=package.instructions,
        frontmatter=_frontmatter(package.manifest),
        files=_file_listing(package_root, inner_dir),
        total_size=total_size,
        default_key=_default_key(package.manifest.name, inner_dir),
    )


def _check_member(member: zipfile.ZipInfo) -> None:
    name = member.filename
    if not name or name.startswith("/") or "\\" in name or re.match(r"^[A-Za-z]:", name):
        raise invalid_package()
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise invalid_package()
    if _is_symlink(member):
        raise invalid_package()
    suffix = path.suffix.lower()
    if suffix in ARCHIVE_EXTENSIONS:
        raise invalid_package()
    if suffix not in ALLOWED_EXTENSIONS:
        raise invalid_package()


def _is_symlink(member: zipfile.ZipInfo) -> bool:
    return (member.external_attr >> 16) & 0o170000 == SYMLINK_MODE


def _extract(archive: zipfile.ZipFile, destination: Path) -> None:
    root = destination.resolve()
    for member in archive.infolist():
        target = (destination / member.filename).resolve()
        if target != root and root not in target.parents:
            raise invalid_package()
    archive.extractall(destination)


def _locate_package_root(temp_root: Path) -> tuple[Path, str]:
    if (temp_root / SKILL_FILE_NAME).is_file():
        return temp_root, ""
    entries = list(temp_root.iterdir())
    if len(entries) == 1 and entries[0].is_dir() and (entries[0] / SKILL_FILE_NAME).is_file():
        return entries[0], entries[0].name
    raise invalid_package()


def _load_package(root: Path) -> SkillPackage:
    try:
        return SkillPackage.load(root)
    except SkillPackageError as exc:
        raise invalid_package() from exc


def _scan_secrets(root: Path) -> None:
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in TEXT_EXTENSIONS:
            continue
        if _contains_secret(path):
            raise invalid_package()


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
    return {
        "name": manifest.name,
        "description": manifest.description,
        "execution": manifest.execution.value,
        "platform_label": manifest.platform_label,
    }


def _default_key(name: str, inner_dir: str) -> str:
    return slugify(inner_dir) or slugify(name)
