from pathlib import Path
from uuid import uuid4

import pytest

from adapters.sandbox import (
    LocalSandboxExecutor,
    LocalWorkspaceManager,
    SandboxCapabilityProvider,
    register_builtin_sandbox_capabilities,
)
from framework.capability.registry import CapabilityRegistry
from framework.capability.runtime import CapabilityRuntime
from framework.contracts.context import TrustedExecutionContext
from framework.web.errors import AppError
from framework.workspace.models import WorkspaceOwnerType
from framework.workspace.policy import SandboxPolicy


@pytest.mark.asyncio
async def test_workspace_file_read_write_edit_glob_and_grep(tmp_path: Path):
    manager = LocalWorkspaceManager(tmp_path)
    workspace = await manager.create(
        tenant_id="tenant-a",
        owner_type=WorkspaceOwnerType.EXECUTION,
        owner_id=uuid4(),
    )
    executor = LocalSandboxExecutor(manager)
    provider = SandboxCapabilityProvider(executor)
    registry = CapabilityRegistry()
    register_builtin_sandbox_capabilities(registry, provider)
    runtime = CapabilityRuntime(registry)

    context = TrustedExecutionContext(
        actor_user_id=uuid4(),
        tenant_id="tenant-a",
        workspace_id=workspace.id,
        effective_capability_set={
            "filesystem.read",
            "filesystem.write",
            "filesystem.edit",
            "filesystem.glob",
            "filesystem.grep",
        },
    )

    await runtime.invoke(
        name="filesystem.write",
        input={"path": "reports/result.md", "content": "alpha\nbeta\n"},
        context=context,
    )
    result = await runtime.invoke(
        name="filesystem.read",
        input={"path": "reports/result.md"},
        context=context,
    )
    assert result.data["content"] == "alpha\nbeta\n"

    await runtime.invoke(
        name="filesystem.edit",
        input={"path": "reports/result.md", "old_text": "beta", "new_text": "gamma"},
        context=context,
    )

    glob_result = await runtime.invoke(
        name="filesystem.glob",
        input={"pattern": "reports/*.md"},
        context=context,
    )
    assert glob_result.data["matches"] == ["reports/result.md"]

    grep_result = await runtime.invoke(
        name="filesystem.grep",
        input={"pattern": "gamma", "glob": "reports/*.md"},
        context=context,
    )
    assert grep_result.data["matches"][0]["line"] == 2


@pytest.mark.asyncio
async def test_workspace_rejects_absolute_and_parent_escape(tmp_path: Path):
    manager = LocalWorkspaceManager(tmp_path)
    workspace = await manager.create(
        tenant_id="tenant-a",
        owner_type=WorkspaceOwnerType.CONVERSATION,
        owner_id=uuid4(),
    )
    provider = SandboxCapabilityProvider(LocalSandboxExecutor(manager))
    registry = CapabilityRegistry()
    register_builtin_sandbox_capabilities(registry, provider)
    runtime = CapabilityRuntime(registry)
    context = TrustedExecutionContext(
        actor_user_id=uuid4(),
        tenant_id="tenant-a",
        workspace_id=workspace.id,
        effective_capability_set={"filesystem.read", "filesystem.write"},
    )

    with pytest.raises(AppError) as absolute_error:
        await runtime.invoke(
            name="filesystem.write",
            input={"path": "/tmp/escape.txt", "content": "x"},
            context=context,
        )
    assert absolute_error.value.code == "SANDBOX_PATH_INVALID"

    with pytest.raises(AppError) as traversal_error:
        await runtime.invoke(
            name="filesystem.write",
            input={"path": "../escape.txt", "content": "x"},
            context=context,
        )
    assert traversal_error.value.code == "SANDBOX_PATH_ESCAPE"


@pytest.mark.asyncio
async def test_workspace_tenant_isolation(tmp_path: Path):
    manager = LocalWorkspaceManager(tmp_path)
    workspace = await manager.create(
        tenant_id="tenant-a",
        owner_type=WorkspaceOwnerType.EXECUTION,
        owner_id=uuid4(),
    )
    provider = SandboxCapabilityProvider(LocalSandboxExecutor(manager))
    registry = CapabilityRegistry()
    register_builtin_sandbox_capabilities(registry, provider)
    runtime = CapabilityRuntime(registry)

    context = TrustedExecutionContext(
        actor_user_id=uuid4(),
        tenant_id="tenant-b",
        workspace_id=workspace.id,
        effective_capability_set={"filesystem.read"},
    )

    with pytest.raises(AppError) as error:
        await runtime.invoke(
            name="filesystem.read",
            input={"path": "x.txt"},
            context=context,
        )
    assert error.value.code == "WORKSPACE_FORBIDDEN"


@pytest.mark.asyncio
async def test_shell_is_disabled_and_realtime_high_risk_is_blocked(tmp_path: Path):
    manager = LocalWorkspaceManager(tmp_path)
    workspace = await manager.create(
        tenant_id="tenant-a",
        owner_type=WorkspaceOwnerType.EXECUTION,
        owner_id=uuid4(),
    )
    provider = SandboxCapabilityProvider(
        LocalSandboxExecutor(manager, SandboxPolicy(allow_shell=False))
    )
    registry = CapabilityRegistry()
    register_builtin_sandbox_capabilities(registry, provider)
    runtime = CapabilityRuntime(registry)
    context = TrustedExecutionContext(
        actor_user_id=uuid4(),
        tenant_id="tenant-a",
        workspace_id=workspace.id,
        effective_capability_set={"shell.execute"},
    )

    with pytest.raises(AppError) as realtime_error:
        await runtime.invoke(
            name="shell.execute",
            input={"argv": ["python", "-V"]},
            context=context,
        )
    assert realtime_error.value.code == "CAPABILITY_REQUIRES_EXECUTION"

    with pytest.raises(AppError) as worker_error:
        await runtime.invoke(
            name="shell.execute",
            input={"argv": ["python", "-V"]},
            context=context,
            invocation_mode="worker",
        )
    assert worker_error.value.code == "SANDBOX_SHELL_DISABLED"


@pytest.mark.asyncio
async def test_shell_executable_allowlist(tmp_path: Path):
    manager = LocalWorkspaceManager(tmp_path)
    workspace = await manager.create(
        tenant_id="tenant-a",
        owner_type=WorkspaceOwnerType.EXECUTION,
        owner_id=uuid4(),
    )
    provider = SandboxCapabilityProvider(
        LocalSandboxExecutor(
            manager,
            SandboxPolicy(allow_shell=True, allowed_executables={"python"}),
        )
    )
    registry = CapabilityRegistry()
    register_builtin_sandbox_capabilities(registry, provider)
    runtime = CapabilityRuntime(registry)
    context = TrustedExecutionContext(
        actor_user_id=uuid4(),
        tenant_id="tenant-a",
        workspace_id=workspace.id,
        effective_capability_set={"shell.execute"},
    )

    with pytest.raises(AppError) as error:
        await runtime.invoke(
            name="shell.execute",
            input={"argv": ["sh", "-c", "echo unsafe"]},
            context=context,
            invocation_mode="worker",
        )
    assert error.value.code == "SANDBOX_EXECUTABLE_FORBIDDEN"
