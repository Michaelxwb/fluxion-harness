from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

_spec = importlib.util.spec_from_file_location(
    "check_frontend_i18n", ROOT / "scripts/check_frontend_i18n.py"
)
assert _spec and _spec.loader
checker = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(checker)
compare_locale_files = checker.compare_locale_files
compare_source_references = checker.compare_source_references
LOCALE_DIR = ROOT / "apps/console-platform/frontend/src/locales"
SOURCE_DIR = ROOT / "apps/console-platform/frontend/src"


def _write(tmp_path: Path, name: str, payload: dict[str, object]) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _write_source(search_dir: Path, rel: str, body: str) -> Path:
    path = search_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def test_e02_missing_locale_key_is_reported(tmp_path: Path) -> None:
    zh = _write(tmp_path, "zh-CN.json", {"hello": "你好", "nested": {"key": "值"}})
    en = _write(tmp_path, "en-US.json", {"hello": "Hello"})
    problems, count = compare_locale_files(zh, en)
    assert count == 2
    assert any("Missing in en-US" in item and "nested.key" in item for item in problems)


def test_e02_empty_and_extra_values_are_reported(tmp_path: Path) -> None:
    zh = _write(tmp_path, "zh-CN.json", {"hello": "你好"})
    en = _write(tmp_path, "en-US.json", {"hello": "   ", "extra": "Extra"})
    problems, _ = compare_locale_files(zh, en)
    assert any("Empty in en-US" in item for item in problems)
    assert any("Missing in zh-CN" in item for item in problems)


def test_e02_real_locale_files_are_aligned() -> None:
    problems, count = compare_locale_files(LOCALE_DIR / "zh-CN.json", LOCALE_DIR / "en-US.json")
    assert problems == []
    assert count > 0


def test_e02_referenced_but_undefined_key_is_reported(tmp_path: Path) -> None:
    zh = _write(tmp_path, "zh-CN.json", {"common": {"save": "保存"}})
    src = tmp_path / "src"
    _write_source(src, "components/Panel.tsx", "const a = t('common.save');\nconst b = t('gone.missing');\n")

    problems = compare_source_references(zh, src)
    assert len(problems) == 1
    assert "gone.missing" in problems[0]
    assert "Panel.tsx:2" in problems[0]


def test_e02_dynamic_and_non_key_calls_are_not_treated_as_references(tmp_path: Path) -> None:
    zh = _write(tmp_path, "zh-CN.json", {"common": {"save": "保存"}})
    src = tmp_path / "src"
    _write_source(
        src,
        "components/Panel.tsx",
        "const a = t(`audit.resultStatus.${status}`);\nconst b = t(variable);\nconst c = t('保存');\n",
    )

    assert compare_source_references(zh, src) == []


def test_e02_real_frontend_source_references_are_defined() -> None:
    """回归：曾出现 t('task.columns.updateTime') 有引用但两侧词条都没有而 checker 仍通过。"""
    assert compare_source_references(LOCALE_DIR / "zh-CN.json", SOURCE_DIR) == []
