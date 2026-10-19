from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "apps/console-platform/frontend/src/modules/model-management"


def _source(name: str) -> str:
    return (MODULE / name).read_text(encoding="utf-8")


def test_e09_edit_form_keeps_protocol_readonly() -> None:
    source = _source("ModelFormModal.tsx")
    protocol_at = source.index('field="protocol"')
    assert "disabled" in source[protocol_at : protocol_at + 200]
    assert 'initValue="OPENAI"' in source


def test_e06_output_budget_is_a_positive_integer_field() -> None:
    """输出预算（`params.max_tokens`）必填正整数；连接/密钥字段已退出模型表单（归凭据面）。"""
    source = _source("ModelFormModal.tsx")
    assert "model.form.maxTokensInvalid" in source and "ModelInputError" in source
    assert "readOutputBudget" in source
    assert "base_url" not in source and "api_key" not in source


def test_api_key_is_not_a_model_form_field() -> None:
    """密钥写入只在凭据面板（只写不留空重发）；模型表单不持任何密钥字段。"""
    source = _source("ModelFormModal.tsx")
    assert "api_key" not in source and "model.form.apiKeyKeep" not in source
    # 编辑回显只认真正发送的键：旧行缺预算显式留空，不伪造默认
    assert "readOutputBudget(props.model.params) ?? undefined" in source


def test_model_page_follows_console_skeleton_and_batch_test_entry() -> None:
    source = _source("ModelPage.tsx")
    assert "PageHeader" in source and "PageSection" in source
    assert "RemoteTable" in source
    assert 'data-testid="batch-test"' in source
    assert "rowSelection" in source
    assert "batchTestModels" in source


def test_model_form_uses_shared_form_modal_with_hints() -> None:
    source = _source("ModelFormModal.tsx")
    assert "components/common/FormModal" in source
    assert "width={720}" in source
    assert "common.save" in source
    for key in ("model.form.protocolHint", "model.form.providerHint", "model.form.maxTokensHint"):
        assert key in source, f"缺少字段说明: {key}"
    assert "extraText" in source
    assert "protocol: 'OPENAI'" in source, "协议必须回显 OpenAI"
