from __future__ import annotations

SESSION_KEY_PREFIX = "platform_session"
SESSION_INDEX_PREFIX = "platform_sessions"

NONE_ACTOR_SCOPE = "none"


def user_actor_scope(user_id: str) -> str:
    return f"user:{user_id}"


def shared_actor_scope(shared_credential_id: str) -> str:
    return f"shared:{shared_credential_id}"


def platform_session_key(
    *,
    tenant_id: str,
    platform_id: str,
    actor_scope: str,
    credential_version: str,
    adapter_key: str,
    adapter_version: str,
) -> str:
    return (
        f"{SESSION_KEY_PREFIX}:{tenant_id}:{platform_id}:{actor_scope}"
        f":{credential_version}:{adapter_key}:{adapter_version}"
    )


def platform_sessions_index_key(*, tenant_id: str, platform_id: str) -> str:
    return f"{SESSION_INDEX_PREFIX}:{tenant_id}:{platform_id}"
