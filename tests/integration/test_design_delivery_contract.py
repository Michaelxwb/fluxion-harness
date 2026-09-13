"""Execute the design's permission CAS against isolated PostgreSQL test tables.

No application schema is upgraded or cleared. Foreign keys are omitted only in
these scratch tables so the tests can isolate the CAS and new CHECK constraints.
"""

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Table, insert, select, text, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.schema import CreateSchema, CreateTable, DropSchema

from adapters.postgres.models import CapabilityImplementationModel, ChannelDeliveryModel
from tests.design_contracts import CHANNEL_DOC, contract_text
from tests.integration.conftest import TEST_DATABASE_URL

PERMISSION_SQL = text(contract_text(CHANNEL_DOC, "delivery-permit-consume-sql", "sql"))


async def _require_database(engine: AsyncEngine) -> None:
    try:
        async with engine.connect():
            pass
    except (OSError, SQLAlchemyError, TimeoutError) as error:
        message = f"Contract PostgreSQL unavailable: {type(error).__name__}"
        if os.environ.get("REQUIRE_PG") == "1":
            pytest.fail(message)
        pytest.skip(message)


@pytest.fixture
async def contract_database() -> AsyncIterator[AsyncEngine]:
    namespace = f"contract_review_{uuid4().hex}"
    engine = create_async_engine(
        TEST_DATABASE_URL,
        connect_args={"server_settings": {"search_path": namespace}, "timeout": 5},
    )
    created = False
    try:
        await _require_database(engine)
        async with engine.begin() as connection:
            await connection.execute(CreateSchema(namespace))
            created = True
            for model in (ChannelDeliveryModel, CapabilityImplementationModel):
                table = model.__table__
                assert isinstance(table, Table)
                await connection.execute(CreateTable(table, include_foreign_key_constraints=[]))
        yield engine
    finally:
        if created:
            async with engine.begin() as connection:
                await connection.execute(DropSchema(namespace, cascade=True, if_exists=True))
        await engine.dispose()


async def _delivery(engine: AsyncEngine, *, expired: bool = False) -> UUID:
    identifier = uuid4()
    expires = datetime.now(UTC) + timedelta(minutes=-1 if expired else 5)
    async with engine.begin() as connection:
        await connection.execute(insert(ChannelDeliveryModel).values(
            id=identifier, tenant_id="test-tenant", execution_id=uuid4(), delivery_route_id=uuid4(),
            channel="WECOM", event_type="completed", event_id=str(uuid4()), message_key=str(uuid4()),
            payload_json={"type": "TEXT", "text": "contract test"}, status="SENDING",
            attempt=1, lease_epoch=1, lease_expires_at=expires, dedupe_key=str(uuid4()),
        ))
    return identifier


async def _consume(
    engine: AsyncEngine, identifier: UUID, *, tenant: str = "test-tenant", epoch: int = 1
) -> bool:
    async with engine.begin() as connection:
        result = await connection.execute(
            PERMISSION_SQL, {"delivery_id": identifier, "tenant_id": tenant, "lease_epoch": epoch}
        )
        return result.one_or_none() is not None


async def test_concurrent_permission_consumption_has_one_winner(contract_database: AsyncEngine) -> None:
    identifier = await _delivery(contract_database)
    results = await asyncio.gather(
        _consume(contract_database, identifier), _consume(contract_database, identifier)
    )
    assert sorted(results) == [False, True]
    assert not await _consume(contract_database, identifier)
    async with contract_database.connect() as connection:
        consumed_at = await connection.scalar(select(ChannelDeliveryModel.permit_consumed_at).where(
            ChannelDeliveryModel.id == identifier
        ))
    assert consumed_at is not None


@pytest.mark.parametrize(("tenant", "epoch"), [("other-tenant", 1), ("test-tenant", 0)])
async def test_wrong_identity_or_epoch_does_not_consume_permission(
    contract_database: AsyncEngine, tenant: str, epoch: int
) -> None:
    identifier = await _delivery(contract_database)
    assert not await _consume(contract_database, identifier, tenant=tenant, epoch=epoch)
    assert await _consume(contract_database, identifier)


async def test_expired_permission_is_rejected(contract_database: AsyncEngine) -> None:
    identifier = await _delivery(contract_database, expired=True)
    assert not await _consume(contract_database, identifier)


@pytest.mark.parametrize("status", ["PENDING", "UNKNOWN", "DELIVERED"])
async def test_non_sending_delivery_cannot_consume_permission(
    contract_database: AsyncEngine, status: str
) -> None:
    identifier = await _delivery(contract_database)
    async with contract_database.begin() as connection:
        await connection.execute(update(ChannelDeliveryModel).where(
            ChannelDeliveryModel.id == identifier
        ).values(status=status))
    assert not await _consume(contract_database, identifier)


async def test_a_new_epoch_does_not_revalidate_the_old_token(contract_database: AsyncEngine) -> None:
    identifier = await _delivery(contract_database)
    assert await _consume(contract_database, identifier)
    # Represents a new Worker claim after an explicitly proven NOT_SENT result.
    async with contract_database.begin() as connection:
        await connection.execute(update(ChannelDeliveryModel).where(
            ChannelDeliveryModel.id == identifier
        ).values(lease_epoch=2, permit_consumed_at=None))
    assert not await _consume(contract_database, identifier, epoch=1)
    assert await _consume(contract_database, identifier, epoch=2)


@pytest.mark.parametrize("modes", [["SYNC"], ["ASYNC"], ["SYNC", "ASYNC"]])
async def test_database_accepts_all_canonical_support_sets(
    contract_database: AsyncEngine, modes: list[str]
) -> None:
    async with contract_database.begin() as connection:
        await connection.execute(insert(CapabilityImplementationModel).values(
            capability_id=uuid4(), implementation_type="HTTP", supported_execution_modes=modes
        ))


@pytest.mark.parametrize("modes", [[], ["SYNC", "SYNC"], ["UNKNOWN"]])
async def test_database_rejects_invalid_support_sets(
    contract_database: AsyncEngine, modes: list[str]
) -> None:
    with pytest.raises(IntegrityError, match="modes"):
        async with contract_database.begin() as connection:
            await connection.execute(insert(CapabilityImplementationModel).values(
                capability_id=uuid4(), implementation_type="HTTP", supported_execution_modes=modes
            ))


async def test_database_rejects_shared_auth_without_a_secret(contract_database: AsyncEngine) -> None:
    with pytest.raises(IntegrityError, match="violates check constraint"):
        async with contract_database.begin() as connection:
            await connection.execute(insert(CapabilityImplementationModel).values(
                capability_id=uuid4(), implementation_type="HTTP", auth_mode="SHARED_SECRET"
            ))
