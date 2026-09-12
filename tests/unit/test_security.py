from framework.web.security import (
    ROLE_ADMIN,
    ROLE_BUILDER,
    SessionPrincipal,
    generate_session_token,
    hash_password,
    hash_token,
    verify_password,
)


def test_password_hash_round_trips() -> None:
    stored = hash_password("s3cret-pass")
    assert stored.startswith("pbkdf2_sha256$")
    assert "s3cret-pass" not in stored
    assert verify_password("s3cret-pass", stored)


def test_password_hash_rejects_wrong_password_and_malformed_hash() -> None:
    stored = hash_password("s3cret-pass")
    assert not verify_password("wrong", stored)
    assert not verify_password("s3cret-pass", "not-a-valid-hash")
    assert not verify_password("s3cret-pass", "md5$1$abc$def")


def test_password_hash_is_salted_per_call() -> None:
    assert hash_password("same") != hash_password("same")


def test_session_token_is_random_and_only_hash_compared() -> None:
    token = generate_session_token()
    assert token != generate_session_token()
    assert len(token) >= 32
    digest = hash_token(token)
    assert digest != token and len(digest) == 64


def test_principal_role_checks() -> None:
    admin = SessionPrincipal(account_id="a", tenant_id="default", username="admin", role=ROLE_ADMIN)
    builder = SessionPrincipal(
        account_id="b", tenant_id="default", username="dev", role=ROLE_BUILDER
    )
    assert admin.is_admin is True
    assert builder.is_admin is False
