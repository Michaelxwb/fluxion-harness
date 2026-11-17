"""`tests/e2e/mssw_token_factory.py` 自测：工厂签出的 token 必须被**真实验签实现**接受。

这是 TASK-013 验收套件的地基：工厂与 `verifier` / `aes_ecb` 不是两套算法，而是
「同一份常量 + 同一份加密函数」。一旦漂移，先红在这里，而不是二十条验收用例一起假红。
"""

from __future__ import annotations

import pytest
from muad_console_platform.application.external_assertion.config import load_platform_configs
from muad_console_platform.application.external_assertion.verifier import (
    AssertionRejected,
    verify_assertion,
)

from tests.e2e.mssw_token_factory import (
    issue,
    issue_expired,
    issue_mismatched_factors,
    issue_tampered_payload,
    issue_with_algnone,
    issue_with_bad_signature,
    new_platform,
)

EXPERT_ID = "6a4ca2554a2a45d4b1ad5996"
TENANT_ID = "1001"


@pytest.fixture()
def platform():
    return new_platform()


@pytest.fixture()
def config(platform):
    return load_platform_configs(platform.platforms_json())[platform.platform_key]


def test_issued_token_verifies_and_carries_identity(platform, config) -> None:
    token = issue(platform, expert_id=EXPERT_ID, tenant_id=TENANT_ID)

    verified = verify_assertion(token, config)

    assert verified.claims["_id"] == EXPERT_ID
    assert verified.claims["expert_id"] == EXPERT_ID
    assert verified.claims["tenant_id"] == TENANT_ID
    assert verified.claims["exp"] == -1


def test_platforms_json_never_contains_private_key(platform) -> None:
    payload = platform.platforms_json()
    assert "PRIVATE KEY" not in payload
    assert "BEGIN" in payload, "公有 PEM 应写进因子配置"


def test_expired_token_is_rejected_by_max_age(platform, config) -> None:
    token = issue_expired(platform, expert_id=EXPERT_ID, tenant_id=TENANT_ID, max_session_age_sec=60)
    with pytest.raises(AssertionRejected) as excinfo:
        verify_assertion(token, config, max_session_age_sec=60)
    assert excinfo.value.reason == "assertion_expired"


def test_fresh_token_passes_max_age(platform, config) -> None:
    token = issue(platform, expert_id=EXPERT_ID, tenant_id=TENANT_ID)
    assert verify_assertion(token, config, max_session_age_sec=60).claims["_id"] == EXPERT_ID


def test_bad_signature_is_rejected(platform, config) -> None:
    token = issue_with_bad_signature(platform, expert_id=EXPERT_ID, tenant_id=TENANT_ID)
    with pytest.raises(AssertionRejected) as excinfo:
        verify_assertion(token, config)
    assert excinfo.value.reason == "bad_signature"


def test_alg_none_is_rejected(platform, config) -> None:
    token = issue_with_algnone(platform, expert_id=EXPERT_ID, tenant_id=TENANT_ID)
    with pytest.raises(AssertionRejected) as excinfo:
        verify_assertion(token, config)
    assert excinfo.value.reason == "algorithm_not_allowed"


def test_tampered_payload_is_rejected(platform, config) -> None:
    token = issue_tampered_payload(platform, expert_id=EXPERT_ID, tenant_id=TENANT_ID)
    with pytest.raises(AssertionRejected) as excinfo:
        verify_assertion(token, config)
    assert excinfo.value.reason == "bad_signature"


def test_mismatched_aud_iss_is_rejected(platform, config) -> None:
    token = issue_mismatched_factors(platform, expert_id=EXPERT_ID, tenant_id=TENANT_ID)
    with pytest.raises(AssertionRejected) as excinfo:
        verify_assertion(token, config)
    assert excinfo.value.reason in {"audience_mismatch", "issuer_mismatch"}
