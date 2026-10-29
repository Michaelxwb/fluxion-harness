"""Webchat 通知的 Redis 键：watermark 供 SSE 建流短路 backfill（不是通知权威）。"""

from __future__ import annotations

import uuid
from typing import Final

NOTIFICATION_WATERMARK_PREFIX: Final = "webchat:notify:seq:"
#: watermark 只需覆盖通知保留期；过期后建流按 after_seq 回填，不影响正确性。
NOTIFICATION_WATERMARK_TTL_SEC: Final = 30 * 24 * 3600


def notification_watermark_key(tenant_id: str, user_id: uuid.UUID) -> str:
    return f"{NOTIFICATION_WATERMARK_PREFIX}{tenant_id}:{user_id}"
