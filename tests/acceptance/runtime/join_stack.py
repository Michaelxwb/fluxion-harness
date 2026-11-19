"""Background-task acceptance stack: real services, Console authority and a gated ASYNC skill.

The Run-side ASYNC contract is DETACH-only (TASK-006): the Run returns an acceptance
receipt and the Worker executes the task independently. This fixture stays shared by the
runtime/gateway acceptance suites so the gated skill and model probe are built once.
"""

from __future__ import annotations

import pytest
from muad_console_platform.infrastructure.models.control import (
    ModelCredential,
    ModelDeployment,
    SkillArtifact,
)
from sqlalchemy import select

from tests.acceptance.runtime.test_submission_hardening import _db
from tests.acceptance.task_schedule.conftest import live_stack as schedule_stack
from tests.acceptance.task_schedule.environment import (
    ServiceProcess,
    build_skill_zip,
    free_port,
)


async def _member_credential(session, model_id):
    """该模型的成员凭据行：运行期的“实时认证值”在这里（逻辑模型已无密钥列）。"""
    return (
        await session.scalars(
            select(ModelCredential)
            .join(ModelDeployment, ModelDeployment.credential_id == ModelCredential.id)
            .where(
                ModelDeployment.model_id == model_id,
                ModelDeployment.is_deleted.is_(False),
            )
        )
    ).one()


@pytest.fixture(scope="module", name="join_stack")
def join_stack(tmp_path_factory: pytest.TempPathFactory):
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
                credential = await _member_credential(session, stack.model_id)
                credential.base_url = probe.url + "/v1"
                artifact = (
                    await session.scalars(
                        select(SkillArtifact).where(
                            SkillArtifact.skill_id == stack.skill_id,
                            # seed_control 同 skill 下另有一份 BATCH 制品，取常规（非 batch）那份。
                            SkillArtifact.id != stack.batch_artifact_id,
                        )
                    )
                ).one()
                artifact.storage_key = "skills/join-probe.zip"
                artifact.checksum = build_skill_zip(
                    stack.artifact_root,
                    artifact.storage_key,
                    body=(
                        "import json, sys, time, pathlib\n"
                        "p=json.loads(sys.stdin.read())\n"
                        "gate=pathlib.Path(p['gate'])\n"
                        "while not gate.exists(): time.sleep(.05)\n"
                        "print(json.dumps({'actual_job_result':p['index']}))\n"
                    ),
                )

        _db(update)
        try:
            yield stack
        finally:
            probe.stop()
            stack.llm_url = original_llm
