"""Final-answer verification: bounded attempts with an explicit exhaustion outcome."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum


class VerificationStatus(StrEnum):
    VERIFIED = "VERIFIED"
    EXHAUSTED = "VERIFICATION_EXHAUSTED"


@dataclass(frozen=True, slots=True)
class VerificationOutcome:
    status: VerificationStatus
    candidate: str | None
    attempts: int
    reasons: tuple[str, ...]


#: 返回 `(是否通过, 原因)`；原因会写进下一次候选的修正提示与审计。
Verifier = Callable[[str], Awaitable[tuple[bool, str]]]


async def run_verification(
    candidates: Sequence[str], verifier: Verifier, *, max_attempts: int
) -> VerificationOutcome:
    """按顺序验证候选，返回首个通过的候选；全部不通过时显式耗尽。"""
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")
    reasons: list[str] = []
    for index, candidate in enumerate(candidates[:max_attempts]):
        passed, reason = await verifier(candidate)
        if passed:
            return VerificationOutcome(
                VerificationStatus.VERIFIED, candidate, index + 1, tuple(reasons)
            )
        reasons.append(reason)
    return VerificationOutcome(
        VerificationStatus.EXHAUSTED, None, min(len(candidates), max_attempts), tuple(reasons)
    )
