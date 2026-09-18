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
LOCALE_DIR = ROOT / "apps/console-platform/frontend/src/locales"


def _write(tmp_path: Path, name: str, payload: dict[str, object]) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
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
