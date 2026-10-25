"""Webchat wire shapes. Owners authenticate, authorize and apply live input policy."""

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    AfterValidator,
    AwareDatetime,
    ConfigDict,
    Field,
    JsonValue,
    SecretStr,
    StrictBool,
    StrictStr,
    model_validator,
)

from .channel import MAX_PAGE_SIZE, AttachmentKind
from .enums import RunStatus
from .platform_settings import WebchatSettings, parse_platform_settings
from .tasks import ContractModel


def _non_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("text must not be blank")
    return value


def _non_blank_token(value: SecretStr) -> SecretStr:
    _non_blank(value.get_secret_value())
    return value


ChatText = Annotated[StrictStr, AfterValidator(_non_blank)]
ChatToken = Annotated[SecretStr, Field(strict=True), AfterValidator(_non_blank_token)]
ChatCount = Annotated[int, Field(strict=True, ge=0, le=(1 << 63) - 1)]
ChatPositiveCount = Annotated[int, Field(strict=True, ge=1, le=(1 << 63) - 1)]
ChatPageSize = Annotated[int, Field(strict=True, ge=1, le=MAX_PAGE_SIZE)]
ChatPositiveSeconds = Annotated[float, Field(strict=True, gt=0)]
ChatAuthMethod = Literal["USER_TOKEN"]
ConversationStatus = Literal["ACTIVE", "ARCHIVED"]
ActiveRunStatus = Literal["CREATED", "RUNNING", "WAITING_TOOL", "WAITING_INPUT"]
ExecutionStage = Literal[
    "WAITING_RUNTIME", "WAITING_MODEL", "RUNNING", "WAITING_TOOL", "WAITING_INPUT",
    "COMPLETED", "FAILED", "CANCELLED",
]


class WebchatContract(ContractModel):
    model_config = ConfigDict(frozen=True, hide_input_in_errors=True, allow_inf_nan=False)


class WebchatTokenExchange(WebchatContract):
    token: ChatToken = Field(exclude=True, repr=False)


class WebchatTokenMutation(WebchatContract):
    expected_revision: ChatCount


class WebchatTokenMetadata(WebchatContract):
    configured: StrictBool
    status: Literal["NOT_CONFIGURED", "ACTIVE", "EXPIRED", "REVOKED"]
    token_id: UUID | None
    revision: ChatCount
    issued_at: AwareDatetime | None
    expires_at: AwareDatetime | None

    @model_validator(mode="after")
    def _complete_metadata(self) -> Self:
        if not self.configured:
            if (self.status != "NOT_CONFIGURED" or self.revision != 0
                    or any(value is not None for value in (self.token_id, self.issued_at, self.expires_at))):
                raise ValueError("unconfigured token must have empty metadata and revision zero")
        elif (self.status == "NOT_CONFIGURED" or self.token_id is None or self.revision < 1
              or self.issued_at is None or self.expires_at is None):
            raise ValueError("configured token requires complete metadata and positive revision")
        elif self.expires_at <= self.issued_at:
            raise ValueError("expires_at must follow issued_at")
        return self


class WebchatTokenIssued(WebchatTokenMetadata):
    token: ChatToken = Field(exclude=True, repr=False)

    @model_validator(mode="after")
    def _active_issuance(self) -> Self:
        if self.status != "ACTIVE":
            raise ValueError("only an active issuance may return a token")
        return self

    def to_issuance_payload(self) -> dict[str, JsonValue]:
        """Explicit one-time response, never a metadata GET or persisted receipt."""
        payload: dict[str, JsonValue] = self.model_dump(mode="json")
        payload["token"] = self.token.get_secret_value()
        return payload


class ChatPrincipal(WebchatContract):
    """Trusted internal authentication result; never a browser request body."""

    tenant_id: ChatText
    platform_user_id: UUID
    session_id: UUID
    auth_method: ChatAuthMethod


class ChatPrincipalRequest(WebchatContract):
    """Internal identity gate input (design API-12): the presented CHAT session secret.

    The Gateway forwards the HttpOnly CHAT session value it received; the principal is
    derived from the PG session/token/user rows, so self-reported user_id/tenant are not
    part of the shape (extra fields are rejected before any lookup).
    """

    session_token: ChatToken = Field(exclude=True, repr=False)


class ChatAuthorizedAgent(WebchatContract):
    """An Agent the verified user may currently use; no instructions, model or grant leaks."""

    agent_id: UUID
    key: ChatText
    name: ChatText


class ChatPrincipalResponse(WebchatContract):
    """Verified principal + authoritative session deadline + currently authorized Agents.

    `principal.session_id` is the `auth_context_id` compared against `X-Chat-Context`
    (a mismatched context is rejected before this shape is produced), and `expires_at`
    lets a long-lived consumer stop at the session deadline instead of sliding it.
    """

    principal: ChatPrincipal
    expires_at: AwareDatetime
    agents: list[ChatAuthorizedAgent]


class WebchatSessionInfo(WebchatContract):
    tenant_id: ChatText
    user_id: UUID
    display_name: StrictStr
    session_id: UUID
    expires_at: AwareDatetime
    auth_context_id: UUID
    auth_method: ChatAuthMethod

    @model_validator(mode="after")
    def _session_context(self) -> Self:
        if self.auth_context_id != self.session_id:
            raise ValueError("auth_context_id must identify the CHAT session")
        return self


class WebchatEntry(WebchatContract):
    entry_id: UUID
    agent_id: UUID
    display_name: ChatText


class WebchatPageQuery(WebchatContract):
    page: ChatPositiveCount = 1
    page_size: ChatPageSize | None = None

    def effective_page_size(self, settings: WebchatSettings) -> int:
        return settings.history_page_size if self.page_size is None else self.page_size


class WebchatHistoryQuery(WebchatPageQuery):
    through_seq: ChatCount | None = None

    @model_validator(mode="after")
    def _frozen_watermark(self) -> Self:
        if self.page > 1 and self.through_seq is None:
            raise ValueError("through_seq is required for subsequent history pages")
        return self


class WebchatPage[ItemT](WebchatContract):
    items: list[ItemT]
    page: ChatPositiveCount
    page_size: ChatPageSize
    total: ChatCount

    @model_validator(mode="after")
    def _page_bounds(self) -> Self:
        if len(self.items) > self.page_size or len(self.items) > self.total:
            raise ValueError("items exceed page_size or total")
        return self


class WebchatEntryPage(WebchatPage[WebchatEntry]):
    pass


class WebchatMessageRequest(WebchatContract):
    conversation_id: UUID
    message_id: ChatText
    text: StrictStr = ""
    upload_ids: list[UUID] = Field(default_factory=list)

    @model_validator(mode="after")
    def _message_content(self) -> Self:
        if not self.text.strip() and not self.upload_ids:
            raise ValueError("message requires text or upload_ids")
        if len(set(self.upload_ids)) != len(self.upload_ids):
            raise ValueError("upload_ids must be distinct")
        return self


class WebchatFileMetadata(WebchatContract):
    kind: AttachmentKind
    media_type: ChatText
    size: ChatPositiveCount
    filename: ChatText | None = None
    checksum: Annotated[StrictStr, Field(pattern=r"^sha256:[0-9a-f]{64}$")]


class WebchatUpload(WebchatFileMetadata):
    upload_id: UUID
    expires_at: AwareDatetime


class WebchatUploadRegistration(WebchatContract):
    """Gateway → Runtime 的受信登记请求（API-12 内部面）：共享字节已写好，这里只登记权威元信息。

    身份三件套（`user_id`/`agent_id`/`entry_id`）由**受信调用方**显式携带——它们来自已验证的
    CHAT 主体与入口解析，不是浏览器请求体；`storage_key` 是 Gateway 生成的暂存相对键。
    词表/键形状/大小上限由 Runtime 的登记原语统一裁决（稳定错误码），本形状只保证身份与
    必需字段的存在性；`artifact_id`/`upload_id` 之类的预造引用没有字段位置（extra 一律拒绝）。
    """

    user_id: UUID
    agent_id: UUID
    entry_id: UUID
    storage_key: ChatText
    kind: AttachmentKind
    media_type: ChatText
    checksum: ChatText
    filename: ChatText | None = None
    size: ChatPositiveCount


class WebchatAttachment(WebchatFileMetadata):
    artifact_id: UUID
    available: StrictBool


class WebchatHistoryMessage(WebchatContract):
    event_id: UUID
    seq: ChatPositiveCount
    run_id: UUID
    role: Literal["USER", "ASSISTANT"]
    text: StrictStr
    attachments: list[WebchatAttachment] = Field(default_factory=list)


class WebchatMessagePage(WebchatPage[WebchatHistoryMessage]):
    through_seq: ChatCount

    @model_validator(mode="after")
    def _ordered_within_watermark(self) -> Self:
        previous = 0
        for item in self.items:
            if item.seq <= previous or item.seq > self.through_seq:
                raise ValueError("history seq must increase within through_seq")
            previous = item.seq
        return self


class WebchatExecutionStatus(WebchatContract):
    stage: ExecutionStage
    reason: ChatText | None = None
    queued_at: AwareDatetime | None = None
    deadline_at: AwareDatetime | None = None
    started_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def _queue_deadline(self) -> Self:
        waiting = self.stage in {"WAITING_RUNTIME", "WAITING_MODEL"}
        if waiting or self.queued_at is not None or self.deadline_at is not None:
            if self.queued_at is None or self.deadline_at is None:
                raise ValueError("queued_at and deadline_at must both be provided for queue waiting")
            if self.deadline_at <= self.queued_at:
                raise ValueError("deadline_at must follow queued_at")
        return self


class WebchatConfirmationOption(WebchatContract):
    value: ChatText
    label: ChatText


class WebchatConfirmation(WebchatContract):
    submission_id: UUID
    source_event_id: UUID
    prompt: ChatText
    options: Annotated[list[WebchatConfirmationOption], Field(min_length=1)]
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def _distinct_options(self) -> Self:
        values = [option.value for option in self.options]
        if len(set(values)) != len(values):
            raise ValueError("confirmation option values must be distinct")
        return self


class WebchatResumeRequest(WebchatContract):
    submission_id: UUID
    source_event_id: UUID
    message_id: ChatText
    selection: ChatText


class WebchatActiveRun(WebchatContract):
    id: UUID
    status: ActiveRunStatus
    execution_status: WebchatExecutionStatus
    confirmation: WebchatConfirmation | None = None


#: 受理响应头：Runtime 在 202/SSE 响应头里带回受理裁决与阶段投影，Gateway 不必再读首帧/重查 Run。
#: 值均为 ASCII（JSON ensure_ascii），可直接放 HTTP 头。
ADMISSION_RUN_ID_HEADER = "X-Muad-Run-Id"
ADMISSION_CONVERSATION_ID_HEADER = "X-Muad-Conversation-Id"
ADMISSION_RESUMED_HEADER = "X-Muad-Resumed"
ADMISSION_EXECUTION_STATUS_HEADER = "X-Muad-Execution-Status"


class WebchatMessageAccepted(WebchatContract):
    conversation_id: UUID
    run_id: UUID
    status: RunStatus
    execution_status: WebchatExecutionStatus


#: 取消回执的显示状态：`CANCELLING` 是取消请求已受理但尚未落地（Runtime `display_run_status`）。
RunDisplayStatus = Literal[
    "CREATED", "RUNNING", "WAITING_TOOL", "WAITING_INPUT",
    "COMPLETED", "FAILED", "CANCELLED", "CANCELLING",
]


class WebchatRunCancelResult(WebchatContract):
    """API-08 取消回执：原 Run 的真实显示状态 + 取消请求标记（Runtime `run_cancel_view` 投影）。"""

    run_id: UUID
    status: RunDisplayStatus
    cancel_requested: StrictBool


class WebchatResumeReceipt(WebchatContract):
    """API-08 恢复回执：原 Run 继续执行后的真实阶段（不伪造进度）。"""

    run_id: UUID
    submission_id: UUID
    status: RunStatus
    execution_status: WebchatExecutionStatus


class WebchatConversationScope(WebchatContract):
    """内部归属解析（API-09/15 的入口解析接线；design §3.4 公共路径不带入口）。

    只描述 Owner 对**本主体名下会话**的归属结论：网关据此走入口显式的读取端点，
    浏览器不参与这一解析、也不自报入口/Agent。
    """

    conversation_id: UUID
    entry_id: UUID
    agent_id: UUID


class WebchatConversationCreate(WebchatContract):
    """The entry is in the route; identity and the initial title are server-owned."""


class WebchatConversationCreated(WebchatContract):
    conversation_id: UUID
    entry_id: UUID
    title: StrictStr
    status: Literal["ACTIVE"]
    last_seq: Annotated[int, Field(strict=True, ge=0, le=0)]
    active_run: None


class WebchatConversationItem(WebchatContract):
    conversation_id: UUID
    title: StrictStr
    create_time: AwareDatetime
    last_message_at: AwareDatetime
    last_run_id: UUID | None
    status: ConversationStatus
    active_run_status: ActiveRunStatus | None


class WebchatConversationPage(WebchatPage[WebchatConversationItem]):
    pass


class WebchatConversationDetail(WebchatContract):
    conversation_id: UUID
    entry_id: UUID
    agent_id: UUID
    title: StrictStr
    status: ConversationStatus
    last_seq: ChatCount
    last_run_id: UUID | None
    active_run: WebchatActiveRun | None

    @model_validator(mode="after")
    def _active_run_identity(self) -> Self:
        if self.active_run is not None and self.last_run_id != self.active_run.id:
            raise ValueError("active_run must identify last_run_id")
        return self


class WebchatClientLimits(WebchatContract):
    history_page_size: ChatPageSize
    text_max_bytes: ChatPositiveCount
    upload_max_bytes: ChatPositiveCount
    upload_max_per_message: ChatPositiveCount
    notification_poll_sec: ChatPositiveSeconds

    @model_validator(mode="after")
    def _business_schema_bounds(self) -> Self:
        parse_platform_settings({"webchat": self.model_dump()})
        return self


class WebchatClientTransport(WebchatContract):
    sse_event_max_bytes: ChatPositiveCount
    sse_heartbeat_sec: ChatPositiveSeconds
    reconnect_base_ms: ChatPositiveCount
    reconnect_cap_ms: ChatPositiveCount
    reconnect_max_attempts: ChatPositiveCount

    @model_validator(mode="after")
    def _reconnect_schema_bounds(self) -> Self:
        parse_platform_settings({"webchat": {
            "reconnect_base_ms": self.reconnect_base_ms, "reconnect_cap_ms": self.reconnect_cap_ms,
            "reconnect_max_attempts": self.reconnect_max_attempts,
        }})
        return self


class WebchatClientConfig(WebchatContract):
    settings_revision: ChatCount
    transport_revision: ChatText
    webchat: WebchatClientLimits
    transport: WebchatClientTransport

    @classmethod
    def from_settings(
        cls, *, settings_revision: int, transport_revision: str, webchat: WebchatSettings,
        sse_event_max_bytes: int, sse_heartbeat_sec: float,
    ) -> Self:
        """Project only approved browser fields; transport revision is an opaque config fingerprint."""
        return cls(
            settings_revision=settings_revision, transport_revision=transport_revision,
            webchat=WebchatClientLimits(
                history_page_size=webchat.history_page_size, text_max_bytes=webchat.text_max_bytes,
                upload_max_bytes=webchat.upload_max_bytes,
                upload_max_per_message=webchat.upload_max_per_message,
                notification_poll_sec=webchat.notification_poll_sec,
            ),
            transport=WebchatClientTransport(
                sse_event_max_bytes=sse_event_max_bytes, sse_heartbeat_sec=sse_heartbeat_sec,
                reconnect_base_ms=webchat.reconnect_base_ms, reconnect_cap_ms=webchat.reconnect_cap_ms,
                reconnect_max_attempts=webchat.reconnect_max_attempts,
            ),
        )
