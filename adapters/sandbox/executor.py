from pathlib import Path
from uuid import UUID

from adapters.sandbox.path_guard import resolve_confined_path
from adapters.sandbox.tools import edit_file, glob_files, grep_files, read_file, run_shell, write_file
from framework.contracts.context import TrustedExecutionContext
from framework.contracts.sandbox import SandboxResult
from framework.web.errors import AppError
from framework.workspace.contracts import WorkspaceManager
from framework.workspace.policy import SandboxPolicy


class LocalSandboxExecutor:
    """Development/trusted-environment executor.

    This is not a production security sandbox. It provides workspace path
    confinement, explicit shell opt-in, executable allowlisting, timeouts and
    output limits. Production should replace it with a container/namespace/VM
    backed implementation of the same SandboxExecutor contract.
    """

    def __init__(self, workspace_manager: WorkspaceManager, policy: SandboxPolicy | None = None):
        self.workspace_manager = workspace_manager
        self.policy = policy or SandboxPolicy()

    async def execute(
        self,
        *,
        workspace_id: UUID,
        operation: str,
        arguments: dict[str, object],
        context: TrustedExecutionContext,
    ) -> SandboxResult:
        workspace = await self.workspace_manager.get(workspace_id)
        if workspace.tenant_id != context.tenant_id:
            raise AppError(
                code="WORKSPACE_FORBIDDEN",
                message="workspace does not belong to current tenant",
                status_code=403,
            )

        if operation == "shell.execute":
            if not self.policy.allow_shell:
                raise AppError(
                    code="SANDBOX_SHELL_DISABLED", message="shell execution is disabled", status_code=403
                )
        elif operation not in self.policy.allowed_operations:
            raise AppError(
                code="SANDBOX_OPERATION_FORBIDDEN",
                message="sandbox operation is not allowed",
                status_code=403,
            )

        root = Path(workspace.root_ref).resolve()

        if operation == "filesystem.read":
            path = resolve_confined_path(root, str(arguments.get("path", "")), must_exist=True)
            return SandboxResult(data=read_file(path, max_bytes=self.policy.max_read_bytes))

        if operation == "filesystem.write":
            path = resolve_confined_path(root, str(arguments.get("path", "")))
            content = arguments.get("content")
            if not isinstance(content, str):
                raise AppError(
                    code="SANDBOX_CONTENT_INVALID", message="content must be a string", status_code=400
                )
            return SandboxResult(data=write_file(path, content, max_bytes=self.policy.max_write_bytes))

        if operation == "filesystem.edit":
            path = resolve_confined_path(root, str(arguments.get("path", "")), must_exist=True)
            old_text = arguments.get("old_text")
            new_text = arguments.get("new_text")
            if not isinstance(old_text, str) or not isinstance(new_text, str):
                raise AppError(
                    code="SANDBOX_EDIT_INVALID", message="old_text/new_text must be strings", status_code=400
                )
            return SandboxResult(
                data=edit_file(
                    path,
                    old_text=old_text,
                    new_text=new_text,
                    replace_all=bool(arguments.get("replace_all", False)),
                    max_bytes=self.policy.max_write_bytes,
                )
            )

        if operation == "filesystem.glob":
            return SandboxResult(
                data=glob_files(
                    root,
                    str(arguments.get("pattern", "**/*")),
                    max_results=self.policy.max_glob_results,
                )
            )

        if operation == "filesystem.grep":
            pattern = arguments.get("pattern")
            if not isinstance(pattern, str):
                raise AppError(
                    code="SANDBOX_GREP_INVALID", message="pattern must be a string", status_code=400
                )
            return SandboxResult(
                data=grep_files(
                    root,
                    pattern=pattern,
                    file_glob=str(arguments.get("glob", "**/*")),
                    max_results=self.policy.max_grep_results,
                    max_read_bytes=self.policy.max_read_bytes,
                )
            )

        if operation == "shell.execute":
            argv = arguments.get("argv")
            if not isinstance(argv, list):
                raise AppError(
                    code="SANDBOX_SHELL_ARGV_INVALID", message="argv must be a list", status_code=400
                )
            raw_timeout = arguments.get("timeout_seconds", self.policy.max_shell_timeout_seconds)
            requested = (
                int(raw_timeout)
                if isinstance(raw_timeout, (int, float, str))
                else self.policy.max_shell_timeout_seconds
            )
            timeout_seconds = min(max(requested, 1), self.policy.max_shell_timeout_seconds)
            return SandboxResult(
                data=await run_shell(
                    root,
                    argv=argv,
                    timeout_seconds=timeout_seconds,
                    allowed_executables=self.policy.allowed_executables,
                    max_output_bytes=self.policy.max_output_bytes,
                )
            )

        raise AppError(code="SANDBOX_OPERATION_UNKNOWN", message="unknown sandbox operation", status_code=400)
