"""后台任务列表的状态列：触发方式/任务状态/投递状态三枚图标合并在一列。

旧实现把三者拆成三列（96 + 108 + 128 = 332px），在 1280 宽屏幕上挤压了 Skill/时间/失败原因。
现在只保留一列（104px），每枚图标带 Tooltip 与 `aria-label`，既省宽又可读、可测试。
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "apps/console-platform/frontend"
MODULE = FRONTEND / "src/modules/task-schedule"
PAGE_TSX = MODULE / "TaskPage.tsx"
ICONS_TSX = MODULE / "TaskStatusIcons.tsx"

ESBUILD = FRONTEND / "node_modules/.bin/esbuild"
NODE = shutil.which("node")


def _read(path: Path) -> str:
    assert path.exists(), f"缺少前端文件：{path}"
    return path.read_text(encoding="utf-8")


def _compact(source: str) -> str:
    stripped = re.sub(r"//[^\n]*", "", re.sub(r"/\*[\s\S]*?\*/", "", source))
    return re.sub(r"\s+", "", stripped)


def _toolchain_problem() -> str | None:
    if NODE is None:
        return "node 不在 PATH（本用例需要真实执行前端编译产物）"
    if not ESBUILD.exists():
        return f"缺少 {ESBUILD}；先跑 npm --prefix apps/console-platform/frontend ci 再执行本用例"
    return None


def _compile_tsx(source: Path, outfile: Path) -> str:
    completed = subprocess.run(
        [
            str(ESBUILD),
            str(source),
            "--bundle",
            "--format=esm",
            "--loader:.tsx=tsx",
            f"--outfile={outfile}",
            "--external:react",
            "--external:react-dom",
            "--external:react-i18next",
            "--external:react-router-dom",
            "--external:@douyinfe/semi-ui",
            "--external:@douyinfe/semi-ui/lib/es/form",
            "--external:@douyinfe/semi-icons",
        ],
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, f"编译失败 {source.name}：{completed.stderr}"
    return outfile.read_text(encoding="utf-8")


def test_task_page_uses_one_combined_status_column() -> None:
    """三列合并为一列：列表只引用 TaskStatusIcons，不再渲染三套文本/Tag。"""
    page = _read(PAGE_TSX)
    assert "TaskStatusIcons" in page and "<TaskStatusIcons" in page
    assert page.count("task.columns.statusIcons") == 1
    assert "task.columns.triggerType" not in page, "触发方式不再是独立列"
    assert "task.columns.deliveryStatus" not in page, "投递状态不再是独立列"
    assert "StatusTag" not in page, "列表状态已改为图标，不再用 Tag"
    status_column = page.index("task.columns.statusIcons")
    assert "width: 104" in page[status_column : status_column + 260]
    for removed in (
        "task.columns.triggerType",
        "task.columns.deliveryStatus",
        "STATUS_COLORS",
        "statusOptions",
    ):
        assert removed not in _compact(page), f"旧的三列实现残留：{removed}"


def test_status_icons_express_three_facts_with_accessible_labels() -> None:
    """三枚图标分别表达触发/状态/投递，且都有 Tooltip + aria-label + testid。"""
    source = _read(ICONS_TSX)
    compact = _compact(source)
    assert compact.count("<StatusIcon") == 3
    assert "task.columns.triggerType" in source
    assert "task.columns.status" in source
    assert "task.columns.deliveryStatus" in source
    assert 'role="img"' in source and "aria-label" in source
    assert "<Tooltip" in source
    assert "data-testid={props.testId}" in source
    assert "testId={`task-trigger-" in source
    assert "testId={`task-status-" in source
    assert "testId={`task-delivery-" in source
    for token in (
        "IMMEDIATE",
        "SCHEDULED",
        "QUEUED",
        "RUNNING",
        "WAITING",
        "COMPLETED",
        "FAILED",
        "CANCELLED",
        "PENDING",
        "SENT",
        "NONE",
    ):
        assert token in source, f"图标映射缺状态：{token}"
    for icon in (
        "IconBolt",
        "IconCalendarClock",
        "IconHourglass",
        "IconSpin",
        "IconPause",
        "IconTickCircle",
        "IconAlertCircle",
        "IconMinusCircle",
        "IconSend",
        "IconMail",
        "IconAlertTriangle",
        "IconMinus",
    ):
        assert icon in source, f"缺少图标：{icon}"


def test_status_icons_compile_to_real_typescript_artifacts(tmp_path: Path) -> None:
    """真实 TSX 编译产物：三枚图标、tooltip 与 testid 都在模块图里。"""
    problem = _toolchain_problem()
    if problem is not None:
        pytest.skip(problem)

    icons = _compile_tsx(ICONS_TSX, tmp_path / "task-status-icons.mjs")
    assert "task-status-icons" in icons
    assert 'role: "img"' in icons and "aria-label" in icons
    for testid_prefix in ("task-trigger-", "task-status-", "task-delivery-"):
        assert testid_prefix in icons
    for icon in (
        "IconBolt",
        "IconCalendarClock",
        "IconSend",
        "IconMail",
        "IconAlertTriangle",
        "IconTickCircle",
    ):
        assert icon in icons

    page = _compile_tsx(PAGE_TSX, tmp_path / "task-page.mjs")
    assert "TaskStatusIcons" in page
