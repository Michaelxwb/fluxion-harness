from pathlib import Path

import pytest
from muad_contracts import SkillExecutionMode
from muad_skill_sdk import SkillManifest, SkillPackage, SkillPackageError


def _write(root: Path, frontmatter: str, body: str = "# Skill") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "SKILL.md").write_text(f"---\n{frontmatter}\n---\n\n{body}\n", encoding="utf-8")
    return root


def test_loads_manifest_and_instructions_from_skill_md(tmp_path: Path) -> None:
    root = _write(
        tmp_path / "policy-check",
        "name: policy-check\ndescription: checks policy\nexecution: async\nplatform_label: MSS",
        "# Policy Check\n\nFollow the steps.",
    )

    package = SkillPackage.load(root)

    assert package.manifest.name == "policy-check"
    assert package.manifest.description == "checks policy"
    assert package.manifest.execution is SkillExecutionMode.ASYNC
    assert package.manifest.platform_label == "MSS"
    assert package.instructions == "# Policy Check\n\nFollow the steps."


def test_execution_defaults_to_sync_and_label_is_optional(tmp_path: Path) -> None:
    root = _write(tmp_path / "skill", "name: demo\ndescription: demo skill")

    manifest = SkillPackage.load(root).manifest

    assert manifest.execution is SkillExecutionMode.SYNC
    assert manifest.platform_label is None
    assert isinstance(manifest, SkillManifest)


def test_missing_skill_md_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(SkillPackageError) as error:
        SkillPackage.load(tmp_path / "missing")
    assert "SKILL.md" in str(error.value)


def test_frontmatter_is_required(tmp_path: Path) -> None:
    root = tmp_path / "skill"
    root.mkdir()
    (root / "SKILL.md").write_text("# no frontmatter\n", encoding="utf-8")

    with pytest.raises(SkillPackageError):
        SkillPackage.load(root)


def test_unterminated_frontmatter_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "skill"
    root.mkdir()
    (root / "SKILL.md").write_text("---\nname: demo\n", encoding="utf-8")

    with pytest.raises(SkillPackageError):
        SkillPackage.load(root)


def test_bad_yaml_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "skill"
    root.mkdir()
    (root / "SKILL.md").write_text("---\nname: [unclosed\ndescription: x\n---\n", encoding="utf-8")

    with pytest.raises(SkillPackageError):
        SkillPackage.load(root)


def test_missing_required_fields_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(SkillPackageError):
        SkillPackage.load(_write(tmp_path / "a", "description: no name"))
    with pytest.raises(SkillPackageError):
        SkillPackage.load(_write(tmp_path / "b", "name: no-description"))


def test_invalid_execution_mode_is_rejected(tmp_path: Path) -> None:
    root = _write(tmp_path / "skill", "name: demo\ndescription: demo\nexecution: later")

    with pytest.raises(SkillPackageError):
        SkillPackage.load(root)


def test_scripts_are_sorted_and_filtered(tmp_path: Path) -> None:
    root = _write(tmp_path / "skill", "name: demo\ndescription: demo")
    scripts = root / "scripts"
    scripts.mkdir()
    (scripts / "b.py").write_text("", encoding="utf-8")
    (scripts / "a.py").write_text("", encoding="utf-8")
    (scripts / "notes.txt").write_text("", encoding="utf-8")

    names = [path.name for path in SkillPackage.load(root).scripts()]

    assert names == ["a.py", "b.py"]


def test_resources_include_references_and_assets(tmp_path: Path) -> None:
    root = _write(tmp_path / "skill", "name: demo\ndescription: demo")
    (root / "references").mkdir()
    (root / "references" / "guide.md").write_text("", encoding="utf-8")
    (root / "assets").mkdir()
    (root / "assets" / "template.txt").write_text("", encoding="utf-8")
    (root / "scripts").mkdir()
    (root / "scripts" / "main.py").write_text("", encoding="utf-8")

    names = [path.name for path in SkillPackage.load(root).resources()]

    assert names == ["template.txt", "guide.md"]


def test_scripts_and_resources_are_empty_without_directories(tmp_path: Path) -> None:
    root = _write(tmp_path / "skill", "name: demo\ndescription: demo")

    package = SkillPackage.load(root)

    assert package.scripts() == ()
    assert package.resources() == ()
