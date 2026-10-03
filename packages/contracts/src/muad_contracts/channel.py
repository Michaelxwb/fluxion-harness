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

#: 交付审计的三种结局（设计 API-03）。`DELIVERED` 是**终态**：同一产物对同一路由一旦交付成功，
#: 该行不再被后续写覆盖（失败重试成功是把同一行从 FAILED 更新成 DELIVERED，不是新增一行）。
DeliveryAuditOutcome = Literal["DELIVERED", "FAILED", "DEGRADED"]


class PageMeta(ContractModel):
    """统一列表分页字段（required API Rule：page>=1、1<=page_size<=100）。"""

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)
    total: int = Field(default=0, ge=0)


class AttachmentRef(ContractModel):
    """产物/附件引用：**两个方向共用一个形状**（设计 AD-6-C）。

    刻意只描述"**已经拿到手的字节**"——只有存储键与元信息，**不含**任何取件凭据
    （url / aes_key / media_id / download_code ……）：取件方式渠道私有（AD-8）。

    **两个方向各自填哪些字段**（2026-10-03 明确；此前只有入站这一个方向）：

    | 字段 | 入站（用户发来的） | 出站（Agent 产出、要发出去的） |
    |------|------------------|------------------------------|
    | `storage_key`/`kind`/`media_type`/`size`/… | ✓ | ✓ |
    | `source_channel` | **必填**（字节来自哪个渠道） | 无（产物是 Agent 产的，没有来源渠道） |
    | `artifact_id` | 无（此刻 DB 行还没建，AD-1-B） | **必填**（见下方两处用途） |

    出站方向 `artifact_id` 的两处用途：① 交付审计的幂等键 `(tenant_id, artifact_id, route_key)`
    要它；② 降级为签名取件直链时，适配器靠它拼 `/api/v1/artifacts/{artifact_id}/content`。
    **所以它不能只放在 `DeliveryRequest.artifact_ids` 里**——那样适配器得去关联两个字段，
    是二次来源，也是接新渠道时最容易踩空的地方。

    **一个类型而不是两个**是有意的：两个方向的字段重合 6/7，拆成两个孪生类型必然各自漂移，
    而且某个渠道**双向**都用时（收到的附件又要转出去）还得写转换——那正是返工面。
    代价是入站的"来源渠道必有"从**类型保证**降为**约定**：唯一的构造处是网关落盘时传入的
    `envelope.channel`，改动它时要留意这一点。
    """

    storage_key: str = Field(min_length=1)
    kind: AttachmentKind
    media_type: str = Field(min_length=1)
    size: int = Field(gt=0)
    filename: str | None = None
    checksum: str = Field(min_length=1)
    #: 入站方向必填；出站方向不需要（见类文档）
    source_channel: ChannelName | None = None
    #: 出站方向必填；入站方向拿不到（AD-1-B）
    artifact_id: UUID | None = None


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


class ArtifactDeliveryAuditRequest(ContractModel):
    """交付审计事件（设计 API-03）：网关 → console 内部端点（**网关不持库**）。

    **与入站审计表刻意不同形**：路由用 `(channel, route_key)` 而不是 `channel + bot_id +
    external_user_id`。`route_key` 是**适配器产出的可读不透明串**（企微 = `{bot_id}:{userid}`，
    未来 web chat = `session:{id}`）—— 核心域与审计表都不认它的内部形状，否则上 web chat 时
    那两个渠道私有列会**填不出真值**。

    同样**没有自由 JSON 字段**：交付凭据与取件令牌在类型上就无处可放（RULE-secret-001 的
    审计腿由结构保证，不是靠写入前脱敏）。
    """

    artifact_id: UUID
    channel: ChannelName
    route_key: str = Field(min_length=1, max_length=256)
    delivery_key: str = Field(min_length=1, max_length=128)
    outcome: DeliveryAuditOutcome
    reason_code: str = Field(default="", max_length=64)
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
