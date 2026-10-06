from pathlib import Path

import pytest
from muad_agent_core.skill import SkillManifest, SkillPackage, SkillPackageError
from muad_contracts import SkillExecutionMode
from muad_skill_sdk.skill_package import (
    MAX_DESCRIPTION_BYTES,
)
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
        "name: policy-check\ndescription: checks policy\nexecution: async\nplatform_label: example-platform",
    )

    package = SkillPackage.load(root)

    assert package.manifest.name == "policy-check"
    assert package.manifest.description == "checks policy"
    assert package.manifest.execution is SkillExecutionMode.ASYNC
    assert package.manifest.platform_label == "example-platform"


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


def test_multiline_description_is_rejected(tmp_path: Path) -> None:
    """多行 description 不是排版问题：它会被**逐字插进系统提示的目录行**。

    实测（2026-10-07）：`description: |` 后面跟一段任意文本，装配出的提示里就出现了伪造的
    `## 新规则` 段——而系统提示落在压缩的受保护前缀里，任何层都不会动它。
    """
    root = _write(tmp_path / "skill", "name: demo\ndescription: |\n  first\n  second")

    with pytest.raises(SkillPackageError, match="single line"):
        SkillPackage.load(root)


def test_multiline_name_and_platform_label_are_rejected(tmp_path: Path) -> None:
    """目录行里 `name` 与 `platform_label` 同样是插值字段，同样必须单行。"""
    named = _write(tmp_path / "a", 'name: "demo\\nx"\ndescription: demo')
    labelled = _write(tmp_path / "b", 'name: demo\ndescription: demo\nplatform_label: "x\\ny"')

    with pytest.raises(SkillPackageError, match="single line"):
        SkillPackage.load(named)
    with pytest.raises(SkillPackageError, match="single line"):
        SkillPackage.load(labelled)


def test_oversized_description_is_rejected(tmp_path: Path) -> None:
    """单条不能吃掉整个目录预算：在「超限丢整条」的规则下，一条超长描述会把其余全挤掉。"""
    root = _write(tmp_path / "skill", f"name: demo\ndescription: {'x' * (MAX_DESCRIPTION_BYTES + 1)}")

    with pytest.raises(SkillPackageError, match=f"{MAX_DESCRIPTION_BYTES} UTF-8 bytes"):
        SkillPackage.load(root)


def test_catalog_field_maxima_are_byte_based_and_inclusive(tmp_path: Path) -> None:
    """口径是 UTF-8 字节、边界是**严格大于**（`harness-skill` 的阈值规则）。"""
    exact = _write(tmp_path / "exact", f"name: demo\ndescription: {'x' * MAX_DESCRIPTION_BYTES}")
    assert SkillPackage.load(exact).manifest.description == "x" * MAX_DESCRIPTION_BYTES

    # 167 个汉字 = 501 字节 > 500，但字符数只有 167 —— 按字符数判会放它过去
    wide = _write(tmp_path / "wide", f"name: demo\ndescription: {'说' * 167}")
    assert len("说" * 167) == 167
    with pytest.raises(SkillPackageError):
        SkillPackage.load(wide)


def test_catalog_fields_are_stripped(tmp_path: Path) -> None:
    """前后空白不进目录行（`description: "  x  "` 会在提示词里留出诡异空档）。"""
    root = _write(tmp_path / "skill", 'name: "  demo  "\ndescription: "  checks policy  "')

    manifest = SkillPackage.load(root).manifest

    assert (manifest.name, manifest.description) == ("demo", "checks policy")
