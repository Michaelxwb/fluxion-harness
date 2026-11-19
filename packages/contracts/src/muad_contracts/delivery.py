from __future__ import annotations

import hashlib
import re
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from .canonical import canonical_json
from .channel import AttachmentRef
from .tasks import ContractModel, DeliveryRouteInput

#: 后台任务最终结果键：`execution:{execution_id}:final`（design §3.3.10「一条 Execution
#: 一份结果」；重投命中同一回执）。
EXECUTION_DELIVERY_KEY = re.compile(r"^execution:[0-9a-fA-F-]{36}:final$")
#: 消息级交付键：`execution:{task_id}:message:{message_id}`（设计 §4.5；显式重发用新
#: message_id 生成新键，允许再次发送）。
EXECUTION_MESSAGE_DELIVERY_KEY = re.compile(
    r"^execution:[0-9a-fA-F-]{36}:message:[0-9a-fA-F-]{36}$"
)
#: 人工问题键：`human:{interaction_id}`（design §3.3.10「一个问题一份投递」；问题不属于
#: 任何 Conversation，按 interaction 身份而不是 execution 记账）。
HUMAN_DELIVERY_KEY = re.compile(r"^human:[0-9a-fA-F-]{36}$")
#: 会话内交付键：`run:{run_id}:{artifact_id}`（TASK-006 新增，设计 §3.4 API-01）
RUN_DELIVERY_KEY = re.compile(r"^run:[0-9a-fA-F-]{36}:[0-9a-fA-F-]{36}$")
#: 消息级交付键：`run:{run_id}:message:{message_id}`（设计 §4.5；正文 + 多附件的唯一单元）。
RUN_MESSAGE_DELIVERY_KEY = re.compile(
    r"^run:[0-9a-fA-F-]{36}:message:[0-9a-fA-F-]{36}$"
)
#: Run 终态兜底交付键：与 `runtime.channel_notification.delivery_key` **同一个稳定键**
#: （TASK-063 的 `run:{run_id}:terminal`；设计 §3.6「活跃回复会话的收尾与兜底主动交付使用
#: 同一稳定 delivery_key」）。TASK-066 的 Runtime 投递泵按它经 `/internal/deliveries` 主动交付。
TERMINAL_DELIVERY_KEY = re.compile(r"^run:[0-9a-fA-F-]{36}:terminal$")
#: 所有 Run 作用域投递键的公共前缀；`run_id` 由此导出，不为同一事实新增字段。
_RUN_SCOPED_KEY = re.compile(r"^run:([0-9a-fA-F-]{36}):")

#: 消息级投递快照边界（与 Runtime `MessageDeliveryPayload` 同一口径）。
MAX_MESSAGE_DELIVERY_TEXT_CHARS = 20_000
MAX_MESSAGE_DELIVERY_ATTACHMENTS = 20

#: `message_delivery.status` 的公开词表。
MessageDeliveryStatus = Literal[
    "PENDING", "DELIVERING", "DELIVERED", "DEGRADED", "UNKNOWN", "FAILED"
]


def delivery_target_key(route: DeliveryRouteInput) -> str:
    """渠道中立的路由身份：只用于同消息去重与定位，不进入用户 UI。

    Worker 写 `task.message_outbox` 与 Runtime 写 `runtime.message_delivery` 必须用**同一
    口径**：两处各算一遍，哈希输入稍有差异就会把同一条消息记成两个目标，重试随之重复发送。
    """
    identity = route.model_dump(mode="json")
    digest = hashlib.sha256(canonical_json(identity).encode("utf-8")).hexdigest()
    return f"{route.channel}:{digest[:48]}"


class DeliveryMessage(ContractModel):
    """要投递给用户的内容。`type` 决定该填哪个载荷——不再只有文本（TASK-006）。

    **载荷里没有任何渠道私有发送形状**：产物走 `AttachmentRef`，由适配器决定怎么发
    （直发，或降级为取件直链）。核心域与交付契约都不认渠道的发送体（`RULE-im-002`）。

    `type="terminal"` 是 Run 终态的单发送者形态：正文可以省略（失败/取消由网关按
    `status`/`error_code` 用消息目录渲染），但必须给出终态 `status`。
    """

    type: Literal["text", "artifact", "image", "terminal", "message"] = "text"
    text: str = ""
    artifact: AttachmentRef | None = None
    #: 一条消息多个附件；Web pull 与 push 共用同一列表。
    attachments: list[AttachmentRef] = Field(default_factory=list)
    #: 仅 terminal：Run 终态（COMPLETED/FAILED/CANCELLED），网关据此渲染空正文的终态文案。
    status: str = ""
    #: 仅 terminal：失败码（消息目录的 code），用于渲染用户可见的错误文案。
    error_code: str | None = None

    @model_validator(mode="after")
    def _payload_matches_type(self) -> Self:
        if self.type == "text":
            if not self.text:
                raise ValueError("type=text 时 text 必填且非空")
        elif self.type == "terminal":
            if not self.status:
                raise ValueError("type=terminal 时 status 必填")
        elif self.type == "message":
            if not self.text and not self.attachments:
                raise ValueError("type=message 时 text 或 attachments 至少一个非空")
        elif self.artifact is None:
            raise ValueError(f"type={self.type} 时 artifact 必填")
        return self


class DeliveryRequest(ContractModel):
    """一次交付请求。**两种形态共用一个契约**（设计 §3.4 API-01/02）：

    - **后台任务**：`task_id` 必填，`delivery_key = execution:{task_id}:final`（最终结果）
      或 `human:{interaction_id}`（人工问题）；
    - **会话内**（TASK-006 新增）：`task_id` 省略，`delivery_key = run:{run_id}:{artifact_id}`。

    `task_id` 由必填改为可省是**放宽**，既有 worker 调用零改动。

    `tenant_id` 随请求走而不是走请求头：交付审计的幂等键是
    `(tenant_id, artifact_id, route_key)`，租户是**这条事实的一部分**，不是传输层上下文；
    两个调用方（runtime / worker）本来就都持有它。放进必填字段，网关就不会因为"没带头"
    而写不出一条本该写下的审计。
    """

    tenant_id: str = Field(min_length=1)
    task_id: UUID | None = None
    delivery_key: str = Field(min_length=1)
    route: DeliveryRouteInput
    message: DeliveryMessage
    artifact_ids: list[UUID] = Field(default_factory=list)
    #: 发起本次 Run 的入站消息身份（终态用于找回**原消息**的回复流；后台任务为空）。
    message_id: str | None = None
    #: 投递仲裁身份（Runtime 投递泵的租约）：发送端按 (tenant, delivery_key, target_key)
    #: 持久去重与 fencing；同一个 message_key 可以扇出到多个目标。
    delivery_attempt_id: UUID | None = None
    delivery_epoch: int | None = None
    #: 渠道中立的稳定目标身份（`WECOM:<route-hash>`）。单目标旧调用可省略；
    #: 多目标扇出时网关据此区分同一 message_key 的不同收件人。
    target_key: str | None = Field(default=None, max_length=160)
    #: 受理时的接收者/Agent 身份：Gateway 在真实发送前按原入口复核权限，
    #: 授权被撤销或接收者/Agent 变了就不发（与受理时的身份比对）。
    agent_id: UUID | None = None
    platform_user_id: UUID | None = None

    @property
    def run_id(self) -> str | None:
        """会话内/终态兜底交付的 Run id；后台任务交付（`execution:`/`human:`）为 `None`。

        由交付键导出，不新增字段：键和 Run 是**同一条事实**，多一个字段就会有两种说法。
        适配器按它把产物回到起这个 Run 的那条消息（同路由可能有多条入站消息）。
        """
        matched = _RUN_SCOPED_KEY.match(self.delivery_key)
        return matched.group(1) if matched else None

    @model_validator(mode="after")
    def _delivery_key_matches_scope(self) -> Self:
        """键的形态必须与 `task_id` 的有无一致，且必须是**已知**形态之一。

        不做成一个宽松的正则是因为"有 task_id 却给了 run: 键"是个**真错误**（调用方搞错了
        自己在哪条路径上），放过它会让幂等键指向另一个事实。无 `task_id` 的两种合法形态：
        会话内产物交付（`run:{run_id}:{artifact_id}`）与 Run 终态兜底文本交付
        （`run:{run_id}:terminal`，与持久通知的稳定键同形）。

        有 `task_id` 时两种形态各记各的身份：最终结果键的 id 必须**就是**这个 Execution
        （`execution:{task_id}:final`）；人工问题按 interaction 记账（`human:{interaction_id}`），
        `task_id` 只是上下文——把问题键绑到 execution 上会让同一执行的两次提问互相覆盖。
        """
        if self.task_id is not None:
            if EXECUTION_DELIVERY_KEY.match(self.delivery_key):
                if self.delivery_key != f"execution:{self.task_id}:final":
                    raise ValueError(
                        "最终结果键必须为 execution:{task_id}:final 且与 task_id 一致"
                    )
            elif EXECUTION_MESSAGE_DELIVERY_KEY.match(self.delivery_key):
                if not self.delivery_key.startswith(f"execution:{self.task_id}:message:"):
                    raise ValueError(
                        "消息键必须为 execution:{task_id}:message:{message_id} 且与 task_id 一致"
                    )
            elif not HUMAN_DELIVERY_KEY.match(self.delivery_key):
                raise ValueError(
                    "有 task_id 时 delivery_key 必须为 execution:{task_id}:final、"
                    "execution:{task_id}:message:{message_id} 或 human:{interaction_id}"
                )
        elif not (
            RUN_DELIVERY_KEY.match(self.delivery_key)
            or RUN_MESSAGE_DELIVERY_KEY.match(self.delivery_key)
            or TERMINAL_DELIVERY_KEY.match(self.delivery_key)
        ):
            raise ValueError(
                "无 task_id 时 delivery_key 必须为 run:{run_id}:{artifact_id}、"
                "run:{run_id}:message:{message_id} 或 run:{run_id}:terminal"
            )
        return self


class DeliveryResponse(ContractModel):
    accepted: bool
    delivered: bool
    # 命中成功键的重放为 True（200 且不重发）
    deduplicated: bool
    #: 与 `deduplicated` **同值**（网关两个都发）。留两个名字是**兼容**：网关自始就发 `duplicate`，
    #: 既有调用方与验收用例（`tests/gateway/test_delivery_api.py`、dfx / task_schedule 验收）
    #: 断言的是它；`deduplicated` 是后来才进契约的那个。
    #:
    #: **2026-10-03 补**：这个字段此前**没进契约**，而 `ContractModel` 是 `extra="forbid"` ⇒
    #: runtime 这条新调用方（TASK-006 的会话内交付）一 `model_validate` 就抛
    #: `ValidationError: duplicate — Extra inputs are not permitted`，**把一次已经成功的投递
    #: 报成了失败**（用户端表现为"文件发不出去、可重试"，而文件其实已经到了）。真机实测两次复现。
    #: worker 那条路径读得松散（`response.json().get("data")`）所以一直没露。
    duplicate: bool = False
    #: 交付结局：`DELIVERED` 直发成功 / `DEGRADED` 降级为签名取件链接（TASK-006）
    outcome: Literal["DELIVERED", "DEGRADED"] | None = None
    #: 仅降级时有值——用户实际收到的取件直链
    fallback_url: str | None = None


class MessageDeliveryPayloadInput(ContractModel):
    """Task outbox → Runtime 的消息快照：正文 + 附件，至少一个非空。"""

    text: str = Field(default="", max_length=MAX_MESSAGE_DELIVERY_TEXT_CHARS)
    attachments: list[AttachmentRef] = Field(
        default_factory=list, max_length=MAX_MESSAGE_DELIVERY_ATTACHMENTS
    )

    @model_validator(mode="after")
    def _payload_not_empty(self) -> Self:
        if not self.text and not self.attachments:
            raise ValueError("message payload must carry text or attachments")
        if any(attachment.artifact_id is None for attachment in self.attachments):
            raise ValueError("outbound attachments require artifact_id")
        identities = [attachment.artifact_id for attachment in self.attachments]
        if len(set(identities)) != len(identities):
            raise ValueError("attachment artifact_ids must be distinct")
        return self


class MessageDeliveryEnqueueRequest(ContractModel):
    """Worker → Runtime 的消息级投递受理请求（`POST /internal/message-deliveries`）。

    `message_key` 跨自动重试/崩溃恢复稳定；`target_key` 由 `delivery_target_key(route)`
    唯一导出，Runtime 会复核后才写 `message_delivery`。
    """

    tenant_id: str = Field(min_length=1, max_length=64)
    owner_type: Literal["TASK"]
    owner_id: UUID
    message_key: str = Field(min_length=1, max_length=128)
    kind: Literal["TASK_RESULT"]
    target_key: str = Field(min_length=1, max_length=256)
    route: DeliveryRouteInput
    payload: MessageDeliveryPayloadInput

    @model_validator(mode="after")
    def _message_key_matches_owner(self) -> Self:
        final_key = f"execution:{self.owner_id}:final"
        message_prefix = f"execution:{self.owner_id}:message:"
        if self.message_key == final_key:
            return self
        if not self.message_key.startswith(message_prefix):
            raise ValueError("task message_key must be execution:{owner_id}:final or :message:{id}")
        try:
            UUID(self.message_key.removeprefix(message_prefix))
        except ValueError as exc:
            raise ValueError("task message_key must end with a UUID") from exc
        return self


class MessageDeliveryEnqueueReceipt(ContractModel):
    """受理回执：`duplicate=True` 表示命中已有 `(message_key, target_key)`，不重复发送。"""

    delivery_id: UUID
    status: MessageDeliveryStatus
    message_key: str
    target_key: str
    duplicate: bool = False
