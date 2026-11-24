"""Real draft/archive HTTP and subprocess review through a complete Runtime turn."""

import hashlib
import io
import json
import uuid
import zipfile
from pathlib import Path

from muad_contracts import ResolvedSkill
from sqlalchemy import text

from .conftest import run_db
from .test_run_lifecycle import _fresh_conversation, _parse_sse, _payload

KEY = "acceptance-review-draft"
REVIEW_KEY = "self-skill-review"
MANIFEST = json.dumps({"name": KEY, "author": "acceptance", "platforms": []})
REVIEW_ARGS = ("--target", f"skill-drafts/{KEY}", "--format", "json")


def seed_review_skill(stack, db):
    from muad_console_platform.infrastructure.models.control import AgentSkillBinding, Skill, SkillArtifact

    directory = Path(__file__).resolve().parents[3] / "skills/self-skill-review"
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as package:
        for path in directory.rglob("*"):
            if path.is_file() and path.suffix != ".pyc":
                package.write(path, path.relative_to(directory))
    data = archive.getvalue()
    checksum = "sha256:" + hashlib.sha256(data).hexdigest()
    skill_id, artifact_id = uuid.uuid4(), uuid.uuid4()
    storage_key = f"skills/{skill_id}/{artifact_id}/skill.zip"
    stored = stack.artifact_root / storage_key
    stored.parent.mkdir(parents=True)
    stored.write_bytes(data)
    frontmatter = {
        "result_contract": {
            "version": 1,
            "profile": "skill-review",
            "checksum": checksum,
            "entrypoint": "scripts/check.py",
        }
    }

    async def insert(factory):
        async with factory.begin() as session:
            skill = Skill(
                id=skill_id,
                tenant_id=stack.tenant_id,
                key=REVIEW_KEY,
                name=REVIEW_KEY,
                description="Review a draft",
                enabled=True,
                user_scope="ALL",
            )
            session.add(skill)
            await session.flush()
            session.add(
                SkillArtifact(
                    id=artifact_id,
                    skill_id=skill_id,
                    version="0.1.4",
                    checksum=checksum,
                    storage_key=storage_key,
                    frontmatter_json=frontmatter,
                    execution_mode="SYNC",
                    package_size=len(data),
                    validation_status="VALID",
                    created_by=stack.platform_user_id,
                )
            )
            await session.flush()
            skill.current_artifact_id = artifact_id
            session.add(AgentSkillBinding(agent_id=stack.agent_id, skill_id=skill_id, sort_order=0))

    db(insert)
    return ResolvedSkill(
        skill_id=skill_id,
        artifact_id=artifact_id,
        key=REVIEW_KEY,
        name=REVIEW_KEY,
        description="Review a draft",
        version="0.1.4",
        checksum=checksum,
        storage_key=storage_key,
        frontmatter=frontmatter,
        source="ADMIN",
    )


def seed_review_draft(http, stack):
    url = f"{stack.console_url}/internal/skills/drafts"
    identity = {
        "tenant_id": stack.tenant_id,
        "owner_user_id": str(stack.platform_user_id),
        "agent_id": str(stack.agent_id),
    }
    response = http.post(url, headers=stack.service_headers(), json={**identity, "key": KEY})
    response.raise_for_status()
    draft = response.json()["data"]
    response = http.put(
        f"{url}/{draft['id']}/files",
        headers=stack.service_headers(),
        json={
            **identity,
            "expected_revision": draft["revision"],
            "path": "SKILL.md",
            "content": f"---\nname: {KEY}\ndescription: Acceptance review\n---\n\n# Acceptance review\n",
        },
    )
    response.raise_for_status()
    return draft["id"], response.json()["data"]["revision"]


def test_real_review_findings_then_repaired_draft(live_stack, http):
    seed_review_skill(live_stack, run_db)
    draft_id, revision = seed_review_draft(http, live_stack)
    execute = {
        "name": "execute",
        "arguments": json.dumps(
            {
                "target": "skill",
                "skill_key": REVIEW_KEY,
                "input": {},
                "argv": REVIEW_ARGS,
            }
        ),
    }
    revise = {
        "name": "skill_draft",
        "arguments": json.dumps(
            {
                "action": "write",
                "draft_id": draft_id,
                "path": "muad.skill.json",
                "content": MANIFEST,
                "expected_revision": revision,
            }
        ),
    }
    conversation = _fresh_conversation(http, live_stack)
    http.post(f"{live_stack.llm_url}/script", json={"tools": [execute, revise, execute]}).raise_for_status()
    try:
        reply = http.post(
            f"{live_stack.runtime_url}/v1/runs",
            headers=live_stack.runtime_headers(),
            json=_payload(live_stack, "Review draft", conversation_id=conversation),
        )
        reply.raise_for_status()
        terminal = _parse_sse(reply.text)[-1]
        assert terminal["type"] == "run.completed"
        assert terminal["data"]["status"] == "COMPLETED"
    finally:
        http.post(f"{live_stack.llm_url}/script", json={}).raise_for_status()

    async def audit(factory):
        async with factory() as session:
            rows = await session.execute(
                text("""
                SELECT a.execution_status, a.business_status, a.exit_code FROM runtime.tool_call_audit a
                WHERE a.tenant_id=:tenant AND a.conversation_id=:conversation AND a.tool_name='execute'
                ORDER BY a.create_time, a.id
            """),
                {"tenant": live_stack.tenant_id, "conversation": conversation},
            )
            return [tuple(row) for row in rows]

    assert run_db(audit) == [("FAILED", "FAILED", 1), ("COMPLETED", "SUCCEEDED", 0)]
