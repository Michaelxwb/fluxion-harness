import asyncio
import time
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.control import ModelDefinition
from ..infrastructure.repositories.model_deployment_repository import ModelDeploymentRepository
from ..infrastructure.repositories.model_repository import ModelRepository
from .model_probe_service import PROBE_CONCURRENCY, PROBE_TIMEOUT_SEC, probe_model_endpoint


class ModelTestService:
    """批量测试：Console 侧 OpenAI 兼容探测，只更新测试状态，不触碰 revision。

    探测原语与物理 Key 检测（`ModelProbeService`）共用同一份实现：401/超时/传输错误的映射
    只有一处口径，两处不会漂移。
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._models = ModelRepository(session)
        self._deployments = ModelDeploymentRepository(session)

    async def batch_test(self, tenant_id: str, model_ids: Sequence[uuid.UUID]) -> list[dict[str, Any]]:
        models = list(await self._models.list_by_ids(tenant_id, list(model_ids)))
        known = {model.id for model in models}
        credentials = await self._deployments.find_execution_credentials(
            tenant_id, [model.id for model in models]
        )
        semaphore = asyncio.Semaphore(PROBE_CONCURRENCY)

        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_SEC) as client:

            async def run(model: ModelDefinition) -> dict[str, Any]:
                started = time.monotonic()
                if not model.enabled:
                    return self._entry(model.id, "FAILED", "MODEL_DISABLED", started)
                credential = credentials.get(model.id)
                if credential is None:
                    # 没有可用成员就没有执行入口：不发请求，如实报 CREDENTIAL_MISSING
                    return self._entry(model.id, "FAILED", "CREDENTIAL_MISSING", started)
                async with semaphore:
                    probe = await probe_model_endpoint(
                        client, model_id=model.model_id, credential=credential
                    )
                return self._entry(model.id, probe.status, probe.error_code, started)

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
