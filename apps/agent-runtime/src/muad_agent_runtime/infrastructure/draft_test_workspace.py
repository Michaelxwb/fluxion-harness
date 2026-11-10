"""草稿测试的 workspace 租约、共享数据与跨实例互斥。"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from muad_agent_core.sandbox import WorkspaceError, WorkspaceLease, WorkspaceStore
from muad_agent_core.sandbox.workspace_shell import DEFAULT_WORKSPACE_LEASE_SEC
from muad_agent_core.skill.draft_shell import DraftShellArchive, DraftShellError, DraftShellRequest
from muad_common.draft_testing import (
    draft_test_data_dir,
    draft_test_lock_key,
    prepare_draft_test_data,
)
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from .workspace_store import SessionFactoryProvider

logger = logging.getLogger("muad_agent_runtime.draft_test_workspace")


class SqlDraftTestWorkspaceStore:
    def __init__(self, workspaces: WorkspaceStore, session_factory_provider: SessionFactoryProvider) -> None:
        self._workspaces = workspaces
        self._sessions = session_factory_provider

    @asynccontextmanager
    async def guard(self, request: DraftShellRequest) -> AsyncIterator[None]:
        """先互斥再取草稿，避免删除与 archive 抓取之间产生陈旧副本执行。"""
        try:
            factory = self._sessions()
            async with factory() as session, session.begin():
                key = draft_test_lock_key(request.tenant_id, request.owner_user_id, request.draft_id)
                if not await session.scalar(select(func.pg_try_advisory_xact_lock(key))):
                    raise DraftShellError(
                        "DRAFT_TEST_BUSY", "This draft is already being tested", retryable=True
                    )
                yield
        except ValueError as exc:
            raise DraftShellError("COMMON_VALIDATION_ERROR", "Invalid draft test identity") from exc
        except SQLAlchemyError as exc:
            logger.warning("draft_test_workspace_unavailable error=%s", type(exc).__name__)
            raise DraftShellError("WORKSPACE_UNAVAILABLE", "Draft test data is unavailable") from exc

    @asynccontextmanager
    async def open(self, request: DraftShellRequest, archive: DraftShellArchive) -> AsyncIterator[Path]:
        lease = await self._acquire(request, archive)
        try:
            directory = draft_test_data_dir(lease.workspace_dir, archive.draft_id).resolve()
            yield directory
        except ValueError as exc:
            raise DraftShellError("DRAFT_PATH_INVALID", str(exc)) from exc
        except OSError as exc:
            logger.warning("draft_test_directory_unavailable error=%s", type(exc).__name__)
            raise DraftShellError("WORKSPACE_UNAVAILABLE", "Draft test data is unavailable") from exc
        finally:
            await self._release(lease)

    async def _acquire(self, request: DraftShellRequest, archive: DraftShellArchive) -> WorkspaceLease:
        def prepare(root: Path) -> None:
            prepare_draft_test_data(root, archive.draft_id, reset=request.reset_data)

        try:
            return await self._workspaces.acquire(
                tenant_id=request.tenant_id,
                user_id=request.owner_user_id,
                agent_id=archive.agent_id,
                execution_id=request.execution_id,
                run_id=request.run_id,
                lease_sec=max(DEFAULT_WORKSPACE_LEASE_SEC, request.timeout_sec + 30.0),
                prepare=prepare,
            )
        except WorkspaceError as exc:
            raise DraftShellError(exc.code, str(exc)) from exc

    async def _release(self, lease: WorkspaceLease) -> None:
        try:
            await self._workspaces.refresh_usage(lease)
        except Exception as exc:  # noqa: BLE001 - 统计失败不得覆盖测试结果
            logger.warning("draft_test_usage_refresh_failed error=%s", type(exc).__name__)
        try:
            await self._workspaces.release(lease)
        except Exception as exc:  # noqa: BLE001 - 释放失败可见，租约到期仍可回收
            logger.warning("draft_test_lease_release_failed error=%s", type(exc).__name__)
