"""A quote is provenance, never proof of authorization or truth."""

from muad_contracts.memory import MEMORY_VALUE_MAX_CHARS


def normalize_newlines(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


def verify_quote(message_text: str, quote: str) -> bool:
    """Accept whole-message evidence without a trusted unit boundary.

    A positive result does not authorize saving the text or grant USER status.
    Runtime must bind this result to the current submission and still confirm.
    """
    normalized = normalize_newlines(quote)
    return (
        bool(normalized.strip())
        and len(normalized) <= MEMORY_VALUE_MAX_CHARS
        and normalized == normalize_newlines(message_text)
    )
