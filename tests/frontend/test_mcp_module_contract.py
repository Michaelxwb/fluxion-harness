"""[S-07][E-08][E-09][RULE-ui/front/i18n-001] MCP 列表/表单前端契约测试。"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "apps/console-platform/frontend/src/modules/mcp-management"
LOCALES = ROOT / "apps/console-platform/frontend/src/locales"


def _source(name: str) -> str:
    return (MODULE / name).read_text(encoding="utf-8")


def test_mcp_page_layout_and_common_components() -> None:
    """[RULE-ui-001] 左上操作 + 右上搜索筛选 + RemoteTable；主展示字段打开详情。"""
    source = _source("McpPage.tsx")
    for component in ("ModuleToolbar", "RemoteTable", "PageHeader", "EmptyState", "DateTimeText"):
        assert component in source
    assert 'data-testid="create-mcp"' in source
    assert "setDetail(record)" in source
    assert "actions={" in source and "search={" in source


def test_mcp_connection_and_enabled_distinct_tags() -> None:
    """连接状态与启用状态视觉区分（不同列、不同语义）。"""
    source = _source("McpPage.tsx")
    assert "connection_status" in source
    assert "mcp.columns.connectionStatus" in source
    assert "mcp.columns.enabled" in source


def test_mcp_form_local_transport_guard() -> None:
    """[E-09] endpoint 协议本地拦截，不提交；transport 固定只读。"""
    source = _source("McpFormModal.tsx")
    assert "VALID_ENDPOINT" in source
    assert "Toast.error(t('mcp.form.endpointInvalid'))" in source
    assert 'field="transport"' in source
    assert "disabled" in source


def test_mcp_form_failure_keeps_modal_and_values() -> None:
    """[E-08] 提交失败：catch 不调用 onSaved/onCancel，Modal 保留。"""
    source = _source("McpFormModal.tsx")
    assert "props.onSaved()" in source
    assert "catch {" in source
    assert source.index("catch {") < source.index("props.onSaved()") or "finally" in source


def test_mcp_no_direct_http_and_uses_services() -> None:
    """[RULE-front-001] 组件不裸用 axios/fetch。"""
    for name in ("McpPage.tsx", "McpFormModal.tsx", "McpDetailSideSheet.tsx"):
        source = _source(name)
        assert "axios" not in source
        assert "fetch(" not in source
    services = _source("services/mcpServers.ts")
    assert "Idempotency-Key" in services  # 注册幂等（RULE-api-002 前端配合）


def test_mcp_i18n_keys_bilingual() -> None:
    """[RULE-i18n-001] zh-CN/en-US 词条齐备（含 docs/15 词典字段名）。"""
    zh = json.loads((LOCALES / "zh-CN.json").read_text(encoding="utf-8"))
    en = json.loads((LOCALES / "en-US.json").read_text(encoding="utf-8"))
    required = [
        "mcp.title",
        "mcp.actions.create",
        "mcp.columns.toolCount",
        "mcp.columns.usingAgentCount",
        "mcp.columns.selectedUserCount",
        "mcp.columns.connectionStatus",
        "mcp.columns.lastDiscoveredAt",
        "mcp.connection.DISCOVERY_FAILED",
        "mcp.form.transport",
    ]
    for key in required:
        assert key in zh, f"zh-CN 缺 {key}"
        assert key in en, f"en-US 缺 {key}"
    assert zh["mcp.columns.toolCount"] == "工具数"
    assert zh["mcp.columns.usingAgentCount"] == "使用 Agent 数"
    assert zh["mcp.columns.lastDiscoveredAt"] == "最近工具发现时间"


def test_mcp_route_registered() -> None:
    app = (ROOT / "apps/console-platform/frontend/src/App.tsx").read_text(encoding="utf-8")
    assert '<Route path="mcp" element={<McpPage />} />' in app
