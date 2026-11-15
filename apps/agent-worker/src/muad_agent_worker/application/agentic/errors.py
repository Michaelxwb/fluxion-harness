"""Agentic 适配层的错误类型：只表达"这次执行为什么不能继续"。

应用层不 import Worker 的执行器错误（那会让 application 依赖 worker），由执行器在边界上把
`AgenticError` 翻成 `TaskExecutionError`：错误码是同一份，重试语义只在那里裁决。
"""

from __future__ import annotations


class AgenticError(Exception):
    """确定性/可重试的执行失败：`code` 是落终态或进重试分流的稳定码。"""

    def __init__(self, code: str, message: str, *, retryable: bool | None = None) -> None:
        self.code = code
        self.message = message
        self.retryable = retryable
        super().__init__(message)


__all__ = ["AgenticError"]
