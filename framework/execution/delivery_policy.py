import random

DELIVERY_DEAD_LETTER = "DELIVERY_DEAD_LETTER"


def delivery_dedupe_key(execution_id: str, event: str) -> str:
    return f"delivery:{execution_id}:{event}"


def delivery_backoff_seconds(attempt: int, *, base_seconds: int | None = None) -> float:
    """Exponential backoff with bounded jitter (V1.7 D03)."""
    if attempt < 1:
        raise ValueError("attempt must start at 1")
    if base_seconds is None:
        from framework.settings import get_settings

        base_seconds = get_settings().delivery_backoff_base_seconds
    return base_seconds * (2.0 ** (attempt - 1)) + random.uniform(0, base_seconds)
