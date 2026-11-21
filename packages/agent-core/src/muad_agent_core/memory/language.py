"""Reply-language policy shared by interactive Runs and scheduled AGENTIC tasks.

The platform default is not always the effective default: an explicit user
memory preference (``PREFERENCE/language``) overrides it until the current
message asks for another language.  Both the Runtime prompt path and the
Worker scheduled path call these functions, so the priority is defined once.
"""

from __future__ import annotations

from collections.abc import Iterable

from muad_contracts.i18n import normalize_locale

from .selection import MemoryRecord


def memory_language(records: Iterable[MemoryRecord]) -> str | None:
    """Return the first valid ``PREFERENCE/language`` value in selection order.

    Selection already resolves cardinality and priority; this function only
    projects the chosen record into the canonical locale.  Invalid or
    non-language records are ignored instead of being guessed.
    """
    for record in records:
        if record.memory_kind != "PREFERENCE" or record.predicate.lower() != "language":
            continue
        content = record.content
        value = content.get("value") if isinstance(content, dict) else None
        normalized = normalize_locale(value if isinstance(value, str) else None)
        if normalized is not None:
            return normalized
    return None


def resolve_reply_locale(
    platform_locale: str | None,
    memory_locale: str | None,
) -> str | None:
    """Resolve the effective default reply locale: memory > platform.

    A ``None`` result means neither source supplied a supported locale; the
    prompt builder then omits the language section (old/unknown executions).
    """
    return normalize_locale(memory_locale) or normalize_locale(platform_locale)


__all__ = ["memory_language", "resolve_reply_locale"]
