"""[E-07][S-26] SandboxRunner 工厂、bwrap 探测与 policy 边界。

真实边界（不 mock）：

- bwrap 探测执行**真实可执行文件**（临时目录里的 shell 脚本 / PATH 查找），
  缺失或版本不兼容时工厂必须 fail closed，不能回退宿主执行；
- dev-unsafe 必须显式开启，执行真实子进程并留下审计事件；
- bwrap argv 必须带 ``--unshare-all`` / ``--die-with-parent`` / ``--new-session`` /
  ``--clearenv``，只读挂载已批准包、可写挂载 workspace，且 env 只保留 allowlist；
- rlimit 由真实子进程 ``resource.getrlimit`` 读回，证明限制确实生效。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import sys
from pathlib import Path

import pytest
import yaml
from muad_agent_core.sandbox import (
    BwrapRunner,
    DevUnsafeRunner,
    SandboxCancelled,
    SandboxLimits,
    SandboxMount,
    SandboxPolicy,
    SandboxProfile,
    SandboxSpec,
    SandboxTimeout,
    SandboxUnavailable,
    build_bwrap_argv,
    build_sandbox_runner,
    probe_bwrap,
    sanitize_env,
    workspace_layout_from_settings,
)
from muad_common import SharedSettings
from pydantic import ValidationError

pytestmark = pytest.mark.unit


def _policy(*, source: str = "USER", dev_unsafe: bool = False) -> SandboxPolicy:
    return SandboxPolicy.for_source(source, dev_unsafe=dev_unsafe)


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


def _package(tmp_path: Path) -> Path:
    package = tmp_path / "skill"
    package.mkdir(parents=True, exist_ok=True)
    (package / "SKILL.md").write_text("# test skill\n", encoding="utf-8")
    return package


def _spec(
    tmp_path: Path,
    *,
    policy: SandboxPolicy | None = None,
    command: tuple[str, ...] | None = None,
    timeout_sec: float = 10.0,
    cancel_event: asyncio.Event | None = None,
    env: dict[str, str] | None = None,
    limits: SandboxLimits | None = None,
    with_package: bool = True,
) -> SandboxSpec:
    return SandboxSpec(
        policy=policy or _policy(),
        command=command or (sys.executable, "-c", "print('ok')"),
        workspace_dir=_workspace(tmp_path),
        package_dir=_package(tmp_path) if with_package else None,
        env=env or {},
        timeout_sec=timeout_sec,
        cancel_event=cancel_event,
        limits=limits,
        name="unit-sandbox",
    )


def _fake_bwrap(path: Path, version: str) -> Path:
    path.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then\n'
        f'  echo "bubblewrap {version}"\n'
        "  exit 0\n"
        "fi\n"
        "exit 2\n",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def _flag_value(argv: list[str], flag: str) -> list[str]:
    """取一对 ``--flag value`` 里的 value（同 flag 只出现一次时使用）。"""
    index = argv.index(flag)
    return argv[index + 1]


def _pair_index(argv: list[str], *sequence: str) -> int:
    for index in range(len(argv) - len(sequence) + 1):
        if tuple(argv[index : index + len(sequence)]) == sequence:
            return index
    raise AssertionError(f"argv 缺少序列 {sequence!r}: {argv!r}")


def test_missing_bwrap_fails_closed_with_visible_event(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    events = []
    with caplog.at_level(logging.WARNING):
        with pytest.raises(SandboxUnavailable) as excinfo:
            build_sandbox_runner(
                mode="bwrap",
                env="dev",
                platform="linux",
                bwrap_path=str(tmp_path / "missing-bwrap"),
                observer=events.append,
            )

    assert excinfo.value.code == "SANDBOX_UNAVAILABLE"
    assert excinfo.value.result is None
    assert events and events[-1].kind == "sandbox_unavailable"
    assert events[-1].detail["mode"] == "bwrap"
    assert "sandbox_unavailable" in caplog.text


def test_incompatible_bwrap_version_fails_closed(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    bwrap = _fake_bwrap(tmp_path / "bwrap", "0.11.0")
    events = []
    with caplog.at_level(logging.WARNING):
        with pytest.raises(SandboxUnavailable) as excinfo:
            build_sandbox_runner(
                mode="bwrap",
                env="dev",
                platform="linux",
                bwrap_path=str(bwrap),
                observer=events.append,
            )

    assert excinfo.value.code == "SANDBOX_UNAVAILABLE"
    assert "0.11.0" in str(excinfo.value)
    assert events and events[-1].kind == "sandbox_unavailable"
    assert "sandbox_unavailable" in caplog.text


def test_supported_bwrap_probe_returns_bwrap_runner(tmp_path: Path) -> None:
    bwrap = _fake_bwrap(tmp_path / "bwrap", "0.13.1")

    probe = probe_bwrap(str(bwrap))
    assert probe.path == str(bwrap)
    assert probe.version == (0, 13, 1)
    assert probe.raw_version == "bubblewrap 0.13.1"

    runner = build_sandbox_runner(
        mode="bwrap", env="dev", platform="linux", bwrap_path=str(bwrap)
    )
    assert isinstance(runner, BwrapRunner)
    assert runner.version == (0, 13, 1)


def test_unknown_sandbox_mode_is_rejected() -> None:
    with pytest.raises(SandboxUnavailable) as excinfo:
        build_sandbox_runner(mode="seatbelt", env="dev", platform="darwin")
    assert excinfo.value.code == "SANDBOX_UNAVAILABLE"


def test_macos_without_explicit_dev_unsafe_is_refused() -> None:
    with pytest.raises(SandboxUnavailable) as excinfo:
        build_sandbox_runner(mode="bwrap", env="dev", platform="darwin")
    assert excinfo.value.code == "SANDBOX_UNAVAILABLE"
    assert "darwin" in str(excinfo.value)


def test_dev_unsafe_mode_is_only_allowed_in_dev() -> None:
    with pytest.raises(SandboxUnavailable) as excinfo:
        build_sandbox_runner(mode="dev-unsafe", env="prod", platform="darwin")
    assert excinfo.value.code == "SANDBOX_UNAVAILABLE"

    with pytest.raises(ValidationError):
        SharedSettings(_env_file=None, env="prod", sandbox_mode="dev-unsafe")


def test_k8s_deploy_manifests_pin_bwrap_and_never_enable_dev_unsafe() -> None:
    """[S-26] 生产/本地 K8s 配置必须显式 bwrap；dev-unsafe 只能由 macOS Dev 显式开启。"""
    root = Path(__file__).resolve().parents[2]
    deploy = root / "deploy/k8s"
    configmap = next(
        doc
        for doc in yaml.safe_load_all((deploy / "base/configmap.yaml").read_text(encoding="utf-8"))
        if isinstance(doc, dict) and doc.get("kind") == "ConfigMap"
    )
    assert configmap["data"].get("SANDBOX_MODE") == "bwrap", (
        "K8s ConfigMap 必须显式 pin SANDBOX_MODE=bwrap（不靠 SharedSettings 默认值）"
    )
    for path in sorted(deploy.rglob("*.yaml")):
        documents = [doc for doc in yaml.safe_load_all(path.read_text(encoding="utf-8")) if doc]
        values = yaml.safe_dump_all(documents, allow_unicode=True)
        assert "dev-unsafe" not in values, (
            f"{path.relative_to(root)} 不得启用 dev-unsafe（生产配置不能绕过 bwrap）"
        )


async def test_dev_unsafe_requires_policy_flag_and_emits_audit(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    runner = build_sandbox_runner(mode="dev-unsafe", env="dev", platform="darwin")
    assert isinstance(runner, DevUnsafeRunner)

    with pytest.raises(SandboxUnavailable) as excinfo:
        await runner.run(_spec(tmp_path, policy=_policy(dev_unsafe=False)))
    assert "dev-unsafe" in str(excinfo.value)

    events = []
    explicit_runner = build_sandbox_runner(
        mode="dev-unsafe", env="dev", platform="darwin", observer=events.append
    )
    with caplog.at_level(logging.INFO):
        result = await explicit_runner.run(
            _spec(tmp_path, policy=_policy(dev_unsafe=True))
        )

    assert result.exit_code == 0
    assert result.stdout.strip() == "ok"
    assert any(event.kind == "sandbox_dev_unsafe_execution" for event in events)
    audit = next(event for event in events if event.kind == "sandbox_dev_unsafe_execution")
    assert audit.detail["name"] == "unit-sandbox"
    assert "sandbox_dev_unsafe_execution" in caplog.text


def test_bwrap_argv_is_isolated_and_env_is_allowlisted(tmp_path: Path) -> None:
    spec = _spec(
        tmp_path,
        env={
            "PATH": "/usr/bin:/bin",
            "HOME": "/root",
            "SKILL_OUTPUT_DIR": "/workspace/outputs/run-1",
            "SKILL_DATA_DIR": "/workspace/data",
            "INTERNAL_SERVICE_TOKEN": "service-token",
            "DATABASE_URL": "postgresql://user:secret@db/muad",
            "REDIS_URL": "redis://cache/0",
            "AWS_SECRET_ACCESS_KEY": "aws-secret",
            "MODEL_API_KEY": "model-secret",
            "CUSTOM_FLAG": "drop-me",
        },
    )

    argv = build_bwrap_argv(spec, "/usr/bin/bwrap")

    assert argv[0] == "/usr/bin/bwrap"
    for flag in ("--unshare-all", "--die-with-parent", "--new-session", "--share-net", "--clearenv"):
        assert flag in argv, f"缺少隔离参数 {flag}: {argv!r}"

    assert _pair_index(argv, "--ro-bind", "/usr", "/usr") >= 0
    assert _pair_index(argv, "--ro-bind-try", "/lib64", "/lib64") >= 0
    assert _pair_index(argv, "--dev", "/dev") >= 0
    assert _pair_index(argv, "--ro-bind", str(spec.package_dir), "/skill") >= 0
    assert _pair_index(argv, "--bind", str(spec.workspace_dir), "/workspace") >= 0
    assert _pair_index(argv, "--chdir", "/workspace") >= 0
    assert _flag_value(argv, "--size") == str(spec.effective_limits.tmpfs_bytes)
    assert _pair_index(argv, "--tmpfs", "/tmp") >= 0

    separator = argv.index("--")
    assert argv[separator + 1 :] == list(spec.command)

    assert "INTERNAL_SERVICE_TOKEN" not in argv
    assert "DATABASE_URL" not in argv
    assert "REDIS_URL" not in argv
    assert "AWS_SECRET_ACCESS_KEY" not in argv
    assert "MODEL_API_KEY" not in argv
    assert "CUSTOM_FLAG" not in argv
    assert _pair_index(argv, "--setenv", "SKILL_OUTPUT_DIR", "/workspace/outputs/run-1") >= 0
    assert _pair_index(argv, "--setenv", "SKILL_DATA_DIR", "/workspace/data") >= 0
    assert _pair_index(argv, "--setenv", "HOME", "/tmp") >= 0

    joined = " ".join(argv)
    for forbidden_target in ("/mnt", "/proc", "/var/run", "/var/lib/docker"):
        assert f"{forbidden_target} {forbidden_target}" not in joined


def test_forbidden_mount_target_and_dangerous_allowlist_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(SandboxUnavailable):
        SandboxMount(source=tmp_path, target="/proc", readonly=True)

    with pytest.raises(SandboxUnavailable):
        SandboxPolicy(env_allowlist=("PATH", "INTERNAL_SERVICE_TOKEN"))


def test_policy_maps_skill_source_to_profile() -> None:
    assert _policy(source="SYSTEM").profile is SandboxProfile.TRUSTED
    assert _policy(source="ADMIN").profile is SandboxProfile.TRUSTED
    assert _policy(source="USER").profile is SandboxProfile.UNTRUSTED
    with pytest.raises(SandboxUnavailable):
        _policy(source="UNKNOWN")


def test_sanitize_env_drops_credentials_but_keeps_skill_context() -> None:
    env = sanitize_env(
        {
            "PATH": "/usr/bin",
            "HOME": "/root",
            "SKILL_OUTPUT_DIR": "/workspace/outputs/run-1",
            "SKILL_INTERNAL_SERVICE_TOKEN": "must-drop",
            "INTERNAL_SERVICE_TOKEN": "must-drop",
            "DATABASE_URL": "must-drop",
            "CUSTOM": "must-drop",
        },
        default_path="/bin",
        home="/tmp",
    )

    assert env["PATH"] == "/usr/bin"
    assert env["HOME"] == "/tmp"
    assert env["SKILL_OUTPUT_DIR"] == "/workspace/outputs/run-1"
    assert "SKILL_INTERNAL_SERVICE_TOKEN" not in env
    assert "INTERNAL_SERVICE_TOKEN" not in env
    assert "DATABASE_URL" not in env
    assert "CUSTOM" not in env


def test_workspace_layout_projects_typed_settings(tmp_path: Path) -> None:
    from muad_agent_core.sandbox.workspace import DAY_SECONDS, MIB

    settings = SharedSettings(
        _env_file=None,
        workspace_root=str(tmp_path / "workspaces"),
        workspace_quota_mib=600,
        workspace_user_quota_mib=700,
        workspace_file_limit=123,
        workspace_output_ttl_days=2,
        workspace_data_ttl_days=3,
    )

    layout = workspace_layout_from_settings(settings)

    assert layout.root == tmp_path / "workspaces"
    assert layout.quota_bytes == 600 * MIB
    assert layout.user_quota_bytes == 700 * MIB
    assert layout.file_limit == 123
    assert layout.output_ttl_seconds == 2 * DAY_SECONDS
    assert layout.data_ttl_seconds == 3 * DAY_SECONDS
    assert layout.workspace_dir(tenant_id="t", user_id="u", agent_id="a") == (
        tmp_path / "workspaces" / "t" / "u" / "a"
    )
    assert layout.output_dir(
        tenant_id="t", user_id="u", agent_id="a", execution_id="run-1"
    ) == tmp_path / "workspaces" / "t" / "u" / "a" / "outputs" / "run-1"


async def test_rlimits_are_applied_to_the_child(tmp_path: Path) -> None:
    limits = SandboxLimits(
        tmpfs_bytes=8 * 1024 * 1024,
        address_space_bytes=512 * 1024 * 1024,
        cpu_seconds=30,
        max_processes=None,
        max_file_bytes=1024 * 1024,
        open_files=64,
    )
    spec = _spec(
        tmp_path,
        policy=_policy(dev_unsafe=True),
        command=(
            sys.executable,
            "-c",
            "import resource\n"
            "print('fsize', resource.getrlimit(resource.RLIMIT_FSIZE))\n"
            "print('nofile', resource.getrlimit(resource.RLIMIT_NOFILE))\n"
            "print('as', resource.getrlimit(resource.RLIMIT_AS))\n",
        ),
        limits=limits,
    )

    result = await DevUnsafeRunner().run(spec)

    assert result.exit_code == 0
    assert "fsize (1048576, 1048576)" in result.stdout
    assert "nofile (64, 64)" in result.stdout
    if sys.platform.startswith("linux"):
        # macOS 的内核不接受 RLIMIT_AS（EINVAL）；Linux 上必须真实生效。
        assert "as (536870912, 536870912)" in result.stdout


def _process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


async def _wait_for_file(path: Path, *, timeout_sec: float = 10.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_sec
    while asyncio.get_running_loop().time() < deadline:
        if path.exists():
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"marker 未出现: {path}")


async def test_timeout_reaps_process_group_and_next_run_works(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    marker = workspace / "grandchild.pid"
    script = (
        "import os, subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "pid = os.fork()\n"
        "if pid == 0:\n"
        "    os.fork()\n"
        "    time.sleep(60)\n"
        "    os._exit(0)\n"
        f"open({str(marker)!r}, 'w').write(str(child.pid))\n"
        "time.sleep(60)\n"
    )
    spec = _spec(
        tmp_path,
        policy=_policy(dev_unsafe=True),
        command=(sys.executable, "-c", script),
        timeout_sec=1.0,
    )

    with pytest.raises(SandboxTimeout) as excinfo:
        await DevUnsafeRunner().run(spec)

    assert excinfo.value.code == "SANDBOX_TIMEOUT"
    child_pid = int(marker.read_text(encoding="utf-8"))
    assert not _process_alive(child_pid), "后台子进程在超时后仍是孤儿"

    again = _spec(
        tmp_path,
        policy=_policy(dev_unsafe=True),
        command=(sys.executable, "-c", "print('after-timeout')"),
    )
    result = await DevUnsafeRunner().run(again)
    assert result.exit_code == 0
    assert result.stdout.strip() == "after-timeout"


async def test_cancel_reaps_process_group(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    marker = workspace / "cancel-child.pid"
    cancel_event = asyncio.Event()
    script = (
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"open({str(marker)!r}, 'w').write(str(child.pid))\n"
        "time.sleep(60)\n"
    )
    spec = _spec(
        tmp_path,
        policy=_policy(dev_unsafe=True),
        command=(sys.executable, "-c", script),
        timeout_sec=30.0,
        cancel_event=cancel_event,
    )
    task = asyncio.create_task(DevUnsafeRunner().run(spec))
    try:
        await _wait_for_file(marker)
        cancel_event.set()
        with pytest.raises(SandboxCancelled) as excinfo:
            await task
    finally:
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    assert excinfo.value.code == "SANDBOX_CANCELLED"
    assert not _process_alive(int(marker.read_text(encoding="utf-8")))
