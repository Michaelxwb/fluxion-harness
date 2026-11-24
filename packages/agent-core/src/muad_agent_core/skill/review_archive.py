"""Authorize and materialize a draft version for a managed read-only review."""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Protocol

import httpx
from muad_contracts.skill_results import FrozenReviewDraft

from .contracts import SkillExecutionError, SkillReviewTarget
from .draft_shell import DraftShellArchive, extract_draft_archive
from .result_profiles import RESULT_INVALID, review_draft_key, review_target_fingerprint

ReviewArchiveLoader = Callable[[str], Awaitable[DraftShellArchive]]


class ReviewArchiveSettings(Protocol):
    @property
    def skill_draft_file_limit(self) -> int: ...

    @property
    def skill_draft_file_max_bytes(self) -> int: ...

    @property
    def skill_draft_quota_mib(self) -> int: ...

    @property
    def console_platform_url(self) -> str: ...

    @property
    def internal_service_token(self) -> str | None: ...


def _extract(
    archive: DraftShellArchive, directory: Path, settings: ReviewArchiveSettings,
) -> SkillReviewTarget:
    root = extract_draft_archive(
        archive,
        directory,
        max_files=settings.skill_draft_file_limit,
        max_file_bytes=settings.skill_draft_file_max_bytes,
        max_total_bytes=settings.skill_draft_quota_mib * 1024 * 1024,
    )
    fingerprint, count = review_target_fingerprint(root)
    return SkillReviewTarget(root, archive.draft_id, archive.revision, fingerprint, count)


@asynccontextmanager
async def review_target(
    argv: tuple[str, ...], loader: ReviewArchiveLoader | None, *,
    settings: ReviewArchiveSettings, pinned: FrozenReviewDraft | None = None,
) -> AsyncIterator[SkillReviewTarget]:
    key = review_draft_key(argv)
    if loader is None:
        raise SkillExecutionError("Draft review is not available for this execution", code=RESULT_INVALID)
    archive = await loader(key)
    if pinned is not None and (
        key != pinned.key
        or archive.draft_id != str(pinned.draft_id)
        or archive.revision != pinned.revision
        or "sha256:" + hashlib.sha256(archive.zip_bytes).hexdigest() != pinned.checksum
    ):
        raise SkillExecutionError("Draft no longer matches the admitted review version", code=RESULT_INVALID)
    with TemporaryDirectory(prefix="muad-skill-review-") as temporary:
        task = asyncio.create_task(asyncio.to_thread(_extract, archive, Path(temporary) / key, settings))
        try:
            target = await asyncio.shield(task)
        except asyncio.CancelledError:
            await asyncio.gather(task, return_exceptions=True)
            raise
        yield target


def review_archive_response(response: httpx.Response, *, agent_id: str) -> DraftShellArchive:
    try:
        draft_id = str(uuid.UUID(response.headers["X-Draft-Id"]))
        actual_agent = str(uuid.UUID(response.headers["X-Draft-Agent-Id"]))
        revision = int(response.headers["X-Draft-Revision"])
        if actual_agent != agent_id or revision < 1:
            raise ValueError("unexpected draft scope or revision")
    except (KeyError, ValueError) as exc:
        raise SkillExecutionError("Review archive is not valid", code=RESULT_INVALID) from exc
    return DraftShellArchive(
        draft_id=draft_id, revision=revision, agent_id=actual_agent, zip_bytes=response.content,
    )


async def load_review_archive(
    key: str, *, tenant_id: str, owner_user_id: str, agent_id: str, settings: ReviewArchiveSettings,
) -> DraftShellArchive:
    headers = {"Accept": "application/zip"}
    if settings.internal_service_token:
        headers["X-Internal-Service"] = settings.internal_service_token
    try:
        async with httpx.AsyncClient(trust_env=False) as client:
            response = await client.get(
                f"{settings.console_platform_url.rstrip('/')}/internal/skills/drafts/by-key/{key}/review-archive",
                params={"tenant_id": tenant_id, "owner_user_id": owner_user_id, "agent_id": agent_id},
                headers=headers,
            )
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise SkillExecutionError("Authorized review archive unavailable", code=RESULT_INVALID) from exc
    return review_archive_response(response, agent_id=agent_id)
