"""[S-05][S-06][RULE-ui-detail-001] MCP 详情/工具明细前端契约。"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "apps/console-platform/frontend/src/modules/mcp-management"


def _source(name: str) -> str:
    return (MODULE / name).read_text(encoding="utf-8")


def test_header_actions_inline_right_with_tabs_below() -> None:
    """[RULE-ui-detail-001] Header 左标题右操作，Tabs 在其下。"""
    source = _source("McpDetailSideSheet.tsx")
    assert "title={props.server.name}" in source
    assert "actions={" in source
    assert 'data-testid="edit-mcp"' in source
    assert 'data-testid="test-mcp"' in source
    assert 'data-testid="discover-mcp"' in source
    for tab_key in ('"basic"', '"tools"', '"agents"', '"users"'):
        assert f'itemKey={tab_key}' in source


def test_discover_failure_keeps_catalog_view() -> None:
    """[E-06] 刷新失败：catch 保留视图，不本地清空；reload 只在成功后。"""
    source = _source("McpDetailSideSheet.tsx")
    discover_block = source.split('data-testid="discover-mcp"')[1].split("</Button>")[0]
    assert "catch" in discover_block
    assert "setItems" not in discover_block  # 不直接清本地列表


def test_tool_detail_shows_schema_and_effect_no_tool_controls() -> None:
    """[S-06] 工具详情展示 input schema/操作类型；无 Tool 级启停/授权控件。"""
    source = _source("McpToolTable.tsx")
    assert "input_schema" in source
    assert "mcp.tools.effect" in source
    assert "getTool" in source
    assert "Switch" not in source and "Popconfirm" not in source


def test_discover_button_loading_state() -> None:
    """[S-05] 刷新目录按钮 loading；成功后 reload。"""
    source = _source("McpDetailSideSheet.tsx")
    assert "discovering" in source
    assert "setDiscovering(true)" in source
    assert "void reload()" in source
