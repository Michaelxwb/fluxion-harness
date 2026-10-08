"""Real Run sources for operation reservation tests; no pre-existing operation."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from muad_agent_runtime.application.task_client import TaskSubmissionContext
from muad_agent_runtime.infrastructure.models.runtime import Conversation, RunRecord, RuntimeSnapshot
from muad_contracts import ResolvedAgent, ResolvedModel, ResolvedSkill, RunStatus, SkillExecutionMode


async def seed_submission(factory, *, pending_limit=1, resolved=None, tenant=None, actor=None, run_id=None):
    agent = (
        resolved.agent
        if resolved
        else ResolvedAgent(id=uuid4(), key="agent", revision=1, instructions="", runtime_config={})
    )
    model = (
        resolved.model
        if resolved
        else ResolvedModel(
            id=uuid4(), revision=1, model_id="probe", base_url="http://model.invalid", params={}
        )
    )
    skill = (
        resolved.skills[0]
        if resolved
        else ResolvedSkill(
            skill_id=uuid4(),
            artifact_id=uuid4(),
            key="async",
            name="Async",
            description="",
            version="1",
            checksum="sha256:" + "a" * 64,
            storage_key="skills/async.zip",
            execution_mode=SkillExecutionMode.ASYNC,
        )
    )
    tenant, actor = tenant or str(uuid4()), actor or uuid4()
    async with factory() as session, session.begin():
        conversation = Conversation(tenant_id=tenant, user_id=actor, agent_id=agent.id)
        session.add(conversation)
        await session.flush()
        run_id = run_id or uuid4()
        snapshot = RuntimeSnapshot(
            run_id=run_id,
            agent_revision=agent.revision,
            model_revision=model.revision,
            tenant_id=tenant,
            agent_json=agent.model_dump(mode="json"),
            model_json=model.model_dump(mode="json", exclude={"api_key"}),
            skill_catalog_json=[row.model_dump(mode="json") for row in resolved.skills]
            if resolved
            else [skill.model_dump(mode="json")],
            mcp_catalog_json=[],
            policy_json={"async_tools": {"pending_operation_limit": pending_limit}},
            prompt_template_version="1",
            schema_version=1,
            content_hash="sha256:" + "b" * 64,
        )
        session.add(snapshot)
        await session.flush()
        run = RunRecord(
            id=run_id,
            tenant_id=tenant,
            conversation_id=conversation.id,
            user_id=actor,
            agent_id=agent.id,
            snapshot_id=snapshot.id,
            status=RunStatus.RUNNING,
            input_text="async",
            trace_id="submission-test",
            deadline_at=datetime.now(UTC) + timedelta(minutes=10),
        )
        session.add(run)
        await session.flush()
    context = TaskSubmissionContext(
        tenant_id=tenant,
        actor_user_id=actor,
        agent=agent,
        model=model,
        skills=(skill,),
        source_run_id=run.id,
    )
    return context, skill, run
