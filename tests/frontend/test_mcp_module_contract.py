"""[S-07][E-08][E-09][RULE-ui/front/i18n-001] MCP 列表/表单前端契约测试。"""

from __future__ import annotations

import json
import re
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
    assert "connection_status" in source


def test_mcp_connection_and_enabled_distinct_tags() -> None:
    """连接状态与启用状态视觉区分（不同列、不同颜色）。"""
    source = _source("McpPage.tsx")
    assert "mcp.columns.connectionStatus" in source
    assert "mcp.columns.enabled" in source
    assert "value ? 'light-blue' : 'grey'" in source
    assert "value === 'AVAILABLE' ? 'green'" in source


def test_mcp_form_local_transport_guard() -> None:
    """[E-09] endpoint 协议本地拦截不提交；transport 为只读 streamable-http 字段。"""
    source = _source("McpFormModal.tsx")
    assert "VALID_ENDPOINT" in source
    assert "Toast.error(t('mcp.form.endpointInvalid'))" in source
    assert 'field="transport"' in source
    assert "disabled" in source
    assert 'initValue="streamable-http"' in source


def test_mcp_form_follows_interaction_layout() -> None:
    """交互稿排版：双列栅格 + 整行字段 + Banner + 分区标题 + 高级配置。"""
    source = _source("McpFormModal.tsx")
    assert 'className="form-grid"' in source
    assert 'className="form-field-full"' in source
    assert 'className="form-field-banner"' in source
    assert "form-section-title" in source and "form-section-hint" in source
    assert "mcp.form.banner" in source
    assert "mcp.form.authConfig" in source
    assert 'field="enabled"' in source
    assert "mcp.form.saveAndDiscover" in source
    assert "discoverTools" in source


def test_mcp_form_failure_keeps_modal_and_values() -> None:
    """[E-08] 提交失败：MCP_CONFIG_INVALID 落到字段、Modal 保留、不覆盖本地表单。"""
    source = _source("McpFormModal.tsx")
    assert "apiErrorBody" in source
    assert "MCP_CONFIG_INVALID" in source
    assert "setError('endpoint'" in source
    assert "props.onSaved()" in source
    assert "catch (error)" in source
    catch_block = source.split("catch (error)")[1]
    assert "props.onSaved()" not in catch_block
    assert "props.onCancel()" not in catch_block


def test_mcp_page_error_state_and_race_guards() -> None:
    source = _source("McpPage.tsx")
    assert "ErrorState" in source
    assert "requestSeq" in source
    assert "keywordInput" in source and "setTimeout" in source
    assert "mcp.actions.discover" in source  # 列表操作列刷新工具


def test_mcp_no_direct_http_and_uses_services() -> None:
    """[RULE-front-001] 组件不裸用 axios/fetch；注册使用 newRequestId 幂等键。"""
    for name in ("McpPage.tsx", "McpFormModal.tsx", "McpDetailSideSheet.tsx"):
        source = _source(name)
        assert "axios" not in source
        assert "fetch(" not in source
    form = _source("McpFormModal.tsx")
    assert "newRequestId()" in form
    assert "crypto.randomUUID" not in form
    services = _source("services/mcpServers.ts")
    assert "Idempotency-Key" in services


def test_mcp_module_i18n_keys_complete() -> None:
    """[RULE-i18n-001] 模块内所有静态 t('key') 必须在 zh-CN/en-US 同时存在。"""
    keys: set[str] = set()
    for path in MODULE.rglob("*.tsx"):
        keys |= set(re.findall(r"(?<![A-Za-z_])t\(\s*'([^']+)'", path.read_text(encoding="utf-8")))
    static_keys = {key for key in keys if "${" not in key}
    assert static_keys, "未解析到任何 i18n key"
    for locale in ("zh-CN", "en-US"):
        data = json.loads((LOCALES / f"{locale}.json").read_text(encoding="utf-8"))
        missing = sorted(key for key in static_keys if key not in data)
        assert not missing, f"{locale} 缺少词条: {missing}"


def test_mcp_route_registered() -> None:
    app = (ROOT / "apps/console-platform/frontend/src/App.tsx").read_text(encoding="utf-8")
    assert '<Route path="mcp" element={<McpPage />} />' in app
