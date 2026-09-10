from pathlib import Path

from framework.web.errors import AppError


def edit_file(path: Path, *, old_text: str, new_text: str, replace_all: bool, max_bytes: int) -> dict[str, object]:
    if not path.is_file():
        raise AppError(code="SANDBOX_NOT_FILE", message="path is not a file", status_code=400)
    content = path.read_text(encoding="utf-8")
    count = content.count(old_text)
    if count == 0:
        raise AppError(code="SANDBOX_EDIT_NOT_FOUND", message="old_text was not found", status_code=409)
    if count > 1 and not replace_all:
        raise AppError(
            code="SANDBOX_EDIT_AMBIGUOUS",
            message="old_text occurs multiple times; use a unique match or replace_all=true",
            status_code=409,
        )
    updated = content.replace(old_text, new_text, -1 if replace_all else 1)
    if len(updated.encode("utf-8")) > max_bytes:
        raise AppError(code="SANDBOX_WRITE_TOO_LARGE", message="edited file exceeds max write size", status_code=413)
    path.write_text(updated, encoding="utf-8")
    return {"replacements": count if replace_all else 1}
