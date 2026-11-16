"""外部断言认证的纯单元测试（不依赖数据库）。

覆盖 RULE-auth-002 的五步链中「可脱离存储验证」的部分：算法白名单、签名、
密文比对、claim 策略、身份提取与指纹计算。身份查表部分需要真实 PG，留给集成层。
"""

from __future__ import annotations

import base64
import json

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from muad_console_platform.application.external_assertion.aes_ecb import (
    derive_key,
    encrypt_expected,
    matches_expected,
)
from muad_console_platform.application.external_assertion.config import (
    PlatformConfigError,
    load_platform_configs,
)
from muad_console_platform.application.external_assertion.principal import (
    compute_identity_hash,
    extract_identity,
)
from muad_console_platform.application.external_assertion.verifier import (
    AssertionRejected,
    verify_assertion,
)

ISSUER = "mssw-issuer"
AUDIENCE = "mssw-audience"
SALT = b"mssw-test-salt"


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


@pytest.fixture(scope="module")
def keypair() -> tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey]:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private, private.public_key()


@pytest.fixture()
def config(keypair):
    _, public = keypair
    pem = public.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")
    payload = {
        "mssw": {
            "public_key": pem,
            "issuer": ISSUER,
            "audience": AUDIENCE,
            "salt": SALT.decode("utf-8"),
            "identity_keys": ["expert_id", "tenant_id"],
            "identity_field_labels": {"expert_id": "专家 ID", "tenant_id": "租户 ID"},
        }
    }
    return load_platform_configs(json.dumps(payload))["mssw"]


def _sign(private, claims: dict, *, alg: str = "RS256") -> str:
    header = _b64url(json.dumps({"alg": alg, "typ": "JWT"}, separators=(",", ":")).encode())
    body = _b64url(json.dumps(claims, separators=(",", ":")).encode())
    signature = private.sign(f"{header}.{body}".encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{header}.{body}.{_b64url(signature)}"


def _claims(**overrides) -> dict:
    base = {
        "_id": "6a4ca2554a2a45d4b1ad5996",
        "expert_id": "6a4ca2554a2a45d4b1ad5996",
        "tenant_id": "1001",
        "job_number": "14806",
        "aud": _b64(encrypt_expected(AUDIENCE, SALT)),
        "iss": [_b64(encrypt_expected(ISSUER, SALT))],
        "exp": -1,
    }
    base.update(overrides)
    return base


# --- AES 因子 -------------------------------------------------------------


def test_derive_key_right_pads_salt_to_32_bytes() -> None:
    assert derive_key(b"abc") == b"abc" + b"\x00" * 29


def test_derive_key_rejects_oversized_salt() -> None:
    with pytest.raises(ValueError):
        derive_key(b"x" * 33)


def test_matches_expected_accepts_single_element_array() -> None:
    encrypted = _b64(encrypt_expected(ISSUER, SALT))
    assert matches_expected([encrypted], ISSUER, SALT) is True


def test_matches_expected_rejects_wrong_plaintext() -> None:
    encrypted = _b64(encrypt_expected("someone-else", SALT))
    assert matches_expected(encrypted, AUDIENCE, SALT) is False


def test_matches_expected_rejects_multi_element_array() -> None:
    encrypted = _b64(encrypt_expected(ISSUER, SALT))
    assert matches_expected([encrypted, encrypted], ISSUER, SALT) is False


# --- 验签 -----------------------------------------------------------------


def test_valid_assertion_is_accepted(keypair, config) -> None:
    private, _ = keypair
    verified = verify_assertion(_sign(private, _claims()), config)
    assert verified.claims["expert_id"] == "6a4ca2554a2a45d4b1ad5996"


def test_alg_none_is_rejected(keypair, config) -> None:
    private, _ = keypair
    token = _sign(private, _claims(), alg="none")
    with pytest.raises(AssertionRejected) as exc:
        verify_assertion(token, config)
    assert exc.value.reason == "algorithm_not_allowed"


def test_wrong_signature_is_rejected(keypair, config) -> None:
    _, public_key = keypair
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = _sign(other, _claims())
    with pytest.raises(AssertionRejected) as exc:
        verify_assertion(token, config)
    assert exc.value.reason == "bad_signature"
    assert public_key is not None


def test_tampered_payload_is_rejected(keypair, config) -> None:
    private, _ = keypair
    header, _, signature = _sign(private, _claims()).split(".")
    forged = _b64url(json.dumps(_claims(tenant_id="9999"), separators=(",", ":")).encode())
    with pytest.raises(AssertionRejected) as exc:
        verify_assertion(f"{header}.{forged}.{signature}", config)
    assert exc.value.reason == "bad_signature"


def test_audience_mismatch_is_rejected(keypair, config) -> None:
    private, _ = keypair
    token = _sign(private, _claims(aud=_b64(encrypt_expected("other-audience", SALT))))
    with pytest.raises(AssertionRejected) as exc:
        verify_assertion(token, config)
    assert exc.value.reason == "audience_mismatch"


def test_issuer_mismatch_is_rejected(keypair, config) -> None:
    private, _ = keypair
    token = _sign(private, _claims(iss=[_b64(encrypt_expected("other-issuer", SALT))]))
    with pytest.raises(AssertionRejected) as exc:
        verify_assertion(token, config)
    assert exc.value.reason == "issuer_mismatch"


def test_malformed_token_is_rejected(config) -> None:
    with pytest.raises(AssertionRejected) as exc:
        verify_assertion("not-a-jwt", config)
    assert exc.value.reason == "malformed_token"


def test_stale_issued_at_is_rejected_when_max_age_enforced(keypair, config) -> None:
    private, _ = keypair
    token = _sign(private, _claims(iat=1))
    with pytest.raises(AssertionRejected) as exc:
        verify_assertion(token, config, max_session_age_sec=60)
    assert exc.value.reason == "assertion_expired"


def test_max_age_is_skipped_when_not_configured(keypair, config) -> None:
    private, _ = keypair
    token = _sign(private, _claims(iat=1))
    assert verify_assertion(token, config, max_session_age_sec=0).claims["exp"] == -1


# --- 身份提取与指纹 -------------------------------------------------------


def test_extract_identity_requires_every_configured_key(config) -> None:
    with pytest.raises(AssertionRejected) as exc:
        extract_identity({"expert_id": "x"}, config)
    assert exc.value.reason == "identity_key_missing:tenant_id"


def test_extract_identity_rejects_blank_value(config) -> None:
    with pytest.raises(AssertionRejected):
        extract_identity({"expert_id": "  ", "tenant_id": "1001"}, config)


def test_identity_hash_is_order_independent() -> None:
    left = compute_identity_hash({"expert_id": "a", "tenant_id": "b"})
    right = compute_identity_hash({"tenant_id": "b", "expert_id": "a"})
    assert left == right
    assert len(left) == 64


def test_identity_hash_changes_with_tenant() -> None:
    left = compute_identity_hash({"expert_id": "a", "tenant_id": "b"})
    right = compute_identity_hash({"expert_id": "a", "tenant_id": "c"})
    assert left != right


# --- 配置 ---------------------------------------------------------------


def test_empty_config_means_no_platform() -> None:
    assert load_platform_configs("") == {}
    assert load_platform_configs(None) == {}


def test_missing_factor_is_a_config_error(keypair) -> None:
    _, public = keypair
    pem = public.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")
    with pytest.raises(PlatformConfigError):
        load_platform_configs(json.dumps({"mssw": {"public_key": pem, "issuer": ISSUER}}))


def test_oversized_salt_is_a_config_error(keypair) -> None:
    _, public = keypair
    pem = public.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")
    payload = {
        "mssw": {
            "public_key": pem,
            "issuer": ISSUER,
            "audience": AUDIENCE,
            "salt": "x" * 33,
        }
    }
    with pytest.raises(PlatformConfigError):
        load_platform_configs(json.dumps(payload))


def test_config_repr_hides_factors(config) -> None:
    rendered = repr(config)
    assert ISSUER not in rendered
    assert AUDIENCE not in rendered
