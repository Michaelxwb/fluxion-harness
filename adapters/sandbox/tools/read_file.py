from pathlib import Path

from framework.web.errors import AppError


def read_file(path: Path, *, max_bytes: int) -> dict[str, object]:
    if not path.is_file():
        raise AppError(code="SANDBOX_NOT_FILE", message="path is not a file", status_code=400)
    size = path.stat().st_size
    if size > max_bytes:
        raise AppError(code="SANDBOX_READ_TOO_LARGE", message="file exceeds max read size", status_code=413)
    return {"content": path.read_text(encoding="utf-8"), "size": size}
