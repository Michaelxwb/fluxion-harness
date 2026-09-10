from pathlib import Path

from adapters.sandbox.path_guard import resolve_confined_path, validate_glob_pattern


def glob_files(root: Path, pattern: str, *, max_results: int) -> dict[str, object]:
    pattern = validate_glob_pattern(pattern)
    results: list[str] = []
    for candidate in root.glob(pattern):
        relative = str(candidate.relative_to(root))
        resolve_confined_path(root, relative)
        results.append(relative)
        if len(results) >= max_results:
            break
    return {"pattern": pattern, "matches": sorted(results), "truncated": len(results) >= max_results}
