"""Durable runtime input consumed only at complete model/tool round boundaries."""

from muad_agent_core.agent.continuation import RunnerCheckpoint, RuntimeEventBatch, WaitDecision
from muad_agent_core.model import ModelMessage, ModelRole
from sqlalchemy import select

from ...infrastructure.db import SessionFactory
from ...infrastructure.models.runtime import CanonicalEvent
from ..attachments.tool_results import ArtifactResultWriter
from .continuation_service import CONSUMABLE_TYPES, ExecutionIdentity, checkpoint_execution, try_wait
from .materialization import external_data, materialize_results


class DurableRuntimeEventPort:
    def __init__(
        self,
        factory: SessionFactory,
        identity: ExecutionIdentity,
        *,
        writer: ArtifactResultWriter | None = None,
    ) -> None:
        self._factory, self._identity, self._writer = factory, identity, writer

    async def drain_ready(self, cursor: int, limit: int) -> RuntimeEventBatch:
        materialized = await materialize_results(
            self._factory, self._identity, writer=self._writer, batch_limit=limit
        )
        async with self._factory() as session:
            rows = tuple(
                await session.scalars(
                    select(CanonicalEvent)
                    .where(
                        CanonicalEvent.tenant_id == self._identity.tenant_id,
                        CanonicalEvent.run_id == self._identity.run_id,
                        CanonicalEvent.event_type.in_(CONSUMABLE_TYPES),
                        CanonicalEvent.seq > cursor,
                        CanonicalEvent.is_deleted.is_(False),
                    )
                    .order_by(CanonicalEvent.seq)
                    .limit(limit + 1)
                )
            )
        selected = rows[:limit]
        messages = tuple(
            ModelMessage(ModelRole.USER, external_data(row.event_type, row.payload_json or {}))
            for row in selected
        )
        return RuntimeEventBatch(
            messages,
            selected[-1].seq if selected else cursor,
            len(rows) > limit or len(materialized) == limit,
        )

    async def checkpoint(self, checkpoint: RunnerCheckpoint) -> None:
        await checkpoint_execution(self._factory, self._identity, checkpoint)

    async def try_wait(self, checkpoint: RunnerCheckpoint, *, assistant_text: str) -> WaitDecision:
        return await try_wait(self._factory, self._identity, checkpoint, assistant_text=assistant_text)
