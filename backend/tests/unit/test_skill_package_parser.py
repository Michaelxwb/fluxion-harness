"""TASK-009：Skill Package parser 单元测试（RED 先行）。

覆盖 V4 §71：manifest 缺失 / SKILL.md 缺失 / schema 错误 / knowledge 缺失 /
path traversal / symlink / zip bomb / artifact hash / DSL 标记拒绝。
"""

from __future__ import annotations

import io
import zipfile

import pytest
import yaml

from fluxion.services.skill_package import (
    SkillPackageError,
    build_skill_package,
    parse_skill_package,
)


def _zip(files: dict[str, bytes | str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            data = content.encode("utf-8") if isinstance(content, str) else content
            archive.writestr(name, data)
    return buf.getvalue()


def _manifest(**overrides: object) -> str:
    base: dict[str, object] = {
        "name": "helper",
        "version": "1",
        "description": "d",
        "required_capabilities": [],
        "knowledge": ["knowledge/faq.md"],
    }
    base.update(overrides)
    return yaml.safe_dump(base)


def _valid_files() -> dict[str, bytes | str]:
    return {
        "manifest.yaml": _manifest(),
        "SKILL.md": "# Helper\nBe helpful.",
        "knowledge/faq.md": "# FAQ\nA: B",
    }


def test_valid_package_parses() -> None:
    bundle = parse_skill_package(_zip(_valid_files()))
    assert bundle.manifest.name == "helper"
    assert "Be helpful." in bundle.skill_md
    assert bundle.knowledge_files == ["knowledge/faq.md"]
    assert bundle.artifact_hash


def test_manifest_missing_rejected() -> None:
    files = _valid_files()
    del files["manifest.yaml"]
    with pytest.raises(SkillPackageError, match="manifest"):
        parse_skill_package(_zip(files))


def test_skill_md_missing_rejected() -> None:
    files = _valid_files()
    del files["SKILL.md"]
    with pytest.raises(SkillPackageError, match="SKILL.md"):
        parse_skill_package(_zip(files))


def test_manifest_schema_error_rejected() -> None:
    with pytest.raises(SkillPackageError):
        parse_skill_package(_zip({**_valid_files(), "manifest.yaml": "name: [unclosed"}))


def test_knowledge_reference_missing_rejected() -> None:
    files = _valid_files()
    del files["knowledge/faq.md"]
    with pytest.raises(SkillPackageError, match="knowledge"):
        parse_skill_package(_zip(files))


def test_path_traversal_rejected() -> None:
    with pytest.raises(SkillPackageError, match="traversal"):
        parse_skill_package(_zip({**_valid_files(), "../evil.md": "x"}))


def test_symlink_rejected() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in _valid_files().items():
            data = content.encode("utf-8") if isinstance(content, str) else content
            archive.writestr(name, data)
        info = zipfile.ZipInfo("link.md")
        info.create_system = 3
        info.external_attr = (0o120777 << 16)
        archive.writestr(info, "SKILL.md")
    with pytest.raises(SkillPackageError, match="symlink"):
        parse_skill_package(buf.getvalue())


def test_zip_bomb_rejected() -> None:
    with pytest.raises(SkillPackageError, match="bomb|ratio|large"):
        parse_skill_package(
            _zip(_valid_files()),
            max_uncompressed_bytes=10,
        )


def test_workflow_dsl_markers_rejected() -> None:
    with pytest.raises(SkillPackageError, match="workflow|DSL|retry|compensation"):
        parse_skill_package(
            _zip(
                {
                    **_valid_files(),
                    "manifest.yaml": _manifest(
                        workflow={"steps": [{"retry": 3, "compensation": "undo"}]}
                    ),
                }
            )
        )


def test_build_skill_package_materializes_spec() -> None:
    bundle = parse_skill_package(_zip(_valid_files()))
    spec = build_skill_package(bundle)
    assert spec["instructions"] == "# Helper\nBe helpful."
    assert spec["knowledge_manifest"] == {"files": ["knowledge/faq.md"]}
