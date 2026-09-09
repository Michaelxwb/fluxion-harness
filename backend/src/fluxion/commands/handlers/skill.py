"""SkillCommand：本轮显式选择某个 Skill 执行（设计 §14）。

命令层只解析意图（skill_id + 原始 prompt），不构造"已授权 Skill"：
是否允许由 Runtime 在同一次 ExecutionSnapshot 中校验（§4.2），本层仅做
前置可用性检查以给出即时拒绝（defense in depth，最终以 Snapshot 为准）。

约束：用户不得 pin 版本（`skill@v2` 拒绝）；空 prompt 用明确默认输入，
不送空串进模型（§14.5）；拒绝时不回显 prompt 正文（§22.1）。
"""

from __future__ import annotations

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
    RuntimeInvocation,
)
from fluxion.commands.errors import COMMAND_USAGE_INVALID, SKILL_NOT_AVAILABLE
from fluxion.services.capability_query_service import CapabilityQueryService
from fluxion.services.runtime_contracts import InvocationDirective, InvocationKind

MAX_SKILL_ID_LEN = 128


def default_skill_prompt(skill_id: str) -> str:
    """空 prompt 时的明确系统输入（§14.5）。"""
    return (
        f"用户已显式激活 Skill {skill_id}，请按照该 Skill 的交互入口开始，"
        "并向用户询问完成任务所需的信息。"
    )


class SkillCommandHandler(CommandHandler):
    """/skill <skill_id> [prompt]：显式激活 Skill，复用 Runtime 执行路径。"""

    def __init__(self, capabilities: CapabilityQueryService) -> None:
        self._capabilities = capabilities
        self._descriptor = CommandDescriptor(
            name="skill",
            usage="/skill <skill_id> [prompt]",
            description="使用指定 Skill",
            auth_scope=CommandAuthScope.AUTHENTICATED,
            channel_scope=CommandChannelScope.ALL,
            category=CommandCategory.CAPABILITY,
        )

    @property
    def descriptor(self) -> CommandDescriptor:
        return self._descriptor

    async def execute(
        self,
        context: CommandContext,
        invocation: CommandInvocation,
    ) -> CommandOutcome:
        if not invocation.args:
            return ImmediateReply(
                command="skill", code=COMMAND_USAGE_INVALID, message="用法：/skill <skill_id> [prompt]"
            )
        skill_id = invocation.args[0]
        if "@" in skill_id or len(skill_id) > MAX_SKILL_ID_LEN:
            # 禁止 pin 资源版本（§14.1）；超长 id 不可能是合法 skill。
            return ImmediateReply(
                command="skill", code=COMMAND_USAGE_INVALID, message="用法：/skill <skill_id> [prompt]"
            )
        if context.platform_user_id is None:
            return ImmediateReply(
                command="skill", code=COMMAND_USAGE_INVALID, message="当前上下文无法使用 Skill"
            )
        effective = await self._capabilities.list_effective_skills(
            context.tenant_id, context.platform_user_id, context.agent_id
        )
        if skill_id not in {skill.skill_id for skill in effective}:
            return ImmediateReply(
                command="skill",
                code=SKILL_NOT_AVAILABLE,
                message=f"Skill {skill_id} 当前不可用，发送 /skills 查看可用列表",
            )
        prompt = _prompt_after_skill_id(invocation.remainder, skill_id)
        if not prompt:
            prompt = default_skill_prompt(skill_id)
        return RuntimeInvocation(
            prompt=prompt,
            directive=InvocationDirective(kind=InvocationKind.SKILL, capability_id=skill_id),
        )


def _prompt_after_skill_id(remainder: str, skill_id: str) -> str:
    """取 skill_id 之后的原始 prompt（内部 spacing 保留，仅去分隔空白）。"""
    if remainder.startswith(skill_id):
        return remainder[len(skill_id):].lstrip(" \t")
    return ""
