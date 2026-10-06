"""[B-203 v2][RULE-front-001] 概览前端 service 层与类型契约（指标化改造后的事实）。

前端 API 只经 services/ 收口（共享 apiClient 自动带 X-Locale/X-Request-Id/CSRF），组件不裸用
axios/fetch；后端 snake_case 与前端 camelCase 的字段映射只发生在 service 层
（`enabled_agents` → `enabledAgents`、`task_trend` → `taskTrend`）。

v2：页面为纯指标页——`getOverview` 只消费 KPI（接口仍返回两组列表，前端不使用）；
新增 `getOverviewMetrics`（`/overview/metrics`，指标图数据）。列表项类型与映射已随列表移除。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "apps/console-platform/frontend/src/modules/overview-dashboard"
SERVICE = MODULE / "services/overviewService.ts"
TYPES = MODULE / "types.ts"

# 可见中文与全角字符：去掉注释后仍出现在字符串字面量里即视为硬编码文案
CJK = re.compile(r"[　-〿一-鿿！-～]")

# KPI 字段映射（snake_case ↔ camelCase）
KPI_MAPPING = (
    ("enabledAgents", "enabled_agents"),
    ("enabledSkills", "enabled_skills"),
    ("activeTasks", "active_tasks"),
    ("activeSchedules", "active_schedules"),
)

# 指标字段映射
METRICS_MAPPING = (
    ("taskTrend", "task_trend"),
    ("taskStatus", "task_status"),
    ("timezone", "timezone"),
)

DECLARED_TYPES = ("OverviewKpis", "TaskTrendPoint", "OverviewMetrics", "OverviewData")

REMOVED_TYPES = ("RecentTaskItem", "NextScheduleItem")


def _source(path: Path) -> str:
    assert path.exists(), f"缺少前端模块文件：{path}"
    return path.read_text(encoding="utf-8")


def _function(source: str, name: str) -> str:
    """取 `export async function <name>(...)` 的实现（到下一个顶层 export 声明为止）。"""
    marker = f"export async function {name}("
    assert marker in source, f"缺少 service 方法：{name}"
    body = source[source.index(marker) :]
    following = body.find("\nexport ", 1)
    return body if following == -1 else body[:following]


def test_module_files_exist() -> None:
    """模块目录与两个契约文件齐备。"""
    _source(SERVICE)
    _source(TYPES)


def test_service_uses_shared_api_client_only() -> None:
    """[RULE-front-001] 只经共享 apiClient；不裸用 axios/fetch，也不自建实例。"""
    source = _source(SERVICE)
    assert "from '../../../api/client'" in source
    assert "api.get" in source
    assert "axios" not in source
    assert "fetch(" not in source
    assert "create(" not in source


def test_service_is_read_only_with_two_aggregate_calls() -> None:
    """概览只读：KPI 聚合 + 指标聚合两个方法，不发写请求。"""
    source = _source(SERVICE)
    methods = re.findall(r"export async function (\w+)\(", source)
    assert methods == ["getOverview", "getOverviewMetrics"], f"service 方法漂移，实际：{methods}"
    overview = _function(source, "getOverview")
    assert "'/overview'" in overview, "KPI 聚合请求路径必须是 /overview"
    metrics = _function(source, "getOverviewMetrics")
    assert "'/overview/metrics'" in metrics, "指标聚合请求路径必须是 /overview/metrics"
    assert "api.post" not in source and "api.put" not in source and "api.delete" not in source, (
        "概览为只读模块，不得出现写请求"
    )


def test_field_mapping_lives_in_service_layer() -> None:
    """[RULE-front-001] snake_case → camelCase 映射只在 service 层。"""
    service = _source(SERVICE)
    types = _source(TYPES)
    for camel, snake in (*KPI_MAPPING, *METRICS_MAPPING):
        assert camel in service, f"service 未映射出 {camel}"
        if camel != snake:
            assert snake in service, f"service 未声明后端字段 {snake}"
            assert snake not in types, f"types.ts 泄漏了后端字段名 {snake}"


def test_types_declare_metrics_contract() -> None:
    """types.ts 与指标化改造后的契约一致：列表项类型已移除，指标类型齐备。"""
    types = _source(TYPES)
    for name in DECLARED_TYPES:
        assert f"export interface {name}" in types, f"缺少类型 {name}"
    for name in REMOVED_TYPES:
        assert f"interface {name}" not in types, f"列表项类型 {name} 应已随列表移除"
    kpis = types[types.index("export interface OverviewKpis") :]
    kpis = kpis[: kpis.find("\n}")]
    for field in ("enabledAgents", "enabledSkills", "activeTasks", "activeSchedules"):
        assert field in kpis, f"OverviewKpis 缺字段 {field}"
    trend = types[types.index("export interface TaskTrendPoint") :]
    trend = trend[: trend.find("\n}")]
    for field in ("date", "total", "failed"):
        assert field in trend, f"TaskTrendPoint 缺字段 {field}"


def test_service_and_types_have_no_hardcoded_copy() -> None:
    """[RULE-front-001] 文案一律走 i18n key：service/types 的字符串字面量不得含中文。"""
    for path in (SERVICE, TYPES):
        literals = re.findall(r"'([^'\n]*)'|\"([^\"\n]*)\"", _source(path))
        flat = [text for pair in literals for text in pair if text]
        offenders = [text for text in flat if CJK.search(text)]
        assert offenders == [], f"{path.name} 存在硬编码文案：{offenders}"
