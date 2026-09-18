import asyncio
import time
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.control import ModelDefinition
from ..infrastructure.repositories.model_repository import ModelRepository

PROBE_TIMEOUT_SEC = 5.0
PROBE_CONCURRENCY = 4
CHAT_PROBE_PATH = "/chat/completions"
MODELS_PATH = "/models"


async def _probe(client: httpx.AsyncClient, model: ModelDefinition) -> tuple[str, str | None]:
    headers = {"Authorization": f"Bearer {model.api_key}"} if model.api_key else {}
    base = model.base_url.rstrip("/")
    try:
        response = await client.get(f"{base}{MODELS_PATH}", headers=headers)
        if response.status_code in (404, 405):
            response = await client.post(
                f"{base}{CHAT_PROBE_PATH}",
                headers=headers,
                json={
                    "model": model.model_id,
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 1,
                },
            )
    except httpx.TimeoutException:
        return "FAILED", "MODEL_UNAVAILABLE"
    except httpx.TransportError:
        return "FAILED", "MODEL_UNAVAILABLE"

    if 200 <= response.status_code < 300:
        return "AVAILABLE", None
    if response.status_code in (401, 403):
        return "FAILED", "CREDENTIAL_MISSING"
    if response.status_code >= 500:
        return "FAILED", "MODEL_UNAVAILABLE"
    return "FAILED", "COMMON_INTERNAL_ERROR"


class ModelTestService:
    """批量测试：Console 侧 OpenAI 兼容探测，只更新测试状态，不触碰 revision。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._models = ModelRepository(session)

    async def batch_test(self, tenant_id: str, model_ids: Sequence[uuid.UUID]) -> list[dict[str, Any]]:
        models = list(await self._models.list_by_ids(tenant_id, list(model_ids)))
        known = {model.id for model in models}
        semaphore = asyncio.Semaphore(PROBE_CONCURRENCY)

        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_SEC) as client:

            async def run(model: ModelDefinition) -> dict[str, Any]:
                started = time.monotonic()
                if not model.enabled:
                    return self._entry(model.id, "FAILED", "MODEL_DISABLED", started)
                async with semaphore:
                    status, error_code = await _probe(client, model)
                return self._entry(model.id, status, error_code, started)

            results = list(await asyncio.gather(*(run(model) for model in models)))

        for model, result in zip(models, results, strict=True):
            model.last_test_status = result["status"]
            model.last_test_at = result["tested_at"]
        missing = [
            self._entry(model_id, "FAILED", "COMMON_NOT_FOUND", time.monotonic())
            for model_id in model_ids
            if model_id not in known
        ]
        await self._session.flush()
        return results + missing

    def _entry(
        self, model_id: uuid.UUID, status: str, error_code: str | None, started: float
    ) -> dict[str, Any]:
        return {
            "model_id": str(model_id),
            "status": status,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "error_code": error_code,
            "tested_at": datetime.now(UTC),
        }
