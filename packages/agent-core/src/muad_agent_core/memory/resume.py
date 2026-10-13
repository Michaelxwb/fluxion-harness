"""Conservative complete-request grammar; lexical matching cannot grant execution rights."""

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

from muad_contracts.memory import MemoryResumeOption, memory_hash

from .rank import MemoryRecord

_AMBIGUOUS = re.compile(
    r"不|没|别|什么意思|是什么|还是|如果|除非|吗|[?？]|\b(?:not|no|don't|if|means|is)\b", re.I
)
_CHINESE = re.compile(r"(?:请(?:帮我)?|帮我)?(?:继续|接着|恢复|续接)(?:做|处理)?(.*)")
_ENGLISH = re.compile(r"(?:please\s+)?(?:continue|resume)(?:\s+(.*))?", re.I)
_CHOICE = re.compile(
    r"(?:选(?:择)?\s*)?第?\s*([0-9一二三四五六七八九十]+)\s*个?|(?:option\s+|#)([0-9]+)", re.I
)
_DIGITS = {word: number for number, word in enumerate("零一二三四五六七八九", 0)}


@dataclass(frozen=True, slots=True)
class ResumeIntent:
    theme: str | None


def _text(text: str) -> str:
    return unicodedata.normalize("NFKC", text).strip().rstrip("。.!！").strip()


def parse_resume_intent(text: str) -> ResumeIntent | None:
    text = _text(text)
    if _AMBIGUOUS.search(text):
        return None
    match = _CHINESE.fullmatch(text)
    if match is not None:
        theme = re.sub(r"^(?:上次|昨天|之前)(?:的)?", "", match.group(1)).strip()
        theme = re.sub(r"(?:的)?(?:任务|工作)$", "", theme).strip()
        return ResumeIntent(theme or None)
    match = _ENGLISH.fullmatch(text)
    if match is None:
        return None
    theme = re.sub(r"^(?:the\s+)?(?:previous|last|yesterday(?:'s)?)\s*", "", match.group(1) or "", flags=re.I)
    theme = re.sub(r"^(?:the\s+)?(?:task|work)$", "", theme, flags=re.I).strip()
    return ResumeIntent(theme or None)


def _number(value: str) -> int | None:
    if value.isascii() and value.isdigit():
        return int(value)
    if value in _DIGITS:
        return _DIGITS[value]
    if value.count("十") == 1:
        tens, units = value.split("十")
        if (not tens or tens in _DIGITS) and (not units or units in _DIGITS):
            return (_DIGITS.get(tens, 1) * 10) + _DIGITS.get(units, 0)
    return None


def choose_resume_option(text: str, items: Sequence[MemoryResumeOption]) -> MemoryResumeOption | None:
    exact = unicodedata.normalize("NFKC", text).strip()
    text = _text(exact)
    if _AMBIGUOUS.search(text):
        return None
    match = _CHOICE.fullmatch(text)
    if match is not None:
        number = _number(match.group(1) or match.group(2))
        return next((row for row in items if row.number == number), None)
    matches = [row for row in items if exact in (row.memory_key, unicodedata.normalize("NFKC", row.title))]
    return matches[0] if len(matches) == 1 else None


def resume_candidate_hash(record: MemoryRecord) -> str:
    return memory_hash(
        {
            "memory_key": record.memory_key,
            "version": record.version,
            "content": record.content.model_dump(mode="json"),
            "expires_at": record.expires_at.isoformat() if record.expires_at else None,
        }
    )
