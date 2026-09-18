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


def test_e06_base_url_schema_blocks_non_http_submit() -> None:
    source = _source("ModelFormModal.tsx")
    assert "https?" in source and "pattern:" in source
    assert "model.form.baseUrlInvalid" in source


def test_api_key_blank_means_keep_on_edit() -> None:
    source = _source("ModelFormModal.tsx")
    assert "model.form.apiKeyKeep" in source
    assert "props.model !== null" in source
    assert "values.api_key" in source


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
    assert "width={520}" in source
    assert "common.save" in source
    for key in ("model.form.protocolHint", "model.form.baseUrlHint", "model.form.apiKeyNotice"):
        assert key in source, f"缺少字段说明: {key}"
    assert "extraText" in source
    assert "protocol: 'OPENAI'" in source, "协议必须回显 OpenAI"
