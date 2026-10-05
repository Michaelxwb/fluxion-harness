"""平台设置写入的幂等落表与指纹（design §3.4 API-02/04、RULE-api-002）。

保存与回滚都是「可重试提交」：同 `Idempotency-Key` 同指纹重放首次结果，异指纹返回
`IDEMPOTENCY_MISMATCH`。指纹口径与合规实现 `channel_service.bind_fingerprint` 一致——
规范化 JSON（`sort_keys=True` + 紧凑分隔符）的 SHA256，**判别键含 `endpoint` 与 `tenant_id`**
（存量 `agent_service`/`mcp_service`/`skill_service` 的 `|` 拼接旧写法不在本模块沿用）。

真实并发控制：先取 `pg_advisory_xact_lock`（键由 `(tenant_id, idempotency_key, endpoint)` 派生）
把同键请求串行化，`control.platform_setting_idempotency` 的 partial unique 只作兜底。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.control import PlatformSettingIdempotency

#: 写入口路由模板；同一 `Idempotency-Key` 在不同端点互不干扰。
ENDPOINT_SAVE = "/api/v1/platform-settings"
ENDPOINT_RESTORE = "/api/v1/platform-settings/revisions/{revision}/restore"

FINGERPRINT_PREFIX = "sha256:"


def idempotency_fingerprint(
    tenant_id: str, endpoint: str, fields: Mapping[str, Any]
) -> str:
    """规范化 JSON 指纹；判别键含 endpoint 与 tenant_id（RULE-api-002）。"""
    canonical = json.dumps(
        {"endpoint": endpoint, "tenant_id": tenant_id, **fields},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return FINGERPRINT_PREFIX + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class PlatformSettingIdempotencyStore:
    """平台设置幂等表的最小读写：加锁 → 重放 → 记录。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def lock(self, tenant_id: str, idempotency_key: str, endpoint: str) -> None:
        """同一 (tenant, key, endpoint) 串行化，避免并发重复消费。"""
        digest = hashlib.sha256(
            f"{tenant_id}|{idempotency_key}|{endpoint}".encode()
        ).digest()
        lock_key = int.from_bytes(digest[:8], "big", signed=True)
        await self._session.execute(select(func.pg_advisory_xact_lock(lock_key)))

    async def replay(
        self,
        tenant_id: str,
        idempotency_key: str,
        endpoint: str,
        fingerprint: str,
    ) -> dict[str, Any] | None:
        """已记录则返回首次结果；指纹不一致即 `IDEMPOTENCY_MISMATCH`。"""
        record = await self._find(tenant_id, idempotency_key, endpoint)
        if record is None:
            return None
        if record.request_fingerprint != fingerprint:
            raise AppError(ErrorCode.IDEMPOTENCY_MISMATCH)
        return dict(record.response_json)

    async def record(
        self,
        tenant_id: str,
        idempotency_key: str,
        endpoint: str,
        fingerprint: str,
        response: Mapping[str, Any],
    ) -> None:
        self._session.add(
            PlatformSettingIdempotency(
                tenant_id=tenant_id,
                idempotency_key=idempotency_key,
                endpoint=endpoint,
                request_fingerprint=fingerprint,
                response_json=dict(response),
            )
        )
        await self._session.flush()

    async def _find(
        self, tenant_id: str, idempotency_key: str, endpoint: str
    ) -> PlatformSettingIdempotency | None:
        record: PlatformSettingIdempotency | None = await self._session.scalar(
            select(PlatformSettingIdempotency).where(
                PlatformSettingIdempotency.tenant_id == tenant_id,
                PlatformSettingIdempotency.idempotency_key == idempotency_key,
                PlatformSettingIdempotency.endpoint == endpoint,
                PlatformSettingIdempotency.is_deleted.is_(False),
            )
        )
        return record
