"""Memory service regressions through the current command contract and isolated PG."""

from dataclasses import replace
from uuid import uuid4

import pytest
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts.memory import MemoryCandidate
from pydantic import ValidationError

from tests.agent_runtime.memory_redesign_write_support import (
    command,
    commands,
    context,
    seed_facts,
    source_context,
    upsert,
    writer,
)

pytestmark = pytest.mark.integration


async def test_b110_write_and_read_with_tenant_user_isolation(async_tool_database) -> None:
    service, ctx = writer(async_tool_database), context()
    result = await service.execute(ctx, command(upsert("output.language", "zh-CN")))
    assert result.response.items[0].version == 1
    assert len(await service.list_entries(ctx.tenant_id, ctx.user_id)) == 1
    assert await service.list_entries(ctx.tenant_id, uuid4()) == []
    assert await service.list_entries("other-tenant", ctx.user_id) == []
    other = replace(ctx, tenant_id="other-tenant")
    await service.execute(other, command(upsert("output.language", "en-US")))
    assert (await service.list_entries(ctx.tenant_id, ctx.user_id))[0]["content_json"] == {"value": "zh-CN"}


async def test_b110_update_bumps_version_not_duplicate(async_tool_database) -> None:
    service, ctx = writer(async_tool_database), context()
    first = await service.execute(ctx, command(upsert("style.tone", "formal")))
    second = await service.execute(ctx, command(upsert("style.tone", "concise", expected=1)))
    assert first.response.items[0].id == second.response.items[0].id
    assert second.response.items[0].version == 2
    entries = await service.list_entries(ctx.tenant_id, ctx.user_id)
    assert len(entries) == 1 and entries[0]["content_json"] == {"value": "concise"}


async def test_b110_disabled_and_deleted_filtered(async_tool_database) -> None:
    service, ctx = writer(async_tool_database), context()
    await service.execute(ctx, command(upsert("enabled"), upsert("disabled")))
    await service.execute(
        ctx, command(commands().MemoryMutation(action="DISABLE", memory_key="disabled", expected_version=1))
    )
    assert [row["memory_key"] for row in await service.list_entries(ctx.tenant_id, ctx.user_id)] == [
        "enabled"
    ]
    await service.execute(
        ctx, command(commands().MemoryMutation(action="DELETE", memory_key="enabled", expected_version=1))
    )
    assert await service.list_entries(ctx.tenant_id, ctx.user_id) == []


def test_b110_categories_constrained() -> None:
    with pytest.raises(ValidationError):
        MemoryCandidate.model_validate(
            {"memory_key": "k", "category": "RUNTIME_FACT", "content": {"value": "test"}}
        )


async def test_s02_same_key_overwrite_requires_explicit_reenable(async_tool_database) -> None:
    service, ctx = writer(async_tool_database), context()
    await service.execute(ctx, command(upsert()))
    await service.execute(
        ctx, command(commands().MemoryMutation(action="DISABLE", memory_key="color", expected_version=1))
    )
    with pytest.raises(AppError) as exc:
        await service.execute(ctx, command(upsert(value="red", expected=2)))
    assert exc.value.code == ErrorCode.MEMORY_CONFIRMATION_INVALID
    mutation = upsert(value="red", expected=2).model_copy(update={"enable": True})
    await service.execute(ctx, command(mutation))
    rows = await service.list_entries(ctx.tenant_id, ctx.user_id)
    assert len(rows) == 1 and rows[0]["version"] == 3
    assert rows[0]["enabled"] is True and rows[0]["content_json"] == {"value": "red"}


async def test_injection_query_filters_unconfirmed_disabled_deleted_and_expired(async_tool_database) -> None:
    service, ctx = writer(async_tool_database), context()
    user_ctx = await source_context(async_tool_database, ctx)
    await service.execute(user_ctx, command(upsert("a.user")))
    await service.execute(ctx, command(upsert("b.admin"), upsert("c.deleted"), upsert("d.disabled")))
    await service.execute(
        ctx,
        command(
            commands().MemoryMutation(action="DELETE", memory_key="c.deleted", expected_version=1),
            commands().MemoryMutation(action="DISABLE", memory_key="d.disabled", expected_version=1),
        ),
    )
    await seed_facts(async_tool_database, ctx, 1, author="AGENT")
    await seed_facts(async_tool_database, ctx, 1, prefix="expired", expired=True)
    injected = await service.list_for_injection(ctx.tenant_id, ctx.user_id, limit=10)
    assert [row["memory_key"] for row in injected] == ["b.admin", "a.user"]
    assert [row["author"] for row in injected] == ["ADMIN", "USER"]
    limited = await service.list_for_injection(ctx.tenant_id, ctx.user_id, limit=1)
    assert [row["memory_key"] for row in limited] == ["b.admin"]


async def test_search_filters_by_key_prefix_across_authors(async_tool_database) -> None:
    service, ctx = writer(async_tool_database), context()
    await service.execute(ctx, command(upsert("reply.language"), upsert("work.style")))
    await seed_facts(async_tool_database, ctx, 1, author="AGENT", prefix="reply")
    prefixed = await service.search(ctx.tenant_id, ctx.user_id, prefix="reply.", limit=10)
    assert sorted(row["memory_key"] for row in prefixed) == ["reply.1", "reply.language"]
    assert {row["author"] for row in prefixed} == {"ADMIN", "AGENT"}
    assert len(await service.search(ctx.tenant_id, ctx.user_id, limit=1)) == 1


async def test_read_only_scope_does_not_create_scope_row(async_tool_database) -> None:
    from tests.agent_runtime.memory_redesign_write_support import counters

    service, ctx = writer(async_tool_database), context()
    assert await service.list_entries(ctx.tenant_id, ctx.user_id) == []
    assert await service.list_for_injection(ctx.tenant_id, ctx.user_id, limit=10) == []
    assert await service.search(ctx.tenant_id, ctx.user_id, limit=10) == []
    assert (await counters(async_tool_database, ctx))["memory_scope"] == 0
