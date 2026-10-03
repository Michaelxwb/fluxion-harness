from __future__ import annotations

import re
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from .channel import AttachmentRef
from .tasks import ContractModel, DeliveryRouteInput

#: 后台任务交付键：`task:{task_id}:final`（既有形态，**不变**）
TASK_DELIVERY_KEY = re.compile(r"^task:[0-9a-fA-F-]{36}:final$")
#: 会话内交付键：`run:{run_id}:{artifact_id}`（TASK-006 新增，设计 §3.4 API-01）
RUN_DELIVERY_KEY = re.compile(r"^run:[0-9a-fA-F-]{36}:[0-9a-fA-F-]{36}$")


class DeliveryMessage(ContractModel):
    """要投递给用户的内容。`type` 决定该填哪个载荷——不再只有文本（TASK-006）。

    **载荷里没有任何渠道私有发送形状**：产物走 `AttachmentRef`，由适配器决定怎么发
    （直发，或降级为取件直链）。核心域与交付契约都不认渠道的发送体（`RULE-im-002`）。
    """

    type: Literal["text", "artifact", "image"] = "text"
    text: str = ""
    artifact: AttachmentRef | None = None

    @model_validator(mode="after")
    def _payload_matches_type(self) -> Self:
        if self.type == "text":
            if not self.text:
                raise ValueError("type=text 时 text 必填且非空")
        elif self.artifact is None:
            raise ValueError(f"type={self.type} 时 artifact 必填")
        return self


class DeliveryRequest(ContractModel):
    """一次交付请求。**两种形态共用一个契约**（设计 §3.4 API-01/02）：

    - **后台任务**（既有）：`task_id` 必填，`delivery_key = task:{task_id}:final`；
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

    @model_validator(mode="after")
    def _delivery_key_matches_scope(self) -> Self:
        """两种形态**互斥**：键的形态必须与 `task_id` 的有无一致。

        不做成一个宽松的正则是因为"有 task_id 却给了 run: 键"是个**真错误**（调用方搞错了
        自己在哪条路径上），放过它会让幂等键指向另一个事实。
        """
        if self.task_id is not None:
            if self.delivery_key != f"task:{self.task_id}:final":
                raise ValueError(
                    "有 task_id 时 delivery_key 必须为 task:{task_id}:final 且与它一致"
                )
        elif not RUN_DELIVERY_KEY.match(self.delivery_key):
            raise ValueError("无 task_id 时 delivery_key 必须为 run:{run_id}:{artifact_id}")
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
