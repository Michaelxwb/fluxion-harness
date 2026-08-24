from __future__ import annotations

from dataclasses import dataclass

SUCCESS = 0
VALIDATION_FAILED = 30_001
RESOURCE_NOT_FOUND = 31_004
RESOURCE_CONFLICT = 31_009
BINDING_VALIDATION_FAILED = 32_001
BINDING_CONFLICT = 32_002
VERSION_CONFLICT = 33_009
FORBIDDEN = 35_003
INTERNAL_ERROR = 39_001


@dataclass(frozen=True, slots=True)
class ConsoleError(Exception):
    code: int
    message: str
    status_code: int

    def __str__(self) -> str:
        return self.message


class ConsoleValidationError(ConsoleError):
    def __init__(self, message: str = "validation failed") -> None:
        super().__init__(VALIDATION_FAILED, message, 400)


class ConsoleResourceNotFoundError(ConsoleError):
    def __init__(self, message: str = "resource not found") -> None:
        super().__init__(RESOURCE_NOT_FOUND, message, 404)


class ConsoleResourceConflictError(ConsoleError):
    def __init__(self, message: str = "resource conflict") -> None:
        super().__init__(RESOURCE_CONFLICT, message, 409)


class ConsoleBindingValidationError(ConsoleError):
    def __init__(self, message: str = "binding validation failed") -> None:
        super().__init__(BINDING_VALIDATION_FAILED, message, 400)


class ConsoleBindingConflictError(ConsoleError):
    def __init__(self, message: str = "binding conflict") -> None:
        super().__init__(BINDING_CONFLICT, message, 409)


class ConsoleVersionConflictError(ConsoleError):
    def __init__(self, message: str = "version conflict") -> None:
        super().__init__(VERSION_CONFLICT, message, 409)


class ConsoleForbiddenError(ConsoleError):
    def __init__(self, message: str = "forbidden") -> None:
        super().__init__(FORBIDDEN, message, 403)


class ConsoleInternalError(ConsoleError):
    def __init__(self, message: str = "internal error") -> None:
        super().__init__(INTERNAL_ERROR, message, 500)
