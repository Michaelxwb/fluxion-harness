"""Production memory tools wired to isolated PG and the same Run context loader."""

from dataclasses import replace
from uuid import uuid4

from muad_agent_core.tools import ToolCall
from muad_agent_runtime.application.memory_tools import MemoryToolSet

from tests.agent_runtime.memory_cache_support import queries
from tests.agent_runtime.memory_context_support import loader
from tests.agent_runtime.memory_redesign_write_support import writer


def tools(engine, ctx, *, memory=None, confirmation=None):
    return MemoryToolSet(
        service=writer(engine),
        queries=queries(engine),
        context=replace(ctx, actor_type="USER", actor_id=str(ctx.user_id)),
        loader=memory or loader(engine, ctx),
        confirmation=confirmation,
    )


async def recall(tool_set, arguments):
    import json

    result = await tool_set.recall(
        arguments, call=ToolCall(call_id=uuid4().hex, name="recall")
    )
    return json.loads(result.content)
