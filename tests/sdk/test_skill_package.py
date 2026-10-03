import json
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
        "name: policy-check\ndescription: checks policy\nexecution: async\nplatform_label: example-platform",
        "# Policy Check\n\nFollow the steps.",
    )

    package = SkillPackage.load(root)

    assert package.manifest.name == "policy-check"
    assert package.manifest.description == "checks policy"
    assert package.manifest.execution is SkillExecutionMode.ASYNC
    assert package.manifest.platform_label == "example-platform"
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


def test_javascript_scripts_are_discoverable(tmp_path: Path) -> None:
    root = _write(tmp_path, "name: greeting\ndescription: greeting")
    (root / "scripts").mkdir()
    for name in ("run.mjs", "other.js", "common.cjs", "notes.txt"):
        (root / "scripts" / name).write_text("", encoding="utf-8")
    assert [path.name for path in SkillPackage.load(root).scripts()] == [
        "common.cjs",
        "other.js",
        "run.mjs",
    ]


@pytest.mark.parametrize(
    "entrypoint",
    [
        "../outside.mjs",
        "/tmp/run.mjs",
        "C:/run.mjs",
        "scripts\\run.mjs",
        "scripts/run.txt",
        "",
        42,
    ],
)
def test_invalid_manifest_entrypoint_is_rejected(tmp_path: Path, entrypoint: object) -> None:
    """**清单写坏了**要拒绝（路径逃逸 / 绝对路径 / 盘符 / 反斜杠 / 非脚本扩展名 / 空值）。

    **注意这里不再包含「文件不存在」**：那一档已改为**降级**而不是拒绝，
    见 `test_declared_entrypoint_missing_falls_back_...`。
    """
    root = _write(tmp_path, "name: greeting\ndescription: greeting")
    (root / "scripts").mkdir()
    (root / "scripts/run.txt").write_text("", encoding="utf-8")
    (root / "muad.skill.json").write_text(json.dumps({"entrypoint": entrypoint}))
    with pytest.raises(SkillPackageError):
        SkillPackage.load(root)


@pytest.mark.parametrize("manifest", ["[]", "{", '{"runtime":"shell"}'])
def test_invalid_script_manifest_is_rejected(tmp_path: Path, manifest: str) -> None:
    root = _write(tmp_path, "name: greeting\ndescription: greeting")
    (root / "muad.skill.json").write_text(manifest)
    with pytest.raises(SkillPackageError):
        SkillPackage.load(root)


def test_entrypoint_with_nul_byte_is_a_package_error(tmp_path: Path) -> None:
    """含 NUL 的 entrypoint 必须给出 `SkillPackageError`，而不是让 `ValueError` 逸出。

    NUL 不被既有的任何一条检查拦住（不是绝对路径、没有 `..`、没有反斜杠、扩展名合法），
    却会让 `(root / path).resolve()` 抛 `ValueError: lstat: embedded null character in path`。
    那不是包错误，于是它**以未捕获异常穿过 SDK 边界**：runtime 侧被兜底成
    `COMMON_INTERNAL_ERROR`，Console 导入侧直接 500 —— 两侧都拿不到真实的错误码。
    """
    root = _write(tmp_path, "name: greeting\ndescription: greeting")
    (root / "scripts").mkdir()
    (root / "scripts/run.mjs").write_text("", encoding="utf-8")
    (root / "muad.skill.json").write_text(json.dumps({"entrypoint": "scripts/run\x00.mjs"}))

    with pytest.raises(SkillPackageError):
        SkillPackage.load(root)


def test_declared_entrypoint_missing_falls_back_instead_of_killing_the_package(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """声明了入口但**文件不在** ⇒ 降级到常规搜索顺序，而不是把整个包判死。

    旧行为直接抛 `SkillPackageError`，于是一个同时带着**可用的** `scripts/main.py` 和一份
    过时/写错的清单入口的包，会**从"能跑"整个变成不可用**——而清单在过去是被忽略的
    （即"行为收紧"影响到了既有包）。降级的安全性：候选脚本都在**同一个包内**、同一作者、
    同一信任域，回退不等于越权。

    但降级**必须留痕**：静默忽略一个声明会掩盖作者的笔误，所以断言同时要求 WARNING。
    """
    root = _write(tmp_path, "name: greeting\ndescription: greeting")
    (root / "scripts").mkdir()
    (root / "scripts/main.py").write_text("", encoding="utf-8")
    (root / "muad.skill.json").write_text(json.dumps({"entrypoint": "scripts/run.mjs"}))

    with caplog.at_level("WARNING"):
        package = SkillPackage.load(root)  # 不抛

    assert [path.name for path in package.scripts()] == ["main.py"]
    assert any("skill_entrypoint_missing" in record.getMessage() for record in caplog.records), (
        "降级必须留痕，否则作者的笔误被静默吞掉"
    )


def test_script_extension_check_is_case_insensitive(tmp_path: Path) -> None:
    """扩展名判定**大小写不敏感** —— 与 Console 导入白名单**同源**。

    Console 用 `path.suffix.lower()` 判定成员是否在白名单内，执行侧若用精确比较，
    `scripts/run.JS` 就会**能导入、却在执行时被判成"不支持的扩展名"**（同样的错位还包括
    `UP.PY` 被当成非 Python 脚本交给 Node 执行）。以**门禁侧（Console）为准**：
    它能放行的，下游必须能执行。
    """
    root = _write(tmp_path, "name: greeting\ndescription: greeting")
    (root / "scripts").mkdir()
    (root / "scripts/run.JS").write_text("", encoding="utf-8")
    (root / "scripts/helper.MJS").write_text("", encoding="utf-8")
    (root / "muad.skill.json").write_text(json.dumps({"entrypoint": "scripts/run.JS"}))

    package = SkillPackage.load(root)  # 不抛

    assert [path.name for path in package.scripts()] == ["helper.MJS", "run.JS"]
