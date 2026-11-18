"""TASK-024：定时 Skill/Agent 契约文档必须覆盖关键事实。"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "docs/operations/scheduled-skill-contract.md"
MIGRATION = ROOT / "docs/operations/legacy-skill-migration.md"


def test_scheduled_skill_contract_covers_payload_and_shared_contract() -> None:
    text = CONTRACT.read_text(encoding="utf-8")

    for keyword in (
        "Job = schedule + payload + session + delivery + policy",
        "`REMINDER` / `SKILL_PLAN` / `AGENT_TURN`",
        "`input` 只写 stdin",
        "`argv` 追加到脚本参数",
        "Web pull 不是 `delivery_intent`",
        "`record_result(no_change)`",
        "`PlatformSettings.agent`",
        "`MessagePayload {text, attachments[]}`",
        "`delivery=PUSH`",
        "试跑是真实执行",
    ):
        assert keyword in text


def test_legacy_migration_has_scheduled_task_section() -> None:
    text = MIGRATION.read_text(encoding="utf-8")

    assert "## 7. 定时任务迁移" in text
    assert "scheduled-skill-contract.md" in text
    assert "`input` 走 stdin" in text
    assert "`argv` 追加到脚本" in text
    assert "`record_result(no_change)`" in text
    assert "`delivery=PUSH`" in text
