"""Scheduled-turn result recording (`record_result`) and user-visible text rules.

The model expresses the outcome of an unattended run through a structured tool call instead
of relying on magic text. ``no_change`` is the only status that suppresses the user-visible
text message; attachments are independent and still enter the message. The selected status
also travels to the task record so the task centre can show what this run concluded.

The recorder is execution-local: it is created for one ``execute_agentic`` call and read at
finalization. It has no external side effects.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from muad_agent_core.prompt import strip_internal_tokens
from muad_agent_core.tools import ToolDefinition, ToolEffect, ToolRegistry

RECORD_RESULT_TOOL = "record_result"
RECORD_RESULT_ALLOWLIST_KEY = f"PLATFORM:{RECORD_RESULT_TOOL}"
NO_CHANGE = "no_change"
RESULT_STATUSES = ("no_change", "progress", "done", "blocked", "needs_attention")

RECORD_RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["status"],
    "properties": {
        "status": {
            "type": "string",
            "enum": list(RESULT_STATUSES),
            "description": (
                "Outcome of this unattended run. Use no_change when nothing changed; the "
                "platform then suppresses the text message but still delivers attachments."
            ),
        }
    },
    "additionalProperties": False,
}


class ResultRecorder:
    """Records the last structured status selected by the model for this execution."""

    def __init__(self) -> None:
        self._status: str | None = None

    @property
    def status(self) -> str | None:
        return self._status

    def record(self, status: str) -> None:
        if status not in RESULT_STATUSES:
            raise ValueError(f"unknown result status: {status!r}")
        self._status = status

    def register(self, registry: ToolRegistry) -> None:
        async def record(arguments: Mapping[str, Any], *, call_id: str) -> str:
            raw = arguments.get("status")
            if not isinstance(raw, str) or raw not in RESULT_STATUSES:
                return f'{{"status":"INVALID","error":"status must be one of {list(RESULT_STATUSES)}"}}'
            self.record(raw)
            return f'{{"status":"RECORDED","result_status":"{raw}"}}'

        registry.register(
            ToolDefinition(
                name=RECORD_RESULT_TOOL,
                description=(
                    "Record this unattended run's outcome. Use no_change when nothing changed: "
                    "the platform suppresses the text message but still delivers attachments."
                ),
                input_schema=RECORD_RESULT_SCHEMA,
                effect=ToolEffect.READ,
                handler=record,
            )
        )


def user_visible_text(text: str, *, status: str | None) -> str:
    """Apply the platform's text rules: no internal tokens, no text on no_change."""
    if status == NO_CHANGE:
        return ""
    return strip_internal_tokens(text)


__all__ = [
    "NO_CHANGE",
    "RECORD_RESULT_ALLOWLIST_KEY",
    "RECORD_RESULT_SCHEMA",
    "RECORD_RESULT_TOOL",
    "RESULT_STATUSES",
    "ResultRecorder",
    "user_visible_text",
]
