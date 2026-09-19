"""[E-08][E-09] Agent 表单契约：key 冲突保留 Modal / 编辑携带 expected_revision。"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "apps/console-platform/frontend/src/modules/agent-management"


def _form_source() -> str:
    return (MODULE / "AgentFormModal.tsx").read_text(encoding="utf-8")


def test_key_conflict_keeps_modal_and_form() -> None:
    """[E-09] key 冲突（409）：catch 不关闭 Modal、不重置本地表单。"""
    source = _form_source()
    assert "props.onSaved()" in source
    assert "catch {" in source
    assert "props.onCancel()" not in source.split("catch {")[1].split("}")[0]
    assert "setSaving(false)" in source


def test_edit_carries_expected_revision() -> None:
    """[RULE-snapshot 引用] 编辑提交携带 expected_revision。"""
    source = _form_source()
    assert "expected_revision: props.agent.revision" in source
