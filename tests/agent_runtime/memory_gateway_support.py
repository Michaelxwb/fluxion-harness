"""Real Gateway routing/rendering against the Runtime socket and PG fixture."""

from dataclasses import dataclass, field
from uuid import uuid4

import sqlalchemy as sa
from muad_contracts import ChannelContext, ChannelEnvelope, ChannelResolveResponse, MessageInput, RunRequest
from muad_im_gateway.application.inbound import InboundPipeline
from muad_im_gateway.channels.fake import FakeChannelAdapter
from muad_im_gateway.infrastructure.dedupe import NullDedupeStore
from muad_im_gateway.main import app

from tests.gateway.fakes import FakeConsoleClient


@dataclass
class Reply:
    texts: list[str] = field(default_factory=list)
    chunks: list[str] = field(default_factory=list)
    finished: int = 0
    closed: bool = False

    def bind_run(self, run_id):
        self.run_id = run_id

    async def update_status(self, text):
        pass

    async def stream(self, text):
        self.chunks.append(text)

    async def send(self, text):
        self.texts.append(text)

    async def reply_once(self, text):
        self.texts.append(text)

    async def finish(self):
        if not self.closed:
            self.closed = True
            self.finished += 1


class Adapter(FakeChannelAdapter):
    def __init__(self):
        super().__init__()
        self.replies = {}

    def open_reply(self, route, message_id):
        reply = Reply()
        self.replies[message_id] = reply
        return reply


def envelope(text, *, message_id=None):
    return ChannelEnvelope(
        channel="WECOM",
        bot_id="fixture",
        external_user_id="fixture-user",
        external_conversation_id="fixture",
        message_id=message_id or uuid4().hex,
        text=text,
    )


def pipeline(stack):
    console = FakeConsoleClient()
    console.resolve_response = ChannelResolveResponse(
        bound=True, authorized=True, agent_id=stack.run.agent_id, platform_user_id=stack.run.user_id
    )
    return InboundPipeline(
        console=console,
        runtime=stack.gateway,
        dedupe=NullDedupeStore(),
        catalog=app.state.message_catalog,
        tenant_id=stack.run.tenant_id,
        delta_flush_interval_sec=0,
    )


async def original_submission(stack):
    """Bind the seeded original submission to its actual Gateway wire fingerprint for replay."""
    from muad_agent_runtime.application.run_service import _creation_fingerprint
    from muad_agent_runtime.application.run_submission import ENDPOINT_CREATE_RUN
    from muad_agent_runtime.infrastructure.models.runtime import RunSubmission

    incoming = envelope("记住蓝色")
    request = RunRequest(
        agent_id=stack.run.agent_id,
        platform_user_id=stack.run.user_id,
        channel=ChannelContext(
            type=incoming.channel,
            bot_id=incoming.bot_id,
            external_user_id=incoming.external_user_id,
            external_conversation_id=incoming.external_conversation_id,
        ),
        message=MessageInput(id=incoming.message_id, text=incoming.text),
    )
    async with stack.factory.begin() as session:
        row = await session.scalar(sa.select(RunSubmission).where(RunSubmission.run_id == stack.run.id))
        row.idempotency_key = incoming.message_id
        row.endpoint = ENDPOINT_CREATE_RUN
        row.request_fingerprint = _creation_fingerprint(request, stack.run.tenant_id)
    return incoming
