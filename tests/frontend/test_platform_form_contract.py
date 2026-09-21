from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "apps/console-platform/frontend/src/modules/project-platform"


def test_form_uses_shared_modal_and_conditional_resolver_fields() -> None:
    source = (MODULE / "ProjectPlatformForm.tsx").read_text(encoding="utf-8")
    assert "components/common/FormModal" in source
    assert "width={800}" in source
    assert "common.save" in source
    assert "resolver_type === 'BASE_URL'" in source or "resolverType === 'BASE_URL'" in source
    assert "disabled={props.platform !== null}" in source
    for mode in ("USER_ONLY", "SHARED_ONLY", "USER_THEN_SHARED", "NONE"):
        assert mode in source


def test_adapter_config_renders_from_schema() -> None:
    source = (MODULE / "ProjectPlatformForm.tsx").read_text(encoding="utf-8")
    assert "platform_config_schema" in source
    assert "adapter_config." in source
    assert "enum" in source and "integer" in source


def test_form_uses_two_column_grid_and_localized_adapter_fields() -> None:
    source = (MODULE / "ProjectPlatformForm.tsx").read_text(encoding="utf-8")
    assert "form-grid" in source, "表单必须使用双列栅格"
    assert "platform.form.adapterSection" in source
    assert "platform.adapterField." in source, "Adapter 字段必须走 i18n 而非裸 schema key"
    assert "form-grid-spacer" in source


def test_form_maps_backend_errors_to_fields() -> None:
    source = (MODULE / "ProjectPlatformForm.tsx").read_text(encoding="utf-8")
    assert "apiErrorBody" in source
    assert "PLATFORM_KEY_EXISTS" in source
    assert "setError('key'" in source
    assert "COMMON_VALIDATION_ERROR" in source
