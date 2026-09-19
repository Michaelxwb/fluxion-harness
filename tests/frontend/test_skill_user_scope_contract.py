"""[S-07][E-06] Skill 用户范围/指定用户前端契约测试。"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "apps/console-platform/frontend/src/modules/skill-management"


def _source(name: str) -> str:
    return (MODULE / name).read_text(encoding="utf-8")


def test_all_scope_shows_hint_only() -> None:
    """[S-07] user_scope=ALL 时指定用户 Tab 只提示，不提供维护操作。"""
    source = _source("SelectedUserTable.tsx")
    assert "props.userScope === 'ALL'" in source
    assert "skill.users.allScopeHint" in source
    # ALL 分支在维护控件渲染之前 return
    assert source.index("props.userScope === 'ALL'") < source.index('data-testid="add-selected-user"')


def test_selected_scope_single_relation_ops() -> None:
    """[RULE-rel-001 引用] 添加/移除为单关系 POST/DELETE，失败保持当前 Tab + Toast。"""
    source = _source("SelectedUserTable.tsx")
    assert "addSelectedUser" in source
    assert "removeSelectedUser" in source
    assert "Popconfirm" in source
    assert "props.onChanged?.()" in source
    service = _source("services/skills.ts")
    assert "api.post<ApiResponse<Record<string, unknown>>>(`/skills/${id}/users/${userId}`)" in service
    assert "api.delete<ApiResponse<Record<string, unknown>>>(`/skills/${id}/users/${userId}`)" in service


def test_scope_change_modal_uses_user_scope_endpoint() -> None:
    source = _source("SkillScopeModal.tsx")
    assert "setUserScope" in source
    assert "FormModal" in source
    sheet = _source("SkillDetailSideSheet.tsx")
    assert 'data-testid="change-user-scope"' in sheet
