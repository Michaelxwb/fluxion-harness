from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "apps/console-platform/frontend/src/modules/project-platform"


def test_credential_tab_matches_interaction_layout() -> None:
    source = (MODULE / "PlatformCredentialTab.tsx").read_text(encoding="utf-8")
    assert "platform.credentials.configureUser" in source
    assert "listPlatformCredentials" in source
    for key in (
        "platform.credentials.user",
        "platform.credentials.account",
        "platform.credentials.status",
        "platform.credentials.updatedTime",
        "platform.credentials.defaultShared",
        "platform.credentials.defaultSharedHint",
    ):
        assert key in source, f"缺少文案 {key}"
    assert "credential-card" in source, "共享凭据必须使用卡片布局"


def test_credential_form_is_schema_driven_and_never_echoes_secrets() -> None:
    source = (MODULE / "PlatformCredentialTab.tsx").read_text(encoding="utf-8")
    assert "credential_schema" in source
    assert "'x-secret'" in source
    assert "mode={field.secret ? 'password' : undefined}" in source
    assert "platform.credentials.notEchoed" in source
    assert "saveUserCredential" in source and "saveSharedCredential" in source


def test_test_modal_reports_localized_stages_without_raw_secrets() -> None:
    source = (MODULE / "PlatformTestModal.tsx").read_text(encoding="utf-8")
    for key in (
        "platform.test.configValid",
        "platform.test.connectivity",
        "platform.test.credentialRefStatus",
        "platform.test.target",
        "platform.test.failureReason",
        "platform.test.error.",
        "platform.test.hint",
    ):
        assert key in source
    assert "JSON.stringify" not in source, "不得直接展示原始 JSON"
    assert "testPlatform" in source
