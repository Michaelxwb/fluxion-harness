from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import Field

from .enums import ChannelName
from .tasks import ContractModel

# 与 api-kit `muad_api.response` 的分页边界保持一致（contracts 为独立包，不反向依赖 api-kit）
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100

AttachmentKind = Literal["IMAGE", "DOCUMENT", "OTHER"]

#: 渠道中性词汇：回调里出现了**本渠道不接收**的载荷形态时，用它让核心域知道"来过一条"。
#: 刻意不描述渠道私有的取件形状（url/aes_key/media_id...），否则渠道差异会渗透核心域（AD-8）。
UnsupportedMedia = Literal["VOICE", "VIDEO", "OTHER"]

#: 入站审计的三种结局（设计 API-10）。
InboundAuditOutcome = Literal["RECEIVED", "REJECTED", "FAILED"]


class PageMeta(ContractModel):
    """统一列表分页字段（required API Rule：page>=1、1<=page_size<=100）。"""

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)
    total: int = Field(default=0, ge=0)


class AttachmentRef(ContractModel):
    """附件引用：渠道边界与消息契约**同型**使用（设计 AD-6-C）。

    刻意只描述"**已经拿到手的字节**"——只有存储键与元信息，**不含**任何取件凭据
    （url / aes_key / media_id / download_code ……）：取件方式渠道私有（AD-8）。
    也**不含** `artifact_id`：artifact 行由 Runtime 在 Run 建立后写，渠道侧此刻拿不到 DB 标识（AD-1-B）。
    """

    storage_key: str = Field(min_length=1)
    kind: AttachmentKind
    media_type: str = Field(min_length=1)
    size: int = Field(gt=0)
    filename: str | None = None
    checksum: str = Field(min_length=1)
    source_channel: ChannelName


class ChannelEnvelope(ContractModel):
    channel: ChannelName
    bot_id: str = Field(min_length=1)
    external_user_id: str = Field(min_length=1)
    external_conversation_id: str | None = None
    message_id: str = Field(min_length=1)
    text: str = ""
    # 渠道适配器产出的附件引用，与 Runtime 侧 MessageInput.attachments 同型（零转换）
    attachments: list[AttachmentRef] = Field(default_factory=list)
    # 非空表示这条消息的载荷形态本渠道不接收（如语音/视频）：核心域据此给用户明确反馈，
    # 而不是让消息在渠道边界静默消失（RULE-01）。默认 None ⇒ 序列化结果与改造前一致。
    unsupported_media: UnsupportedMedia | None = None


class InboundAuditRequest(ContractModel):
    """入站审计事件（设计 API-10）：网关 → console 内部端点。

    **字段全部枚举化/结构化，刻意没有自由形式的 JSON 字段** —— 因此 `aes_key`、媒体 URL
    这类取件凭据**在类型上就无处可放**（RULE-secret-001 的审计腿由结构保证，而不是靠写入前
    的运行时脱敏）。谁发的什么被拒、为什么，全部落在具名字段里。
    """

    channel: ChannelName
    bot_id: str = Field(min_length=1)
    external_message_id: str = Field(min_length=1)
    external_user_id: str = Field(min_length=1)
    outcome: InboundAuditOutcome
    reason_code: str = ""
    attachment_count: int = Field(ge=0)
    accepted_count: int = Field(ge=0)
    total_bytes: int = Field(ge=0)
    trace_id: str | None = None


class ChannelResolveRequest(ContractModel):
    channel: ChannelName
    bot_id: str = Field(min_length=1)
    external_user_id: str = Field(min_length=1)
    external_conversation_id: str | None = None


class ChannelResolveResponse(ContractModel):
    bound: bool
    agent_id: UUID | None = None
    platform_user_id: UUID | None = None
    authorized: bool = False


class ChannelBindRequest(ContractModel):
    channel: ChannelName
    bot_id: str = Field(min_length=1)
    external_user_id: str = Field(min_length=1)
    bind_code: str = Field(min_length=1)


class ChannelBindResponse(ContractModel):
    platform_user_id: UUID
    bound: bool = True


class ChannelSkillItem(ContractModel):
    """API-04 允许字段；不含 SKILL.md 全文或未授权资源。"""

    skill_id: UUID
    key: str = Field(min_length=1)
    name: str = Field(min_length=1)
    platform_label: str = Field(min_length=1)
    description: str = ""


class ChannelSkillsResponse(PageMeta):
    items: list[ChannelSkillItem] = Field(default_factory=list)


class BotSnapshotItem(ContractModel):
    bot_account_id: UUID
    bot_id: str
    # 仅内存/受保护内部快照使用：不从 repr/异常详情回显
    secret: str | None = Field(default=None, repr=False)
    agent_id: UUID
    enabled: bool = True


class BotSnapshotResponse(PageMeta):
    revision: str
    items: list[BotSnapshotItem] = Field(default_factory=list)
