"""SkillsCommand：查看当前真正可用的 Skill（设计 §13）。

只读查询，不执行模型；有效集合由 CapabilityQueryService 计算
（Agent 声明 ∩ 用户授权 ∩ Published ∩ 闭包）。
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
)
from fluxion.commands.errors import COMMAND_USAGE_INVALID, OK
from fluxion.services.capability_query_service import CapabilityQueryService


class SkillsCommandHandler(CommandHandler):
    """/skills：列出当前 Agent + 当前用户真正可用的 Skill。"""

    def __init__(self, capabilities: CapabilityQueryService) -> None:
        self._capabilities = capabilities
        self._descriptor = CommandDescriptor(
            name="skills",
            usage="/skills",
            description="查看可用 Skill",
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
        if invocation.args:
            return ImmediateReply(command="skills", code=COMMAND_USAGE_INVALID, message="用法：/skills")
        if context.platform_user_id is None:
            return ImmediateReply(
                command="skills", code=COMMAND_USAGE_INVALID, message="当前上下文无法查询 Skill"
            )
        skills = await self._capabilities.list_effective_skills(
            context.tenant_id, context.platform_user_id, context.agent_id
        )
        if not skills:
            return ImmediateReply(command="skills", code=OK, message="当前没有可用 Skill")
        lines = ["可用 Skills："]
        for skill in skills:
            blurb = skill.description or skill.name
            lines.append(f"- {skill.skill_id} — {blurb}")
        lines.append("")
        lines.append("使用：/skill <skill_id> [问题]")
        return ImmediateReply(command="skills", code=OK, message="\n".join(lines))
