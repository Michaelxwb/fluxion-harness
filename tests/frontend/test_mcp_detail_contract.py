"""[S-05][S-06][E-06][RULE-ui-detail-001] MCP 详情/工具明细前端契约测试。"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "apps/console-platform/frontend/src/modules/mcp-management"


def _source(name: str) -> str:
    return (MODULE / name).read_text(encoding="utf-8")


def test_detail_sidesheet_header_actions_and_tabs() -> None:
    """[RULE-ui-detail-001] 标题/副标题居左，操作与关闭 X 同行靠右，Tabs 其下。"""
    source = _source("McpDetailSideSheet.tsx")
    assert "DetailSideSheet" in source
    assert "title={props.server.name}" in source
    assert "subtitle={props.server.key}" in source
    assert "actions={" in source
    for testid in ("edit-mcp", "test-mcp", "change-mcp-scope", "discover-mcp"):
        assert testid in source
    for tab_key in ('"basic"', '"tools"', '"agents"', '"users"'):
        assert f'itemKey={tab_key}' in source


def test_detail_uses_fetched_detail_for_edit_and_scope() -> None:
    """编辑/变更范围基于详情 API 数据（不拿列表行强转），加载完成前禁用。"""
    sheet = _source("McpDetailSideSheet.tsx")
    assert "props.onEdit(detail)" in sheet
    assert "disabled={detail === null}" in sheet
    assert "getMcpServer" in sheet
    assert "as McpServerDetail" not in _source("McpPage.tsx")
    assert "McpScopeModal" in sheet
    assert "setUserScope" in _source("services/mcpServers.ts")


def test_detail_has_error_states_and_catalog_fields() -> None:
    sheet = _source("McpDetailSideSheet.tsx")
    assert "ErrorState" in sheet
    assert "mcp.detail.catalogHash" in sheet
    assert "mcp.detail.lastDiscoveryError" in sheet
    assert "tool_catalog_hash" in sheet
    assert "last_discovery_error" in sheet


def test_discover_failure_keeps_catalog_view() -> None:
    """[E-06] 刷新失败：catch 保留视图，不本地清空；成功后 reload 工具快照。"""
    source = _source("McpDetailSideSheet.tsx")
    discover_block = source.split("const discover =")[1].split("};")[0]
    assert "catch" in discover_block
    assert "setToolReloadKey" in discover_block
    assert "setItems" not in discover_block


def test_agents_tab_lists_bound_agents() -> None:
    sheet = _source("McpDetailSideSheet.tsx")
    assert "McpAgentTable" in sheet
    table = _source("McpAgentTable.tsx")
    assert "listMcpAgents" in table
    assert "PaginationFooter" in table
    assert "ErrorState" in table


def test_tool_detail_shows_schema_and_effect_no_tool_controls() -> None:
    """[S-06] 工具详情展示 input schema/操作类型；无 Tool 级启停/授权控件。"""
    source = _source("McpToolTable.tsx")
    assert "input_schema" in source
    assert "mcp.tools.effect" in source
    assert "getTool" in source
    assert "Switch" not in source and "Popconfirm" not in source
    assert "RemoteTable" in source  # 分页，避免 100 条截断
    assert "ErrorState" in source


def test_discover_button_loading_state() -> None:
    """[S-05] 刷新目录按钮 loading；成功后 reload。"""
    source = _source("McpDetailSideSheet.tsx")
    assert "discovering" in source
    assert "setDiscovering(true)" in source
    assert "void reload()" in source


def test_test_modal_has_error_state() -> None:
    source = _source("McpTestModal.tsx")
    assert "ErrorState" in source
    assert "failed" in source
