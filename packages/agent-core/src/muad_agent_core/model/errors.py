from __future__ import annotations


class ModelProviderError(RuntimeError):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class ModelUnavailableError(ModelProviderError):
    pass


class ModelRateLimitedError(ModelProviderError):
    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        self.retry_after = retry_after
        super().__init__(message)


class ModelRequestError(ModelProviderError):
    pass
