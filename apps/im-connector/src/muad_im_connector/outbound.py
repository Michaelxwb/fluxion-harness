"""Connector 出站命令消费：按 bot 流读取、幂等执行、fencing 与降级回报。

为什么按 bot 分流（`im.outbound:{tenant}:{bot_id}`）：bot lease 是逐 bot 的，两个 Connector
可以各自持有同一 shard 里的不同 bot；只要命令进入共享 shard，非 owner 就可能读到别人的命令。
按 bot 分流后，Connector 只读自己持有 lease 的 bot 流，命令归属由键本身保证。

诚实结局（design §3.3 / E-04）：

- `DELIVERED`：按要求送达（会话内回复或主动发送）；
- `DEGRADED`：reply_ref 过期/epoch 失配/会话内发送失败，改走主动发送——用户确实收到了内容，
  但形态与请求不同；结果里带原因码，调用方可写 DEGRADED 审计；
- `FENCED`：本进程已不是该 bot 的 owner，**不发送**，命令留给新 owner；
- `FAILED` / `EXPIRED`：显式失败，绝不假装成功。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol, cast

from muad_contracts import (
    AttachmentRef,
    ChannelName,
    DeliveryMessage,
    DeliveryRouteInput,
)
from muad_contracts.im import (
    DeliveryCommand,
    DeliveryKind,
    command_result_key,
    outbound_cursor_key,
)
from redis.asyncio import Redis

from .lease import BotLease, RedisLeaseManager
from .metrics import (
    record_outbound,
)
from .reply_store import ReplyStore

logger = logging.getLogger(__name__)

RESULT_TTL_SEC = 600
COMMAND_MARKER_PREFIX = "connector:cmd"
DEAD_STREAM = "im.outbound:dead"
STREAM_MAXLEN = 100_000
RESULT_CODE_REPLY_EXPIRED = "REPLY_REF_EXPIRED"
RESULT_CODE_REPLY_FAILED = "REPLY_SEND_FAILED"
RESULT_CODE_LEASE_LOST = "LEASE_LOST"
RESULT_CODE_FENCED = "FENCED"
RESULT_CODE_CHANNEL_UNAVAILABLE = "CHANNEL_UNAVAILABLE"
RESULT_CODE_EXECUTION_FAILED = "COMMAND_EXECUTION_FAILED"
RESULT_CODE_EXPIRED = "COMMAND_EXPIRED"
RESULT_CODE_BAD_PAYLOAD = "COMMAND_PAYLOAD_INVALID"


class CommandStatus(StrEnum):
    ACCEPTED = "ACCEPTED"
    DELIVERED = "DELIVERED"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"
    FENCED = "FENCED"
    EXPIRED = "EXPIRED"
    DUPLICATE = "DUPLICATE"
    #: 非终态：bot 连接尚未就绪；命令留在 Stream 里，下一次 XREAD 重试（不写结果、不前进 cursor）。
    RETRY = "RETRY"


@dataclass(frozen=True, slots=True)
class CommandResult:
    command_id: str
    status: CommandStatus
    code: str = ""
    detail: str = ""
    epoch: int = 0
    fallback_url: str | None = None
    #: 适配器产出的路由不透明串：网关写交付审计时用它（不自己拼渠道形状）。
    route_key: str = ""

    @property
    def terminal(self) -> bool:
        return self.status in (
            CommandStatus.DELIVERED,
            CommandStatus.DEGRADED,
            CommandStatus.FAILED,
            CommandStatus.EXPIRED,
        )

    def payload(self) -> dict[str, Any]:
        return {
            "command_id": self.command_id,
            "status": self.status.value,
            "code": self.code,
            "detail": self.detail,
            "epoch": self.epoch,
            "fallback_url": self.fallback_url,
            "route_key": self.route_key,
            "at": datetime.now(UTC).isoformat(),
        }


class ConnectorChannelPort(Protocol):
    """Connector 需要的渠道能力：只含"发出去"与连接存在性，不暴露 SDK 类型。"""

    def has_bot(self, bot_id: str) -> bool: ...

    async def send_active(self, route: DeliveryRouteInput, message: DeliveryMessage) -> None: ...

    async def send_reply_text(
        self, route: DeliveryRouteInput, reply_ref: str, text: str
    ) -> None: ...

    async def send_reply_stream(
        self,
        route: DeliveryRouteInput,
        reply_ref: str,
        stream_id: str,
        text: str,
        *,
        finish: bool,
    ) -> None: ...

    async def deliver_artifact_with_target(
        self,
        route: DeliveryRouteInput,
        artifact: AttachmentRef,
        *,
        tenant_id: str,
        run_id: str | None,
        reply_ref: str | None,
    ) -> tuple[str, str | None]:
        """返回 `(outcome, fallback_url)`；outcome 是渠道中立的 DELIVERED/DEGRADED。"""
        ...

    def route_key(self, route: DeliveryRouteInput) -> str:
        """把一条路由压成**可读的不透明串**（交付审计用；形状由适配器决定）。"""
        ...


def route_of(command: DeliveryCommand) -> DeliveryRouteInput:
    return DeliveryRouteInput(
        channel=cast(ChannelName, command.channel),
        bot_id=command.bot_id,
        external_user_id=command.external_user_id,
        external_conversation_id=command.external_conversation_id,
    )


class CommandExecutor:
    """把一条命令翻译成渠道动作；所有降级都在这里显式留原因。"""

    def __init__(
        self,
        *,
        adapter: ConnectorChannelPort,
        leases: RedisLeaseManager,
        replies: ReplyStore,
        owner_id: str,
    ) -> None:
        self._adapter = adapter
        self._leases = leases
        self._replies = replies
        self._owner_id = owner_id
        self._buffers: dict[str, list[str]] = {}

    async def execute(self, command: DeliveryCommand) -> CommandResult:
        lease = await self._leases.read(command.tenant_id, command.bot_id)
        if lease is None or lease.owner_id != self._owner_id:
            return self._result(
                command,
                CommandStatus.FENCED,
                code=RESULT_CODE_LEASE_LOST,
                detail="bot lease is not owned by this connector",
                epoch=lease.epoch if lease else 0,
            )
        if not self._adapter.has_bot(command.bot_id):
            # 连接还没建好（快照已拿到 lease，SDK 仍在握手）：不能写 FAILED 让调用方以为
            # 永久失败；命令留在 Stream 里由下一轮重试。
            return self._result(
                command,
                CommandStatus.RETRY,
                code=RESULT_CODE_CHANNEL_UNAVAILABLE,
                detail="bot connection is not ready",
                epoch=lease.epoch,
            )
        if command.epoch and command.epoch > lease.epoch:
            return self._result(
                command,
                CommandStatus.FENCED,
                code=RESULT_CODE_FENCED,
                detail=f"command epoch {command.epoch} > owner epoch {lease.epoch}",
                epoch=lease.epoch,
            )
        reply_ref, reply_problem = await self._usable_reply(command, lease)
        route = route_of(command)
        try:
            if command.kind is DeliveryKind.TEXT:
                return await self._text(command, route, reply_ref, reply_problem, lease)
            if command.kind is DeliveryKind.DELTA:
                return await self._delta(command, route, reply_ref, reply_problem, lease)
            if command.kind is DeliveryKind.TERMINAL:
                return await self._terminal(command, route, reply_ref, reply_problem, lease)
            if command.kind is DeliveryKind.ARTIFACT:
                return await self._artifact(command, route, reply_ref, reply_problem, lease)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 渠道任意异常都要变成诚实失败
            logger.warning(
                "connector_command_failed command_id=%s kind=%s error=%s: %s",
                command.command_id,
                command.kind,
                type(exc).__name__,
                exc,
            )
            return self._result(
                command,
                CommandStatus.FAILED,
                code=RESULT_CODE_CHANNEL_UNAVAILABLE,
                detail=type(exc).__name__,
                epoch=lease.epoch,
            )
        return self._result(
            command,
            CommandStatus.FAILED,
            code=RESULT_CODE_EXECUTION_FAILED,
            detail=f"unsupported kind {command.kind}",
            epoch=lease.epoch,
        )

    async def _usable_reply(
        self, command: DeliveryCommand, lease: BotLease
    ) -> tuple[str | None, str]:
        """判断 reply_ref 是否可用；不可用时返回原因（调用方据此 DEGRADED）。"""
        if not command.reply_ref:
            return None, ""
        if command.epoch and command.epoch < lease.epoch:
            return None, RESULT_CODE_REPLY_EXPIRED
        target = await self._replies.resolve_ref(
            tenant_id=command.tenant_id,
            bot_id=command.bot_id,
            reply_ref=command.reply_ref,
        )
        if target is None:
            return None, RESULT_CODE_REPLY_EXPIRED
        if command.epoch and target.epoch and command.epoch != target.epoch:
            return None, RESULT_CODE_REPLY_EXPIRED
        return command.reply_ref, ""

    async def _text(
        self,
        command: DeliveryCommand,
        route: DeliveryRouteInput,
        reply_ref: str | None,
        reply_problem: str,
        lease: BotLease,
    ) -> CommandResult:
        if reply_ref is not None:
            try:
                await self._adapter.send_reply_text(route, reply_ref, command.text)
                return self._result(command, CommandStatus.DELIVERED, epoch=lease.epoch)
            except Exception as exc:  # noqa: BLE001 - 会话回调失效时降级主动发送
                logger.warning(
                    "connector_reply_text_failed command_id=%s error=%s",
                    command.command_id,
                    type(exc).__name__,
                )
                reply_problem = RESULT_CODE_REPLY_FAILED
        await self._adapter.send_active(route, DeliveryMessage(text=command.text))
        status = CommandStatus.DEGRADED if command.reply_ref else CommandStatus.DELIVERED
        return self._result(
            command, status, code=reply_problem, detail="active send", epoch=lease.epoch
        )

    async def _delta(
        self,
        command: DeliveryCommand,
        route: DeliveryRouteInput,
        reply_ref: str | None,
        reply_problem: str,
        lease: BotLease,
    ) -> CommandResult:
        key = self._buffer_key(command)
        if reply_ref is not None:
            try:
                await self._adapter.send_reply_stream(
                    route,
                    reply_ref,
                    command.stream_id or key,
                    command.text,
                    finish=False,
                )
                return self._result(command, CommandStatus.DELIVERED, epoch=lease.epoch)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "connector_reply_stream_failed command_id=%s error=%s",
                    command.command_id,
                    type(exc).__name__,
                )
                reply_problem = RESULT_CODE_REPLY_FAILED
        self._buffers.setdefault(key, []).append(command.text)
        return self._result(
            command,
            CommandStatus.DEGRADED if command.reply_ref else CommandStatus.ACCEPTED,
            code=reply_problem,
            detail="buffered for active send",
            epoch=lease.epoch,
        )

    async def _terminal(
        self,
        command: DeliveryCommand,
        route: DeliveryRouteInput,
        reply_ref: str | None,
        reply_problem: str,
        lease: BotLease,
    ) -> CommandResult:
        key = self._buffer_key(command)
        buffered = "".join(self._buffers.pop(key, []))
        text = buffered + command.text
        if reply_ref is not None and text:
            try:
                await self._adapter.send_reply_stream(
                    route,
                    reply_ref,
                    command.stream_id or key,
                    text,
                    finish=True,
                )
                await self._replies.remember_stream(
                    tenant_id=command.tenant_id,
                    run_id=str(command.run_id or key),
                    stream_id=command.stream_id or key,
                    last_text=text,
                    epoch=lease.epoch,
                )
                return self._result(command, CommandStatus.DELIVERED, epoch=lease.epoch)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "connector_reply_terminal_failed command_id=%s error=%s",
                    command.command_id,
                    type(exc).__name__,
                )
                reply_problem = RESULT_CODE_REPLY_FAILED
        if text:
            await self._adapter.send_active(route, DeliveryMessage(text=text))
        status = CommandStatus.DEGRADED if command.reply_ref else CommandStatus.DELIVERED
        return self._result(
            command,
            status,
            code=reply_problem,
            detail="active send (terminal)",
            epoch=lease.epoch,
        )

    async def _artifact(
        self,
        command: DeliveryCommand,
        route: DeliveryRouteInput,
        reply_ref: str | None,
        reply_problem: str,
        lease: BotLease,
    ) -> CommandResult:
        artifact = command.artifact
        if artifact is None:
            return self._result(
                command,
                CommandStatus.FAILED,
                code=RESULT_CODE_EXECUTION_FAILED,
                detail="artifact is missing",
                epoch=lease.epoch,
            )
        outcome, fallback_url = await self._adapter.deliver_artifact_with_target(
            route,
            artifact,
            tenant_id=command.tenant_id,
            run_id=str(command.run_id) if command.run_id else None,
            reply_ref=reply_ref,
        )
        if outcome == "DEGRADED":
            return self._result(
                command,
                CommandStatus.DEGRADED,
                code=(
                    "ARTIFACT_DEGRADED"
                    if reply_ref is not None
                    else RESULT_CODE_REPLY_EXPIRED
                ),
                detail="artifact degraded",
                epoch=lease.epoch,
                fallback_url=fallback_url,
            )
        status = (
            CommandStatus.DEGRADED
            if command.reply_ref and reply_ref is None
            else CommandStatus.DELIVERED
        )
        return self._result(
            command,
            status,
            code=reply_problem if status is CommandStatus.DEGRADED else "",
            detail="artifact sent" if status is CommandStatus.DELIVERED else "active artifact send",
            epoch=lease.epoch,
            fallback_url=fallback_url,
        )

    def _buffer_key(self, command: DeliveryCommand) -> str:
        return str(command.run_id or command.stream_id or command.command_id)

    def _result(
        self,
        command: DeliveryCommand,
        status: CommandStatus,
        *,
        code: str = "",
        detail: str = "",
        epoch: int = 0,
        fallback_url: str | None = None,
    ) -> CommandResult:
        return CommandResult(
            command_id=str(command.command_id),
            status=status,
            code=code,
            detail=detail,
            epoch=epoch,
            fallback_url=fallback_url,
            route_key=self._route_key(command),
        )

    def _route_key(self, command: DeliveryCommand) -> str:
        """审计用的路由串：适配器产不出也不能把一次**已完成的发送**改成失败。"""
        try:
            return self._adapter.route_key(route_of(command))
        except Exception as exc:  # noqa: BLE001 - 观测字段不得影响投递结论
            logger.warning(
                "connector_route_key_unavailable command_id=%s error=%s",
                command.command_id,
                type(exc).__name__,
            )
            return ""


class OutboundRunner:
    """按 bot 流消费命令；cursor 持久化让接管者能续读未处理命令。"""

    def __init__(
        self,
        redis: Redis,
        *,
        executor: CommandExecutor,
        leases: RedisLeaseManager,
        batch: int = 64,
        max_age_sec: int = 600,
        max_attempts: int = 3,
    ) -> None:
        if batch < 1 or max_age_sec < 1 or max_attempts < 1:
            raise ValueError("outbound limits must be positive")
        self._redis = redis
        self._executor = executor
        self._leases = leases
        self._batch = batch
        self._max_age = max_age_sec
        self._max_attempts = max_attempts
        self._bots: tuple[tuple[str, str], ...] = ()
        self._cursors: dict[tuple[str, str], str] = {}
        self._attempts: dict[str, int] = {}
        self._stopped = asyncio.Event()

    def set_bots(self, bots: tuple[tuple[str, str], ...]) -> None:
        self._bots = tuple(sorted(set(bots)))
        self._cursors = {
            key: cursor for key, cursor in self._cursors.items() if key in self._bots
        }
        self._attempts = {
            key: value for key, value in self._attempts.items()
            if any(key.startswith(f"{tenant}:{bot}:") for tenant, bot in self._bots)
        }

    async def run_forever(self) -> None:
        while not self._stopped.is_set():
            if not self._bots:
                await self._sleep(1.0)
                continue
            await self._ensure_cursors()
            streams = {
                self._stream_key(tenant, bot): self._cursors[(tenant, bot)]
                for tenant, bot in self._bots
            }
            try:
                response = await cast(
                    Awaitable[Any],
                    self._redis.xread(
                        cast(Any, streams), count=self._batch, block=1000
                    ),
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - Redis 抖动不退出循环
                logger.warning("connector_outbound_xread_failed error=%s", type(exc).__name__)
                await self._sleep(1.0)
                continue
            if not response:
                continue
            if await self._drain(response):
                continue
            # RETRY（连接未就绪）或可重试失败：退避 1s，避免在握手窗口里空转。
            await self._sleep(1.0)

    def stop(self) -> None:
        self._stopped.set()

    async def _ensure_cursors(self) -> None:
        for tenant, bot in self._bots:
            key = (tenant, bot)
            if key in self._cursors:
                continue
            stored = await self._redis.get(outbound_cursor_key(tenant, bot))
            self._cursors[key] = _text(stored) or "0-0"

    async def _drain(self, response: list[Any]) -> bool:
        """处理一批消息；返回 False 表示遇到需要重试的失败（调用方稍后从同一 cursor 重读）。"""
        for stream_key, entries in response:
            key = self._bot_of_stream(_text(stream_key))
            if key is None:
                continue
            for entry_id, fields in entries:
                outcome = await self._handle_entry(key, _text(entry_id), _fields(fields))
                if outcome == "retry":
                    return False
                await self._advance(key, _text(entry_id))
        return True

    async def _handle_entry(
        self, bot_key: tuple[str, str], entry_id: str, fields: dict[str, str]
    ) -> str:
        tenant_id, bot_id = bot_key
        raw = fields.get("event", "")
        try:
            command = DeliveryCommand.model_validate_json(raw)
        except Exception as exc:  # noqa: BLE001 - 坏载荷必须进 dead-letter
            logger.error(
                "connector_outbound_bad_payload tenant=%s bot=%s entry=%s error=%s",
                tenant_id,
                bot_id,
                entry_id,
                type(exc).__name__,
            )
            await self._dead_letter(fields, f"{RESULT_CODE_BAD_PAYLOAD}: {type(exc).__name__}", 1)
            record_outbound("FAILED", "bad_payload")
            return "advance"
        result_key = command_result_key(command.command_id)
        if await self._redis.exists(result_key):
            record_outbound("DUPLICATE", "result_exists")
            return "advance"
        age = time.time() - command.created_at.timestamp()
        if age > self._max_age:
            await self._write_result(
                CommandResult(
                    command_id=str(command.command_id),
                    status=CommandStatus.EXPIRED,
                    code=RESULT_CODE_EXPIRED,
                    detail=f"command age {age:.0f}s exceeds {self._max_age}s",
                )
            )
            record_outbound("EXPIRED", "age")
            return "advance"
        result = await self._executor.execute(command)
        if result.status is CommandStatus.RETRY:
            record_outbound("RETRY", result.code)
            return "retry"
        if result.status is CommandStatus.FENCED and result.code == RESULT_CODE_LEASE_LOST:
            # 已经失权：命令留给新 owner；不写结果、不推进 cursor，避免把新 owner 的
            # 进度覆盖掉（cursor 是 bot 级共享键）。
            record_outbound("FENCED", result.code)
            return "retry"
        attempt_key = f"{tenant_id}:{bot_id}:{command.command_id}"
        attempts = self._attempts.get(attempt_key, 0) + 1
        self._attempts[attempt_key] = attempts
        if result.status is CommandStatus.FENCED:
            await self._write_result(result)
            self._attempts.pop(attempt_key, None)
            record_outbound(result.status.value, result.code)
            return "advance"
        if result.status is CommandStatus.FAILED and attempts < self._max_attempts:
            logger.warning(
                "connector_outbound_retry command_id=%s attempt=%s code=%s",
                command.command_id,
                attempts,
                result.code,
            )
            return "retry"
        self._attempts.pop(attempt_key, None)
        await self._write_result(result)
        if result.status is CommandStatus.FAILED:
            await self._dead_letter(fields, result.code or "failed", attempts)
        record_outbound(result.status.value, result.code)
        return "advance"

    async def _write_result(self, result: CommandResult) -> None:
        payload = json.dumps(result.payload(), ensure_ascii=False)
        async with self._redis.pipeline(transaction=False) as pipe:
            pipe.set(command_result_key(result.command_id), payload, ex=RESULT_TTL_SEC)
            pipe.set(
                f"{COMMAND_MARKER_PREFIX}:{result.command_id}", "1", ex=RESULT_TTL_SEC
            )
            await pipe.execute()

    async def _dead_letter(self, fields: dict[str, str], error: str, attempts: int) -> None:
        await self._redis.xadd(
            DEAD_STREAM,
            cast(Any, {**fields, "error": error, "attempts": str(attempts)}),
            maxlen=10_000,
            approximate=True,
        )
        record_outbound("DEAD_LETTER", error)

    async def _advance(self, key: tuple[str, str], entry_id: str) -> None:
        # cursor 是 bot 级共享键：只有当前 owner 才能写。旧 owner 在失权瞬间写 cursor
        # 会让新 owner 跳过尚未执行的命令。
        lease = await self._leases.read(*key)
        if lease is None or lease.owner_id != self._leases.owner_id:
            return
        self._cursors[key] = entry_id
        await self._redis.set(outbound_cursor_key(*key), entry_id)

    async def _sleep(self, seconds: float) -> None:
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._stopped.wait(), timeout=seconds)

    @staticmethod
    def _stream_key(tenant_id: str, bot_id: str) -> str:
        return f"im.outbound:{tenant_id}:{bot_id}"

    def _bot_of_stream(self, stream_key: str) -> tuple[str, str] | None:
        for tenant, bot in self._bots:
            if stream_key == self._stream_key(tenant, bot):
                return tenant, bot
        return None


def _fields(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    return {_text(key): _text(value) for key, value in raw.items()}


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


__all__ = [
    "CommandExecutor",
    "CommandResult",
    "CommandStatus",
    "ConnectorChannelPort",
    "OutboundRunner",
    "route_of",
]
