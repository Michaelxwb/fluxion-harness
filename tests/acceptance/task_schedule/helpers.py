"""TASK-041 验收共用：从真实 control schema 组装 Resolved 契约与提交上下文。"""

from __future__ import annotations

import uuid
from typing import Any, cast

from muad_contracts import (
    ResolvedAgent,
    ResolvedModel,
    ResolvedSkill,
    SkillExecutionMode,
)
from sqlalchemy import text

from .environment import LiveStack, run_db


def load_resolved(live_stack: LiveStack) -> dict[str, Any]:
    """按真实 control 行组装 agent/model/skill（不 mock resolve）。"""

    async def query(factory: Any) -> dict[str, Any]:
        async with factory() as session:
            agent = (
                await session.execute(
                    text(
                        "SELECT id, key, revision, instructions, model_id FROM control.agent_definition "
                        "WHERE id = :id"
                    ),
                    {"id": live_stack.agent_id},
                )
            ).one()
            model = (
                await session.execute(
                    text(
                        "SELECT id, revision, model_id, base_url FROM control.model_definition "
                        "WHERE id = :id"
                    ),
                    {"id": agent[4]},
                )
            ).one()
            artifact = (
                await session.execute(
                    text(
                        "SELECT a.id, a.checksum, a.storage_key FROM control.skill_artifact a "
                        "JOIN control.skill s ON s.current_artifact_id = a.id "
                        "WHERE s.id = :skill_id"
                    ),
                    {"skill_id": live_stack.skill_id},
                )
            ).one()
            skill_key = (
                await session.execute(
                    text("SELECT key FROM control.skill WHERE id = :id"),
                    {"id": live_stack.skill_id},
                )
            ).scalar_one()
        return {
            "agent": ResolvedAgent(
                id=agent[0],
                key=agent[1],
                revision=agent[2],
                instructions=agent[3] or "",
                runtime_config={},
            ),
            "model": ResolvedModel(
                id=model[0],
                revision=model[1],
                model_id=model[2],
                base_url=model[3],
                params={},
            ),
            "skill": ResolvedSkill(
                skill_id=live_stack.skill_id,
                artifact_id=artifact[0],
                key=skill_key,
                name=skill_key,
                description="",
                version="1.0.0",
                checksum=artifact[1],
                storage_key=artifact[2],
                execution_mode=SkillExecutionMode.ASYNC,
                frontmatter={},
            ),
        }

    return cast(dict[str, Any], run_db(query))


def submission_context(live_stack: LiveStack) -> Any:
    from muad_agent_runtime.application.task_client import TaskSubmissionContext

    resolved = load_resolved(live_stack)
    # 真实 Agent 往往绑定多个 Skill：放一个排在前面的无效 Skill，
    # 证明 Worker 按 skill_artifact_id 执行目标 Skill 而不是取快照第一个。
    decoy = resolved["skill"].model_copy(
        update={
            "skill_id": uuid.uuid4(),
            "artifact_id": uuid.uuid4(),
            "key": "e2e_decoy_skill",
            "storage_key": "skills/e2e-decoy/missing.zip",
        }
    )
    return (
        TaskSubmissionContext(
            tenant_id=live_stack.tenant_id,
            actor_user_id=live_stack.platform_user_id,
            agent=resolved["agent"],
            model=resolved["model"],
            skills=(decoy, resolved["skill"]),
            source_run_id=uuid.uuid4(),
        ),
        resolved,
    )
