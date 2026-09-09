"""BindCommand：IM 渠道首次身份绑定（设计 §9）。

绑定兑换继续复用 `redeem_bind_code()` 原子语义；兑换失败抛 ChannelBindError
（边界异常，保持 `api/channel.py` 与既有测试的错误映射契约）。
`/bind` code 只参与哈希计算，永不进入日志/audit/trace（§25.9）。
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import datetime
from uuid import uuid4

from fluxion.commands.contracts import (
    CommandAuthScope,
    CommandCategory,
    CommandChannelScope,
    CommandContext,
    CommandDescriptor,
    CommandHandler,
    CommandInvocation,
    CommandOutcome,
    ImmediateReply,
)
from fluxion.commands.errors import (
    ALREADY_BOUND,
    BOUND,
    COMMAND_NOT_AVAILABLE,
    COMMAND_USAGE_INVALID,
    ChannelBindError,
)
from fluxion.registry import BindCodeRejected, BindRedemption, ChannelRegistryStore


def hash_bind_code(code: str) -> str:
    """绑定码哈希（SHA-256）。code 明文永不入库/入日志（§25.9）。"""
    return hashlib.sha256(code.strip().encode("utf-8")).hexdigest()


class BindCommandHandler(CommandHandler):
    """`/bind <code>`：唯一允许 `authenticated=False` 的命令。"""

    def __init__(
        self,
        store: ChannelRegistryStore,
        clock: Callable[[], datetime],
    ) -> None:
        self._store = store
        self._clock = clock
        self._descriptor = CommandDescriptor(
            name="bind",
            usage="/bind <code>",
            description="绑定 IM 渠道身份",
            auth_scope=CommandAuthScope.ANONYMOUS,
            # ALL + 处理器内分支：匿名（任意通道）允许兑换（H1 自举路径）；
            # 已绑定 + Web 不可用（§9.3），已绑定 + IM 回 already_bound。
            channel_scope=CommandChannelScope.ALL,
            category=CommandCategory.IDENTITY,
        )

    @property
    def descriptor(self) -> CommandDescriptor:
        return self._descriptor

    async def execute(
        self,
        context: CommandContext,
        invocation: CommandInvocation,
    ) -> CommandOutcome:
        if context.authenticated:
            if context.channel_type == "web":
                return ImmediateReply(
                    command="bind",
                    code=COMMAND_NOT_AVAILABLE,
                    message="当前通道不支持 /bind",
                )
            return ImmediateReply(
                command="bind",
                code=ALREADY_BOUND,
                message="已经绑定，无需重复绑定",
            )
        if len(invocation.args) != 1:
            return ImmediateReply(
                command="bind",
                code=COMMAND_USAGE_INVALID,
                message="用法：/bind <code>",
            )
        if context.channel_user_id is None:
            return ImmediateReply(
                command="bind",
                code=COMMAND_USAGE_INVALID,
                message="当前通道无法绑定身份",
            )
        try:
            identity = await self._store.redeem_bind_code(
                BindRedemption(
                    tenant_id=context.tenant_id,
                    channel_type=context.channel_type,
                    channel_user_id=context.channel_user_id,
                    code_hash=hash_bind_code(invocation.args[0]),
                    request_id=context.request_id,
                    audit_id=f"audit_{uuid4().hex}",
                    now=self._clock(),
                )
            )
        except BindCodeRejected as exc:
            raise ChannelBindError(exc.reason) from exc
        return ImmediateReply(
            command="bind",
            code=BOUND,
            message="身份绑定成功",
            data={"platform_user_id": identity.platform_user_id},
        )
