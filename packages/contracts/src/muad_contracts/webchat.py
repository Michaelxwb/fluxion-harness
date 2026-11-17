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

from .attachments import SUPPORTED_UPLOAD_FORMATS, AttachmentFormat
from .channel import MAX_PAGE_SIZE, AttachmentKind
from .enums import DraftKind, DraftStatus, RunStatus, ScheduleMode
from .platform_settings import WebchatSettings, parse_platform_settings
from .scheduling import SchedulePreview
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
#: 认证方式：`USER_TOKEN` 是我方 WebChat 会话令牌；`EXTERNAL_ASSERTION` 是外部平台 JWT
#: （如 MSSWWEB 的 soc-token）验签后的主体。两者产出的 `ChatPrincipal` 结构一致。
ChatAuthMethod = Literal["USER_TOKEN", "EXTERNAL_ASSERTION"]
#: 外部断言未通过授权时的原因（`allowed=false` 时给出，便于 BFF 区分「查无此人」与「有此人不许进」）。
ExternalDenialReason = Literal[
    "UNBOUND",  # 验签通过，但无 external_identity 记录（不伪造主体）
    "NO_ENTRY_GRANT",  # 身份存在，但当前入口无授权（主体照常返回）
    "USER_DISABLED",  # 用户已停用
    "NO_AGENT_GRANT",  # 入口下无可用 Agent（入口未绑 Agent 或 Agent 已被删除）
    "AGENT_DISABLED",  # 入口绑定的 Agent 被停用（`AgentDefinition.enabled=false`）
]
ConversationStatus = Literal["ACTIVE", "ARCHIVED"]
ActiveRunStatus = Literal["CREATED", "RUNNING", "WAITING_TOOL", "WAITING_INPUT"]
ExecutionStage = Literal[
    "WAITING_RUNTIME",
    "WAITING_MODEL",
    "RUNNING",
    "WAITING_TOOL",
    "WAITING_INPUT",
    "COMPLETED",
    "FAILED",
    "CANCELLED",
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
            if (
                self.status != "NOT_CONFIGURED"
                or self.revision != 0
                or any(value is not None for value in (self.token_id, self.issued_at, self.expires_at))
            ):
                raise ValueError("unconfigured token must have empty metadata and revision zero")
        elif (
            self.status == "NOT_CONFIGURED"
            or self.token_id is None
            or self.revision < 1
            or self.issued_at is None
            or self.expires_at is None
        ):
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
    """入口项（API-03）。

    与 `ExternalIdentityGrant` 的区别：这里只服务 CHAT 会话面（`USER_TOKEN` 与
    `EXTERNAL_ASSERTION` 共用），`allowed` / `reason` 只有在断言面才有判定来源——
    WebChat 面的 `reason` 恒为 `None`，`allowed` 恒为 `True`（该面没有「入口级不可用」概念，
    不可用入口直接不出现）。
    """

    entry_id: UUID
    agent_id: UUID
    display_name: ChatText
    #: 该入口当前是否可用。断言面上「已授权但机器人停用」会以 `allowed=False` 出现，
    #: 供 BFF 展示但置灰（见 BFF 对接文档 §4.3）。
    allowed: StrictBool = True
    reason: ExternalDenialReason | None = None

    @model_validator(mode="after")
    def _denial_shape(self) -> Self:
        if self.allowed and self.reason is not None:
            raise ValueError("an allowed entry must not carry a denial reason")
        if not self.allowed and self.reason is None:
            raise ValueError("a denied entry requires a denial reason")
        return self


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


class WebchatScheduleDraftReady(WebchatContract):
    """定时任务 Draft 的确认卡片事实（design §3.2.8 / §3.5.10）。

    它**不是** Run 的 `confirmation.required`：后者绑定同一次 Run 的 interrupt + resume，
    确认的是「这个 Run 是否继续」；这里是一个跨 Run 存在的待确认配置，确认走 surface 的
    确认端点（API-31 → API-27），与 Run 生命周期无关。两者共用一个词表会让客户端调错端点。
    载荷只含卡片要展示的事实：身份/hash/模式/脱敏预览/过期时刻，没有 Secret 位置。
    """

    draft_id: UUID
    draft_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    kind: DraftKind
    mode: ScheduleMode
    preview: SchedulePreview
    expires_at: AwareDatetime


class WebchatHistoryMessage(WebchatContract):
    event_id: UUID
    seq: ChatPositiveCount
    run_id: UUID
    role: Literal["USER", "ASSISTANT"]
    text: StrictStr
    attachments: list[WebchatAttachment] = Field(default_factory=list)
    #: 定时任务卡片：Draft 事件不是普通消息，但它必须能以同一条持久事实回到历史里
    #: （刷新后仍可确认），因此挂在助手条目上而不是另造一种历史条目。只有卡片条目带这个键，
    #: 普通消息的线格式不因此多一个 `draft: null`。
    draft: WebchatScheduleDraftReady | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    #: 卡片的确认状态：历史投影按跨 Owner Schema 只读聚合从 `task.task_schedule_draft.status`
    #: 读出，刷新后直接据它渲染（不需要客户端再单独查一次）。缺省表示状态不可知——
    #: 客户端必须按不可确认处理，不得当成 PENDING。
    draft_status: DraftStatus | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    #: canonical 事件落库时间（消息发送/接收的权威时刻，页面只展示不推算）。
    created_at: AwareDatetime


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
ADMISSION_RUN_STATUS_HEADER = "X-Muad-Run-Status"


class WebchatMessageAccepted(WebchatContract):
    conversation_id: UUID
    run_id: UUID
    status: RunStatus
    execution_status: WebchatExecutionStatus


#: 取消回执的显示状态：`CANCELLING` 是取消请求已受理但尚未落地（Runtime `display_run_status`）。
RunDisplayStatus = Literal[
    "CREATED",
    "RUNNING",
    "WAITING_TOOL",
    "WAITING_INPUT",
    "COMPLETED",
    "FAILED",
    "CANCELLED",
    "CANCELLING",
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


class WebchatConversationDeletion(WebchatContract):
    """会话删除回执：`deleted=True` 表示该会话当前已达成删除（重复删除同形）。"""

    conversation_id: UUID
    deleted: Literal[True] = True


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
    #: 会话级未读红点：该会话存在 `read_at IS NULL` 的通知。按会话聚合，不做消息级。
    has_unread: StrictBool = False
    #: 会话来源入口（同一 user + Agent 的会话可来自多个入口；列表不按入口过滤）。
    entry_id: UUID | None = None


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
    #: 未读与水位：三者来自**同一快照**，消费者不得从一个推另一个。
    #: `last_seq` 是 canonical 消息序号（历史分页 `through_seq` 与 SSE `after_seq` 共用）；
    #: `notification_through_seq` 是通知序号（已读水位用），与 canonical/SSE 序号互相独立。
    has_unread: StrictBool = False
    notification_through_seq: ChatCount = 0

    @model_validator(mode="after")
    def _active_run_identity(self) -> Self:
        if self.active_run is not None and self.last_run_id != self.active_run.id:
            raise ValueError("active_run must identify last_run_id")
        return self


class WebchatMarkReadRequest(WebchatContract):
    """已读上报：只标记通知序号不高于 `notification_through_seq` 的通知。

    超前水位（大于当前最大通知序号）返回 `CURSOR_AHEAD`，不做部分标记。
    """

    notification_through_seq: ChatCount


class WebchatMarkReadReceipt(WebchatContract):
    conversation_id: UUID
    notification_through_seq: ChatCount
    has_unread: StrictBool


#: `upload_formats`（媒体类型 + 展示扩展名）来自 `muad_contracts.attachments` 的唯一事实，
#: 与门控同一份清单；浏览器据此展示提示并过滤文件选择器，不在前端另存一份。
class WebchatClientLimits(WebchatContract):
    history_page_size: ChatPageSize
    text_max_bytes: ChatPositiveCount
    upload_max_bytes: ChatPositiveCount
    upload_max_per_message: ChatPositiveCount
    notification_poll_sec: ChatPositiveSeconds
    upload_formats: tuple[AttachmentFormat, ...]

    @model_validator(mode="after")
    def _business_schema_bounds(self) -> Self:
        # `upload_formats` 是代码常量投影、不是平台设置字段：只把设置字段交给 schema 复核。
        parse_platform_settings({"webchat": self.model_dump(exclude={"upload_formats"})})
        return self


class WebchatClientTransport(WebchatContract):
    sse_event_max_bytes: ChatPositiveCount
    sse_heartbeat_sec: ChatPositiveSeconds
    reconnect_base_ms: ChatPositiveCount
    reconnect_cap_ms: ChatPositiveCount
    reconnect_max_attempts: ChatPositiveCount

    @model_validator(mode="after")
    def _reconnect_schema_bounds(self) -> Self:
        parse_platform_settings(
            {
                "webchat": {
                    "reconnect_base_ms": self.reconnect_base_ms,
                    "reconnect_cap_ms": self.reconnect_cap_ms,
                    "reconnect_max_attempts": self.reconnect_max_attempts,
                }
            }
        )
        return self


# ---------------------------------------------------------------------------
# 外部平台身份契约（Console 管理面 + Gateway 内部面）
# ---------------------------------------------------------------------------


class ExternalPrincipalRequest(WebchatContract):
    """Gateway → Console 内部身份门（对应 `POST /internal/channel/external-principal`）。

    网关转发它从 Cookie 拿到的原始外部断言（如 MSSW 的 `soc-token`）；`platform_key`
    与服务端配置的因子共同决定验签方式。主体自报的 tenant/user 不在形状内。
    """

    platform_key: ChatText
    channel: ChatText
    assertion: ChatToken = Field(exclude=True, repr=False)
    entry_id: UUID | None = None


class ExternalIdentityGrant(WebchatContract):
    """逐入口授权视图（API-01），同时是 `/identity` 的 `grants[]` 与 `/entries` 的判定来源。

    设计要点：**在列表里但 `allowed=false`** 与 **不在列表里** 语义不同——后者表示该入口从未
    授权给此人，前者表示授权存在但当前不可用（`AGENT_DISABLED` 等）。BFF 靠这个区别决定
    「不展示入口」还是「展示但置灰」。
    """

    entry_id: UUID
    agent_id: UUID
    entry_name: ChatText
    channel: ChatText
    allowed: StrictBool
    reason: ExternalDenialReason | None = None

    @model_validator(mode="after")
    def _denial_shape(self) -> Self:
        if self.allowed and self.reason is not None:
            raise ValueError("an allowed grant must not carry a denial reason")
        if not self.allowed and self.reason is None:
            raise ValueError("a denied grant requires a denial reason")
        return self


class ExternalPrincipalResult(WebchatContract):
    """验签结果。`allowed=false` 时 `principal` 可能为空（UNBOUND）或非空（NO_ENTRY_GRANT）。

    只有 `allowed=true` 才会被 Gateway 当作已认证主体；`reason` 便于 BFF 区分拒绝类别。
    """

    allowed: StrictBool
    principal: ChatPrincipal | None = None
    reason: ExternalDenialReason | None = None
    #: 该主体在**当前入口**下可用的 Agent（空列表表示无授权）。
    agents: list[ChatAuthorizedAgent] = Field(default_factory=list)
    #: 逐入口裁决：认证一次即可同时供 `/entries` 判定「哪些入口可用」，无需二次查询。
    #: 与 `agents` 视角不同——`agents` 按 Agent 去重，这里每个入口一行。
    grants: list[ExternalIdentityGrant] = Field(default_factory=list)
    #: 断言会话的权威到期时刻（allowed=true 时必填）：网关据此给长流设最长时限，到点即停。
    #: 外部断言无 `exp` 时取 `iat + 最大会话年龄`。
    expires_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def _denial_shape(self) -> Self:
        if self.allowed:
            if self.principal is None or self.reason is not None:
                raise ValueError("an allowed result requires a principal and no denial reason")
            if self.expires_at is None:
                raise ValueError("an allowed result requires an expires_at")
        elif self.reason is None:
            raise ValueError("a denied result requires a denial reason")
        elif self.reason == "UNBOUND" and self.principal is not None:
            raise ValueError("an unbound result must not fabricate a principal")
        return self


class ExternalIdentityQuery(WebchatContract):
    """内部调用：`POST /internal/channel/external-identity`（API-01 的 Console 侧输入）。

    与 `ExternalPrincipalRequest` 分开而不是给它加开关：`external-principal` 是 Gateway
    的每次请求热路径，它的契约面越小越好；`/identity` 是独立查询，输入还多一个
    「问哪个入口」。
    """

    platform_key: ChatText
    channel: ChatText
    assertion: ChatToken = Field(exclude=True, repr=False)
    entry_id: UUID | None = None


class ExternalIdentityView(WebchatContract):
    """`GET /api/v1/msswwweb/identity` 的响应（API-01）：外部平台据此判断「此人能否用机器人」。

    与 `/entries` 的分工：`/entries` 是**资源接口**（只列可用入口，拒绝走 401/403）；
    `/identity` 是**权限查询**（未通过是正常结果，恒 200）——所以这里用 `allowed` + `reason`
    表达，而不是靠 HTTP 状态码。

    `authenticated` 只表达「验签通过」；符号层面的问题（签名/算法/因子/时效）在 `/identity`
    上仍走 401，那属于「无效请求」，不是「权限查询的结果」。
    """

    authenticated: StrictBool
    platform_user_id: UUID | None = None
    external_identity_id: UUID | None = None
    platform_key: ChatText
    allowed: StrictBool
    reason: ExternalDenialReason | None = None
    grants: list[ExternalIdentityGrant] = Field(default_factory=list)
    #: 权限版本号：授权/身份/用户状态变更时递增，供 BFF 判断自己的缓存是否过期。
    auth_revision: int | None = None

    @model_validator(mode="after")
    def _denial_shape(self) -> Self:
        if not self.authenticated:
            raise ValueError("an unauthenticated view must not be served (use 401)")
        if self.allowed and self.reason is not None:
            raise ValueError("an allowed view must not carry a denial reason")
        if not self.allowed and self.reason is None:
            raise ValueError("a denied view requires a denial reason")
        if self.reason == "UNBOUND" and self.platform_user_id is not None:
            raise ValueError("an unbound view must not fabricate a platform user")
        return self


class ExternalIdentityKeys(WebchatContract):
    """身份键字段集合：必须与平台配置的 `identity_keys` 完全一致（不多不少）。"""

    identity: dict[ChatText, ChatText]

    @model_validator(mode="after")
    def _non_empty(self) -> Self:
        if not self.identity:
            raise ValueError("identity must contain at least one key")
        if any(not value.strip() for value in self.identity.values()):
            raise ValueError("identity values must not be blank")
        return self


class ExternalIdentityUpsert(WebchatContract):
    platform_key: ChatText
    identity: dict[ChatText, ChatText]


class ExternalEntryGrantRequest(WebchatContract):
    channel_account_id: UUID


class ExternalEntryGrantItem(WebchatContract):
    entry_id: UUID
    bot_id: UUID
    name: ChatText


class ExternalIdentityItem(WebchatContract):
    identity_id: UUID
    platform_key: ChatText
    identity: dict[ChatText, ChatText]
    status: Literal["ACTIVE", "DISABLED"]
    grants: list[ExternalEntryGrantItem] = Field(default_factory=list)


class ExternalPlatformStatus(WebchatContract):
    """平台元数据投影：只暴露管理员需要看的字段，**不含任何验签因子**。"""

    platform_key: ChatText
    display_name: ChatText
    configured: StrictBool
    identity_keys: tuple[ChatText, ...]
    identity_field_labels: dict[ChatText, ChatText] = Field(default_factory=dict)


class WebchatClientConfig(WebchatContract):
    settings_revision: ChatCount
    transport_revision: ChatText
    webchat: WebchatClientLimits
    transport: WebchatClientTransport

    @classmethod
    def from_settings(
        cls,
        *,
        settings_revision: int,
        transport_revision: str,
        webchat: WebchatSettings,
        sse_event_max_bytes: int,
        sse_heartbeat_sec: float,
    ) -> Self:
        """Project only approved browser fields; transport revision is an opaque config fingerprint."""
        return cls(
            settings_revision=settings_revision,
            transport_revision=transport_revision,
            webchat=WebchatClientLimits(
                history_page_size=webchat.history_page_size,
                text_max_bytes=webchat.text_max_bytes,
                upload_max_bytes=webchat.upload_max_bytes,
                upload_max_per_message=webchat.upload_max_per_message,
                notification_poll_sec=webchat.notification_poll_sec,
                upload_formats=SUPPORTED_UPLOAD_FORMATS,
            ),
            transport=WebchatClientTransport(
                sse_event_max_bytes=sse_event_max_bytes,
                sse_heartbeat_sec=sse_heartbeat_sec,
                reconnect_base_ms=webchat.reconnect_base_ms,
                reconnect_cap_ms=webchat.reconnect_cap_ms,
                reconnect_max_attempts=webchat.reconnect_max_attempts,
            ),
        )
