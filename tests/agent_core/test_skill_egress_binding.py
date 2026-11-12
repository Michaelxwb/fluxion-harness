"""Unit checks of trusted sandbox compilation and stop scheduling, not E-02."""
import asyncio
import sys
from pathlib import Path

import pytest
from muad_agent_core.sandbox import SandboxResult
from muad_agent_core.skill import SandboxedSkillExecutor, SkillExecutionError, SkillExecutionRequest
from muad_agent_core.skill.sandboxed import _run_with_budget_stop
from muad_contracts.shared_egress import TrustedInvocation
from muad_platform_sdk.egress_ipc import EgressTarget, InvocationEgressBroker


class CaptureRunner:
    name = "bwrap"
    def __init__(self):
        self.spec = None
        self.started = False
    async def run(self, spec):
        self.started = True
        self.spec = spec
        return SandboxResult(0, "{}", "", 0)


def package(tmp_path, *, suffix="py"):
    root = tmp_path / "skill"
    root.mkdir()
    (root / "SKILL.md").write_text("---\nname: probe\ndescription: probe\n---\n")
    (root / "scripts").mkdir()
    script = root / "scripts" / ("main." + suffix)
    script.write_text('print("{}")' if suffix == "py" else 'console.log("{}")')
    return root, script


def broker():
    # The capture runner never makes requests; the SDK backend must remain unused.
    class UnusedBackend:
        async def execute_command(self, *args):
            raise AssertionError("compilation must not contact Redis")
    from muad_platform_sdk.shared_redis import SharedRedisMeter
    meter = SharedRedisMeter(UnusedBackend(), domain="unit", epoch="epoch")
    return InvocationEgressBroker(meter=meter, 
                                 invocation=TrustedInvocation("execution", "invocation", "segment"),
                                 resolve=lambda metadata: EgressTarget("platform", "route"),
                                 deadline_seconds=5, cleanup_seconds=1)


async def test_shared_binding_injects_private_mount_and_trusted_file_launcher(tmp_path):
    root, _ = package(tmp_path)
    runner = CaptureRunner()
    context = broker()
    result = await SandboxedSkillExecutor(runner=runner).execute(SkillExecutionRequest(
        ready_dir=root, input={}, source="USER", timeout_sec=5, egress_broker=context,
        argv=("--literal", "$HOME"), env={"MUAD_EGRESS_SOCKET": "/untrusted/socket"},
    ))
    assert result.exit_code == 0
    spec = runner.spec
    assert spec.command[:4] == (sys.executable, "-E", "-s", "-B")
    assert Path(spec.command[4]).name == "muad_egress_launch.py"
    assert spec.command[5:] == ("/skill/scripts/main.py", "--literal", "$HOME")
    assert spec.env["MUAD_EGRESS_SOCKET"] == "/tmp/fx-egress/broker.sock"
    assert "MUAD_EGRESS_SOCKET" in spec.policy.env_inject
    mounts = [mount for mount in spec.mounts if mount.target == "/tmp/fx-egress"]
    assert len(mounts) == 1 and mounts[0].readonly
    assert not context.socket_path.parent.exists()


async def test_controlled_javascript_is_rejected_before_starting(tmp_path):
    root, script = package(tmp_path, suffix="js")
    runner = CaptureRunner()
    with pytest.raises(SkillExecutionError, match="EGRESS_CLIENT_UNSUPPORTED"):
        await SandboxedSkillExecutor(runner=runner).execute(SkillExecutionRequest(
            ready_dir=root, script=script, input={}, source="USER", timeout_sec=5, egress_broker=broker(),
        ))
    assert not runner.started


async def test_set_stop_flag_wins_even_if_waiter_is_scheduled_later():
    class DelayedWaiter(asyncio.Event):
        async def wait(self):
            await asyncio.sleep(.05)
            return await super().wait()
    signal = DelayedWaiter()
    class FastRunner:
        async def run(self, spec):
            signal.set()
            return SandboxResult(0, "{}", "", 0)
    with pytest.raises(SkillExecutionError, match="LOCAL_BUDGET_EXHAUSTED"):
        await _run_with_budget_stop(FastRunner(), None, signal)


async def test_pre_exhausted_scope_never_starts_business():
    signal = asyncio.Event()
    signal.set()
    runner = CaptureRunner()
    with pytest.raises(SkillExecutionError, match="LOCAL_BUDGET_EXHAUSTED"):
        await _run_with_budget_stop(runner, None, signal)
    assert not runner.started


async def test_unconfirmed_runner_cleanup_is_not_reported_as_normal_budget_stop():
    started, exhausted = asyncio.Event(), asyncio.Event()
    class UnconfirmedRunner:
        async def run(self, spec):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                raise RuntimeError("termination failed") from None
    async def exhaust():
        await started.wait()
        exhausted.set()
    trigger = asyncio.create_task(exhaust())
    try:
        with pytest.raises(SkillExecutionError, match="EGRESS_STOP_UNCONFIRMED"):
            await _run_with_budget_stop(UnconfirmedRunner(), None, exhausted)
    finally:
        await trigger


async def test_native_runner_raises_if_termination_cannot_be_confirmed(monkeypatch):
    from types import SimpleNamespace

    from muad_agent_core.sandbox import SandboxUnavailable
    from muad_agent_core.sandbox import runner as native

    monkeypatch.setattr(native, "TERMINATE_GRACE_SEC", .01)
    monkeypatch.setattr(native, "_signal_group", lambda process, sig: None)
    pending = asyncio.create_task(asyncio.Event().wait())
    try:
        with pytest.raises(SandboxUnavailable, match="termination not confirmed"):
            await native._terminate(SimpleNamespace(pid=0), pending)
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)


def test_shared_and_legacy_accounting_cannot_be_combined(tmp_path):
    root, _ = package(tmp_path)
    with pytest.raises(SkillExecutionError, match="EGRESS_SCOPE_CONFLICT"):
        SkillExecutionRequest(ready_dir=root, input={}, source="USER", timeout_sec=5,
                              egress_broker=broker(), budget_exhausted_event=asyncio.Event())


def test_process_group_signal_failure_is_explicit(monkeypatch):
    from types import SimpleNamespace

    from muad_agent_core.sandbox import SandboxUnavailable
    from muad_agent_core.sandbox import runner as native

    def denied(group, sig):
        raise PermissionError("signal denied")
    monkeypatch.setattr(native.os, "killpg", denied)
    with pytest.raises(SandboxUnavailable, match="process group signal failed"):
        native._signal_group(SimpleNamespace(pid=0), 15)
