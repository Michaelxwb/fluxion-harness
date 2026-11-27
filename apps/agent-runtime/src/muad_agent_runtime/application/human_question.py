"""Persist a normal user question at the existing complete-round boundary."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID, uuid4

from muad_agent_core.agent.continuation import RunnerCheckpoint
from muad_agent_core.agent.suspension import SuspensionRequest
from muad_agent_core.tools import ToolCall, ToolDefinition, ToolEffect, ToolError, ToolResult
from muad_contracts import RunStatus
from muad_contracts.webchat import WebchatConfirmation, WebchatConfirmationOption
from muad_logging.redaction import redact_text
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.db import SessionFactory
from ..infrastructure.models.runtime import CanonicalEvent, RunInterrupt, RunRecord
from .async_tools.continuation_service import ExecutionIdentity, _write_checkpoint
from .checkpoint import _notify_waiting_confirmation
from .execution_status import project_execution_status
from .memory_confirmation import MemoryConfirmationService
from .memory_proposals import locked_confirmation_execution
from .run_events import EventWriter


class HumanQuestion:
    def __init__(
        self,
        factory: SessionFactory,
        identity: ExecutionIdentity,
        submission_id: UUID,
        memory: MemoryConfirmationService | None,
    ) -> None:
        self._factory = factory
        self._identity = identity
        self._submission = submission_id
        self._memory = memory
        self._question: SuspensionRequest | None = None
        #: 模型自带的候选项（`[{id, label}]`）；非空时落库为 `options_json` 并投影成确认卡片。
        #: 默认只允许选择其中之一（严格匹配 id）；`allow_custom` 为真时也接受选项外的自定义文本。
        self._options: list[dict[str, str]] = []
        self._allow_custom: bool = False
        self._multi_select: bool = False

    @property
    def suspend_request(self) -> SuspensionRequest | None:
        if self._memory is not None and self._memory.suspend_request is not None:
            return self._memory.suspend_request
        return self._question

    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="ask_user",
            description=(
                "Pause and ask the user for information required to continue. Provide `options` "
                "(id + label) when the user should pick one of a few choices; omit `options` for a "
                "free-form typed answer. When `options` are given the user may only pick one of them "
                "unless you also set `allow_custom` to true — for yes/no or approve/reject questions "
                "keep `allow_custom` false so the answer stays within the offered choices. Set "
                "`multi_select` true when the user may pick several options at once (submitted "
                "newline-separated). This channel does not accept an `input_schema`."
            ),
            input_schema={
                "type": "object",
                "required": ["question"],
                "additionalProperties": False,
                "properties": {
                    "question": {"type": "string", "minLength": 1, "maxLength": 1500},
                    "options": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 10,
                        "items": {
                            "type": "object",
                            "required": ["id", "label"],
                            "additionalProperties": False,
                            "properties": {
                                "id": {"type": "string", "minLength": 1, "maxLength": 64},
                                "label": {"type": "string", "minLength": 1, "maxLength": 200},
                            },
                        },
                    },
                    "allow_custom": {"type": "boolean", "default": False},
                    "multi_select": {"type": "boolean", "default": False},
                },
            },
            effect=ToolEffect.READ,
            handler=self.ask,
        )

    async def ask(self, arguments: Mapping[str, Any], *, call: ToolCall) -> ToolResult:
        question = arguments.get("question")
        if not isinstance(question, str) or not question.strip() or len(question) > 1500:
            raise ToolError("COMMON_VALIDATION_ERROR", "Provide a short question.")
        options = _parse_options(arguments.get("options"))
        raw_allow_custom = arguments.get("allow_custom", False)
        if not isinstance(raw_allow_custom, bool):
            raise ToolError("COMMON_VALIDATION_ERROR", "allow_custom must be a boolean")
        raw_multi_select = arguments.get("multi_select", False)
        if not isinstance(raw_multi_select, bool):
            raise ToolError("COMMON_VALIDATION_ERROR", "multi_select must be a boolean")
        if self.suspend_request is not None:
            raise ToolError("COMMON_CONFLICT", "A request for user input is already staged.")
        self._options = options
        # allow_custom / multi_select 只有在给了选项时才有意义；无选项本就是自由文本。
        self._allow_custom = bool(raw_allow_custom) and bool(options)
        self._multi_select = bool(raw_multi_select) and bool(options)
        self._question = SuspensionRequest(uuid4(), "HUMAN_INPUT", redact_text(question.strip()))
        return ToolResult('{"waiting_for_user":true}')

    async def suspend(self, progress: RunnerCheckpoint) -> None:
        if self._memory is not None and self._memory.suspend_request is not None:
            await self._memory.suspend(progress)
            return
        request = self._question
        if request is None:
            raise RuntimeError("question was not staged")
        async with self._factory.begin() as session:
            run, saved, now = await locked_confirmation_execution(session, self._identity)
            progress_row = await _write_checkpoint(session, run, saved, progress, now)
            # 模型自带候选优先；没有再回落到上游技能结果的 `customer_options`。两种来源统一成
            # `[{"id", "name"}]`：`resolve_generic_interrupts` 按 `id` 严格匹配、`_question_with_options`
            # 按 `name` 排版，落库结构必须同时满足两者。
            options = (
                [{"id": item["id"], "name": item["label"]} for item in self._options]
                if self._options
                else await _customer_options(session, run, self._submission)
            )
            prompt = _question_with_options(request.prompt, options)
            session.add(
                RunInterrupt(
                    id=request.interrupt_id,
                    tenant_id=run.tenant_id,
                    run_id=run.id,
                    conversation_id=run.conversation_id,
                    interrupt_type=request.reason,
                    prompt_text=prompt,
                    status="WAITING",
                    options_json=options,
                    # HUMAN_INPUT 不用 proposal；借该列存 allow_custom / multi_select，
                    # 零迁移且不污染 options 结构。
                    proposal_json={
                        "allow_custom": self._allow_custom,
                        "multi_select": self._multi_select,
                    },
                    expires_at=run.deadline_at,
                )
            )
            run.status, run.lease_owner, run.lease_until, run.update_time = (
                RunStatus.WAITING_INPUT,
                None,
                None,
                now,
            )
            writer = EventWriter(session)
            event_id = uuid4()
            expires_at = run.deadline_at
            payload: dict[str, Any]
            if expires_at is not None:
                # 每次挂起都出「确认卡 / 输入卡」：写 `INTERRUPT_REQUIRED`（→ webchat
                # `confirmation.required`），payload 同时给文本渠道用的 `text_prompt` 和 webchat 用的
                # 完整 `confirmation` DTO。无候选选项时是纯自由文本问询（`free_text=True`、`options=[]`），
                # 前端渲染成一个输入框。只写纯文本消息会让前端停在 WAITING_INPUT 却无处回答。
                free_text = not options
                payload = {
                    "text_prompt": prompt,
                    "prompt": request.prompt,
                    "options": [item["name"] for item in options],
                    "confirmation": WebchatConfirmation(
                        submission_id=request.interrupt_id,
                        source_event_id=request.interrupt_id,
                        prompt=prompt,
                        options=[
                            WebchatConfirmationOption(value=item["id"], label=item["name"])
                            for item in options
                        ],
                        expires_at=expires_at,
                        allow_custom=self._allow_custom or free_text,
                        multi_select=self._multi_select,
                        free_text=free_text,
                    ).model_dump(mode="json"),
                }
                event_type, stream_type = "INTERRUPT_REQUIRED", "interrupt.required"
            else:
                payload = {"text": prompt, "reply_generation": 0}
                event_type, stream_type = "ASSISTANT_MESSAGE", "message.snapshot"
            progress_row.context_upto_seq = await writer.append(
                tenant_id=run.tenant_id,
                conversation_id=run.conversation_id,
                run_id=run.id,
                submission_id=self._submission,
                event_type=event_type,
                payload=payload,
                event_id=event_id,
                stream_type=stream_type,
            )
            await writer.append_run_stage(
                tenant_id=run.tenant_id,
                conversation_id=run.conversation_id,
                run_id=run.id,
                status=project_execution_status(run),
            )
            await _notify_waiting_confirmation(
                session, run=run, interrupt_id=request.interrupt_id, event_id=event_id
            )


def _parse_options(raw: Any) -> list[dict[str, str]]:
    """校验并规范化模型传入的候选：`[{id, label}]` → `[{id, label}]`，id 必须唯一。

    非法结构一律显式失败（不静默丢弃），否则用户会看到一张点不了的卡片。
    """
    if raw is None:
        return []
    if not isinstance(raw, list) or not raw:
        raise ToolError("COMMON_VALIDATION_ERROR", "options must be a non-empty array")
    if len(raw) > 10:
        raise ToolError("COMMON_VALIDATION_ERROR", "at most 10 options are allowed")
    parsed: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, Mapping):
            raise ToolError("COMMON_VALIDATION_ERROR", "each option must be an object")
        option_id = item.get("id")
        label = item.get("label")
        if not isinstance(option_id, str) or not option_id.strip() or len(option_id) > 64:
            raise ToolError("COMMON_VALIDATION_ERROR", "each option needs a short id")
        if not isinstance(label, str) or not label.strip() or len(label) > 200:
            raise ToolError("COMMON_VALIDATION_ERROR", "each option needs a short label")
        if option_id in seen:
            raise ToolError("COMMON_VALIDATION_ERROR", "option ids must be distinct")
        seen.add(option_id)
        parsed.append({"id": option_id.strip(), "label": label.strip()})
    return parsed


async def _customer_options(
    session: AsyncSession, run: RunRecord, submission_id: UUID
) -> list[dict[str, str]]:
    event = CanonicalEvent
    value = await session.scalar(
        select(event.payload_json["outcome"]["customer_options"])
        .where(
            event.tenant_id == run.tenant_id,
            event.run_id == run.id,
            event.submission_id == submission_id,
            event.event_type == "TOOL_CALL",
            event.is_deleted.is_(False),
            event.payload_json["outcome"]["business_status"].as_string() == "NEEDS_INPUT",
        )
        .order_by(event.seq.desc())
        .limit(1)
    )
    return value if isinstance(value, list) else []


def _question_with_options(prompt: str, options: list[dict[str, str]]) -> str:
    if not options:
        return prompt
    return prompt + "\n\n" + "\n".join(f"- {item['name']} ({item['id']})" for item in options)
