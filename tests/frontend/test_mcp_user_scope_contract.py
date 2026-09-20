"""[E-07][RULE-auth/rel-001 引用] MCP 指定用户前端契约。"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "apps/console-platform/frontend/src/modules/mcp-management"


def _source(name: str) -> str:
    return (MODULE / name).read_text(encoding="utf-8")


def test_remove_failure_does_not_mutate_local_rows() -> None:
    """[E-07] 移除失败：不先本地删行，Toast 由 ApiClient 展示。"""
    source = _source("SelectedUserTable.tsx")
    remove_block = source.split("const remove = async")[1].split("};")[0]
    assert "catch {" in remove_block
    assert "setItems" not in remove_block  # 失败路径不直接改本地列表
    assert "ConfirmAction" in source


def test_grants_single_relation_ops() -> None:
    """[RULE-rel-001 引用] 添加/移除为单关系 POST/DELETE。"""
    service = _source("services/mcpServers.ts")
    assert "api.post(`/mcp-servers/${id}/users/${userId}`)" in service
    assert "api.delete(`/mcp-servers/${id}/users/${userId}`)" in service


def test_all_scope_hint_only() -> None:
    """[RULE-auth-001 引用] ALL 时只提示不维护，无 Tool 级授权入口。"""
    source = _source("SelectedUserTable.tsx")
    assert "props.userScope === 'ALL'" in source
    assert "mcp.users.allScopeHint" in source
    assert source.index("props.userScope === 'ALL'") < source.index("mcp-add-selected-user")
