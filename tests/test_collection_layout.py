"""测试文件的导入名必须唯一：撞名时 `pytest tests/` 连收集都过不去。

pytest 默认的 prepend 导入模式按**路径**推模块名：目录链上有 `__init__.py` 的算包段，
第一个没有 `__init__.py` 的目录成为 basedir、它下面那一段起算模块名。于是两个**都没有**
包标记的目录里出现同名 `test_*.py` ⇒ 同一个模块名指向两个文件 ⇒ 收集阶段报
`import file mismatch`（报错里那句「remove __pycache__」是误导），而**单文件跑全绿**。
实例：2026-10-04 `tests/gateway/test_execution_progress.py` 与
`tests/acceptance/im_gateway/test_execution_progress.py`。约定见 `harness-test.md`。
"""

from __future__ import annotations

import collections
import pathlib

TESTS_ROOT = pathlib.Path(__file__).resolve().parent


def _import_name(path: pathlib.Path) -> str:
    """按 pytest 的 prepend 规则推出该文件会被导入成什么模块名。"""
    parts = [path.stem]
    directory = path.parent
    while (directory / "__init__.py").exists() and directory != TESTS_ROOT:
        parts.append(directory.name)
        directory = directory.parent
    return ".".join(reversed(parts))


def test_no_two_test_modules_share_an_import_name() -> None:
    groups: dict[str, list[str]] = collections.defaultdict(list)
    for path in sorted(TESTS_ROOT.rglob("test_*.py")):
        groups[_import_name(path)].append(str(path.relative_to(TESTS_ROOT)))

    collisions = {name: paths for name, paths in groups.items() if len(paths) > 1}
    assert collisions == {}, (
        "这些测试文件会被导入成同一个模块名，`pytest tests/` 会在收集阶段中断"
        "（单文件跑却是绿的）；给其中一个目录补空的 `__init__.py`，或改个更具体的名字：\n"
        + "\n".join(f"  {name} ← {paths}" for name, paths in collisions.items())
    )
