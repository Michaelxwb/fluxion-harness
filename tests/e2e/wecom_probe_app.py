"""本地真实 WebSocket 协议探针：承载官方 WeCom SDK 的认证/消息/流式回复协议。

用途：验收时替代外部企业微信端点，让官方 SDK 与生产 WeComAdapter 经**真实 socket**
收发协议帧；它不替代生产 Adapter/SDK，也不代表企业微信实网已验收。
"""

from __future__ import annotations

import asyncio
import datetime
import ipaddress
import json
import ssl
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import websockets
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

CMD_SUBSCRIBE = "aibot_subscribe"
CMD_HEARTBEAT = "ping"
CMD_RESPONSE = "aibot_respond_msg"
CMD_SEND_MSG = "aibot_send_msg"
CMD_MESSAGE_CALLBACK = "aibot_msg_callback"
CMD_EVENT_CALLBACK = "aibot_event_callback"


def generate_self_signed_cert(root: Path) -> tuple[Path, Path]:
    """本地自签证书：官方 SDK 强制 `ssl=`，因此探针必须以 wss:// 承载真实 TLS。"""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.datetime.now(datetime.UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_path = root / "probe-cert.pem"
    key_path = root / "probe-key.pem"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    return cert_path, key_path


@dataclass
class ProbeFrame:
    connection: int
    frame: dict[str, Any]


@dataclass
class WeComProbe:
    """真实 WS 服务端：记录收到的帧（可回读）并按协议回 ack。"""

    expected_bots: dict[str, str]
    received: list[ProbeFrame] = field(default_factory=list)
    replies: list[dict[str, Any]] = field(default_factory=list)
    auth_failures: list[dict[str, Any]] = field(default_factory=list)
    connections: list[Any] = field(default_factory=list)
    # 故障注入（TASK-031）：均按 bot 粒度编排，只影响目标 bot
    reject_bot_ids: set[str] = field(default_factory=set)
    fail_reply_bots: set[str] = field(default_factory=set)
    disconnect_bots: set[str] = field(default_factory=set)
    connection_bots: dict[int, str] = field(default_factory=dict)
    _server: Any = None
    ws_url: str = ""
    cert_path: Path | None = None

    # ---- 服务生命周期 -----------------------------------------------------
    async def start(self, host: str = "127.0.0.1", port: int = 0) -> str:
        cert_root = Path(tempfile.mkdtemp(prefix="wecom-probe-tls-"))
        cert_path, key_path = generate_self_signed_cert(cert_root)
        self.cert_path = cert_path
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
        self._server = await websockets.serve(self._handler, host, port, ssl=context)
        sockets = list(self._server.sockets)
        bound_port = sockets[0].getsockname()[1]
        self.ws_url = f"wss://{host}:{bound_port}"
        return self.ws_url

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    # ---- 协议处理 ---------------------------------------------------------
    async def _handler(self, websocket: Any) -> None:
        index = len(self.connections)
        self.connections.append(websocket)
        try:
            async for raw in websocket:
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                frame = json.loads(raw)
                self.received.append(ProbeFrame(index, frame))
                if frame.get("cmd") == CMD_SUBSCRIBE:
                    self.connection_bots[index] = str((frame.get("body") or {}).get("bot_id") or "")
                response = self._respond(frame, index)
                if response is not None:
                    await websocket.send(json.dumps(response))
                if (
                    frame.get("cmd") == CMD_SUBSCRIBE
                    and self.connection_bots.get(index) in self.disconnect_bots
                ):
                    await websocket.close()  # 故障注入：认证后立即断线
        except websockets.exceptions.ConnectionClosed:
            return

    def _respond(self, frame: dict[str, Any], index: int) -> dict[str, Any] | None:
        cmd = frame.get("cmd")
        req_id = (frame.get("headers") or {}).get("req_id", "")
        bot_id = str((frame.get("body") or {}).get("bot_id") or "") or self.connection_bots.get(index, "")
        if cmd == CMD_SUBSCRIBE:
            body = frame.get("body") or {}
            expected = self.expected_bots.get(str(body.get("bot_id")))
            if str(body.get("bot_id")) in self.reject_bot_ids:
                self.auth_failures.append(frame)
                return {"headers": {"req_id": req_id}, "errcode": 40101, "errmsg": "handshake rejected"}
            if expected is None or expected != body.get("secret"):
                self.auth_failures.append(frame)
                return {"headers": {"req_id": req_id}, "errcode": 40101, "errmsg": "invalid bot secret"}
            return {"headers": {"req_id": req_id}, "errcode": 0}
        if cmd == CMD_HEARTBEAT:
            return {"headers": {"req_id": req_id}, "errcode": 0}
        if cmd in (CMD_RESPONSE, CMD_SEND_MSG):
            self.replies.append(frame)
            if bot_id in self.fail_reply_bots:
                return {"headers": {"req_id": req_id}, "errcode": 50001, "errmsg": "send failed"}
            return {"headers": {"req_id": req_id}, "errcode": 0}
        return None

    async def drop_connection(self, bot_id: str) -> None:
        """故障注入：主动断开目标 bot 的连接。"""
        for index, socket in enumerate(self.connections):
            if self.connection_bots.get(index) != bot_id:
                continue
            try:
                await socket.close()
            except Exception:  # 已断开则忽略
                continue

    def clear_injections(self) -> None:
        self.reject_bot_ids.clear()
        self.fail_reply_bots.clear()
        self.disconnect_bots.clear()

    # ---- 服务端主动推送（供用例驱动入站路径） --------------------------------
    async def push_message(
        self,
        *,
        bot_id: str,
        message_id: str,
        external_user_id: str,
        text: str,
        reply_id: str,
        chat_id: str | None = None,
    ) -> None:
        frame: dict[str, Any] = {
            "cmd": CMD_MESSAGE_CALLBACK,
            "headers": {"req_id": reply_id},
            "body": {
                "msgtype": "text",
                "msgid": message_id,
                "from": {"userid": external_user_id},
                "text": {"content": text},
            },
        }
        if chat_id is not None:
            frame["body"]["chatid"] = chat_id
        await self._send_to_bot(frame, bot_id)

    async def push_event(
        self,
        *,
        event_type: str,
        external_user_id: str,
        reply_id: str,
        chat_id: str | None = None,
    ) -> None:
        frame: dict[str, Any] = {
            "cmd": CMD_EVENT_CALLBACK,
            "headers": {"req_id": reply_id},
            "body": {"event": {"eventtype": event_type}, "from": {"userid": external_user_id}},
        }
        if chat_id is not None:
            frame["body"]["chatid"] = chat_id
        await self._broadcast(frame)

    async def _send_to_bot(self, frame: dict[str, Any], bot_id: str) -> None:
        """只投递给目标 bot 的连接。

        入站消息去重键是 `im:dedupe:{channel}:{msgid}`（与连接无关）：若把同一个 msgid 广播到
        多个 bot 的连接，先收到的那条会抢先去重并成 Run，其余 bot 侧被静默去重 —— 表现为
        "推给 bot2 却在 bot1 侧回复、bot2 收不到回复"的随机失败（10-im-gateway S-01 偶发根因）。
        """
        targeted = [
            socket
            for index, socket in enumerate(self.connections)
            if self.connection_bots.get(index) == bot_id
        ]
        if not targeted:
            raise AssertionError(f"探针没有 {bot_id} 的已连接客户端，无法推送")
        payload = json.dumps(frame)
        delivered = 0
        for socket in targeted:
            try:
                await socket.send(payload)
                delivered += 1
            except Exception:  # 连接已断：忽略该 socket，其余仍推送
                continue
        if delivered == 0:
            raise AssertionError(f"{bot_id} 的连接不可用，无法推送")

    async def _broadcast(self, frame: dict[str, Any]) -> None:
        if not self.connections:
            raise AssertionError("探针没有已连接的客户端，无法推送")
        payload = json.dumps(frame)
        delivered = 0
        for socket in list(self.connections):
            try:
                await socket.send(payload)
                delivered += 1
            except Exception:  # 连接已断：忽略该 socket，其余仍推送
                continue
        if delivered == 0:
            raise AssertionError("探针没有可用的连接，无法推送")

    # ---- 断言辅助 ---------------------------------------------------------
    def frames_of(self, cmd: str) -> list[dict[str, Any]]:
        return [item.frame for item in self.received if item.frame.get("cmd") == cmd]

    async def wait_for_replies(self, count: int, timeout: float = 5.0) -> list[dict[str, Any]]:
        deadline = asyncio.get_event_loop().time() + timeout
        while len(self.replies) < count:
            if asyncio.get_event_loop().time() > deadline:
                break
            await asyncio.sleep(0.02)
        return list(self.replies)
