"""MEMORY_INJECTED attribution rows; a failed write degrades to no attribution."""

from __future__ import annotations

import logging
from uuid import UUID

from muad_api import AppError
from sqlalchemy.exc import SQLAlchemyError

from ..infrastructure.db import SessionFactory
from .memory_context import InjectionAttribution
from .run_events import EventWriter

logger = logging.getLogger(__name__)

MEMORY_INJECTED_EVENT = "MEMORY_INJECTED"


class MemoryInjectionLog:
    def __init__(
        self,
        factory: SessionFactory,
        *,
        tenant_id: str,
        conversation_id: UUID,
        run_id: UUID,
    ) -> None:
        self._factory = factory
        self._tenant_id = tenant_id
        self._conversation_id = conversation_id
        self._run_id = run_id

    async def record(self, attribution: InjectionAttribution) -> None:
        payload = {
            "run_id": str(self._run_id),
            "selection_hash": attribution.selection_hash,
            "items": [
                {
                    "memory_key": item.memory_key,
                    "version": item.version,
                    "score": item.score,
                    "decision": item.decision,
                }
                for item in attribution.items
            ],
            "dropped": {
                "count": attribution.dropped_count,
                "bytes": attribution.dropped_bytes,
            },
        }
        try:
            async with self._factory() as session:
                await EventWriter(session).append(
                    tenant_id=self._tenant_id,
                    conversation_id=self._conversation_id,
                    run_id=self._run_id,
                    event_type=MEMORY_INJECTED_EVENT,
                    payload=payload,
                )
                await session.commit()
        except (SQLAlchemyError, AppError) as exc:
            logger.warning(
                "memory_injection_attribution_failed",
                extra={
                    "tenant_id": self._tenant_id,
                    "run_id": str(self._run_id),
                    "error_type": type(exc).__name__,
                },
            )
