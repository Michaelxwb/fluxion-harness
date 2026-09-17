from __future__ import annotations

from typing import Any

from .error_codes import ErrorCode


class AppError(Exception):
    # 业务异常只携带 code + 可选模板参数，绝不携带用户可见 message。
    def __init__(
        self,
        code: str | ErrorCode,
        *,
        message_args: dict[str, Any] | None = None,
        data: Any = None,
    ) -> None:
        self.code = str(code)
        self.message_args = message_args or {}
        self.data = data
        super().__init__(self.code)
