"""空回复不得落成"成功但没有正文"：终态前的不变量核验。"""

from uuid import uuid4

from muad_agent_runtime.application import run_service
from muad_agent_runtime.application.async_tools.supervisor import ExecutionSupervisor
from muad_agent_runtime.application.context_builder import DbBackedContextBuilder
from muad_agent_runtime.application.executor import ExecutorEvent
from muad_agent_runtime.application.ports import NullPlatformSettingsClient
from muad_agent_runtime.application.run_submission import (
    ENDPOINT_CREATE_RUN,
    RunSubmissionService,
)
from muad_agent_runtime.infrastructure.models.runtime import CanonicalEvent, RunRecord
from muad_contracts import RunStatus
from sqlalchemy import select

from tests.async_tool_continuations import running_source


async def _stream_empty_outcome(async_tool_database, monkeypatch, fake_resolve):
    factory, context, _, run, identity = await running_source(async_tool_database)
    monkeypatch.setattr(run_service, "get_session_factory", lambda: factory)

    class EmptyReplyExecutor:
        """绕开 Runner 直接给出 COMPLETED + 空正文：模拟 resume 等旁路。"""

        async def run(self):
            yield ExecutorEvent(
                type="runner.outcome", data={"status": "COMPLETED", "final_text": ""}
            )

    async def executor_factory(request):
        return EmptyReplyExecutor()

    supervisor = ExecutionSupervisor()
    async with factory() as session:
        run = await session.get(RunRecord, run.id)
        submission = await RunSubmissionService(lambda: factory).record_in(
            session,
            tenant_id=run.tenant_id,
            idempotency_key=str(uuid4()),
            endpoint=ENDPOINT_CREATE_RUN,
            actor_user_id=run.user_id,
            run_id=run.id,
            conversation_id=run.conversation_id,
            request_fingerprint="sha256:" + "d" * 64,
        )
        await session.commit()
        service = run_service.RunService(
            session,
            fake_resolve,
            identity.owner,
            NullPlatformSettingsClient(),
            supervisor=supervisor,
            executor_factory=executor_factory,
            context_builder=DbBackedContextBuilder(session_factory=lambda: factory),
        )
        events = [
            event
            async for event in service._stream_run(
                run=run,
                agent=context.agent,
                model=context.model,
                skills=(),
                mcp_servers=(),
                mcp_secrets={},
                history=(),
                submission_id=submission.id,
                resumed=True,
            )
        ]
    await supervisor.close()
    return factory, run, events


async def test_completed_run_without_visible_text_fails_and_stores_no_empty_message(
    async_tool_database, monkeypatch, fake_resolve
):
    factory, run, events = await _stream_empty_outcome(
        async_tool_database, monkeypatch, fake_resolve
    )

    assert events[-1].type == "run.failed"
    assert events[-1].data["error_code"] == "MODEL_EMPTY_REPLY"
    async with factory() as session:
        saved = await session.get(RunRecord, run.id)
        assert saved.status == RunStatus.FAILED and saved.error_code == "MODEL_EMPTY_REPLY"
        assert (
            await session.scalar(
                select(CanonicalEvent.id).where(
                    CanonicalEvent.run_id == run.id,
                    CanonicalEvent.event_type == "ASSISTANT_MESSAGE",
                )
            )
            is None
        ), "空的成功回答不得进历史"
