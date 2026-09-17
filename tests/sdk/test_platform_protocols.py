import inspect

import pytest
from muad_contracts import CredentialMode
from muad_platform_sdk import (
    CredentialActor,
    CredentialResolver,
    PlatformAdapter,
    PlatformClient,
    PlatformConfig,
    PlatformRequest,
    PlatformSession,
    PlatformSessionManager,
    PlatformTarget,
    PreparedRequest,
    ResolvedCredential,
    SecretProvider,
    SecretValue,
    SessionMode,
    SessionRequest,
)

ACTOR = CredentialActor(tenant_id="tenant-1", user_id="user-1")
CREDENTIAL = SecretValue(value="ak-value", version="v1")


async def test_dummy_adapter_satisfies_platform_adapter_protocol(
    adapter: PlatformAdapter,
    platform_config: PlatformConfig,
) -> None:
    assert adapter.key == "dummy"
    assert adapter.name == "Dummy Adapter"
    assert adapter.version == "1.0.0"
    assert adapter.session_mode is SessionMode.SESSION
    assert adapter.platform_config_schema["type"] == "object"
    assert adapter.credential_schema["type"] == "object"

    session = await adapter.authenticate(platform_config, CREDENTIAL)
    assert session == PlatformSession(session_id="mss:v1")
    assert await adapter.validate(platform_config, session) is True

    prepared = await adapter.prepare_request(
        platform_config,
        session,
        PlatformRequest(target=PlatformTarget(method="GET", path="/api/customer/C-1001")),
        CREDENTIAL,
    )
    assert prepared == PreparedRequest(method="GET", url="https://mss.example/api/customer/C-1001")


async def test_dummy_platform_client_satisfies_protocol(platform_client: PlatformClient) -> None:
    assert inspect.iscoroutinefunction(platform_client.call)
    assert inspect.iscoroutinefunction(platform_client.request)

    result = await platform_client.call(
        "mss",
        PlatformRequest(target=PlatformTarget(service="svc", operation="op"), payload={"k": "v"}),
    )

    assert result == {"platform_key": "mss", "payload": {"k": "v"}}


async def test_dummy_secret_provider_satisfies_protocol(secret_provider: SecretProvider) -> None:
    assert await secret_provider.get("secret-ref-1") == CREDENTIAL

    with pytest.raises(KeyError):
        await secret_provider.get("missing-ref")


async def test_dummy_credential_resolver_satisfies_protocol(
    credential_resolver: CredentialResolver,
    platform_config: PlatformConfig,
) -> None:
    resolved = await credential_resolver.resolve(ACTOR, platform_config)

    assert isinstance(resolved, ResolvedCredential)
    assert resolved.credential_ref == "secret-ref-1"
    assert resolved.version == "v1"


async def test_credential_resolver_can_return_none_for_none_mode(
    none_credential_resolver: CredentialResolver,
    platform_config: PlatformConfig,
) -> None:
    none_platform = platform_config.model_copy(update={"credential_mode": CredentialMode.NONE})

    assert await none_credential_resolver.resolve(ACTOR, none_platform) is None


async def test_dummy_session_manager_satisfies_protocol(
    session_manager: PlatformSessionManager,
    platform_config: PlatformConfig,
) -> None:
    request = SessionRequest(
        platform=platform_config,
        actor_scope="user:user-1",
        credential_version="v1",
        credential_ref="secret-ref-1",
        credential=CREDENTIAL,
    )

    session = await session_manager.acquire(request)
    assert session == PlatformSession(session_id="mss:v1")

    assert await session_manager.renew(request) == session

    await session_manager.invalidate(platform=platform_config, actor_scope="user:user-1")


async def test_session_manager_returns_none_without_credential(
    session_manager: PlatformSessionManager,
    platform_config: PlatformConfig,
) -> None:
    request = SessionRequest(
        platform=platform_config,
        actor_scope="none",
        credential_version="none",
        credential_ref=None,
        credential=None,
    )

    assert await session_manager.acquire(request) is None
