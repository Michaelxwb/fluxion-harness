"""[B-205][RULE-ui-001] 概览运营列表与运行关系卡源码契约（设计 §3.3/§3.4/§3.6）。

- 两个列表是**仪表盘内的预览块**（≤5 行、无分页、无工具栏），故用 Semi `Table` 关闭分页，
  不套 `RemoteTable`（那是列表页约定：左上操作 + 右上筛选 + 右下分页）；
- 主展示字段（Task ID / Schedule 名称）即详情入口，通过 props 回调上抛，组件自身不导航；
- 空态用公共 `EmptyState` 并给出「查看全部」；状态/时间分别复用 `StatusTag`/`DateTimeText`；
- `RuntimeRelationCard` 纯静态、无 props，文案全部取自词条。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "apps/console-platform/frontend/src/modules/overview-dashboard"
TASKS = MODULE / "components/RecentTaskList.tsx"
SCHEDULES = MODULE / "components/NextScheduleList.tsx"
RELATION = MODULE / "components/RuntimeRelationCard.tsx"

CJK = re.compile(r"[　-〿一-鿿！-～]")


def _source(path: Path) -> str:
    assert path.exists(), f"缺少前端模块文件：{path}"
    return path.read_text(encoding="utf-8")


def _strip_comments(source: str) -> str:
    return re.sub(r"//[^\n]*", "", re.sub(r"/\*[\s\S]*?\*/", "", source))


def _string_literals(source: str) -> list[str]:
    groups = re.findall(r"'([^'\n]*)'|\"([^\"\n]*)\"|`([^`\n]*)`", _strip_comments(source))
    return [text for group in groups for text in group if text]


def test_blocks_are_previews_not_list_pages() -> None:
    """[设计 §3.3] 预览块：Semi Table 关闭分页；不套列表页专用的 RemoteTable/ModuleToolbar。"""
    for path in (TASKS, SCHEDULES):
        body = _source(path)
        code = _strip_comments(body)  # 负向断言只看代码：文档注释里可以提到这些名字
        assert "Table" in code and "pagination={false}" in code, f"{path.name} 应为关闭分页的局部清单"
        assert "RemoteTable" not in code, f"{path.name} 是预览块，不应用列表页的 RemoteTable"
        assert "ModuleToolbar" not in code, f"{path.name} 不得自带列表页工具栏"
        assert "slice(" not in code, f"{path.name} 不得在前端截断行数（≤5 由后端 LIMIT 保证）"


def test_main_field_opens_detail_via_props() -> None:
    """[设计 §3.3.1] 主展示字段即详情入口：经 props 回调上抛，组件自身不导航。"""
    tasks = _source(TASKS)
    schedules = _source(SCHEDULES)
    assert "onOpenTask(item.taskId)" in tasks, "Task ID 须可打开详情"
    assert "onOpenSchedule(item.scheduleId)" in schedules, "Schedule 名称须可打开详情"
    for body in (tasks, schedules):
        code = _strip_comments(body)
        assert "useNavigate" not in code and "navigate(" not in code, "预览块不得自行导航（由页面接线）"


def test_empty_state_and_view_all() -> None:
    """[设计 §3.6] 空态用公共 EmptyState 并给「查看全部」；有数据时也保留查看全部入口。"""
    for path, testid in ((TASKS, "recent-tasks-view-all"), (SCHEDULES, "next-schedules-view-all")):
        body = _source(path)
        assert "<EmptyState" in body, f"{path.name} 空态须用公共 EmptyState"
        assert body.count(f'data-testid="{testid}"') == 2, (
            f"{path.name} 的「查看全部」应在空态与有数据时都可用"
        )
        assert "onViewAll" in body


def test_shared_components_reused() -> None:
    """[设计 §3.3 复用约束] 状态/时间/空态复用公共组件，不自造。"""
    tasks = _source(TASKS)
    schedules = _source(SCHEDULES)
    assert "StatusTag" in tasks and "DateTimeText" in tasks
    assert "StatusTag" in schedules and "DateTimeText" in schedules
    assert "components/common/EmptyState" in tasks and "components/common/EmptyState" in schedules


def test_runtime_relation_card_is_static_and_i18n_driven() -> None:
    """[设计 §3.3 CMP-03] 运行关系卡纯静态、无 props、无取数；文案全部取自词条。"""
    body = _source(RELATION)
    assert "export function RuntimeRelationCard()" in body, "该卡不得接收 props"
    assert "useOverview" not in body and "services/" not in body, "静态说明卡不得取数"
    assert "t('overview.runtimeRelation.title')" in body
    assert "t('overview.runtimeRelation.description')" in body


def test_no_hardcoded_copy_or_http_client() -> None:
    """[RULE-front-001] 文案走 i18n；预览块不直接用 HTTP 客户端。"""
    for path in (TASKS, SCHEDULES, RELATION):
        source = _source(path)
        offenders = [text for text in _string_literals(source) if CJK.search(text)]
        assert offenders == [], f"{path.name} 存在硬编码文案：{offenders}"
        assert "axios" not in source and "fetch(" not in source
