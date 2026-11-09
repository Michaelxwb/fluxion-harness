"""S-02 草稿 Shell 验收支持：真实 Console ASGI + 真实 Docker/bwrap 沙箱。

宿主是 macOS 时没有 bwrap：把 ``build_bwrap_argv`` 生成的 argv 放进带 bwrap 的
Linux 镜像执行，bind mount 与命名空间仍是真实内核边界；Runtime 工具、Console
内部 API、PG revision CAS 全部在宿主真实执行，不 mock 业务边界。
"""

from __future__ import annotations

import asyncio
import io
import os
import shutil
import subprocess
import tempfile
import uuid
import zipfile
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from muad_agent_core.sandbox import (
    SandboxPolicy,
    SandboxResult,
    SandboxSpec,
)
from muad_agent_core.sandbox.runner import _execute  # noqa: PLC2701 - 复用真实进程回收引擎
from muad_agent_core.skill.draft_shell import (
    DraftShellApplyResult,
    DraftShellArchive,
    DraftShellChange,
    DraftShellRequest,
    DraftShellRunner,
    DraftShellWorkspaceStore,
)

from .draft_support import create_draft, write_file
from .sandboxed_support import probe_image


def draft_zip(files: Mapping[str, str | bytes]) -> bytes:
    """用内存 zip 表示一份草稿；只用于 S-03/规则单测的固定输入。"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, content in files.items():
            archive.writestr(path, content)
    return buffer.getvalue()


class DockerBwrapRunner:
    """在 Linux 容器内运行真实 bwrap；宿主路径按原路径 bind 进容器。"""

    name = "bwrap"

    def __init__(self, image: str | None = None) -> None:
        self._image = image or probe_image()

    async def run(self, spec: SandboxSpec) -> SandboxResult:
        from muad_agent_core.sandbox import build_bwrap_argv

        name = f"fx-draft-shell-{uuid.uuid4().hex[:12]}"
        argv = [*build_bwrap_argv(spec, "bwrap")]
        docker: list[str] = [
            "docker",
            "run",
            "--rm",
            "--name",
            name,
            "--security-opt",
            "seccomp=unconfined",
            "--platform",
            "linux/amd64",
        ]
        mounted: set[str] = set()
        for source in (spec.workspace_dir, spec.package_dir, *(m.source for m in spec.mounts)):
            if source is None:
                continue
            text = str(source)
            if text in mounted:
                continue
            mounted.add(text)
            docker.extend(("-v", f"{text}:{text}"))
        docker.extend((self._image, *argv))
        outer = SandboxSpec(
            policy=SandboxPolicy(dev_unsafe=False),
            command=tuple(docker),
            workspace_dir=spec.workspace_dir,
            timeout_sec=spec.timeout_sec,
            output_limit_bytes=spec.output_limit_bytes,
            name="docker-bwrap",
        )
        try:
            return await _execute(
                outer,
                outer.command,
                cwd=None,
                env={"PATH": os.environ.get("PATH", os.defpath), "HOME": "/tmp"},
            )
        finally:
            # `docker run --rm` 在正常/超时路径都会回收；显式 rm 兜底进程组竞态。
            await asyncio.to_thread(
                subprocess.run,
                ["docker", "rm", "-f", name],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )


class MemoryDraftStore:
    """固定草稿 zip 的只读存储；S-03/单测用它替代 Console 取件边界。"""

    def __init__(self, zip_bytes: bytes, *, revision: int = 1) -> None:
        self._archive = zip_bytes
        self.revision = revision
        self.changes: list[DraftShellChange] = []
        self.deletions: list[str] = []

    async def fetch_archive(self, request: DraftShellRequest) -> DraftShellArchive:
        return DraftShellArchive(
            draft_id=request.draft_id,
            revision=self.revision,
            zip_bytes=self._archive,
            agent_id="00000000-0000-0000-0000-000000000001",
        )

    async def apply_changes(
        self,
        request: DraftShellRequest,
        *,
        expected_revision: int,
        changes: Sequence[DraftShellChange],
        deletions: Sequence[str],
    ) -> DraftShellApplyResult:
        self.changes = list(changes)
        self.deletions = list(deletions)
        self.revision = expected_revision + 1
        paths = tuple([change.path for change in changes] + list(deletions))
        return DraftShellApplyResult(revision=self.revision, changed_files=paths)


def dev_unsafe_runner_factory():
    from muad_agent_core.sandbox import DevUnsafeRunner

    runner = DevUnsafeRunner()
    return runner


def docker_runner_factory():
    runner = DockerBwrapRunner()
    return runner


class TemporaryDraftWorkspaces:
    """单元/平台登录态测试使用临时持久根；S-02 显式注入真实 PG 实现。"""

    def __init__(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(prefix="fx-draft-test-", dir="/tmp")

    @asynccontextmanager
    async def guard(self, request: DraftShellRequest) -> AsyncIterator[None]:
        yield

    @asynccontextmanager
    async def open(self, request: DraftShellRequest, archive: DraftShellArchive) -> AsyncIterator[Path]:
        directory = (
            Path(self._temporary.name)
            / request.tenant_id
            / request.owner_user_id
            / archive.agent_id
            / archive.draft_id
            / "data"
        )
        if request.reset_data and directory.exists():
            shutil.rmtree(directory)
        directory.mkdir(parents=True, exist_ok=True)
        yield directory


def build_runner(
    store: object,
    *,
    runner_factory=docker_runner_factory,
    platform_context_factory=None,
    max_file_bytes: int = 5 * 1024 * 1024,
    max_total_bytes: int = 100 * 1024 * 1024,
    max_files: int = 2000,
    output_limit_bytes: int = 64 * 1024,
    temp_root: str | None = None,
    workspaces: DraftShellWorkspaceStore | None = None,
) -> DraftShellRunner:
    return DraftShellRunner(
        store=store,  # type: ignore[arg-type]
        workspaces=workspaces or TemporaryDraftWorkspaces(),
        runner_factory=runner_factory,
        platform_context_factory=platform_context_factory,
        max_file_bytes=max_file_bytes,
        max_total_bytes=max_total_bytes,
        max_files=max_files,
        output_limit_bytes=output_limit_bytes,
        temp_root=temp_root,
    )


async def seed_draft(
    client: Any,
    *,
    tenant_id: str,
    owner_user_id: uuid.UUID,
    key: str,
    files: Mapping[str, str],
) -> tuple[str, int]:
    draft = await create_draft(client, tenant_id=tenant_id, owner_user_id=owner_user_id, key=key)
    revision = int(draft["revision"])
    for path, content in files.items():
        response = await write_file(
            client,
            tenant_id=tenant_id,
            owner_user_id=owner_user_id,
            draft_id=draft["id"],
            path=path,
            content=content,
            expected_revision=revision,
        )
        assert response.status_code == 200, response.text
        revision = int(response.json()["data"]["revision"])
    return draft["id"], revision


def require_docker() -> str:
    if shutil.which("docker") is None:
        raise RuntimeError("S-02 需要 Docker 才能验证真实 bwrap 边界")
    return probe_image()
