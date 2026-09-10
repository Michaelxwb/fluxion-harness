from pathlib import Path

from framework.web.errors import AppError


def write_file(path: Path, content: str, *, max_bytes: int) -> dict[str, object]:
    payload = content.encode("utf-8")
    if len(payload) > max_bytes:
        raise AppError(code="SANDBOX_WRITE_TOO_LARGE", message="content exceeds max write size", status_code=413)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return {"bytes_written": len(payload)}
