from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "apps/console-platform/frontend/src/modules/model-management"


def _source(name: str) -> str:
    return (MODULE / name).read_text(encoding="utf-8")


def test_e09_edit_form_keeps_protocol_readonly() -> None:
    source = _source("ModelFields.tsx")
    protocol_at = source.index('field="protocol"')
    assert "disabled" in source[protocol_at : protocol_at + 200]
    assert 'initValue="OPENAI"' in source


def test_e06_output_budget_is_a_positive_integer_field() -> None:
    """输出预算（`params.max_tokens`）必填正整数；连接/密钥字段已退出模型字段面。"""
    fields = _source("ModelFields.tsx")
    modal = _source("ModelFormModal.tsx")
    assert "model.form.maxTokensInvalid" in fields and "model.form.maxTokens" in fields
    assert "ModelInputError" in modal
    assert "readOutputBudget" in modal
    assert "base_url" not in fields and "api_key" not in fields


def test_api_key_is_not_a_model_form_field() -> None:
    """密钥写入只在凭据面板（只写不留空重发）；模型字段面不持任何密钥字段。"""
    modal = _source("ModelFormModal.tsx")
    fields = _source("ModelFields.tsx")
    for source in (modal, fields):
        assert "api_key" not in source and "model.form.apiKeyKeep" not in source
    # 编辑回显只认真正发送的键：旧行缺预算显式留空，不伪造默认
    assert "readOutputBudget(props.model.params) ?? undefined" in modal


def test_model_page_follows_console_skeleton_and_batch_test_entry() -> None:
    source = _source("ModelPage.tsx")
    assert "PageHeader" in source and "PageSection" in source
    assert "RemoteTable" in source
    assert 'data-testid="batch-test"' in source
    assert "rowSelection" in source
    assert "batchTestModels" in source


def test_model_form_uses_shared_form_modal_with_hints() -> None:
    modal = _source("ModelFormModal.tsx")
    fields = _source("ModelFields.tsx")
    assert "components/common/FormModal" in modal
    assert "width={720}" in modal
    assert "common.save" in modal
    assert "ModelFields" in modal, "字段面必须复用共享组件（新增向导与编辑同源）"
    for key in ("model.form.protocolHint", "model.form.providerHint", "model.form.maxTokensHint"):
        assert key in fields, f"缺少字段说明: {key}"
    assert "extraText" in fields
    assert "protocol: 'OPENAI'" in modal, "协议必须回显 OpenAI"
