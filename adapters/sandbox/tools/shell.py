import asyncio
import os
from pathlib import Path

from framework.web.errors import AppError


async def run_shell(
    root: Path,
    *,
    argv: list[str],
    timeout_seconds: int,
    allowed_executables: set[str],
    max_output_bytes: int,
) -> dict[str, object]:
    if not argv or not all(isinstance(item, str) and item for item in argv):
        raise AppError(code="SANDBOX_SHELL_ARGV_INVALID", message="argv must be a non-empty string list", status_code=400)

    executable = Path(argv[0]).name
    if executable not in allowed_executables:
        raise AppError(
            code="SANDBOX_EXECUTABLE_FORBIDDEN",
            message=f"executable '{executable}' is not allowed",
            status_code=403,
        )

    tmp = root / ".tmp"
    tmp.mkdir(exist_ok=True)
    env = {
        "PATH": os.environ.get("PATH", ""),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "HOME": str(root),
        "TMPDIR": str(tmp),
    }
    process = await asyncio.create_subprocess_exec(
        *argv,
        cwd=root,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
    except TimeoutError as exc:
        process.kill()
        await process.wait()
        raise AppError(code="SANDBOX_SHELL_TIMEOUT", message="command timed out", status_code=408) from exc

    stdout_truncated = len(stdout) > max_output_bytes
    stderr_truncated = len(stderr) > max_output_bytes
    return {
        "argv": argv,
        "exit_code": process.returncode,
        "stdout": stdout[:max_output_bytes].decode("utf-8", errors="replace"),
        "stderr": stderr[:max_output_bytes].decode("utf-8", errors="replace"),
        "stdout_truncated": stdout_truncated,
        "stderr_truncated": stderr_truncated,
    }
