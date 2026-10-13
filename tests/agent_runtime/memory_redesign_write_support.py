"""Real, isolated writer fixtures; no mock business store."""

from dataclasses import replace
from importlib import import_module
from uuid import uuid4

import sqlalchemy as sa
from muad_contracts.memory import FactContent, MemoryCandidate, MemoryCategory
from muad_contracts.platform_settings import MemoryPolicySettings
from sqlalchemy.ext.asyncio import async_sessionmaker


def commands():
    return import_module("muad_agent_runtime.application.memory_commands")


def writer(engine):
    module = import_module("muad_agent_runtime.application.memory_service")
    return module.MemoryService(async_sessionmaker(engine, expire_on_commit=False))


def context(*, tenant="writes", user=None, settings=None, actor="ADMIN"):
    return commands().MemoryWriteContext(
        tenant_id=tenant,
        user_id=user or uuid4(),
        actor_type=actor,
        actor_id="fixture-admin",
        settings=settings or MemoryPolicySettings(),
    )


def upsert(key="color", value="blue", *, expected=None, restore=False):
    candidate = MemoryCandidate(
        memory_key=key, category=MemoryCategory.PREFERENCE, content=FactContent(value=value)
    )
    return commands().MemoryMutation(
        action="UPSERT", candidate=candidate, expected_version=expected, restore=restore
    )


def command(*mutations, command_id=None, endpoint="memory.admin"):
    return commands().MemoryCommand(
        command_id=command_id or uuid4().hex, endpoint=endpoint, mutations=list(mutations)
    )


async def counters(engine, ctx):
    async with engine.connect() as connection:
        return {
            table: (
                await connection.execute(
                    sa.text(f"SELECT count(*) FROM runtime.{table} WHERE tenant_id=:tenant"),
                    {"tenant": ctx.tenant_id},
                )
            ).scalar_one()
            for table in ("user_memory", "user_memory_history", "memory_command_receipt", "memory_scope")
        }


async def seed_facts(engine, ctx, count, *, author="ADMIN", prefix="seed", expired=False):
    async with engine.begin() as connection:
        await connection.execute(
            sa.text(
                "INSERT INTO runtime.user_memory (tenant_id,user_id,memory_key,category,content_json,author,"
                "expires_at,explicit_request,confirmation_event_id,write_policy)"
                " SELECT :tenant,:user,:prefix||'.'||n,'PREFERENCE','{\"value\":\"blue\"}',"
                " CAST(:author AS text),"
                " CASE WHEN :expired THEN now()-interval '1 day'"
                "      WHEN :author='AGENT' THEN now()+interval '30 days' ELSE NULL END,"
                " :author='USER',CASE WHEN :author='USER' THEN gen_random_uuid() ELSE NULL END,"
                " 'SERVER_CONTROLLED'"
                " FROM generate_series(1,:count) AS n"
            ),
            {
                "tenant": ctx.tenant_id,
                "user": ctx.user_id,
                "prefix": prefix,
                "author": author,
                "count": count,
                "expired": expired,
            },
        )


async def install_history_failure(engine, *, key=None, reason=None):
    # Trusted fixture literals only; the failing path is an actual PG trigger.
    if key is not None and key != "new.b":
        raise ValueError("unknown failure fixture")
    if reason is not None and reason not in ("EXPIRE", "QUOTA_EVICT"):
        raise ValueError("unknown failure fixture")
    condition = (
        "NEW.memory_key = 'new.b'" if key else (f"NEW.change_reason = '{reason}'" if reason else "true")
    )
    async with engine.begin() as connection:
        await connection.execute(
            sa.text(
                "CREATE FUNCTION runtime.memory_test_failure() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
                f"IF {condition} THEN RAISE EXCEPTION 'MEMORY_TEST_HISTORY_FAILURE'; END IF;"
                " RETURN NEW; END $$"
            )
        )
        await connection.execute(
            sa.text(
                "CREATE TRIGGER memory_test_failure BEFORE INSERT ON runtime.user_memory_history"
                " FOR EACH ROW EXECUTE FUNCTION runtime.memory_test_failure()"
            )
        )


async def source_context(engine, ctx, *, actor="USER", event_type="USER_MESSAGE", seq=1, revision=0):
    from muad_agent_runtime.infrastructure.models.runtime import CanonicalEvent, Conversation
    from muad_contracts.memory import MemorySourceEvidence
    from sqlalchemy.dialects.postgresql import insert

    conversation_id, event_id = ctx.conversation_id or uuid4(), uuid4()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory.begin() as session:
        await session.execute(
            insert(Conversation)
            .values(id=conversation_id, tenant_id=ctx.tenant_id, user_id=ctx.user_id, agent_id=uuid4())
            .on_conflict_do_nothing(index_elements=["id"])
        )
        session.add(
            CanonicalEvent(
                id=event_id,
                tenant_id=ctx.tenant_id,
                conversation_id=conversation_id,
                seq=seq,
                event_type=event_type,
                payload_json={"text": "fixture memory confirmation", "source_scope_revision": revision},
            )
        )
    return replace(
        ctx,
        actor_type=actor,
        conversation_id=conversation_id,
        confirmation_event_id=event_id if actor == "USER" else None,
        source_run_id=ctx.source_run_id or uuid4(),
        evidence=MemorySourceEvidence(
            source_event_id=event_id, source_seq=seq, source_scope_revision=revision
        ),
    )
