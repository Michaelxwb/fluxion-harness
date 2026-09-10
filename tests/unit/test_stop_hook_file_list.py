"""cf_stop_hook.session_edited_files 的回归门禁。

会话日志是 append-only：文件被删除或移动后，对应的 edit 事件仍然留在日志里。
若这些已不存在的路径被交给 validators，`py_compile` / `mypy` 会报
`No such file or directory`，使该 session 的收尾校验从此永久失败——一个与代码
本身无关的假失败，且不会自愈。

cf_stop_hook 位于 `.code-flow/scripts/`，不是可导入的包，故按路径用 importlib 加载。
"""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).parents[2]
SCRIPTS = ROOT / ".code-flow" / "scripts"


def _load_hook() -> ModuleType:
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location("cf_stop_hook_under_test", SCRIPTS / "cf_stop_hook.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _events(*files: str, sid: str = "s1") -> list[dict[str, object]]:
    return [{"sid": sid, "event": "edit", "data": {"file": name}} for name in files]


def test_deleted_files_are_not_returned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """核心回归：日志里已删除的路径必须被过滤。"""
    hook = _load_hook()
    (tmp_path / "kept.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(hook.cf_log, "read_events", lambda *a, **k: _events("kept.py", "deleted.py"))
    assert hook.session_edited_files(str(tmp_path), "s1") == ["kept.py"]


def test_moved_paths_keep_only_the_existing_target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """归档/移动场景：旧路径消失、新路径存在，只应返回新路径。"""
    hook = _load_hook()
    moved = tmp_path / "new" / "tasks.md"
    moved.parent.mkdir()
    moved.write_text("# plan\n", encoding="utf-8")
    monkeypatch.setattr(
        hook.cf_log,
        "read_events",
        lambda *a, **k: _events("old/tasks.md", "new/tasks.md"),
    )
    assert hook.session_edited_files(str(tmp_path), "s1") == ["new/tasks.md"]


def test_existing_files_are_deduped_and_other_sessions_excluded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hook = _load_hook()
    (tmp_path / "a.py").write_text("", encoding="utf-8")
    events = _events("a.py", "a.py") + _events("b.py", sid="other")
    monkeypatch.setattr(hook.cf_log, "read_events", lambda *a, **k: events)
    assert hook.session_edited_files(str(tmp_path), "s1") == ["a.py"]


def test_events_without_file_are_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hook = _load_hook()
    events: list[dict[str, object]] = [{"sid": "s1", "event": "edit", "data": {}}]
    monkeypatch.setattr(hook.cf_log, "read_events", lambda *a, **k: events)
    assert hook.session_edited_files(str(tmp_path), "s1") == []
