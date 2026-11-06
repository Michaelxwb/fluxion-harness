#!/usr/bin/env python3
"""TASK-003 真实 bwrap 验收探针。

既可在原生 Linux（已有 bwrap）中运行，也可由 ``test_s_10.py`` 等测试挂进
``muad-skill-executor-probe`` 镜像。探针内使用真实 ``SandboxedSkillExecutor``、
``BwrapRunner`` 与 ``bwrap``，不 mock 进程/挂载/文件系统；最后一行输出 JSON。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import resource
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
for candidate in sorted((REPO_ROOT / "packages").glob("*/src")) + sorted((REPO_ROOT / "apps").glob("*/src")):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from muad_agent_core.sandbox import build_sandbox_runner  # noqa: E402
from muad_agent_core.skill import (  # noqa: E402
    OutputLimits,
    SandboxedSkillExecutor,
    SkillExecutionEvent,
    SkillExecutionRequest,
    SkillExecutionResult,
)

SCRIPT_MAIN = """\
import json, os, sys
payload = json.loads(sys.stdin.read() or '{}')
out = os.environ['SKILL_OUTPUT_DIR']
with open(os.path.join(out, 'result.json'), 'w', encoding='utf-8') as handle:
    json.dump({'payload': payload, 'output': out, 'data': os.environ['SKILL_DATA_DIR']}, handle)
print(json.dumps({
    'payload': payload,
    'output': out,
    'data': os.environ['SKILL_DATA_DIR'],
    'flags': [sys.flags.ignore_environment, sys.flags.no_site, sys.flags.dont_write_bytecode],
    'helper': __import__('helper').VALUE,
}))
"""


def _package(base: Path, *, name: str = "skill", main: str | None = None) -> Path:
    root = base / name
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    (root / "SKILL.md").write_text("# probe\n", encoding="utf-8")
    (scripts / "helper.py").write_text("VALUE = 41\n", encoding="utf-8")
    (scripts / "main.py").write_text(main or SCRIPT_MAIN, encoding="utf-8")
    return root


def _tree_checksum(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return "sha256:" + digest.hexdigest()


def _runner() -> Any:
    return build_sandbox_runner(mode="bwrap", env="dev", platform="linux")


def _request(root: Path, workspace: Path, execution_id: str, **kwargs: Any) -> SkillExecutionRequest:
    values: dict[str, Any] = {
        "ready_dir": root,
        "input": {"question": "hello"},
        "timeout_sec": 20.0,
        "source": "USER",
        "workspace_dir": workspace,
        "execution_id": execution_id,
    }
    values.update(kwargs)
    return SkillExecutionRequest(**values)


def _result_payload(result: SkillExecutionResult) -> dict[str, Any]:
    return {
        "status": result.status.value,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "result": dict(result.result) if result.result else None,
        "exit_code": result.exit_code,
        "duration_ms": result.duration_ms,
        "outputs": [
            {
                "relative_path": item.relative_path,
                "size": item.size,
                "checksum": item.checksum,
            }
            for item in result.outputs
        ],
        "output_notice": result.output_notice,
        "workspace_dir": str(result.workspace_dir) if result.workspace_dir else None,
    }


async def scenario_s10() -> dict[str, Any]:
    base = Path(tempfile.mkdtemp(prefix="fx-s10-"))
    root = _package(base)
    workspace = base / "workspace"
    events: list[SkillExecutionEvent] = []
    executor = SandboxedSkillExecutor(runner=_runner(), observer=events.append)
    result = await executor.execute(_request(root, workspace, "s10-exec"))

    return {
        "ok": result.status.value == "SUCCEEDED" and result.exit_code == 0,
        "result": _result_payload(result),
        "package_checksum_before_after": [
            _tree_checksum(root),
            _tree_checksum(root),
        ],
        "events": [
            {
                "source": event.source,
                "status": event.status.value,
                "output_count": event.output_count,
            }
            for event in events
        ],
        "output_file_exists": (workspace / "outputs" / "s10-exec" / "result.json").is_file(),
        "helper_imported": bool(result.result and result.result.get("helper") == 41),
    }


READY_SENTINEL = "artifact_id=probe\nchecksum=sha256:deadbeef\n"


async def scenario_s15() -> dict[str, Any]:
    base = Path(tempfile.mkdtemp(prefix="fx-s15-"))
    main = """\
import json, os
out = os.environ['SKILL_OUTPUT_DIR']
try:
    with open('/skill/evil.txt', 'w', encoding='utf-8') as handle:
        handle.write('escaped')
    write_error = None
except OSError as exc:
    write_error = type(exc).__name__
with open(os.path.join(out, 'result.txt'), 'w', encoding='utf-8') as handle:
    handle.write(os.environ['SKILL_OUTPUT_DIR'])
print(json.dumps({'write_error': write_error, 'skill_readable': os.path.isfile('/skill/SKILL.md')}))
"""
    root = _package(base, main=main)
    (root / "READY").write_text(READY_SENTINEL, encoding="utf-8")
    before = _tree_checksum(root)
    executor = SandboxedSkillExecutor(runner=_runner())
    first_workspace = base / "ws-first"
    second_workspace = base / "ws-second"
    first, second = await asyncio.gather(
        executor.execute(_request(root, first_workspace, "exec-a")),
        executor.execute(_request(root, second_workspace, "exec-b")),
    )
    after = _tree_checksum(root)
    return {
        "ok": first.status.value == "SUCCEEDED" and second.status.value == "SUCCEEDED",
        "first": {
            **_result_payload(first),
            "result_file": (first_workspace / "outputs" / "exec-a" / "result.txt").read_text(
                encoding="utf-8"
            ),
        },
        "second": {
            **_result_payload(second),
            "result_file": (second_workspace / "outputs" / "exec-b" / "result.txt").read_text(
                encoding="utf-8"
            ),
        },
        "ready_unchanged": before == after,
        "ready_sentinel": (root / "READY").read_text(encoding="utf-8"),
        "package_checksum_before": before,
        "package_checksum_after": after,
        "cross_workspace_visible": (first_workspace / "outputs" / "exec-a" / "result.txt").exists()
        and (second_workspace / "outputs" / "exec-b" / "result.txt").exists(),
        "first_workspace_only_a": sorted(path.name for path in (first_workspace / "outputs").iterdir())
        == ["exec-a"],
        "second_workspace_only_b": sorted(path.name for path in (second_workspace / "outputs").iterdir())
        == ["exec-b"],
    }


async def scenario_s47() -> dict[str, Any]:
    base = Path(tempfile.mkdtemp(prefix="fx-s47-"))
    main = """\
import json, os
out = os.environ['SKILL_OUTPUT_DIR']
os.makedirs(os.path.join(out, 'reports'), exist_ok=True)
with open(os.path.join(out, 'reports', 'a.txt'), 'w', encoding='utf-8') as handle:
    handle.write('alpha')
with open(os.path.join(out, 'empty.bin'), 'wb') as handle:
    handle.write(b'')
os.symlink('/etc/passwd', os.path.join(out, 'passwd-link'))
os.symlink('/tmp', os.path.join(out, 'escape-dir'))
print(json.dumps({'ok': True}))
"""
    root = _package(base, main=main)
    executor = SandboxedSkillExecutor(runner=_runner())
    result = await executor.execute(_request(root, base / "ws", "s47-exec"))
    limited_root = _package(
        base,
        name="limited",
        main="""\
import os
out = os.environ['SKILL_OUTPUT_DIR']
open(os.path.join(out, 'one.txt'), 'w').write('one')
open(os.path.join(out, 'two.txt'), 'w').write('two')
print('done')
""",
    )
    limited = await executor.execute(
        _request(
            limited_root,
            base / "ws-limited",
            "s47-limit",
            output_limits=OutputLimits(max_files=1),
        )
    )
    return {
        "ok": result.status.value == "SUCCEEDED",
        "result": _result_payload(result),
        "registered_paths": [item.relative_path for item in result.outputs],
        "limited": _result_payload(limited),
        "limited_registered_paths": [item.relative_path for item in limited.outputs],
    }


async def scenario_e01() -> dict[str, Any]:
    base = Path(tempfile.mkdtemp(prefix="fx-e01-"))
    marker = "FX_E01_FLOOD_MARKER_73b1"
    main = f"""\
import sys, time
marker = {marker!r}
for _ in range(4096):
    sys.stdout.write('x' * 8192)
    sys.stdout.flush()
time.sleep(0.2)
"""
    root = _package(base, name=f"skill-{marker}", main=main)
    executor = SandboxedSkillExecutor(runner=_runner(), stdout_limit_bytes=65536)
    before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    started = time.monotonic()
    result = await executor.execute(_request(root, base / "ws", "e01-exec"))
    duration = time.monotonic() - started
    after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    await asyncio.sleep(0.5)
    orphans = _matching_processes(marker)
    return {
        "ok": result.output_notice == "SANDBOX_OUTPUT_LIMIT",
        "result": _result_payload(result),
        "stdout_bytes": len(result.stdout.encode("utf-8")),
        "rss_delta_kb": after - before,
        "duration_sec": duration,
        "orphans": orphans,
    }


def _matching_processes(needle: str) -> list[int]:
    found: list[int] = []
    proc = Path("/proc")
    if not proc.is_dir():
        return found
    needle_bytes = needle.encode()
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmdline = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        if needle_bytes in cmdline:
            found.append(int(entry.name))
    return sorted(found)


async def scenario_e03() -> dict[str, Any]:
    os.environ["INTERNAL_SERVICE_TOKEN"] = "probe-service-token-81f2"
    os.environ["DATABASE_URL"] = "postgresql://probe:secret@127.0.0.1/probe"
    os.environ["REDIS_URL"] = "redis://:probe-secret@127.0.0.1:6379/0"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "probe-aws-secret"
    base = Path(tempfile.mkdtemp(prefix="fx-e03-"))
    main = """\
import json, os
paths = {
    '/mnt': os.path.exists('/mnt'),
    '/proc/1': os.path.exists('/proc/1'),
    '/var/run/docker.sock': os.path.exists('/var/run/docker.sock'),
    '/var/run/secrets/kubernetes.io/serviceaccount/token': os.path.exists(
        '/var/run/secrets/kubernetes.io/serviceaccount/token'
    ),
    '/skill/SKILL.md': os.path.isfile('/skill/SKILL.md'),
    '/workspace': os.path.isdir('/workspace'),
}
try:
    with open('/workspace/probe-write.txt', 'w', encoding='utf-8') as handle:
        handle.write('ok')
    workspace_writable = True
except OSError:
    workspace_writable = False
try:
    with open('/skill/probe-write.txt', 'w', encoding='utf-8') as handle:
        handle.write('no')
    skill_writable = True
except OSError:
    skill_writable = False
secrets = {
    key: os.environ.get(key)
    for key in (
        'INTERNAL_SERVICE_TOKEN', 'DATABASE_URL', 'REDIS_URL',
        'AWS_SECRET_ACCESS_KEY', 'MUAD_SESSION_SOCKET', 'SKILL_TOKEN', 'SKILL_SECRET',
    )
}
print(json.dumps({
    'paths': paths,
    'workspace_writable': workspace_writable,
    'skill_writable': skill_writable,
    'secrets': secrets,
    'allowed_env': os.environ.get('SKILL_ALLOWED'),
    'output': os.environ.get('SKILL_OUTPUT_DIR'),
    'data': os.environ.get('SKILL_DATA_DIR'),
}))
"""
    root = _package(base, main=main)
    executor = SandboxedSkillExecutor(runner=_runner())
    result = await executor.execute(
        _request(
            root,
            base / "ws",
            "e03-exec",
            env={
                "INTERNAL_SERVICE_TOKEN": "request-service-token-a2d1",
                "DATABASE_URL": "postgresql://request:secret@127.0.0.1/request",
                "REDIS_URL": "redis://:request-secret@127.0.0.1:6379/0",
                "SKILL_TOKEN": "skill-token-should-drop",
                "SKILL_SECRET": "skill-secret-should-drop",
                "MUAD_SESSION_SOCKET": "/tmp/should-not-exist.sock",
                "SKILL_ALLOWED": "visible",
            },
        )
    )
    return {"ok": result.status.value == "SUCCEEDED", "result": _result_payload(result)}


async def scenario_cache() -> dict[str, Any]:
    """真实 NFS 目录 → SkillArtifactCache（checksum + READY）→ 沙箱执行。"""
    import hashlib
    import zipfile

    from muad_artifact_store import NfsArtifactStore, SkillArtifactCache

    base = Path(tempfile.mkdtemp(prefix="fx-cache-"))
    store_root = base / "nfs"
    store_root.mkdir()
    archive = store_root / "skills/probe/1.0.0/skill.zip"
    archive.parent.mkdir(parents=True)
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("SKILL.md", "# cache probe\n")
        bundle.writestr("scripts/helper.py", "VALUE = 41\n")
        bundle.writestr("scripts/main.py", SCRIPT_MAIN)
    checksum = "sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest()
    cache = SkillArtifactCache(NfsArtifactStore(store_root), base / "cache")
    ready_dir = await cache.ensure(
        artifact_id="probe-artifact", storage_key="skills/probe/1.0.0/skill.zip", checksum=checksum
    )
    ready_before = (ready_dir / "READY").read_text(encoding="utf-8")
    cache_before = _tree_checksum(ready_dir)
    executor = SandboxedSkillExecutor(runner=_runner())
    result = await executor.execute(_request(ready_dir, base / "ws", "cache-exec"))
    return {
        "ok": result.status.value == "SUCCEEDED",
        "result": _result_payload(result),
        "ready_before": ready_before,
        "ready_after": (ready_dir / "READY").read_text(encoding="utf-8"),
        "cache_checksum_before": cache_before,
        "cache_checksum_after": _tree_checksum(ready_dir),
        "ready_is_file": (ready_dir / "READY").is_file(),
    }


async def scenario_s03() -> dict[str, Any]:
    """真实 broker（Redis/Console/平台 adapter）+ bwrap + 旧 `session-manager` CLI。

    探针运行在容器内，broker 也运行在容器内；Redis/Console/平台桩由宿主机测试提供，
    通过 `host.docker.internal` 以真实 HTTP/Redis 协议访问。最终只回传脱敏形状。
    """
    from muad_platform_sdk.skill_access import SkillPlatformContext

    tenant = os.environ["FX_SESSION_TENANT"]
    actor = os.environ["FX_SESSION_ACTOR"]
    platform_key = os.environ["FX_SESSION_PLATFORM"]
    skill_name = os.environ["FX_SESSION_SKILL"]
    context = SkillPlatformContext(
        tenant_id=tenant,
        actor_user_id=actor,
        execution_id=str(__import__("uuid").uuid4()),
        execution_type="RUN",
        platform_keys=(platform_key,),
        console_url=os.environ["FX_SESSION_CONSOLE_URL"],
        redis_url=os.environ["FX_SESSION_REDIS_URL"],
        service_token=os.environ["FX_SESSION_SERVICE_TOKEN"],
    )
    base = Path(tempfile.mkdtemp(prefix="fx-s03-"))
    root = base / "skill"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    (root / "SKILL.md").write_text(
        f"---\nname: {skill_name}\ndescription: {skill_name}\n"
        f"platforms: [{platform_key}]\n---\nSession probe\n",
        encoding="utf-8",
    )
    (scripts / "main.py").write_text(
        "import json, os, subprocess\n"
        f"info = json.loads(subprocess.check_output(['session-manager', 'get-state', "
        f"'--skill-name', {skill_name!r}]))\n"
        "state = json.load(open(info['sessionStateFile'], encoding='utf-8'))\n"
        f"platform = state['platforms'][{platform_key!r}]\n"
        "cookies = sorted({item['name'] for item in platform['cookies']})\n"
        "print(json.dumps({\n"
        "    'state_path': info['sessionStateFile'],\n"
        "    'platforms': info['platforms'],\n"
        "    'expires_at': platform['expiresAt'],\n"
        "    'cookie_names': cookies,\n"
        "    'base_url': platform['requestProfile']['baseUrl'],\n"
        "    'socket': bool(os.environ.get('MUAD_SESSION_SOCKET')),\n"
        "    'path_has_session': any('fx-session' in part "
        "for part in os.environ.get('PATH', '').split(':')),\n"
        "    'proc_path': info['sessionStateFile'].startswith('/proc/'),\n"
        "    'bin_sh': os.path.exists('/bin/sh'),\n"
        "}))\n",
        encoding="utf-8",
    )
    executor = SandboxedSkillExecutor(runner=_runner())
    result = await executor.execute(_request(root, base / "workspace", "s03-exec", platform_context=context))
    leftovers = sorted(str(path) for path in Path("/tmp").glob("fx-session-*"))
    return {
        "ok": result.status.value == "SUCCEEDED" and result.exit_code == 0,
        "result": _result_payload(result),
        "leftover_session_dirs": leftovers,
    }


SCENARIOS = {
    "s03": scenario_s03,
    "s10": scenario_s10,
    "s15": scenario_s15,
    "s47": scenario_s47,
    "e01": scenario_e01,
    "e03": scenario_e03,
    "cache": scenario_cache,
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", required=True, choices=sorted(SCENARIOS))
    args = parser.parse_args()
    try:
        payload = asyncio.run(SCENARIOS[args.scenario]())
    except Exception as exc:  # noqa: BLE001 - 探针要把失败原因原样带回宿主测试
        payload = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        print(json.dumps(payload, ensure_ascii=False))
        return 1
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
