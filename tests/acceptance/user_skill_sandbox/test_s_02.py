"""S-02 草稿 Shell：Runtime 工具 → 草稿副本 → bwrap → 变更校验 → Console 回写。

真实边界：
- Runtime `skill_draft_shell` 工具（真实 `SkillToolSet` 注册与参数校验）；
- Console 内部 API + 真实 PostgreSQL revision CAS（ASGI 进程内 HTTP，不 mock 路由/服务）；
- 草稿 zip 解包、可写副本与变更扫描（真实文件系统）；
- macOS 上通过 Docker 内的真实 bwrap 执行（Linux 宿主直接 bwrap）。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

import pytest
from muad_agent_core.skill.draft_shell import DraftShellApplyResult, DraftShellChange, DraftShellRequest
from muad_agent_runtime.application.skill_draft_client import ConsoleDraftShellClient
from muad_agent_runtime.application.skill_tools import DraftShellContext, SkillToolSet

from .draft_shell_support import build_runner, require_docker, seed_draft
from .draft_support import INTERNAL_TOKEN, draft_metadata, read_file, write_file

pytestmark = pytest.mark.integration

SKILL_MD = """---
name: draft-shell-acceptance
description: S-02 草稿 Shell 验收
execution: SYNC
---

按步骤执行验收任务。
"""

RUN_PY = """\
import json
from pathlib import Path

Path("reports").mkdir(exist_ok=True)
Path("reports/result.txt").write_text("ok", encoding="utf-8")
print(json.dumps({"ok": True, "cwd": str(Path.cwd())}))
"""

CHECK_JS = "const value = 1;\nconsole.log(value);\n"

PYTHON_NODE_CHECK = (
    "python -m py_compile scripts/run.py && "
    "python scripts/run.py && "
    "node --check scripts/check.js"
)
LINK_PY = "import os\nos.symlink('/etc/passwd', 'escaped-link')\n"
BINARY_PY = "from pathlib import Path\nPath('payload.exe').write_text('MZ', encoding='utf-8')\n"
BIG_PY = "from pathlib import Path\nPath('big.txt').write_text('x' * 4096, encoding='utf-8')\n"
CAS_RUN_PY = (
    "from pathlib import Path\n"
    "Path('notes.txt').write_text('new', encoding='utf-8')\n"
)


@dataclass(frozen=True)
class ShellEnv:
    client: Any
    tenant_id: str
    owner_user_id: uuid.UUID
    agent_id: uuid.UUID
    run_id: str


class _UnusedCache:
    async def ensure(self, **_kwargs: Any) -> None:
        raise AssertionError("draft shell tests do not load skill artifacts")


@pytest.fixture()
async def shell_env(internal_client: Any, draft_tenant: Any) -> ShellEnv:
    require_docker()
    return ShellEnv(
        client=internal_client,
        tenant_id=draft_tenant("acc-s02"),
        owner_user_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        run_id=str(uuid.uuid4()),
    )


def _toolset(store: Any, env: ShellEnv, **runner_options: Any) -> SkillToolSet:
    runner = build_runner(store, **runner_options)
    return SkillToolSet(
        cache=_UnusedCache(),  # type: ignore[arg-type]
        skills=(),
        timeout_sec=60.0,
        draft_shell=runner,
        draft_shell_context=DraftShellContext(
            tenant_id=env.tenant_id,
            owner_user_id=str(env.owner_user_id),
            agent_id=str(env.agent_id),
            run_id=env.run_id,
        ),
    )


def _console_store(env: ShellEnv) -> ConsoleDraftShellClient:
    return ConsoleDraftShellClient(
        base_url="http://test",
        service_token=INTERNAL_TOKEN,
        client=env.client,
    )


async def _call_shell(
    toolset: SkillToolSet,
    *,
    draft_id: str,
    command: str,
    cwd: str | None = None,
    timeout_sec: float | None = None,
) -> dict[str, Any]:
    arguments: dict[str, Any] = {"draft_id": draft_id, "command": command}
    if cwd is not None:
        arguments["cwd"] = cwd
    if timeout_sec is not None:
        arguments["timeout_sec"] = timeout_sec
    return json.loads(await toolset.skill_draft_shell(arguments, call_id=f"call-{uuid.uuid4().hex[:8]}"))


async def _seed(env: ShellEnv, files: dict[str, str], *, key: str = "s02-draft") -> tuple[str, int]:
    return await seed_draft(
        env.client,
        tenant_id=env.tenant_id,
        owner_user_id=env.owner_user_id,
        key=key,
        files=files,
    )


async def _revision(env: ShellEnv, draft_id: str) -> int:
    response = await draft_metadata(
        env.client,
        tenant_id=env.tenant_id,
        owner_user_id=env.owner_user_id,
        draft_id=draft_id,
    )
    assert response.status_code == 200, response.text
    return int(response.json()["data"]["revision"])


async def test_s02_python_node_execute_and_cas_writeback(shell_env: ShellEnv) -> None:
    """[S-02-1/2] python 入口 + node --check 可执行；变更按 revision CAS 回写。"""
    draft_id, revision = await _seed(
        shell_env,
        {"SKILL.md": SKILL_MD, "scripts/run.py": RUN_PY, "scripts/check.js": CHECK_JS},
    )
    toolset = _toolset(_console_store(shell_env), shell_env)
    payload = await _call_shell(
        toolset,
        draft_id=draft_id,
        command=PYTHON_NODE_CHECK,
    )

    assert "error" not in payload, payload
    assert payload["exit_code"] == 0, payload
    assert json.loads(payload["stdout"].strip().splitlines()[-1])["ok"] is True
    assert "reports/result.txt" in payload["changed_files"]
    assert payload["revision"] == revision + 1

    content = await read_file(
        shell_env.client,
        tenant_id=shell_env.tenant_id,
        owner_user_id=shell_env.owner_user_id,
        draft_id=draft_id,
        path="reports/result.txt",
    )
    assert content.status_code == 200, content.text
    assert content.json()["data"]["content"] == "ok"

    listing = await read_file(
        shell_env.client,
        tenant_id=shell_env.tenant_id,
        owner_user_id=shell_env.owner_user_id,
        draft_id=draft_id,
        path="scripts",
    )
    names = {entry["name"] for entry in listing.json()["data"]["entries"]}
    # py_compile 的 __pycache__ 是运行副产物，不写回草稿。
    assert "__pycache__" not in names


async def test_s02_rejects_symlink_disallowed_extension_and_oversize(shell_env: ShellEnv) -> None:
    """[S-02-3] symlink/越界扩展名/超大文件被拒绝，revision 不变且不写回。"""
    draft_id, revision = await _seed(
        shell_env,
        {
            "SKILL.md": SKILL_MD,
            "scripts/link.py": LINK_PY,
            "scripts/binary.py": BINARY_PY,
            "scripts/big.py": BIG_PY,
        },
    )
    toolset = _toolset(_console_store(shell_env), shell_env, max_file_bytes=1024)

    symlink = await _call_shell(toolset, draft_id=draft_id, command="python scripts/link.py")
    assert symlink["error"]["code"] == "DRAFT_PATH_INVALID", symlink

    extension = await _call_shell(toolset, draft_id=draft_id, command="python scripts/binary.py")
    assert extension["error"]["code"] == "DRAFT_PATH_INVALID", extension

    oversize = await _call_shell(toolset, draft_id=draft_id, command="python scripts/big.py")
    assert oversize["error"]["code"] == "DRAFT_QUOTA_EXCEEDED", oversize

    assert await _revision(shell_env, draft_id) == revision
    for path in ("escaped-link", "payload.exe", "big.txt"):
        response = await read_file(
            shell_env.client,
            tenant_id=shell_env.tenant_id,
            owner_user_id=shell_env.owner_user_id,
            draft_id=draft_id,
            path=path,
        )
        assert response.status_code == 404, (path, response.text)


async def test_s02_timeout_and_output_limit_error_codes(shell_env: ShellEnv) -> None:
    """[S-02-4] 命令超时/输出超限返回 SANDBOX_TIMEOUT / SANDBOX_OUTPUT_LIMIT。"""
    draft_id, revision = await _seed(
        shell_env,
        {
            "SKILL.md": SKILL_MD,
            "scripts/spam.py": "print('x' * 10000)\n",
        },
    )
    toolset = _toolset(_console_store(shell_env), shell_env, output_limit_bytes=512)

    timed_out = await _call_shell(
        toolset,
        draft_id=draft_id,
        command="python -c 'import time; time.sleep(10)'",
        timeout_sec=2.0,
    )
    assert timed_out["error"]["code"] == "SANDBOX_TIMEOUT", timed_out

    overflow = await _call_shell(toolset, draft_id=draft_id, command="python scripts/spam.py")
    assert overflow["error"]["code"] == "SANDBOX_OUTPUT_LIMIT", overflow
    assert await _revision(shell_env, draft_id) == revision


class _InterleavingStore:
    """抓取草稿后、回写前插入一次真实 Console 写入，制造 revision CAS 冲突。"""

    def __init__(self, inner: ConsoleDraftShellClient, env: ShellEnv, draft_id: str) -> None:
        self._inner = inner
        self._env = env
        self._draft_id = draft_id
        self._archive_revision = 0

    async def fetch_archive(self, request: DraftShellRequest):
        archive = await self._inner.fetch_archive(request)
        self._archive_revision = archive.revision
        return archive

    async def apply_changes(
        self,
        request: DraftShellRequest,
        *,
        expected_revision: int,
        changes: list[DraftShellChange] | tuple[DraftShellChange, ...],
        deletions: list[str] | tuple[str, ...],
    ) -> DraftShellApplyResult:
        response = await write_file(
            self._env.client,
            tenant_id=self._env.tenant_id,
            owner_user_id=self._env.owner_user_id,
            draft_id=self._draft_id,
            path="external.txt",
            content="external writer",
            expected_revision=self._archive_revision,
        )
        assert response.status_code == 200, response.text
        return await self._inner.apply_changes(
            request,
            expected_revision=expected_revision,
            changes=changes,
            deletions=deletions,
        )


async def test_s02_revision_conflict_rejects_stale_writeback(shell_env: ShellEnv) -> None:
    """[S-02-2] 回写使用抓取时 revision；并发写入后返回 DRAFT_REVISION_CONFLICT。"""
    draft_id, revision = await _seed(
        shell_env,
        {
            "SKILL.md": SKILL_MD,
            "scripts/run.py": CAS_RUN_PY,
        },
        key="s02-cas",
    )
    store = _InterleavingStore(_console_store(shell_env), shell_env, draft_id)
    toolset = _toolset(store, shell_env)
    payload = await _call_shell(toolset, draft_id=draft_id, command="python scripts/run.py")

    assert payload["error"]["code"] == "DRAFT_REVISION_CONFLICT", payload
    assert await _revision(shell_env, draft_id) == revision + 1

    external = await read_file(
        shell_env.client,
        tenant_id=shell_env.tenant_id,
        owner_user_id=shell_env.owner_user_id,
        draft_id=draft_id,
        path="external.txt",
    )
    assert external.status_code == 200
    stale = await read_file(
        shell_env.client,
        tenant_id=shell_env.tenant_id,
        owner_user_id=shell_env.owner_user_id,
        draft_id=draft_id,
        path="notes.txt",
    )
    assert stale.status_code == 404


async def test_s02_other_owner_cannot_shell_someone_elses_draft(shell_env: ShellEnv) -> None:
    """[S-02] 只作用于当前用户当前草稿；跨用户 draft_id 由 Console 拒绝。"""
    draft_id, revision = await _seed(
        shell_env,
        {"SKILL.md": SKILL_MD, "scripts/run.py": "print('owned')\n"},
        key="s02-owner",
    )
    other = ShellEnv(
        client=shell_env.client,
        tenant_id=shell_env.tenant_id,
        owner_user_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        run_id=str(uuid.uuid4()),
    )
    toolset = _toolset(_console_store(other), other)
    payload = await _call_shell(toolset, draft_id=draft_id, command="python scripts/run.py")

    assert payload["error"]["code"] == "DRAFT_NOT_FOUND", payload
    assert await _revision(shell_env, draft_id) == revision
