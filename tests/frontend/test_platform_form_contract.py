from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "apps/console-platform/frontend/src/modules/project-platform"


def test_form_uses_shared_modal_and_conditional_resolver_fields() -> None:
    source = (MODULE / "ProjectPlatformForm.tsx").read_text(encoding="utf-8")
    assert "components/common/FormModal" in source
    assert "width={560}" in source
    assert "common.save" in source
    assert "resolver_type === 'BASE_URL'" in source or "resolverType === 'BASE_URL'" in source
    assert "platform.form.keyImmutable" in source
    for mode in ("USER_ONLY", "SHARED_ONLY", "USER_THEN_SHARED", "NONE"):
        assert mode in source


def test_adapter_config_renders_from_schema() -> None:
    source = (MODULE / "ProjectPlatformForm.tsx").read_text(encoding="utf-8")
    assert "platform_config_schema" in source
    assert "adapter_config." in source
    assert "enum" in source and "integer" in source
