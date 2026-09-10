from datetime import datetime, timedelta
from typing import Literal

HUMAN_COMMANDS = ("RESUME", "CANCEL")
HumanCommand = Literal["RESUME", "CANCEL"]

HUMAN_TIMEOUT = "HUMAN_TIMEOUT"
USER_INACTION = "USER_INACTION"


def compute_human_deadline(entered_at: datetime, *, deadline_hours: int | None = None) -> datetime:
    if deadline_hours is None:
        from framework.settings import get_settings

        deadline_hours = get_settings().human_wait_default_timeout_hours
    if deadline_hours <= 0:
        raise ValueError("human deadline must be positive")
    return entered_at + timedelta(hours=deadline_hours)
