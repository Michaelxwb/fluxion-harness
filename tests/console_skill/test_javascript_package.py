"""Real ZIP validation, local artifact caching and Node execution; no mocked boundary."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from muad_agent_core.skill import ScriptSkillExecutor, SkillExecutionRequest, SkillExecutionStatus
from muad_agent_worker.delivery.messages import build_delivery_message
from muad_agent_worker.infrastructure.models.task import TaskExecution
from muad_agent_worker.worker.execution_outcomes import OutcomeKind, interpret_execution
from muad_api import AppError
from muad_artifact_store import NfsArtifactStore, SkillArtifactCache
from muad_console_platform.infrastructure.skill_validator import checksum_of, validated_package
from muad_skill_sdk import SkillPackage, locate_package_root

from console_skill.packages import skill_md, zip_bytes


@pytest.mark.parametrize("wrapped", [False, True])
async def test_greeting_zip_import_cache_and_node_execution(tmp_path: Path, wrapped: bool) -> None:
    prefix = "greeting/" if wrapped else ""
    data = zip_bytes(
        {
            prefix + "SKILL.md": skill_md("greeting"),
            prefix + "muad.skill.json": json.dumps({"runtime": "script", "entrypoint": "scripts/run.mjs"}),
            prefix + "scripts/run.mjs": 'process.stdout.write("你好，见到你很高兴\\n");',
        }
    )
    with validated_package(data) as package:
        assert package.default_key == "greeting"
    store = NfsArtifactStore(tmp_path / "store")
    store.write("skills/greeting/skill.zip", data)
    cache = SkillArtifactCache(store, tmp_path / "cache")
    ready = await cache.ensure(
        artifact_id="greeting", storage_key="skills/greeting/skill.zip", checksum=checksum_of(data)
    )
    package_root, _ = locate_package_root(ready)
    assert [path.name for path in SkillPackage.load(package_root).scripts()] == ["run.mjs"]
    result = await ScriptSkillExecutor().execute(
        SkillExecutionRequest(
            ready_dir=ready,
            input={},
            timeout_sec=5,
        )
    )
    assert result.status is SkillExecutionStatus.SUCCEEDED
    assert result.stdout == "你好，见到你很高兴\n"
    outcome = interpret_execution(
        {"status": str(result.status), "result": result.result, "stdout": result.stdout},
        now=datetime.now(UTC),
    )
    assert outcome.kind is OutcomeKind.COMPLETED
    assert outcome.result == {"text": "你好，见到你很高兴"}
    message = build_delivery_message(
        TaskExecution(intent_key="greeting", status="COMPLETED", result_json=outcome.result),
        "zh-CN",
    )
    assert "你好，见到你很高兴" in message.text


def test_structured_result_wins_over_stdout_logs() -> None:
    outcome = interpret_execution(
        {"status": "SUCCEEDED", "result": {"summary": "structured"}, "stdout": "log"},
        now=datetime.now(UTC),
    )
    assert outcome.result == {"summary": "structured"}


def test_failed_script_stdout_is_not_a_completed_result() -> None:
    outcome = interpret_execution(
        {"status": "FAILED", "result": None, "stdout": "partial", "stderr": "boom"},
        now=datetime.now(UTC),
    )
    assert outcome.kind is OutcomeKind.RETRYABLE_FAILURE
    assert outcome.result is None


@pytest.mark.parametrize("extension", ["mjs", "cjs"])
def test_javascript_secret_scan_remains_active(extension: str) -> None:
    secret = "AKIAIOSFODNN7EXAMPLE"
    data = zip_bytes({"SKILL.md": skill_md(), f"scripts/run.{extension}": f'const key="{secret}"; '})
    with pytest.raises(AppError) as error:
        with validated_package(data):
            pass
    assert error.value.code == "SKILL_PACKAGE_INVALID"
    assert secret not in str(error.value)
