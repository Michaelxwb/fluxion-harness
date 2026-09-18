from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
USER_DETAIL = ROOT / "apps/console-platform/frontend/src/modules/user-identity/UserDetailTabs.tsx"


def test_credentials_tab_consumes_platform_service_with_user_status() -> None:
    source = USER_DETAIL.read_text(encoding="utf-8")
    assert "listPlatforms" in source and "user_id: props.userId" in source
    for key in (
        "user.credentials.platform",
        "user.credentials.adapter",
        "user.credentials.mode",
        "user.credentials.status",
        "user.credentials.configure",
        "user.credentials.configured",
        "user.credentials.notConfigured",
    ):
        assert key in source, f"缺少文案 {key}"
    assert "credentialTagKey" in source, "配置状态必须映射 Tag"


def test_credentials_tab_never_renders_plaintext() -> None:
    source = USER_DETAIL.read_text(encoding="utf-8")
    assert "saveUserCredential" in source
    assert "'x-secret'" in source
    assert "credential_json" not in source
    assert "user.credentials.notEchoed" in source
