#!/usr/bin/env python3
"""[S-11][E-05] 真实 bwrap 内的 workspace_shell 探针。

由宿主测试在带 bwrap 的 Linux 测试镜像内执行（macOS 没有 bwrap 时），探针内部仍走
真实 ``WorkspaceShellRunner`` → ``BwrapRunner`` → ``bwrap``；不 mock 进程、pip/npm、
PATH 或文件系统。数据库元数据不属于本探针边界，使用内存租约记录（B-01 用真实 PG）。
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import sys
import tempfile
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC = REPO_ROOT / "packages" / "agent-core" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from muad_agent_core.sandbox import (  # noqa: E402
    SANDBOX_OUTPUT_LIMIT_BYTES,
    SandboxOutputLimit,
    SandboxPolicy,
    SandboxProfile,
    SandboxTimeout,
    WorkspaceLayout,
    WorkspaceLeaseRecord,
    WorkspaceShellRequest,
    WorkspaceShellRunner,
    WorkspaceStore,
    build_sandbox_runner,
)


class _MemoryMetadata:
    def __init__(self) -> None:
        self.records: list[WorkspaceLeaseRecord] = []
        self.released: list[uuid.UUID] = []

    async def acquire(
        self,
        *,
        tenant_id: str,
        user_id: str,
        agent_id: str,
        execution_id: str,
        run_id: str,
        task_id: str,
        lease_sec: float,
    ) -> WorkspaceLeaseRecord:
        record = WorkspaceLeaseRecord(
            id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
            execution_id=execution_id,
            lease_until=datetime.now(UTC) + timedelta(seconds=lease_sec),
        )
        self.records.append(record)
        return record

    async def release(self, lease_id: uuid.UUID) -> None:
        self.released.append(lease_id)


def _layout(root: Path) -> WorkspaceLayout:
    return WorkspaceLayout(
        root=root,
        quota_bytes=500 * 1024 * 1024,
        user_quota_bytes=500 * 1024 * 1024,
        file_limit=5000,
        data_ttl_seconds=30 * 24 * 60 * 60,
    )


def _runner(
    *,
    root: Path,
    metadata: _MemoryMetadata,
    output_limit_bytes: int = SANDBOX_OUTPUT_LIMIT_BYTES,
) -> tuple[WorkspaceShellRunner, WorkspaceStore]:
    layout = _layout(root)
    store = WorkspaceStore(layout=layout, metadata=metadata)
    runner = WorkspaceShellRunner(
        store=store,
        runner_factory=lambda: build_sandbox_runner(mode="bwrap", env="dev", platform="linux"),
        policy=SandboxPolicy(profile=SandboxProfile.UNTRUSTED, network=True),
        output_limit_bytes=output_limit_bytes,
    )
    return runner, store


def _ids() -> dict[str, str]:
    return {
        "tenant_id": "probe-tenant",
        "user_id": str(uuid.uuid4()),
        "agent_id": str(uuid.uuid4()),
        "run_id": str(uuid.uuid4()),
    }


def _request(ids: dict[str, str], execution_id: str, command: str, **kwargs: Any) -> WorkspaceShellRequest:
    return WorkspaceShellRequest(
        tenant_id=ids["tenant_id"],
        user_id=ids["user_id"],
        agent_id=ids["agent_id"],
        run_id=ids["run_id"],
        execution_id=execution_id,
        command=command,
        **kwargs,
    )


def _python(script: str) -> str:
    return f"python -c {shlex.quote(script)}"


async def _shell_mode(root: Path) -> dict[str, Any]:
    runner, _ = _runner(root=root, metadata=_MemoryMetadata())
    ids = _ids()
    script = (
        "import json, os, tabulate\n"
        "payload = {'tabulate': tabulate.__version__, "
        "'data': os.environ.get('SKILL_DATA_DIR'), "
        "'output': os.environ.get('SKILL_OUTPUT_DIR'), 'cwd': os.getcwd(), "
        "'session_socket': os.environ.get('MUAD_SESSION_SOCKET')}\n"
        "open(os.path.join(os.environ['SKILL_DATA_DIR'], 'shared.txt'), 'w').write('persisted')\n"
        "open(os.path.join(os.environ['SKILL_OUTPUT_DIR'], 'result.json'), 'w').write(json.dumps(payload))\n"
        "print(json.dumps(payload))\n"
    )
    result = await runner.run(_request(ids, "shell", _python(script)))
    layout = _layout(root)
    data_dir = layout.data_dir(
        tenant_id=ids["tenant_id"], user_id=ids["user_id"], agent_id=ids["agent_id"]
    )
    output_dir = layout.output_dir(
        tenant_id=ids["tenant_id"],
        user_id=ids["user_id"],
        agent_id=ids["agent_id"],
        execution_id="shell",
    )
    return {
        "exit_code": result.exit_code,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "env": json.loads(result.stdout.strip().splitlines()[-1]),
        "data_file": (data_dir / "shared.txt").read_text(encoding="utf-8"),
        "output_file": json.loads((output_dir / "result.json").read_text(encoding="utf-8")),
        "changed_files": list(result.changed_files),
    }


async def _install_mode(root: Path) -> dict[str, Any]:
    metadata = _MemoryMetadata()
    runner, _ = _runner(root=root, metadata=metadata)
    ids = _ids()
    layout = _layout(root)
    output_dir = layout.output_dir(
        tenant_id=ids["tenant_id"],
        user_id=ids["user_id"],
        agent_id=ids["agent_id"],
        execution_id="install",
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        REPO_ROOT / "tests/acceptance/user_skill_sandbox/fixtures/e17_demo_dep",
        output_dir / "pkg-src",
    )
    shutil.copytree(
        REPO_ROOT / "tests/acceptance/user_skill_sandbox/fixtures/s11_npm_dep",
        output_dir / "npm-src",
    )
    command = (
        "set -eu\n"
        "python -m pip install --no-index --no-build-isolation --no-deps "
        '--target "$SKILL_OUTPUT_DIR/python" "$SKILL_OUTPUT_DIR/pkg-src"\n'
        'npm pack --pack-destination "$SKILL_OUTPUT_DIR" "$SKILL_OUTPUT_DIR/npm-src" >/dev/null\n'
        'npm install --prefix "$SKILL_OUTPUT_DIR/node" --no-audit --no-fund --ignore-scripts '
        '"$SKILL_OUTPUT_DIR/s11-demo-dep-1.0.0.tgz"\n'
        'PYTHONPATH="$SKILL_OUTPUT_DIR/python" python -c "import e17_demo_dep; print(e17_demo_dep.VALUE)"\n'
    )
    result = await runner.run(_request(ids, "install", command))
    python_installed = output_dir / "python" / "e17_demo_dep" / "__init__.py"
    node_installed = output_dir / "node" / "node_modules" / "s11-demo-dep" / "package.json"
    fresh = await runner.run(
        _request(_ids(), "fresh-pod", "python -c \"import e17_demo_dep; print('unexpected')\"")
    )
    import site

    system_hits = [
        path
        for path in [*site.getsitepackages(), "/usr/local/lib/node/node_modules"]
        if os.path.exists(os.path.join(path, "e17_demo_dep"))
        or os.path.exists(os.path.join(path, "s11-demo-dep"))
    ]
    return {
        "exit_code": result.exit_code,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "python_installed": python_installed.is_file(),
        "node_installed": node_installed.is_file(),
        "system_hits": system_hits,
        "fresh_import_exit": fresh.exit_code,
        "fresh_import_stderr": fresh.stderr,
        "changed_files": list(result.changed_files),
    }


async def _limits_mode(root: Path) -> dict[str, Any]:
    runner, _ = _runner(root=root, metadata=_MemoryMetadata(), output_limit_bytes=4096)
    ids = _ids()

    async def capture(execution_id: str, command: str, timeout_sec: float) -> dict[str, Any]:
        try:
            result = await runner.run(
                _request(ids, execution_id, command, timeout_sec=timeout_sec)
            )
        except SandboxTimeout as exc:
            return {"code": exc.code, "message": exc.message}
        except SandboxOutputLimit as exc:
            return {"code": exc.code, "message": exc.message}
        return {"code": "OK", "stdout": result.stdout, "exit_code": result.exit_code}

    timeout = await capture("timeout", "sleep 10", 0.5)
    overflow = await capture("overflow", "python -c \"print('x' * 100000)\"", 30)
    normal = await capture("normal", "printf 'ok-after-limits'", 30)
    return {"timeout": timeout, "overflow": overflow, "normal": normal}


async def _no_session_mode(root: Path) -> dict[str, Any]:
    runner, _ = _runner(root=root, metadata=_MemoryMetadata())
    ids = _ids()
    probe = (
        "import json, os, shutil\n"
        "print(json.dumps({'path': os.environ.get('PATH'), "
        "'socket': os.environ.get('MUAD_SESSION_SOCKET'), "
        "'which': shutil.which('session-manager')}))\n"
    )
    env_result = await runner.run(_request(ids, "session-env", _python(probe)))
    call_result = await runner.run(_request(ids, "session-call", "session-manager get-state"))
    return {
        "env": json.loads(env_result.stdout.strip().splitlines()[-1]),
        "call_exit_code": call_result.exit_code,
        "call_stderr": call_result.stderr,
    }


async def _async_main(mode: str) -> dict[str, Any]:
    root = Path(tempfile.mkdtemp(prefix="muad-workspace-shell-", dir="/tmp"))
    try:
        if mode == "shell":
            payload = await _shell_mode(root)
        elif mode == "install":
            payload = await _install_mode(root)
        elif mode == "limits":
            payload = await _limits_mode(root)
        elif mode == "no_session":
            payload = await _no_session_mode(root)
        else:
            raise ValueError(f"unknown probe mode: {mode}")
        return {"ok": True, "mode": mode, "bwrap": shutil.which("bwrap"), **payload}
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main() -> int:
    mode = os.environ.get("MUAD_WORKSPACE_PROBE_MODE", "shell")
    try:
        payload = __import__("asyncio").run(_async_main(mode))
    except Exception as exc:  # noqa: BLE001 - 探针把失败原因带回宿主断言
        payload = {"ok": False, "mode": mode, "error": f"{type(exc).__name__}: {exc}"}
        print(json.dumps(payload, ensure_ascii=False))
        return 1
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
