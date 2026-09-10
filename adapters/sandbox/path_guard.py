from pathlib import Path

from framework.web.errors import AppError


def resolve_confined_path(root: Path, relative_path: str, *, must_exist: bool = False) -> Path:
    candidate_input = Path(relative_path)
    if not relative_path or candidate_input.is_absolute():
        raise AppError(
            code="SANDBOX_PATH_INVALID",
            message="path must be a non-empty workspace-relative path",
            status_code=400,
        )
    if ".." in candidate_input.parts:
        raise AppError(code="SANDBOX_PATH_ESCAPE", message="path traversal is not allowed", status_code=400)

    root = root.resolve()
    candidate = root / candidate_input
    resolved = candidate.resolve() if candidate.exists() or candidate.is_symlink() else candidate.parent.resolve() / candidate.name
    if not resolved.is_relative_to(root):
        raise AppError(code="SANDBOX_PATH_ESCAPE", message="path escapes workspace root", status_code=400)
    if must_exist and not resolved.exists():
        raise AppError(code="SANDBOX_FILE_NOT_FOUND", message="workspace path does not exist", status_code=404)
    return resolved


def validate_glob_pattern(pattern: str) -> str:
    path = Path(pattern)
    if not pattern or path.is_absolute() or ".." in path.parts:
        raise AppError(
            code="SANDBOX_GLOB_INVALID",
            message="glob pattern must stay inside the workspace",
            status_code=400,
        )
    return pattern
