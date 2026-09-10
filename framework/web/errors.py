class AppError(Exception):
    def __init__(self, *, code: str, message: str, status_code: int = 400, details: object | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details


class NotFoundError(AppError):
    def __init__(self, message: str = "resource not found"):
        super().__init__(code="NOT_FOUND", message=message, status_code=404)


class ConflictError(AppError):
    def __init__(self, message: str = "resource conflict"):
        super().__init__(code="CONFLICT", message=message, status_code=409)
