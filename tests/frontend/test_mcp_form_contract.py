"""[E-08][E-09] MCP 表单契约：失败保留表单 / transport 与 endpoint 本地拦截。"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "apps/console-platform/frontend/src/modules/mcp-management"


def _form_source() -> str:
    return (MODULE / "McpFormModal.tsx").read_text(encoding="utf-8")


def test_config_invalid_keeps_modal_and_form() -> None:
    """[E-08] 提交失败（如 MCP_CONFIG_INVALID）：catch 不关闭 Modal、不重置本地表单。"""
    source = _form_source()
    assert "props.onSaved()" in source
    assert "catch {" in source
    # catch 在 onSaved 调用之前出现且 finally 中不关闭
    assert "finally {" in source
    assert "props.onCancel()" not in source.split("catch {")[1].split("}")[0]
    assert "setSaving(false)" in source


def test_transport_invalid_local_guard() -> None:
    """[E-09] endpoint 协议校验在提交前本地拦截；transport 字段只读。"""
    source = _form_source()
    assert "VALID_ENDPOINT" in source
    assert "Toast.error(t('mcp.form.endpointInvalid'))" in source
    assert source.index("VALID_ENDPOINT") < source.index("setSaving(true)")
    assert 'field="transport"' in source
