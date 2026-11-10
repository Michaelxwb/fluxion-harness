"""S-02 草稿 Shell：Runtime 工具 → 草稿副本 → bwrap → 变更校验 → Console 回写。

真实边界：
- Runtime `skill_draft_shell` 工具（真实 `SkillToolSet` 注册与参数校验）；
- Console 内部 API + 真实 PostgreSQL revision CAS（ASGI 进程内 HTTP，不 mock 路由/服务）；
- 草稿 zip 解包、可写副本与变更扫描（真实文件系统）；
- macOS 上通过 Docker 内的真实 bwrap 执行（Linux 宿主直接 bwrap）。
"""

from __future__ import annotations

import json
import shlex
import sys
import uuid
import zipfile
from dataclasses import dataclass, replace
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from muad_agent_core.skill.draft_shell import DraftShellApplyResult, DraftShellChange, DraftShellRequest
from muad_agent_runtime.application.draft_shell import build_draft_shell_runner
from muad_agent_runtime.application.skill_draft_client import ConsoleDraftShellClient
from muad_agent_runtime.application.skill_tools import DraftShellContext, SkillToolSet
from muad_agent_runtime.application.workspace_shell import build_workspace_store
from muad_agent_runtime.infrastructure.draft_test_workspace import SqlDraftTestWorkspaceStore
from muad_common import SharedSettings
from muad_common.draft_testing import draft_test_data_dir

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
    "python -m py_compile scripts/run.py && python scripts/run.py && node --check scripts/check.js"
)
LINK_PY = "import os\nos.symlink('/etc/passwd', 'escaped-link')\n"
BINARY_PY = "from pathlib import Path\nPath('payload.exe').write_text('MZ', encoding='utf-8')\n"
BIG_PY = "from pathlib import Path\nPath('big.txt').write_text('x' * 4096, encoding='utf-8')\n"
CAS_RUN_PY = (
    "import os\nfrom pathlib import Path\n"
    "Path('notes.txt').write_text('new', encoding='utf-8')\n"
    "(Path(os.environ['SKILL_DATA_DIR'])/'state.txt').write_text('cas-test-state')\n"
)
STATEFUL_RUN = (
    "import os, sys\nfrom pathlib import Path\n"
    "p = Path(os.environ['SKILL_DATA_DIR']) / 'state.json'\n"
    "if sys.argv[1] == 'start': p.write_text('round-one')\n"
    "print(p.read_text() if p.exists() else 'NO_STATE')\n"
)


@dataclass(frozen=True)
class ShellEnv:
    client: Any
    tenant_id: str
    owner_user_id: uuid.UUID
    agent_id: uuid.UUID
    run_id: str
    workspaces: SqlDraftTestWorkspaceStore


class _UnusedCache:
    async def ensure(self, **_kwargs: Any) -> None:
        raise AssertionError("draft shell tests do not load skill artifacts")


@pytest.fixture()
async def shell_env(internal_client: Any, draft_tenant: Any, session_factory: Any) -> ShellEnv:
    require_docker()
    return ShellEnv(
        client=internal_client,
        tenant_id=draft_tenant("acc-s02"),
        owner_user_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        run_id=str(uuid.uuid4()),
        workspaces=SqlDraftTestWorkspaceStore(
            build_workspace_store(SharedSettings(), session_factory_provider=lambda: session_factory),
            lambda: session_factory,
        ),
    )


def _toolset(store: Any, env: ShellEnv, **runner_options: Any) -> SkillToolSet:
    runner = build_runner(store, workspaces=env.workspaces, **runner_options)
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
    reset_data: bool = False,
) -> dict[str, Any]:
    arguments: dict[str, Any] = {"draft_id": draft_id, "command": command}
    if cwd is not None:
        arguments["cwd"] = cwd
    if timeout_sec is not None:
        arguments["timeout_sec"] = timeout_sec
    if reset_data:
        arguments["reset_data"] = True
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
    retained = await _call_shell(
        toolset, draft_id=draft_id,
        command=(
            "python -c \"import os; from pathlib import Path; "
            "print((Path(os.environ['SKILL_DATA_DIR'])/'state.txt').read_text())\""
        ),
    )
    assert retained["stdout"].strip() == "cas-test-state", retained


async def test_s02_other_owner_cannot_shell_someone_elses_draft(shell_env: ShellEnv) -> None:
    """[S-02] 只作用于当前用户当前草稿；跨用户 draft_id 由 Console 拒绝。"""
    draft_id, revision = await _seed(
        shell_env,
        {"SKILL.md": SKILL_MD, "scripts/run.py": "print('owned')\n"},
        key="s02-owner",
    )
    other = replace(
        shell_env,
        owner_user_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        run_id=str(uuid.uuid4()),
    )
    toolset = _toolset(_console_store(other), other)
    payload = await _call_shell(toolset, draft_id=draft_id, command="python scripts/run.py")

    assert payload["error"]["code"] == "DRAFT_NOT_FOUND", payload
    assert await _revision(shell_env, draft_id) == revision


async def test_s02_test_state_survives_calls_and_runs(shell_env: ShellEnv) -> None:
    """真实草稿跨工具调用及新 Run 保留测试状态，运行数据不写回包。"""
    draft_id, revision = await _seed(
        shell_env, {"SKILL.md": SKILL_MD, "scripts/run.py": STATEFUL_RUN}, key="stateful"
    )
    toolset = _toolset(_console_store(shell_env), shell_env)
    first = await _call_shell(toolset, draft_id=draft_id, command="python scripts/run.py start")
    second = await _call_shell(toolset, draft_id=draft_id, command="python scripts/run.py status")
    other_run = replace(shell_env, run_id=str(uuid.uuid4()))
    third = await _call_shell(
        _toolset(_console_store(other_run), other_run),
        draft_id=draft_id,
        command="python scripts/run.py status",
    )
    for payload in (first, second, third):
        assert payload["exit_code"] == 0, payload
        assert payload["stdout"].strip() == "round-one", payload
        assert payload["changed_files"] == []
        assert payload["revision"] == revision
    assert await _revision(shell_env, draft_id) == revision
    content = await read_file(
        shell_env.client,
        tenant_id=shell_env.tenant_id,
        owner_user_id=shell_env.owner_user_id,
        draft_id=draft_id,
        path="state.json",
    )
    assert content.status_code == 404
    archive = await _console_store(shell_env).fetch_archive(
        DraftShellRequest(
            tenant_id=shell_env.tenant_id,
            owner_user_id=str(shell_env.owner_user_id),
            draft_id=draft_id,
            command="unused",
        )
    )
    with zipfile.ZipFile(BytesIO(archive.zip_bytes)) as package:
        assert set(package.namelist()) == {"SKILL.md", "scripts/run.py"}


async def _stateful_draft(env: ShellEnv, key: str) -> str:
    draft_id, _ = await _seed(env, {"SKILL.md": SKILL_MD, "scripts/run.py": STATEFUL_RUN}, key=key)
    return draft_id


async def _agent_workspace(env: ShellEnv, draft_id: str) -> Path:
    response = await draft_metadata(
        env.client,
        tenant_id=env.tenant_id,
        owner_user_id=env.owner_user_id,
        draft_id=draft_id,
    )
    assert response.status_code == 200
    agent_id = response.json()["data"]["agent_id"]
    return Path(SharedSettings().workspace_root) / env.tenant_id / str(env.owner_user_id) / agent_id


async def test_s02_editing_source_keeps_test_data(shell_env: ShellEnv) -> None:
    draft_id = await _stateful_draft(shell_env, "edited-state")
    toolset = _toolset(_console_store(shell_env), shell_env)
    first = await _call_shell(toolset, draft_id=draft_id, command="python scripts/run.py start")
    assert first["stdout"].strip() == "round-one", first
    updated = await write_file(
        shell_env.client, tenant_id=shell_env.tenant_id, owner_user_id=shell_env.owner_user_id,
        draft_id=draft_id, path="scripts/run.py", content=STATEFUL_RUN + "\n# edited\n",
        expected_revision=first["revision"],
    )
    assert updated.status_code == 200, updated.text
    continued = await _call_shell(toolset, draft_id=draft_id, command="python scripts/run.py status")
    assert continued["stdout"].strip() == "round-one", continued
    assert continued["revision"] == first["revision"] + 1


async def _delete_draft(env: ShellEnv, draft_id: str):
    return await env.client.delete(
        f"/internal/skills/drafts/{draft_id}",
        params={"tenant_id": env.tenant_id, "owner_user_id": str(env.owner_user_id)},
    )


async def test_s02_test_data_isolated_and_reset_only_affects_current_draft(shell_env: ShellEnv) -> None:
    first = await _stateful_draft(shell_env, "first")
    second = await _stateful_draft(shell_env, "second")
    workspace = await _agent_workspace(shell_env, first)
    formal = workspace / "data" / "state.json"
    formal.parent.mkdir()
    formal.write_text("formal-state")
    toolset = _toolset(_console_store(shell_env), shell_env)
    for draft_id in (first, second):
        before = await _call_shell(toolset, draft_id=draft_id, command="python scripts/run.py status")
        assert before["stdout"].strip() == "NO_STATE", before
        started = await _call_shell(toolset, draft_id=draft_id, command="python scripts/run.py start")
        assert started["stdout"].strip() == "round-one", started
    reset = await _call_shell(
        toolset,
        draft_id=first,
        command="python scripts/run.py status",
        reset_data=True,
    )
    assert reset["stdout"].strip() == "NO_STATE", reset
    kept = await _call_shell(toolset, draft_id=second, command="python scripts/run.py status")
    assert kept["stdout"].strip() == "round-one", kept
    assert formal.read_text() == "formal-state"
    assert draft_test_data_dir(workspace, first) != draft_test_data_dir(workspace, second)


async def test_s02_test_lock_blocks_concurrent_test_and_delete(shell_env: ShellEnv) -> None:
    draft_id = await _stateful_draft(shell_env, "locked")
    other = await _stateful_draft(shell_env, "unlocked")
    toolset = _toolset(_console_store(shell_env), shell_env)
    started = await _call_shell(toolset, draft_id=draft_id, command="python scripts/run.py start")
    assert started["stdout"].strip() == "round-one", started
    request = DraftShellRequest(
        tenant_id=shell_env.tenant_id,
        owner_user_id=str(shell_env.owner_user_id),
        draft_id=draft_id,
        command="unused",
    )
    async with shell_env.workspaces.guard(request):
        blocked = await _call_shell(
            toolset,
            draft_id=draft_id,
            command="python scripts/run.py status",
            reset_data=True,
        )
        assert blocked["error"]["code"] == "DRAFT_TEST_BUSY", blocked
        assert blocked["error"]["retryable"] is True
        deleted = await _delete_draft(shell_env, draft_id)
        assert deleted.status_code == 409, deleted.text
        assert deleted.json()["code"] == "DRAFT_TEST_BUSY"
        independent = await _call_shell(
            toolset,
            draft_id=other,
            command="python scripts/run.py status",
        )
        assert independent["stdout"].strip() == "NO_STATE", independent
    kept = await _call_shell(toolset, draft_id=draft_id, command="python scripts/run.py status")
    assert kept["stdout"].strip() == "round-one", kept
    data_dir = draft_test_data_dir(await _agent_workspace(shell_env, draft_id), draft_id)
    assert (await _delete_draft(shell_env, draft_id)).status_code == 200
    assert not data_dir.parent.exists()
    missing = await _call_shell(toolset, draft_id=draft_id, command="python scripts/run.py status")
    assert missing["error"]["code"] == "DRAFT_NOT_FOUND", missing
    assert not data_dir.exists()


async def test_s02_timeout_preserves_test_data_and_releases_lock(shell_env: ShellEnv) -> None:
    draft_id = await _stateful_draft(shell_env, "timeout-state")
    toolset = _toolset(_console_store(shell_env), shell_env)
    failed = await _call_shell(
        toolset,
        draft_id=draft_id,
        command="python scripts/run.py start && python -c 'import time; time.sleep(10)'",
        timeout_sec=2.0,
    )
    assert failed["error"]["code"] == "SANDBOX_TIMEOUT", failed
    continued = await _call_shell(toolset, draft_id=draft_id, command="python scripts/run.py status")
    assert continued["stdout"].strip() == "round-one", continued


async def test_s02_quota_counts_test_data_and_reset_recovers(
    shell_env: ShellEnv,
    session_factory: Any,
) -> None:
    settings = SharedSettings(workspace_quota_mib=1)
    env = replace(
        shell_env,
        workspaces=SqlDraftTestWorkspaceStore(
            build_workspace_store(settings, session_factory_provider=lambda: session_factory),
            lambda: session_factory,
        ),
    )
    draft_id = await _stateful_draft(env, "quota-state")
    toolset = _toolset(_console_store(env), env)
    written = await _call_shell(
        toolset,
        draft_id=draft_id,
        command=(
            'python -c "import os; from pathlib import Path; '
            "(Path(os.environ['SKILL_DATA_DIR'])/'large.bin').write_bytes(b'x' * (2 * 1024 * 1024))\""
        ),
    )
    assert written["exit_code"] == 0, written
    rejected = await _call_shell(toolset, draft_id=draft_id, command="python scripts/run.py status")
    assert rejected["error"]["code"] == "WORKSPACE_QUOTA_EXCEEDED", rejected
    reset = await _call_shell(
        toolset,
        draft_id=draft_id,
        command="python scripts/run.py status",
        reset_data=True,
    )
    assert reset["stdout"].strip() == "NO_STATE", reset
    data = draft_test_data_dir(await _agent_workspace(env, draft_id), draft_id)
    assert not (data / "large.bin").exists()


async def test_s02_runtime_factory_wires_persistent_draft_workspace(
    shell_env: ShellEnv,
    session_factory: Any,
) -> None:
    draft_id = await _stateful_draft(shell_env, "factory-state")
    settings = SharedSettings(env="dev", sandbox_mode="dev-unsafe")
    for action in ("start", "status"):
        runner = build_draft_shell_runner(
            settings,
            client=_console_store(shell_env),
            session_factory_provider=lambda: session_factory,
        )
        result = await runner.run(
            DraftShellRequest(
                tenant_id=shell_env.tenant_id,
                owner_user_id=str(shell_env.owner_user_id),
                draft_id=draft_id,
                command=f"{shlex.quote(sys.executable)} scripts/run.py {action}",
                run_id=str(uuid.uuid4()),
            )
        )
        assert result.exit_code == 0, result
        assert result.stdout.strip() == "round-one"
