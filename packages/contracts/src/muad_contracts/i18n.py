"""Single source of truth for the locales the platform can speak.

Both the platform-settings schema and the prompt/reply-language policy import
these values.  Keeping the list in one place prevents the settings UI from
accepting a locale that the prompt builder cannot render (or vice versa).
"""

from __future__ import annotations

from typing import Final

#: Canonical locale tags.  The order is stable: it is also the fallback order.
SUPPORTED_LOCALES: Final[tuple[str, str]] = ("zh-CN", "en-US")
DEFAULT_LOCALE: Final = SUPPORTED_LOCALES[0]


def normalize_locale(value: str | None) -> str | None:
    """Return the canonical supported locale for ``value``, or ``None``.

    Accepts the canonical tags plus their language prefixes (``zh``/``en``),
    because user memory stores the value the model wrote from the user's
    request.  Anything outside the supported set is ignored instead of being
    guessed.
    """
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if normalized.startswith("zh"):
        return "zh-CN"
    if normalized.startswith("en"):
        return "en-US"
    return None


__all__ = ["DEFAULT_LOCALE", "SUPPORTED_LOCALES", "normalize_locale"]
