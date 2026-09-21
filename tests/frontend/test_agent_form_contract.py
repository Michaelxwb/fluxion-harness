"""[E-08][E-09] Agent 表单契约：key/revision 冲突保留 Modal / 编辑携带 expected_revision。"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "apps/console-platform/frontend/src/modules/agent-management"


def _form_source() -> str:
    return (MODULE / "AgentFormModal.tsx").read_text(encoding="utf-8")


def test_key_conflict_keeps_modal_and_form() -> None:
    """[E-09] key 冲突（409）：字段级错误、catch 不关闭 Modal、不重置本地表单。"""
    source = _form_source()
    assert "apiErrorBody" in source
    assert "AGENT_KEY_EXISTS" in source
    assert "setError('key'" in source
    assert "props.onSaved()" in source
    assert "catch (error)" in source
    assert "props.onCancel()" not in source.split("catch (error)")[1].split("}")[0]
    assert "setSaving(false)" in source


def test_revision_conflict_shows_refresh_hint() -> None:
    """[E-FE-01] revision 冲突：Modal 保留并提示刷新重试。"""
    source = _form_source()
    assert "REVISION_CONFLICT" in source
    assert "agent.form.revisionConflict" in source


def test_edit_carries_expected_revision_and_prefills_key() -> None:
    """[RULE-snapshot 引用] 编辑提交携带 expected_revision；标识只读展示。"""
    source = _form_source()
    assert "expected_revision: props.agent.revision" in source
    assert "key: props.agent.key" in source


def test_description_can_be_cleared() -> None:
    """描述清空必须提交空串而不是被丢弃。"""
    source = _form_source()
    assert "values.description ?? ''" in source
