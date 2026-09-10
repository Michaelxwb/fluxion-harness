import re
from pathlib import Path

from adapters.sandbox.path_guard import resolve_confined_path, validate_glob_pattern
from framework.web.errors import AppError


def grep_files(
    root: Path,
    *,
    pattern: str,
    file_glob: str,
    max_results: int,
    max_read_bytes: int,
) -> dict[str, object]:
    if len(pattern) > 2048:
        raise AppError(code="SANDBOX_GREP_PATTERN_TOO_LONG", message="grep pattern is too long", status_code=400)
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        raise AppError(code="SANDBOX_GREP_PATTERN_INVALID", message=str(exc), status_code=400) from exc

    file_glob = validate_glob_pattern(file_glob)
    matches: list[dict[str, object]] = []
    for candidate in root.glob(file_glob):
        if not candidate.is_file():
            continue
        relative = str(candidate.relative_to(root))
        resolved = resolve_confined_path(root, relative, must_exist=True)
        if resolved.stat().st_size > max_read_bytes:
            continue
        try:
            content = resolved.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for line_no, line in enumerate(content.splitlines(), start=1):
            if regex.search(line):
                matches.append({"path": relative, "line": line_no, "text": line})
                if len(matches) >= max_results:
                    return {"matches": matches, "truncated": True}
    return {"matches": matches, "truncated": False}
