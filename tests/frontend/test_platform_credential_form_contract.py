from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "apps/console-platform/frontend/src/modules/project-platform"


def test_credential_form_is_schema_driven_and_never_echoes_secrets() -> None:
    source = (MODULE / "PlatformCredentialTab.tsx").read_text(encoding="utf-8")
    assert "credential_schema" in source
    assert "'x-secret'" in source
    assert "mode={field.secret ? 'password' : undefined}" in source
    assert "platform.credentials.notEchoed" in source
    assert "saveUserCredential" in source and "saveSharedCredential" in source


def test_test_modal_reports_stage_results_without_secrets() -> None:
    source = (MODULE / "PlatformTestModal.tsx").read_text(encoding="utf-8")
    for key in (
        "platform.test.configValid",
        "platform.test.connectivity",
        "platform.test.credentialRefStatus",
        "platform.test.details",
        "platform.test.hint",
    ):
        assert key in source
    assert "testPlatform" in source
