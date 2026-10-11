"""[S-08][S-09][S-11][S-12][RULE-ui-detail-001] Agent 详情 5 Tabs 契约测试。"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "apps/console-platform/frontend/src/modules/agent-management"


def _source(name: str) -> str:
    return (MODULE / name).read_text(encoding="utf-8")


def test_detail_header_and_five_tabs() -> None:
    """[RULE-ui-detail-001] 标题/副标题居左，对象操作在列表，详情保留标题与关闭 X，Tabs 其下（5 个）。"""
    source = _source("AgentDetailSideSheet.tsx")
    assert "title={detail?.name" in source
    assert "subtitle={detail?.key" in source
    assert "actions={" not in source
    assert 'data-testid={`edit-agent-${record.key}`}' in _source("AgentPage.tsx")
    for tab in ("basic", "skills", "mcp", "users", "channels"):
        assert f'itemKey="{tab}"' in source


def test_recent_activity_readonly_with_empty_state() -> None:
    """[S-09] 最近活动取审计记录、显示友好标签并提供活动空态。"""
    source = _source("AgentDetailSideSheet.tsx")
    assert "RecentActivity" in source
    assert "listAudits" in source
    assert "agent.detail.noActivity" in source
    services = _source("services/agents.ts")
    assert "resource_id" in services  # 按资源过滤


def test_relation_tabs_single_relation_and_no_switch() -> None:
    """[S-08][S-11][RULE-rel/auth 引用] 绑定选择器 + 行内解除；无绑定级启停开关。"""
    source = _source("AgentDetailSideSheet.tsx")
    for testid in ("bind-skill-select", "bind-mcp-select", "grant-user-select"):
        assert f'testId="{testid}"' in source
    for action in ("bindSkill", "unbindSkill", "bindMcp", "unbindMcp", "grantAgentUser", "revokeAgentUser"):
        assert action in source
    assert "Switch" not in source  # 无绑定级启停
    # 解除/取消均为二次确认软删除，统一走公共 ConfirmAction
    assert source.count("ConfirmAction") >= 3


def test_e10_binding_failure_keeps_tab() -> None:
    """[E-10] 绑定失败：RelationPicker catch 保持 Tab，Toast 由 ApiClient。"""
    source = _source("AgentDetailSideSheet.tsx")
    picker = source.split("const add = async")[1].split("};")[0]
    assert "catch {" in picker
