"""S-02/S-03/E-02/E-04：消费侧直接使用 DB 明文密钥，且日志/审计脱敏。"""

from __future__ import annotations

import json
import sys
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from muad_agent_runtime.application.executor import resolve_model_api_key
from muad_api import AppError
from muad_api.audit import sanitize_audit_payload, write_config_audit
from muad_common import SharedSettings
from muad_contracts import BotSnapshotItem, ResolvedModel
from muad_im_gateway.channels.base import ChannelAdapterUnavailable
from muad_im_gateway.channels.wecom.adapter import WeComAdapter
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests/gateway"))

from fakes import FakeWeComSdkFactory  # noqa: E402


@pytest.fixture()
async def engine() -> AsyncIterator[AsyncEngine]:
    database_url = SharedSettings().database_url
    if not database_url:
        pytest.skip("DATABASE_URL not configured")
    engine = create_async_engine(database_url)
    yield engine
    await engine.dispose()


async def test_s02_runtime_reads_api_key_from_model_field() -> None:
    model = ResolvedModel(
        id=uuid.uuid4(),
        revision=1,
        model_id="gpt-4o-mini",
        base_url="https://llm.test/v1",
        api_key="sk-from-db",
    )

    secret = await resolve_model_api_key(model)

    assert secret.value == "sk-from-db"
    assert secret.version == "db"

    import muad_platform_sdk

    assert not hasattr(muad_platform_sdk, "SecretProvider")


async def test_s03_gateway_consumes_bot_secret_from_db(engine: AsyncEngine) -> None:
    tenant_id = SharedSettings().default_tenant_id
    suffix = uuid.uuid4().hex[:8]
    model_id, agent_id, bot_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    secret_value = f"wecom-secret-{suffix}"
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO control.model_definition "
                "(id, tenant_id, key, name, model_id, base_url, enabled) "
                "VALUES (:id, :tenant_id, :key, 'Consumer Model', 'gpt-4o-mini', "
                "'https://api.example.com/v1', true)"
            ),
            {"id": model_id, "tenant_id": tenant_id, "key": f"consumer-model-{suffix}"},
        )
        await connection.execute(
            text(
                "INSERT INTO control.agent_definition "
                "(id, tenant_id, key, name, instructions, model_id, runtime_config_json, enabled, revision) "
                "VALUES (:id, :tenant_id, :key, 'Consumer Agent', 'help', :model_id, "
                "'{}'::jsonb, true, 1)"
            ),
            {
                "id": agent_id,
                "tenant_id": tenant_id,
                "key": f"consumer-agent-{suffix}",
                "model_id": model_id,
            },
        )
        await connection.execute(
            text(
                "INSERT INTO control.bot_account (id, tenant_id, channel, name, bot_id, secret, agent_id) "
                "VALUES (:id, :tenant_id, 'WECOM', 'Consumer Bot', :bot_id, :secret, :agent_id)"
            ),
            {
                "id": bot_id,
                "tenant_id": tenant_id,
                "bot_id": f"consumer-{suffix}",
                "secret": secret_value,
                "agent_id": agent_id,
            },
        )

    factory = FakeWeComSdkFactory()
    try:
        async with engine.connect() as connection:
            row = (
                await connection.execute(
                    text("SELECT bot_id, secret, agent_id FROM control.bot_account WHERE id = :id"),
                    {"id": bot_id},
                )
            ).one()
        bot = BotSnapshotItem(
            bot_account_id=bot_id, bot_id=row.bot_id, secret=row.secret, agent_id=row.agent_id
        )
        adapter = WeComAdapter(
            sdk_factory=factory,
            bots=[bot],
            backoff_base_sec=0.01,
            backoff_max_sec=0.02,
            stream_flush_interval_sec=1000.0,
        )
        await adapter.start()
        try:
            assert factory.secrets == [(row.bot_id, secret_value)]
        finally:
            await adapter.stop()
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM control.bot_account WHERE id = :id"), {"id": bot_id}
            )
            await connection.execute(
                text("DELETE FROM control.agent_definition WHERE id = :id"), {"id": agent_id}
            )
            await connection.execute(
                text("DELETE FROM control.model_definition WHERE id = :id"), {"id": model_id}
            )


async def test_e02_logs_and_audit_never_contain_plaintext_keys(engine: AsyncEngine) -> None:
    assert sanitize_audit_payload({"api_key": "sk-x", "secret": "s", "name": "ok"}) == {"name": "ok"}

    tenant_id = SharedSettings().default_tenant_id
    user_id = uuid.uuid4()
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO control.platform_user (id, tenant_id, user_code, display_name) "
                "VALUES (:id, :tenant_id, :user_code, 'Audit Plaintext Probe')"
            ),
            {"id": user_id, "tenant_id": tenant_id, "user_code": f"audit-probe-{user_id.hex[:8]}"},
        )
    try:
        async with session_factory() as session:
            await write_config_audit(
                session,
                actor_user_id=uuid.uuid4(),
                resource_type="PLATFORM_USER",
                resource_id=user_id,
                action="UPDATE",
                before={
                    "api_key": "sk-should-not-persist",
                    "secret": "secret-should-not-persist",
                },
                after={
                    "api_key": "sk-should-not-persist",
                    "secret": "secret-should-not-persist",
                },
                tenant_id=tenant_id,
            )
            await session.commit()

        async with engine.connect() as connection:
            row = (
                await connection.execute(
                    text(
                        "SELECT before_json, after_json FROM control.config_audit_log "
                        "WHERE resource_id = :id ORDER BY create_time DESC LIMIT 1"
                    ),
                    {"id": user_id},
                )
            ).one()
        rendered = json.dumps([row.before_json, row.after_json], ensure_ascii=False)
        assert "sk-should-not-persist" not in rendered
        assert "secret-should-not-persist" not in rendered
        assert row.before_json == {}
        assert row.after_json == {}
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM control.config_audit_log WHERE resource_id = :id"), {"id": user_id}
            )
            await connection.execute(
                text("DELETE FROM control.platform_user WHERE id = :id"), {"id": user_id}
            )


async def test_e04_missing_key_fails_without_external_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    model = ResolvedModel(id=uuid.uuid4(), revision=1, model_id="gpt-4o-mini", base_url="https://llm.test/v1")
    with pytest.raises(AppError) as error:
        await resolve_model_api_key(model)
    assert error.value.code == "CREDENTIAL_MISSING"

    factory = FakeWeComSdkFactory()
    adapter = WeComAdapter(
        sdk_factory=factory,
        bots=[
            BotSnapshotItem(
                bot_account_id=uuid.uuid4(), bot_id="no-secret-bot", secret=None, agent_id=uuid.uuid4()
            )
        ],
        backoff_base_sec=0.01,
        backoff_max_sec=0.02,
        stream_flush_interval_sec=1000.0,
    )
    with pytest.raises(ChannelAdapterUnavailable):
        await adapter.start()
    assert factory.clients == []
