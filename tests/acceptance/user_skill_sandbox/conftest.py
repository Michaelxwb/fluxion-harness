"""[user-skill-sandbox] 验收夹具：真实 PostgreSQL 会话（由 acceptance 基座注入空库）。"""

from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from muad_common import SharedSettings, reset_settings_cache
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .draft_support import INTERNAL_TOKEN
from .sandboxed_support import DEFAULT_BASE_IMAGES, _image_exists


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """CI 没有私有 bwrap 测试镜像时明确 skip 整个域。

    该域本来就不在 CI acceptance matrix 里；review-cache 会跑完整 acceptance，
    这里负责让环境缺失表现为可见的 skip，而不是把私有镜像/网络问题伪装成产品失败。
    本地有 fx-sandbox-test:local 或显式 MUAD_SKILL_EXECUTOR_* 镜像时仍真实执行。
    """
    if os.environ.get("CI", "").lower() not in {"1", "true"}:
        return
    configured = os.environ.get("MUAD_SKILL_EXECUTOR_TEST_IMAGE")
    if configured:
        candidates = (configured,)
    else:
        base = os.environ.get("MUAD_SKILL_EXECUTOR_BASE_IMAGE")
        candidates = (base,) if base else DEFAULT_BASE_IMAGES
    if any(_image_exists(image) for image in candidates):
        return
    skip = pytest.mark.skip(
        reason=(
            "CI 未提供 bwrap 测试镜像（fx-sandbox-test:local / "
            "MUAD_SKILL_EXECUTOR_BASE_IMAGE）"
        )
    )
    for item in items:
        item.add_marker(skip)


@pytest.fixture()
def shared_tmp_path() -> Iterator[Path]:
    """Docker Desktop 只共享部分宿主机路径；用 /tmp 让 bind mount 真实可见。"""
    path = Path(tempfile.mkdtemp(prefix="muad-system-deps-", dir="/tmp"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture()
async def session() -> AsyncIterator[AsyncSession]:
    settings = SharedSettings()
    if settings.database_url is None:
        pytest.skip("DATABASE_URL not configured")
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as active:
            yield active
    finally:
        await engine.dispose()


@pytest.fixture()
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """独立 session 工厂：WorkspaceStore 的 acquire/release 必须各自短事务。"""
    settings = SharedSettings()
    if settings.database_url is None:
        pytest.skip("DATABASE_URL not configured")
    engine = create_async_engine(settings.database_url)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.fixture(autouse=True)
def _reset_settings_after_test() -> object:
    yield
    reset_settings_cache()


@pytest.fixture()
def draft_roots(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """草稿/制品落到独立临时目录，避免依赖 dev PVC 与共享 `.data`。"""
    root = tmp_path / "workspaces"
    monkeypatch.setenv("WORKSPACE_ROOT", str(root))
    monkeypatch.setenv("ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", INTERNAL_TOKEN)
    reset_settings_cache()
    return root


@pytest.fixture()
async def internal_client(draft_roots: Path) -> AsyncIterator[AsyncClient]:
    """内部服务身份 + 真实 ASGI 路由；数据库由 acceptance 基座注入。"""
    from muad_console_platform.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"X-Internal-Service": INTERNAL_TOKEN},
    ) as client:
        yield client


@pytest.fixture()
async def approval_env(session: AsyncSession, draft_roots: Path) -> AsyncIterator[object]:
    """TASK-010 审批参与者：管理员、提交者、其他用户、上传 Agent 与真实授权行。"""
    from .approval_support import cleanup_approval_env, seed_approval_env

    env = await seed_approval_env(session, "acc-approval")
    try:
        yield env
    finally:
        await cleanup_approval_env(session, env)


@pytest.fixture()
async def admin_client(approval_env: object) -> AsyncIterator[AsyncClient]:
    """真实管理员登录（CSRF + 会话 Cookie），请求走真实 Console ASGI。"""
    from .approval_support import ApprovalEnv, authenticated_admin

    assert isinstance(approval_env, ApprovalEnv)
    async with authenticated_admin(approval_env) as client:
        yield client


@pytest.fixture()
async def draft_tenant(session: AsyncSession) -> AsyncIterator[Callable[[str], str]]:
    """按用例分配唯一租户，结束后清理本域写入的真实表行。"""
    tenants: list[str] = []

    def make(prefix: str = "acc-draft") -> str:
        tenant = f"{prefix}-{uuid.uuid4()}"
        tenants.append(tenant)
        return tenant

    yield make
    for tenant in tenants:
        for statement in (
            "DELETE FROM task.workspace_lease WHERE workspace_id IN "
            "(SELECT id FROM task.workspace WHERE tenant_id = :tenant_id)",
            "DELETE FROM task.workspace WHERE tenant_id = :tenant_id",
            "DELETE FROM control.skill_import_idempotency WHERE tenant_id = :tenant_id",
            "DELETE FROM control.skill_artifact WHERE skill_id IN "
            "(SELECT id FROM control.skill WHERE tenant_id = :tenant_id)",
            "DELETE FROM control.skill WHERE tenant_id = :tenant_id",
            "DELETE FROM control.skill_draft WHERE tenant_id = :tenant_id",
            "DELETE FROM control.config_audit_log WHERE tenant_id = :tenant_id",
        ):
            await session.execute(text(statement), {"tenant_id": tenant})
    await session.commit()
