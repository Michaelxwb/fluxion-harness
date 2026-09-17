from muad_platform_sdk import (
    NONE_ACTOR_SCOPE,
    SESSION_INDEX_PREFIX,
    SESSION_KEY_PREFIX,
    platform_session_key,
    platform_sessions_index_key,
    shared_actor_scope,
    user_actor_scope,
)


def test_actor_scope_helpers_match_design_doc() -> None:
    assert user_actor_scope("user-1") == "user:user-1"
    assert shared_actor_scope("shared-1") == "shared:shared-1"
    assert NONE_ACTOR_SCOPE == "none"


def test_platform_session_key_is_exact() -> None:
    key = platform_session_key(
        tenant_id="tenant-1",
        platform_id="platform-1",
        actor_scope=user_actor_scope("user-1"),
        credential_version="v1",
        adapter_key="mssw",
        adapter_version="1.0.0",
    )

    assert key == "platform_session:tenant-1:platform-1:user:user-1:v1:mssw:1.0.0"
    assert key.startswith(f"{SESSION_KEY_PREFIX}:")
    assert len(key.split(":")) == 8


def test_platform_session_key_with_shared_actor_scope() -> None:
    key = platform_session_key(
        tenant_id="tenant-1",
        platform_id="platform-1",
        actor_scope=shared_actor_scope("shared-cred-1"),
        credential_version="v2",
        adapter_key="generic_http_session",
        adapter_version="2.1.0",
    )

    assert key == "platform_session:tenant-1:platform-1:shared:shared-cred-1:v2:generic_http_session:2.1.0"


def test_platform_sessions_index_key_is_exact() -> None:
    index_key = platform_sessions_index_key(tenant_id="tenant-1", platform_id="platform-1")

    assert index_key == "platform_sessions:tenant-1:platform-1"
    assert index_key.startswith(f"{SESSION_INDEX_PREFIX}:")
    assert len(index_key.split(":")) == 3
