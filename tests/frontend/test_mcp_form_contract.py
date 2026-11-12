"""[E-08][E-09][S-01][S-02][S-03] MCP 表单契约：认证三态、失败保留、本地校验、折叠。"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "apps/console-platform/frontend/src/modules/mcp-management"


def _form_source() -> str:
    return (MODULE / "McpFormModal.tsx").read_text(encoding="utf-8")


def _source(name: str) -> str:
    return (MODULE / name).read_text(encoding="utf-8")


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def test_config_invalid_keeps_modal_and_form() -> None:
    """[E-08] 提交失败（MCP_CONFIG_INVALID）：字段级错误、不关闭 Modal、不重置本地表单。"""
    source = _form_source()
    assert "apiErrorBody" in source
    assert "MCP_CONFIG_INVALID" in source
    assert "setError('endpoint'" in source
    assert "props.onSaved()" in source
    assert "catch (error)" in source
    catch_block = source.split("catch (error)")[1]
    assert "props.onCancel()" not in catch_block.split("}")[0]
    assert "setSaving(false)" in source


def test_endpoint_invalid_local_guard() -> None:
    """[E-09] endpoint 协议校验在提交前本地拦截，且不进入保存态。"""
    source = _form_source()
    assert "VALID_ENDPOINT" in source
    assert "Tips.error(t('mcp.form.endpointInvalid'))" in source
    assert "setError('endpoint'" in source
    assert source.index("VALID_ENDPOINT") < source.index("setSaving(true)")


def test_transport_is_static_text_not_an_input() -> None:
    """[S-03] 传输协议是静态 Tag/文案，不再渲染只读输入框。"""
    source = _form_source()
    assert 'field="transport"' not in source
    assert 'initValue="streamable-http"' not in source
    assert "mcp.form.transportStatic" in source
    assert "<Tag" in source


def test_no_raw_auth_config_json_input() -> None:
    """[S-01][S-02] 表单不再暴露原始 auth_config JSON，也不在组件里解析 JSON。"""
    source = _form_source()
    assert 'field="auth_config"' not in source
    assert "JSON.parse" not in source
    assert "mcp.form.authConfig" not in source
    assert "auth_config:auth.auth_config" in _compact(source)


def test_advanced_settings_are_collapsed_by_default() -> None:
    """[S-03] 连接超时/TTL 收进默认收起的高级设置；非默认值显示已自定义。"""
    source = _form_source()
    assert "const [advancedOpen, setAdvancedOpen] = useState(false)" in source
    assert 'data-testid="mcp-advanced-toggle"' in source
    assert "display: advancedOpen ? undefined : 'none'" in source
    assert "mcp.form.advancedToggle" in source
    assert "mcp.form.advancedCustomized" in source
    assert 'field="connect_timeout_ms"' in source
    assert 'field="tool_cache_ttl_sec"' in source


def test_auth_mode_fields_cover_three_states() -> None:
    """[S-01][S-02][S-03] 认证方式三选一，凭据字段按模式渲染。"""
    source = _form_source()
    assert "AuthModeFields" in source
    assert "buildAuthPayload" in source
    assert "validateHeaderRows" in source
    auth = _source("AuthModeFields.tsx")
    for mode in ("none", "bearer", "headers"):
        assert f"value: '{mode}'" in auth
    assert 'field="auth_secret"' in auth
    assert 'field="secret_header"' in auth
    assert "mcp.form.authSecretKeep" in auth
    assert "mcp.form.authNoneClearsSecret" in auth


def test_edit_initializes_structured_auth_without_echoing_secret() -> None:
    """[E-04] 编辑从 headers/secret_header 回填；密钥框始终从空值开始。"""
    source = _form_source()
    assert "summarizeAuthMode" in source
    assert "config?.headers" in source
    assert "config?.secret_header" in source
    assert "setSecretValue('')" in source
    assert "auth_secret: ''" in source
    assert "JSON.stringify(props.server.auth_config" not in source


def test_create_discovers_tools_after_save() -> None:
    """[S-01][S-02] 创建成功后沿用自动发现；失败不阻塞保存。"""
    source = _form_source()
    assert "createMcpServer(" in source
    assert "newRequestId()" in source
    assert "discoverTools(created.mcp_id).catch(() => undefined)" in source
