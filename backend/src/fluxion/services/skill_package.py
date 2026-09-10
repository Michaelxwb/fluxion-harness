"""TASK-009：Skill Package 解析与校验（V4 §5-§12 修订版）。

- Package 是唯一 Skill 形态：manifest.yaml + SKILL.md + knowledge/ +
  scripts/ + templates/ + assets/；快速验证与正式包同一 Schema。
- manifest 只做资源声明与运行约束；调用链 DSL（顺序/重试/补偿/审批/
  持久化）一律拒绝，由 Workflow 承担（V4 §80.1）。
- Script Contract V1：stdin JSON → stdout JSON（执行见 Sandbox 复用）。
"""

from __future__ import annotations

import hashlib
import io
import zipfile
from dataclasses import dataclass, field

import yaml  # type: ignore[import-untyped]  # pyyaml 无 stubs；已声明为直接依赖
from pydantic import BaseModel, ValidationError

MAX_PACKAGE_FILES = 200
MAX_SINGLE_FILE_BYTES = 5 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 50 * 1024 * 1024
MAX_COMPRESSION_RATIO = 100
ALLOWED_EXTENSIONS = frozenset(
    {".md", ".markdown", ".yaml", ".yml", ".json", ".txt", ".py", ".sh", ".j2", ".jinja"}
)

# manifest 顶层出现即视为调用链 DSL（V4 §80.1：禁止第二套 Workflow DSL）。
FORBIDDEN_MANIFEST_KEYS = frozenset(
    {
        "workflow",
        "steps",
        "retry",
        "retries",
        "compensation",
        "approval",
        "approval_policy",
        "durable",
        "state",
        "state_machine",
        "transitions",
    }
)


class SkillPackageError(ValueError):
    code = "skill_package_invalid"


class SkillManifest(BaseModel):
    name: str
    version: str = "1"
    description: str = ""
    required_capabilities: list[str] = []
    knowledge: list[str] = []
    scripts: list[str] = []


@dataclass(frozen=True, slots=True)
class SkillPackageBundle:
    manifest: SkillManifest
    skill_md: str
    knowledge_files: list[str]
    script_files: list[str] = field(default_factory=list)
    template_files: list[str] = field(default_factory=list)
    asset_files: list[str] = field(default_factory=list)
    artifact_hash: str = ""
    file_count: int = 0


def parse_skill_package(
    data: bytes,
    *,
    max_uncompressed_bytes: int = MAX_UNCOMPRESSED_BYTES,
) -> SkillPackageBundle:
    """解析并校验 Skill Package ZIP；非法即 SkillPackageError。"""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise SkillPackageError(f"invalid zip: {exc}") from exc
    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_PACKAGE_FILES:
            raise SkillPackageError(f"too many files: {len(infos)}")
        total_uncompressed = 0
        compressed_total = 0
        names: list[str] = []
        for info in infos:
            if info.is_dir():
                continue
            # symlink 拒绝（unix mode 高位）。
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise SkillPackageError(f"symlink refused: {info.filename!r}")
            _reject_unsafe_name(info.filename)
            if info.compress_size == 0 and info.file_size > max_uncompressed_bytes:
                raise SkillPackageError(f"zip bomb suspected: {info.filename}")
            total_uncompressed += info.file_size
            compressed_total += info.compress_size
            names.append(info.filename)
        if total_uncompressed > max_uncompressed_bytes:
            raise SkillPackageError(
                f"package too large: {total_uncompressed} bytes"
            )
        if compressed_total > 0 and total_uncompressed > compressed_total * MAX_COMPRESSION_RATIO:
            raise SkillPackageError(
                f"zip bomb suspected: compression ratio too high ({total_uncompressed}/{compressed_total})"
            )
        contents = {name: archive.read(name) for name in names}
    for name, content in contents.items():
        if len(content) > MAX_SINGLE_FILE_BYTES:
            raise SkillPackageError(f"file too large: {name}")
    manifest = _parse_manifest(contents)
    skill_md = _read_text(contents, "SKILL.md")
    if not skill_md.strip():
        raise SkillPackageError("SKILL.md is empty")
    knowledge_files = _declared_files(contents, manifest.knowledge, "knowledge")
    script_files = _declared_files(contents, manifest.scripts, "scripts")
    template_files = _under_prefix(contents, "templates/")
    asset_files = _under_prefix(contents, "assets/")
    return SkillPackageBundle(
        manifest=manifest,
        skill_md=skill_md,
        knowledge_files=sorted(knowledge_files),
        script_files=sorted(script_files),
        template_files=sorted(template_files),
        asset_files=sorted(asset_files),
        artifact_hash=hashlib.sha256(data).hexdigest(),
        file_count=len(contents),
    )


def build_skill_package(bundle: SkillPackageBundle) -> dict[str, object]:
    """Package → SKILL ResourceDefinition spec 物化（instructions 来源）。"""
    return {
        "name": bundle.manifest.name,
        "instructions": bundle.skill_md,
        "required_capabilities": list(bundle.manifest.required_capabilities),
        "knowledge_manifest": {"files": list(bundle.knowledge_files)},
    }


def _reject_unsafe_name(name: str) -> None:
    if not name or name.startswith("/") or ".." in name.split("/"):
        raise SkillPackageError(f"path traversal refused: {name!r}")
    if name.startswith("__MACOSX/"):
        return
    suffix = "." + name.rsplit(".", 1)[-1].lower() if "." in name.rsplit("/", 1)[-1] else ""
    if suffix and suffix not in ALLOWED_EXTENSIONS:
        raise SkillPackageError(f"extension not allowed: {name!r}")


def _read_text(contents: dict[str, bytes], name: str) -> str:
    raw = contents.get(name)
    if raw is None:
        raise SkillPackageError(f"{name} is missing")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SkillPackageError(f"{name} is not utf-8") from exc


def _parse_manifest(contents: dict[str, bytes]) -> SkillManifest:
    raw = contents.get("manifest.yaml")
    if raw is None:
        raise SkillPackageError("manifest.yaml is missing")
    try:
        data = yaml.safe_load(raw.decode("utf-8"))
    except Exception as exc:
        raise SkillPackageError(f"manifest.yaml invalid: {exc}") from exc
    if not isinstance(data, dict):
        raise SkillPackageError("manifest.yaml must be a mapping")
    forbidden = FORBIDDEN_MANIFEST_KEYS.intersection(str(k) for k in data)
    if forbidden:
        raise SkillPackageError(
            f"manifest must not define workflow DSL: {sorted(forbidden)} "
            "(顺序/重试/补偿/审批由 Workflow 承担)"
        )
    try:
        manifest = SkillManifest.model_validate(data)
    except ValidationError as exc:
        raise SkillPackageError(f"manifest.yaml schema error: {exc}") from exc
    if not manifest.name.strip():
        raise SkillPackageError("manifest name is required")
    return manifest


def _declared_files(
    contents: dict[str, bytes], declared: list[str], kind: str
) -> list[str]:
    missing = [name for name in declared if name not in contents]
    if missing:
        raise SkillPackageError(f"{kind} reference missing: {missing}")
    return list(declared)


def _under_prefix(contents: dict[str, bytes], prefix: str) -> list[str]:
    return [name for name in contents if name.startswith(prefix)]
