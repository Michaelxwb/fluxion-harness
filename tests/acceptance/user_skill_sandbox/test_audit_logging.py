""" [S-41][RULE-log-001] Skill 执行日志走 logging-kit：单行、关联字段、脱敏。

真实边界：
- `SkillToolSet._record_execution` → 真实 logger → `configure_logging` 落盘文本；
- `api-kit.context_scope` 把 trace/run/agent 关联事实桥接进 logging-kit contextvar；
- `RedactionFilter` 对 Cookie/Authorization/api_key 等敏感字段真实脱敏。

断言读取真实日志文件，不 mock Formatter/Filter/Logger。
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, cast

from muad_agent_core.skill.contracts import SkillExecutionResult, SkillExecutionStatus
from muad_agent_runtime.application.skill_tools import SkillToolSet
from muad_api.context import context_scope
from muad_contracts import ResolvedSkill, SkillExecutionMode, SkillSource
from muad_logging import configure_logging

SECRET_CANARY = "s41-log-cookie-token-canary-do-not-leak"


def _reset_root_handlers() -> None:
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)


def _log_lines(tmp_path, service: str, message: str) -> list[str]:
    day = datetime.now().astimezone().strftime("%Y-%m-%d")
    text = (tmp_path / service / f"{day}.log").read_text(encoding="utf-8")
    return [line for line in text.splitlines() if message in line]


def _skill(key: str = "demo-skill") -> ResolvedSkill:
    return ResolvedSkill(
        skill_id=uuid.uuid4(),
        artifact_id=uuid.uuid4(),
        key=key,
        name="Demo Skill",
        description="log acceptance skill",
        version="1.0.0",
        checksum="sha256:" + "a" * 64,
        storage_key="skills/demo-skill/1.0.0/skill.zip",
        execution_mode=SkillExecutionMode.SYNC,
        source=SkillSource.USER,
    )


def test_s41_skill_execution_log_is_single_line_and_correlated(tmp_path) -> None:
    service = f"test-s41-log-{uuid.uuid4().hex[:8]}"
    _reset_root_handlers()
    configure_logging(service, log_dir=tmp_path, console=False)
    tenant = f"acc-s41-log-{uuid.uuid4().hex[:8]}"
    trace_id = f"trace-{uuid.uuid4().hex[:8]}"
    request_id = f"req-{uuid.uuid4().hex[:8]}"
    run_id = f"run-{uuid.uuid4()}"
    agent_id = str(uuid.uuid4())
    execution_id = f"exec-{uuid.uuid4().hex[:8]}"
    skill = _skill()
    tool_set = SkillToolSet(
        cache=cast(Any, object()),
        skills=(skill,),
        timeout_sec=1.0,
        executor=cast(Any, object()),
        execution_id=execution_id,
    )

    try:
        with context_scope(
            tenant_id=tenant,
            trace_id=trace_id,
            request_id=request_id,
            run_id=run_id,
            agent_id=agent_id,
            skill_artifact_id=str(skill.artifact_id),
        ):
            tool_set._record_execution(  # noqa: SLF001 - 执行日志是 TASK-013 的被测边界
                skill,
                SkillExecutionResult(
                    status=SkillExecutionStatus.SUCCEEDED,
                    duration_ms=12,
                ),
            )
    finally:
        logging.shutdown()
        _reset_root_handlers()

    lines = _log_lines(tmp_path, service, "skill_execution")
    assert len(lines) == 1, f"执行日志必须是单行，实际：{lines!r}"
    line = lines[0]
    for expected in (
        f"trace_id={trace_id}",
        f"request_id={request_id}",
        f"run_id={run_id}",
        f"agent_id={agent_id}",
        f"skill_artifact_id={skill.artifact_id}",
        f"tenant_id={tenant}",
        "skill_key=demo-skill",
        f"execution_id={execution_id}",
        "source=USER",
        "status=SUCCEEDED",
        "duration_ms=12",
    ):
        assert expected in line, f"缺少关联字段 {expected!r}: {line}"
    assert SECRET_CANARY not in line


def test_s41_sensitive_log_fields_are_redacted(tmp_path) -> None:
    """Cookie/Authorization/api_key 走 logging-kit 的真实脱敏，不落原文。"""
    service = f"test-s41-redact-{uuid.uuid4().hex[:8]}"
    _reset_root_handlers()
    configure_logging(service, log_dir=tmp_path, console=False)
    try:
        logging.getLogger("muad_console_platform.skill").info(
            "skill_cleanup",
            extra={
                "skill_key": "demo-skill",
                "cookie": SECRET_CANARY,
                "authorization": f"Bearer {SECRET_CANARY}",
                "api_key": SECRET_CANARY,
            },
        )
    finally:
        logging.shutdown()
        _reset_root_handlers()

    lines = _log_lines(tmp_path, service, "skill_cleanup")
    assert len(lines) == 1, f"清理日志必须是单行，实际：{lines!r}"
    line = lines[0]
    assert "skill_key=demo-skill" in line
    assert SECRET_CANARY not in line, "日志不得包含 Cookie/Token/密钥原文"
    assert "cookie=***" in line
    assert "authorization=***" in line
    assert "api_key=***" in line
