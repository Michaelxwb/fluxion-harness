from muad_platform_sdk.adapter.mss_signing import login_authorization


def test_mssw_login_signing_removes_service_prefix() -> None:
    prefixed_url = "https://mssw.example/mss-auth-acl-service/v1/certification/login_agent"
    normalized_url = "https://mssw.example/v1/certification/login_agent"

    mssw = login_authorization(
        prefixed_url,
        "ak-test",
        "sk-test",
        1_700_000_000,
        '{"agent":"test"}',
        branch_tag="MSSW-ADAPTER",
    )
    normalized = login_authorization(
        normalized_url,
        "ak-test",
        "sk-test",
        1_700_000_000,
        '{"agent":"test"}',
    )

    assert mssw == normalized


def test_mssp_login_signing_keeps_service_prefix() -> None:
    prefixed_url = "https://mssp.example/mss-auth-acl-service/v1/certification/login_agent"
    normalized_url = "https://mssp.example/v1/certification/login_agent"

    mssp = login_authorization(
        prefixed_url,
        "ak-test",
        "sk-test",
        1_700_000_000,
        branch_tag="MSSP-ADAPTER",
    )
    normalized = login_authorization(
        normalized_url,
        "ak-test",
        "sk-test",
        1_700_000_000,
    )

    assert mssp != normalized
