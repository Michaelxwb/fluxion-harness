"""[E-07] MCP 指定用户前端契约：单关系操作 / 失败不本地删行 / 远端搜索与分页。"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "apps/console-platform/frontend/src/modules/mcp-management"


def _source(name: str) -> str:
    return (MODULE / name).read_text(encoding="utf-8")


def test_all_scope_shows_hint_only() -> None:
    """ALL 范围仅提示，不发列表/候选请求，也不渲染维护控件。"""
    source = _source("SelectedUserTable.tsx")
    assert "props.userScope === 'ALL'" in source
    assert "mcp.users.allScopeHint" in source
    assert source.index("props.userScope === 'ALL'") < source.index('data-testid="mcp-add-selected-user"')


def test_selected_scope_single_relation_ops() -> None:
    source = _source("SelectedUserTable.tsx")
    assert "addSelectedUser" in source
    assert "removeSelectedUser" in source
    assert "ConfirmAction" in source
    assert "props.onChanged?.()" in source
    assert "remote" in source
    assert "PaginationFooter" in source
    assert "ErrorState" in source
    assert "requestSeq" in source


def test_remove_failure_does_not_delete_row_locally() -> None:
    """[E-07] 移除失败：不本地删行（catch 后直接 return，不调用 reload/onChanged）。"""
    source = _source("SelectedUserTable.tsx")
    remove_block = source.split("const remove = async")[1].split("};")[0]
    assert "catch" in remove_block
    assert "return;" in remove_block
    catch_block = remove_block.split("catch")[1].split("}")[0]
    assert "reload" not in catch_block
    assert "onChanged" not in catch_block
