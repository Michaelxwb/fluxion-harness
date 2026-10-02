from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from muad_contracts import UnsupportedMedia


class WeComSdkError(RuntimeError):
    pass


class WeComSdkConnectionError(WeComSdkError):
    pass


class WeComMediaError(WeComSdkError):
    """媒体取件失败的基类。上层据**具体子类**映射用户可见文案与审计码。"""


class WeComMediaTooLargeError(WeComMediaError):
    """下载途中累计字节超限：**立即中止**，不读完。"""

    def __init__(self, max_bytes: int) -> None:
        super().__init__(f"wecom media exceeds the {max_bytes} bytes limit")
        self.max_bytes = max_bytes


class WeComMediaTimeoutError(WeComMediaError):
    pass


class WeComMediaNetworkError(WeComMediaError):
    """连接/HTTP 状态类失败（含 URL 失效）。"""


class WeComMediaDecryptError(WeComMediaError):
    """解密失败：密钥缺失或密钥不匹配。**不得**退化为"返回密文"。"""


@dataclass(frozen=True, slots=True)
class WeComMediaRef:
    """渠道私有的媒体引用：企微取件靠 `url` + `aeskey`（AD-8）。

    **两个字段都不进 repr**：它们是取件凭据，`RULE-secret-001` 要求不得出现在日志、
    审计或异常文本里——默认 repr 会随任何一次 `repr(message)` / f-string 泄漏出去。
    `aes_key` 可缺失（协议里 `aeskey?`），缺失按解密失败处理。
    """

    url: str = field(repr=False)
    aes_key: str | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class WeComMediaContent:
    """已取到手的媒体内容（**已解密**）。`size` 即 `len(data)`，不另存一份。"""

    data: bytes
    media_type: str
    filename: str | None
    checksum: str


@dataclass(frozen=True, slots=True)
class WeComInboundMessage:
    bot_id: str
    message_id: str
    external_user_id: str
    external_conversation_id: str | None
    message_type: str
    text: str
    reply_id: str
    media: tuple[WeComMediaRef, ...] = ()
    unsupported_media: UnsupportedMedia | None = None


@dataclass(frozen=True, slots=True)
class WeComInboundEvent:
    bot_id: str
    event_type: str
    external_user_id: str
    external_conversation_id: str | None


MessageCallback = Callable[[WeComInboundMessage], None]
EventCallback = Callable[[WeComInboundEvent], None]
AuthenticatedCallback = Callable[[], None]
DisconnectedCallback = Callable[[str], None]
ErrorCallback = Callable[[Exception], None]


class WeComSdkPort(Protocol):
    @property
    def is_connected(self) -> bool: ...

    def on_message(self, callback: MessageCallback) -> None: ...

    def on_event(self, callback: EventCallback) -> None: ...

    def on_authenticated(self, callback: AuthenticatedCallback) -> None: ...

    def on_disconnected(self, callback: DisconnectedCallback) -> None: ...

    def on_error(self, callback: ErrorCallback) -> None: ...

    async def connect(self) -> None: ...

    def disconnect(self) -> None: ...

    async def send_text(self, chat_id: str, text: str) -> None:
        """**主动发送**（`aibot_send_msg`）：用于任务结果等主动投递，不依附回调。

        不要用它回入站回调——官方服务对会话内回复只接受 `aibot_respond_msg`
        （实测以 `errcode=40008 invalid message type` 拒收 `aibot_send_msg`）；
        会话内回复用 `reply_text`。
        """
        ...

    async def reply_text(self, reply_id: str, text: str) -> None:
        """**会话内回复**（`aibot_respond_msg` + 入站回调的 `req_id`）。

        2026-09-30 真机复验：用 `send_text` 回「绑定成功」被企业微信以
        `errcode=40008 invalid message type` 拒收；改用本方法（带回调 req_id）后
        文本回复可正常送达。
        """
        ...

    async def send_stream(
        self,
        reply_id: str,
        stream_id: str,
        content: str,
        *,
        finish: bool,
    ) -> None: ...

    async def download_media(
        self,
        url: str,
        aes_key: str | None,
        *,
        max_bytes: int,
    ) -> WeComMediaContent:
        """**渠道私有**取件：签名里的 `url` + `aes_key` 是企微 aibot 专属形状（AD-8）。

        **不得**提升为跨渠道通用接口，也不得让核心域依赖它。`max_bytes` 由调用方给定
        （产品策略常量的唯一来源是门控），实现层负责在流式读取时**执行**它。
        """
        ...


WeComSdkFactory = Callable[[str, str], WeComSdkPort]
