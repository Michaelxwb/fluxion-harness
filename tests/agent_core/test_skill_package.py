from pathlib import Path

import pytest
from muad_agent_core.skill import SkillManifest, SkillPackage, SkillPackageError
from muad_contracts import SkillExecutionMode
from muad_skill_sdk.skill_package import (
    SkillManifest as SdkSkillManifest,
)
from muad_skill_sdk.skill_package import (
    SkillPackage as SdkSkillPackage,
)
from muad_skill_sdk.skill_package import (
    SkillPackageError as SdkSkillPackageError,
)


def test_agent_core_reexports_skill_sdk_names() -> None:
    assert SkillPackage is SdkSkillPackage
    assert SkillManifest is SdkSkillManifest
    assert SkillPackageError is SdkSkillPackageError


def _write(root: Path, frontmatter: str, body: str = "# Skill") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "SKILL.md").write_text(f"---\n{frontmatter}\n---\n\n{body}\n", encoding="utf-8")
    return root


def test_loads_manifest_from_frontmatter(tmp_path: Path) -> None:
    root = _write(
        tmp_path / "policy-check",
        "name: policy-check\ndescription: checks policy\nexecution: async\nplatform_label: MSS",
    )

    package = SkillPackage.load(root)

    assert package.manifest.name == "policy-check"
    assert package.manifest.description == "checks policy"
    assert package.manifest.execution is SkillExecutionMode.ASYNC
    assert package.manifest.platform_label == "MSS"


def test_execution_defaults_to_sync_and_label_is_optional(tmp_path: Path) -> None:
    root = _write(tmp_path / "skill", "name: demo\ndescription: demo skill")

    manifest = SkillPackage.load(root).manifest

    assert manifest.execution is SkillExecutionMode.SYNC
    assert manifest.platform_label is None


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


def test_scripts_empty_without_scripts_dir(tmp_path: Path) -> None:
    root = _write(tmp_path / "skill", "name: demo\ndescription: demo")

    assert SkillPackage.load(root).scripts() == ()
    assert SkillPackage.load(root).resources() == ()
