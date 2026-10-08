"""S-01/03/05, E-04/05/06/12: real processes, Console authority, HTTP and PG."""

import json
import os
import signal
import threading
from datetime import UTC, datetime, timedelta
from queue import Queue
from uuid import UUID, uuid4

import httpx
import pytest
from muad_agent_runtime.infrastructure.models.async_tools import (
    ToolOperation,
    ToolResultInbox,
)
from muad_agent_runtime.infrastructure.models.runtime import CanonicalEvent, RunRecord, RuntimeSnapshot
from muad_agent_worker.infrastructure.models.task import TaskExecution
from muad_console_platform.infrastructure.models.control import (
    AgentDefinition,
    ModelDefinition,
    SkillArtifact,
)
from sqlalchemy import select, text

from tests.acceptance.runtime.test_submission_hardening import _db, _wait
from tests.acceptance.task_schedule.conftest import live_stack as schedule_stack
from tests.acceptance.task_schedule.environment import ServiceProcess, build_skill_zip, free_port

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="module", name="live_stack")
def join_stack(tmp_path_factory):
    for stack in schedule_stack.__wrapped__(tmp_path_factory):
        runtime = stack.processes["runtime"]
        probe = ServiceProcess(
            "join-model",
            "tests.acceptance.runtime.join_model_probe",
            free_port(),
            runtime.env,
            stack.artifact_root / "join-model.log",
        )
        probe.start()
        original_llm = stack.llm_url
        stack.llm_url = probe.url
        for key in ("worker-1", "worker-2"):
            worker = stack.processes[key]
            worker.stop()
            worker.env["AGENT_RUNTIME_URL"] = stack.runtime_url
            worker.start()

        async def update(factory, stack=stack, probe=probe):
            async with factory() as session, session.begin():
                model = await session.get(ModelDefinition, stack.model_id)
                model.base_url = probe.url + "/v1"
                artifact = (
                    await session.scalars(
                        select(SkillArtifact).where(SkillArtifact.skill_id == stack.skill_id)
                    )
                ).one()
                artifact.storage_key = "skills/join-probe.zip"
                artifact.checksum = build_skill_zip(
                    stack.artifact_root,
                    artifact.storage_key,
                    body=("import json, sys, time, pathlib\n"
                          "p=json.loads(sys.stdin.read())\n"
                          "gate=pathlib.Path(p['gate'])\n"
                          "while not gate.exists(): time.sleep(.05)\n"
                          "print(json.dumps({'actual_job_result':p['index']}))\n"),
                )

        _db(update)
        try:
            yield stack
        finally:
            probe.stop()
            stack.llm_url = original_llm


def _source(stack, run_id):
    async def load(factory):
        async with factory() as session:
            return await session.get(RunRecord, run_id)

    return _db(load)


def _begin(stack, client, tmp_path, *, calls=1):
    gate = tmp_path / str(uuid4())
    client.post(
        stack.llm_url + "/configure", json={"skill_key": stack.skill_key, "gate": str(gate), "calls": calls}
    ).raise_for_status()
    events, result = Queue(), Queue()
    payload = {
        "agent_id": str(stack.agent_id),
        "platform_user_id": str(stack.platform_user_id),
        "channel": {"type": "WECOM"},
        "message": {"id": str(uuid4()), "type": "text", "text": "Check the jobs"},
    }

    def consume():
        try:
            with httpx.Client(timeout=90, trust_env=False) as stream_client:
                with stream_client.stream(
                    "POST", stack.runtime_url + "/v1/runs", headers=stack.service_headers(), json=payload
                ) as response:
                    response.raise_for_status()
                    for line in response.iter_lines():
                        if line.startswith("data:"):
                            event = json.loads(line[5:])
                            events.put(event)
                            if event["type"] in ("run.completed", "run.failed"):
                                result.put(event)
                                return
        except Exception as exc:
            result.put(exc)

    thread = threading.Thread(target=consume, daemon=True)
    thread.start()
    first = events.get(timeout=20)
    return UUID(first["run_id"]), gate, events, result, thread


def _waiting(stack, run_id):
    return _wait(lambda: row if (row := _source(stack, run_id)).status == "WAITING_TOOL" else None)


def _finish(result, thread):
    final = result.get(timeout=75)
    thread.join(timeout=5)
    assert isinstance(final, dict), final
    assert final["type"] == "run.completed" and final["data"]["status"] == "COMPLETED"
    return final


def test_s01_join_receipt_independent_work_wait_release_and_actual_result(live_stack, tmp_path):
    with httpx.Client(timeout=30, trust_env=False) as client:
        run_id, gate, events, result, thread = _begin(live_stack, client, tmp_path)
        row = _waiting(live_stack, run_id)
        assert row.lease_owner is None and row.lease_until is None
        observed = []
        while not events.empty():
            observed.append(events.get_nowait())
        assert any(item["type"] == "tool.completed" for item in observed)
        assert any(item["type"] == "run.waiting_tool" for item in observed)
        requests = client.get(live_stack.llm_url + "/requests").json()["requests"]
        receipt = next(
            message
            for entry in requests
            for message in entry["body"]["messages"]
            if message["role"] == "tool"
        )
        assert json.loads(receipt["content"])["operation_id"]
        gate.touch()
        final = _finish(result, thread)
        assert final["run_id"] == str(run_id) and "actual_job_result" in final["data"]["final_text"]


def test_s03_two_results_original_tool_pair_and_reconstructed_request(live_stack, tmp_path):
    with httpx.Client(timeout=30, trust_env=False) as client:
        run_id, gate, events, result, thread = _begin(live_stack, client, tmp_path, calls=2)
        _waiting(live_stack, run_id)
        gate.touch()
        _finish(result, thread)
        requests = client.get(live_stack.llm_url + "/requests").json()["requests"]
        final = requests[-1]["body"]["messages"]
        assert sum(item["role"] == "tool" for item in final) == 2
        assert sum("BACKGROUND_RESULT" in item["content"] for item in final) == 2

        async def rebuild(factory):
            from muad_agent_core.model.openai_provider import OpenAICompatibleProvider
            from muad_agent_runtime.application.context_builder import DbBackedContextBuilder

            row = _source(live_stack, run_id)
            history = await DbBackedContextBuilder(session_factory=lambda: factory).load_history(
                tenant_id=row.tenant_id, conversation_id=row.conversation_id, user_id=None
            )
            return [OpenAICompatibleProvider._message(message) for message in history]

        # Final assistant was appended after the provider request; everything before it is identical.
        assert _db(rebuild)[:-1] == final[1:]


def test_s05_frozen_configuration_authorization_and_rotated_credentials(live_stack, tmp_path):
    with httpx.Client(timeout=30, trust_env=False) as client:
        run_id, gate, events, result, thread = _begin(live_stack, client, tmp_path)
        _waiting(live_stack, run_id)

        async def mutate(factory):
            async with factory() as session, session.begin():
                snapshot = (
                    await session.scalars(select(RuntimeSnapshot).where(RuntimeSnapshot.run_id == run_id))
                ).one()
                agent = await session.get(AgentDefinition, live_stack.agent_id)
                model = await session.get(ModelDefinition, live_stack.model_id)
                before = agent.instructions, model.params_json, model.api_key, model.model_id
                agent.instructions = "new configuration only"
                model.params_json = {"temperature": 0.8}
                model.model_id = "new-model-only"
                model.api_key = "rotated-memory-only-canary"
                await session.execute(
                    text("UPDATE control.agent_access_grant SET is_deleted=true WHERE agent_id=:id"),
                    {"id": live_stack.agent_id},
                )
                return snapshot.content_hash, before

        snapshot_hash, original = _db(mutate)
        try:
            gate.touch()
            _finish(result, thread)
            requests = client.get(live_stack.llm_url + "/requests").json()["requests"]
            last = requests[-1]
            assert last["authorization"] == "Bearer rotated-memory-only-canary"
            assert last["body"]["model"] == original[3]
            assert "new configuration only" not in json.dumps(last["body"])

            async def frozen(factory):
                async with factory() as session:
                    return (
                        (
                            await session.scalars(
                                select(RuntimeSnapshot).where(RuntimeSnapshot.run_id == run_id)
                            )
                        )
                        .one()
                        .content_hash
                    )

            assert _db(frozen) == snapshot_hash
            denied = client.post(
                live_stack.runtime_url + "/v1/runs",
                headers=live_stack.service_headers(),
                json={
                    "agent_id": str(live_stack.agent_id),
                    "platform_user_id": str(live_stack.platform_user_id),
                    "channel": {"type": "WECOM"},
                    "message": {"id": str(uuid4()), "type": "text", "text": "new"},
                },
            )
            assert denied.status_code == 403
        finally:

            async def restore(factory):
                async with factory() as session, session.begin():
                    agent = await session.get(AgentDefinition, live_stack.agent_id)
                    model = await session.get(ModelDefinition, live_stack.model_id)
                    agent.instructions, model.params_json, model.api_key, model.model_id = original
                    await session.execute(
                        text("UPDATE control.agent_access_grant SET is_deleted=false WHERE agent_id=:id"),
                        {"id": live_stack.agent_id},
                    )

            _db(restore)


def test_e04_result_between_wait_recheck_and_commit_is_not_lost(live_stack, tmp_path):
    async def barrier(factory):
        async with factory() as session, session.begin():
            await session.execute(
                text(
                    "CREATE FUNCTION runtime.wait_commit_barrier() RETURNS trigger LANGUAGE plpgsql AS $$ "
                    "BEGIN IF NEW.event_type='RUN_WAITING_TOOL' THEN PERFORM pg_sleep(2); END IF; "
                    "RETURN NEW; END $$"
                )
            )
            await session.execute(
                text(
                    "CREATE TRIGGER wait_commit_barrier BEFORE INSERT ON runtime.canonical_event "
                    "FOR EACH ROW EXECUTE FUNCTION runtime.wait_commit_barrier()"
                )
            )

    _db(barrier)
    try:
        with httpx.Client(timeout=30, trust_env=False) as client:
            run_id, gate, events, result, thread = _begin(live_stack, client, tmp_path)
            _wait(lambda: len(client.get(live_stack.llm_url + "/requests").json()["requests"]) >= 2)
            gate.touch()
            assert _finish(result, thread)["run_id"] == str(run_id)
    finally:

        async def remove(factory):
            async with factory() as session, session.begin():
                await session.execute(text("DROP TRIGGER wait_commit_barrier ON runtime.canonical_event"))
                await session.execute(text("DROP FUNCTION runtime.wait_commit_barrier()"))

        _db(remove)


def test_e05_killed_waiting_instance_second_process_resumes_one_epoch(live_stack, tmp_path):
    with httpx.Client(timeout=30, trust_env=False) as client:
        run_id, gate, events, result, thread = _begin(live_stack, client, tmp_path)
        row = _waiting(live_stack, run_id)
        original = live_stack.processes["runtime"]
        second = ServiceProcess(
            "continuation-2", original.module, free_port(), original.env, tmp_path / "runtime-2.log"
        )
        third = ServiceProcess(
            "continuation-3", original.module, free_port(), original.env, tmp_path / "runtime-3.log"
        )
        os.kill(original._process.pid, signal.SIGKILL)
        original._process.wait(timeout=10)
        second.start()
        third.start()
        try:
            gate.touch()
            # Worker retries its HTTP result destination until the replacement original port is up.
            original.start()
            completed = _wait(
                lambda: fresh if (fresh := _source(live_stack, run_id)).status == "COMPLETED" else None
            )
            assert completed.execution_epoch == row.execution_epoch + 1

            async def count(factory):
                async with factory() as session:
                    resumes = list(
                        await session.scalars(
                            select(CanonicalEvent).where(
                                CanonicalEvent.run_id == run_id, CanonicalEvent.event_type == "RUN_RESUMED"
                            )
                        )
                    )
                    return resumes

            assert len(_db(count)) == 1
            from muad_agent_core.agent.continuation import RunnerCheckpoint
            from muad_agent_runtime.application.async_tools.continuation_service import (
                ExecutionIdentity,
                ExecutionLeaseLost,
                checkpoint_execution,
                renew_execution,
            )

            async def stale(factory):
                identity = ExecutionIdentity(row.tenant_id, row.id, "expired", row.execution_epoch)
                assert not await renew_execution(factory, identity, lease_sec=30)
                with pytest.raises(ExecutionLeaseLost):
                    await checkpoint_execution(factory, identity, RunnerCheckpoint())

            _db(stale)
        finally:
            second.stop()
            third.stop()
            thread.join(timeout=5)


def test_e06_waiting_deadline_cancels_join_and_late_success_stays_late(live_stack, tmp_path):
    with httpx.Client(timeout=30, trust_env=False) as client:
        run_id, gate, events, result, thread = _begin(live_stack, client, tmp_path)
        _waiting(live_stack, run_id)

        async def expire(factory):
            async with factory() as session, session.begin():
                row = await session.get(RunRecord, run_id)
                row.deadline_at = datetime.now(UTC) - timedelta(seconds=1)
                return (
                    await session.scalars(select(ToolOperation).where(ToolOperation.run_id == run_id))
                ).one()

        op = _db(expire)
        _wait(lambda: _source(live_stack, run_id).status == "FAILED")
        gate.touch()

        async def task(factory):
            async with factory() as session:
                return await session.get(TaskExecution, op.task_id)

        stopped = _wait(lambda: row if (row := _db(task)).status in ("FAILED", "CANCELLED") else None)
        assert stopped.cancel_requested or stopped.error_code == "TASK_DEADLINE_EXCEEDED"

        async def late(factory):
            async with factory() as session:
                return list(
                    await session.scalars(select(ToolResultInbox).where(ToolResultInbox.run_id == run_id))
                )

        assert all(item.late for item in _wait(lambda: _db(late)))
        assert _source(live_stack, run_id).status == "FAILED"
        thread.join(timeout=5)


def test_e12_cleared_credential_has_no_fallback_or_new_surface_leak(live_stack, tmp_path, monkeypatch):
    with httpx.Client(timeout=30, trust_env=False) as client:
        run_id, gate, events, result, thread = _begin(live_stack, client, tmp_path)
        _waiting(live_stack, run_id)

        async def clear(factory):
            async with factory() as session, session.begin():
                model = await session.get(ModelDefinition, live_stack.model_id)
                value = model.api_key
                model.api_key = ""
                return value

        original = _db(clear)
        try:
            gate.touch()
            row = _wait(lambda: fresh if (fresh := _source(live_stack, run_id)).status == "FAILED" else None)
            assert row.error_code == "CREDENTIAL_MISSING"

            async def surfaces(factory):
                async with factory() as session:
                    values = []
                    for table in (
                        "tool_operation",
                        "tool_control_outbox",
                        "tool_result_inbox",
                        "run_continuation",
                        "canonical_event",
                        "runtime_snapshot",
                    ):
                        values.extend(
                            await session.scalars(
                                text(
                                f"SELECT row_to_json(t)::text FROM runtime.{table} t "
                                "WHERE tenant_id=:tenant"
                                ),
                                {"tenant": row.tenant_id},
                            )
                        )
                    return values

            assert original not in "\n".join(_db(surfaces))
            for entry in client.get(live_stack.llm_url + "/requests").json()["requests"]:
                assert original not in json.dumps(entry["body"])
            for path in live_stack.artifact_root.rglob("*"):
                if path.is_file() and path.suffix in (".log", ".bin", ".json"):
                    assert original.encode() not in path.read_bytes()
        finally:

            async def restore(factory):
                async with factory() as session, session.begin():
                    (await session.get(ModelDefinition, live_stack.model_id)).api_key = original

            _db(restore)
            thread.join(timeout=5)
