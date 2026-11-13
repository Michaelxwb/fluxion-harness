"""Draft platform resolution uses the trusted parent Run identity."""

import pytest
from muad_agent_core.skill.draft_shell import DraftShellRequest
from muad_agent_runtime.application.draft_shell import build_draft_shell_runner
from muad_common import SharedSettings


def test_draft_platform_context_uses_parent_run() -> None:
    runner = build_draft_shell_runner(SharedSettings())
    request = DraftShellRequest(
        tenant_id="tenant", owner_user_id="user", draft_id="draft", command="true",
        execution_id="tool-call", run_id="parent-run",
    )
    context = runner._platform_context_factory(request, ("mssp",))
    assert context is not None
    assert (context.execution_type, context.execution_id) == ("RUN", "parent-run")
    assert context.actor_user_id == "user"
    assert context.platform_keys == ("mssp",)


def test_draft_platform_context_requires_parent_run() -> None:
    runner = build_draft_shell_runner(SharedSettings())
    request = DraftShellRequest(
        tenant_id="tenant", owner_user_id="user", draft_id="draft", command="true",
    )
    with pytest.raises(ValueError, match="run identity"):
        runner._platform_context_factory(request, ("mssp",))
