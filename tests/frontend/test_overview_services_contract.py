"""[B-203][RULE-front-001] 概览前端 service 层与类型契约（设计 §3.4/§3.5）。

前端 API 只经 services/ 收口（共享 apiClient 自动带 X-Locale/X-Request-Id/CSRF），组件不裸用
axios/fetch；后端 snake_case 与前端 camelCase 的字段映射只发生在 service 层
（`enabled_agents` → `enabledAgents`、`next_fire_at` → `nextFireAt`）。
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

# 设计 §3.4 的字段契约：前端 camelCase ↔ 后端 snake_case
FIELD_MAPPING = (
    ("enabledAgents", "enabled_agents"),
    ("enabledSkills", "enabled_skills"),
    ("activeTasks", "active_tasks"),
    ("activeSchedules", "active_schedules"),
    ("taskId", "task_id"),
    ("intentKey", "intent_key"),
    ("agentId", "agent_id"),
    ("agentName", "agent_name"),
    ("actorUserId", "actor_user_id"),
    ("actorUserName", "actor_user_name"),
    ("triggerType", "trigger_type"),
    ("deliveryStatus", "delivery_status"),
    ("startedAt", "started_at"),
    ("finishedAt", "finished_at"),
    ("deadlineAt", "deadline_at"),
    ("createTime", "create_time"),
    ("scheduleId", "schedule_id"),
    ("nextFireAt", "next_fire_at"),
    ("lastFireAt", "last_fire_at"),
    ("timezone", "timezone"),
)

# 设计 §3.4 的四个类型；props 契约由 TASK-006/007 落地后在本文件追加断言
DECLARED_TYPES = ("OverviewKpis", "RecentTaskItem", "NextScheduleItem", "OverviewData")


def _source(path: Path) -> str:
    assert path.exists(), f"缺少前端模块文件：{path}"
    return path.read_text(encoding="utf-8")


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _block(source: str, header: str) -> str:
    """取 `header` 声明起至下一个顶格 `}` 的声明体（压缩空白，便于字段断言）。"""
    assert header in source, f"缺少声明：{header}"
    body = source[source.index(header) + len(header) :]
    end = body.find("\n}")
    assert end != -1, f"{header} 声明未闭合"
    return _compact(body[:end])


def _function(source: str, name: str) -> str:
    """取 `export async function <name>(...)` 的实现（到下一个顶层 export 声明为止）。"""
    marker = f"export async function {name}("
    assert marker in source, f"缺少 service 方法：{name}"
    body = source[source.index(marker) :]
    following = body.find("\nexport ", 1)
    return body if following == -1 else body[:following]


def _strip_comments(source: str) -> str:
    return re.sub(r"//[^\n]*", "", re.sub(r"/\*[\s\S]*?\*/", "", source))


def _string_literals(source: str) -> list[str]:
    groups = re.findall(r"'([^'\n]*)'|\"([^\"\n]*)\"|`([^`\n]*)`", _strip_comments(source))
    return [text for group in groups for text in group if text]


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


def test_service_is_read_only_single_aggregate_call() -> None:
    """[设计 §3.1] 概览只读：只有一个聚合方法，取 `/overview`，且不发写请求。"""
    source = _source(SERVICE)
    methods = re.findall(r"export async function (\w+)\(", source)
    assert methods == ["getOverview"], f"概览 service 应只暴露 getOverview，实际：{methods}"
    body = _function(source, "getOverview")
    assert "'/overview'" in body, "聚合请求路径必须是 /overview（apiClient 已含 /api/v1 前缀）"
    assert "api.post" not in source and "api.put" not in source and "api.delete" not in source, (
        "概览为只读模块，不得出现写请求"
    )


def test_field_mapping_lives_in_service_layer() -> None:
    """[RULE-front-001] snake_case → camelCase 映射只在 service 层，且覆盖设计 §3.4 全部字段。"""
    service = _source(SERVICE)
    types = _source(TYPES)

    for camel, snake in FIELD_MAPPING:
        assert snake in service, f"service 未声明后端字段 {snake}"
        assert camel in service, f"service 未映射出 {camel}"
    # 组件契约（types.ts）只出现 camelCase：不得泄漏后端命名。
    # `timezone` 两语言同名，不构成映射，故跳过。
    for camel, snake in FIELD_MAPPING:
        if camel == snake:
            continue
        assert snake not in types, f"types.ts 泄漏了后端字段名 {snake}"


def test_types_declare_design_contract() -> None:
    """types.ts 声明的接口与 design §3.4 一致（含两个联合类型）。"""
    types = _source(TYPES)
    for name in DECLARED_TYPES:
        assert f"export interface {name}" in types, f"缺少类型 {name}"
    assert "export type TaskTriggerType = 'IMMEDIATE' | 'SCHEDULED';" in types
    assert "export type TaskDeliveryStatus = 'PENDING' | 'SENT' | 'FAILED' | 'NONE';" in types
    # 关键字段归属：列表项与 KPI 不得混装
    kpis = _block(types, "export interface OverviewKpis")
    for field in ("enabledAgents", "enabledSkills", "activeTasks", "activeSchedules"):
        assert field in kpis, f"OverviewKpis 缺字段 {field}"


def test_service_and_types_have_no_hardcoded_copy() -> None:
    """[RULE-front-001] 文案一律走 i18n key：service/types 的字符串字面量不得含中文。"""
    for path in (SERVICE, TYPES):
        literals = _string_literals(_source(path))
        offenders = [text for text in literals if CJK.search(text)]
        assert offenders == [], f"{path.name} 存在硬编码文案：{offenders}"
