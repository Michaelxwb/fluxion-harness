import inspect

from muad_platform_sdk import (
    PlatformAdapter,
    PlatformClient,
    PlatformConfig,
    PlatformRequest,
    PlatformSession,
    PlatformSessionManager,
    PlatformTarget,
    PreparedRequest,
    SecretValue,
    SessionMode,
    SessionRequest,
)

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
