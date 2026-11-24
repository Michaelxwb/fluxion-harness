"""A real Worker uses the reviewed draft identity pinned at trusted submission."""

import hashlib

from muad_agent_runtime.application.task_client import build_task_snapshot
from muad_contracts import snapshot_hash

from tests.acceptance.runtime.test_skill_results_conversation import (
    KEY,
    REVIEW_ARGS,
    seed_review_draft,
    seed_review_skill,
)

from .environment import run_db
from .helpers import submission_context
from .test_execution import _wait_terminal


def test_worker_preserves_verified_findings_as_completed(live_stack, http):
    skill = seed_review_skill(live_stack, run_db)
    draft_id, revision = seed_review_draft(http, live_stack)
    headers = live_stack.service_headers()
    archive = http.get(
        f"{live_stack.console_url}/internal/skills/drafts/{draft_id}/archive",
        headers=headers,
        params={
            "tenant_id": live_stack.tenant_id,
            "owner_user_id": str(live_stack.platform_user_id),
            "agent_id": str(live_stack.agent_id),
        },
    )
    archive.raise_for_status()
    context, _ = submission_context(live_stack)
    snapshot, _ = build_task_snapshot(context, skill, argv=REVIEW_ARGS)
    snapshot["review_draft"] = {
        "key": KEY,
        "draft_id": draft_id,
        "revision": revision,
        "checksum": "sha256:" + hashlib.sha256(archive.content).hexdigest(),
    }
    submitted = http.post(
        f"{live_stack.worker_url}/internal/tasks",
        headers=headers,
        json={
            "tenant_id": context.tenant_id,
            "agent_id": str(context.agent.id),
            "actor_user_id": str(context.actor_user_id),
            "task_type": "SKILL",
            "skill_id": str(skill.skill_id),
            "skill_artifact_id": str(skill.artifact_id),
            "skill_execution_mode": "SYNC",
            "input": {},
            "execution_snapshot": snapshot,
            "execution_snapshot_schema_version": snapshot["schema_version"],
            "snapshot_hash": snapshot_hash(snapshot),
            "idempotency_key": "acceptance-review-findings",
        },
    )
    submitted.raise_for_status()
    detail = _wait_terminal(http, live_stack, submitted.json()["data"]["task_id"])
    assert detail["status"] == "COMPLETED", detail
    assert detail["result"]["execution_status"] == "COMPLETED"
    assert detail["result"]["business_status"] == "REVIEW_FINDINGS"
    assert detail["result"]["exit_code"] == 1
    assert detail["result"]["result"]["reviewed_draft"]["revision"] == revision
