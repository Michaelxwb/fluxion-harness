"""RULE-backend-directory-001 静态守护（Phase 5 TASK-001 verifier）。

backend-directory-structure spec：provider 按 PluginType 落 `plugins/<type>/`
一级子包（深度 ≤3）、测试目录与源码同构（`tests/plugins/` ↔ `src/fluxion/plugins/`）、
单文件 ≤500 行。
"""

from __future__ import annotations

from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _BACKEND_ROOT / "src" / "fluxion"
_PLUGINS_ROOT = _SRC_ROOT / "plugins"
_TESTS_ROOT = _BACKEND_ROOT / "tests"

_MAX_FILE_LINES = 500


def _py_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def test_directory_provider_lives_in_typed_subpackage() -> None:
    """provider 必须落 `plugins/<plugin_type>/` 一级子包（artifact/secret/...）。"""
    artifact_dir = _PLUGINS_ROOT / "artifact"
    assert (artifact_dir / "local_fs.py").is_file(), "LocalFileArtifactStore 须落 plugins/artifact/local_fs.py"
    assert (artifact_dir / "s3.py").is_file(), "S3CompatibleArtifactStore 须落 plugins/artifact/s3.py"


def test_directory_plugin_subpackage_depth_bounded() -> None:
    """plugins 子包深度 ≤3：`plugins/<type>/<file>.py`，不出现更深层嵌套。"""
    deep: list[str] = []
    for path in _py_files(_PLUGINS_ROOT):
        relative = path.relative_to(_PLUGINS_ROOT)
        # relative parts 含文件名：plugins/<type>/<file>.py → 3 段
        if len(relative.parts) > 3:
            deep.append(str(relative))
    assert not deep, f"plugins/ 出现超过 3 层的嵌套：{deep}"


def test_directory_tests_mirror_source_layout() -> None:
    """测试目录与源码同构：src/fluxion/plugins/ ↔ tests/plugins/。"""
    assert (_TESTS_ROOT / "plugins" / "test_artifact_store.py").is_file(), (
        "plugins 源码的测试须落 tests/plugins/（与源码同构）"
    )


def test_directory_plugin_files_within_line_budget() -> None:
    """spec Avoid：单文件不超过 500 行。"""
    oversized: list[str] = []
    for path in _py_files(_PLUGINS_ROOT):
        line_count = len(path.read_text(encoding="utf-8").splitlines())
        if line_count > _MAX_FILE_LINES:
            oversized.append(f"{path.relative_to(_PLUGINS_ROOT)}={line_count}")
    assert not oversized, f"plugins/ 超过 500 行的文件：{oversized}"
