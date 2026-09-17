from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI

from .catalog import MessageCatalog
from .handlers import install_exception_handlers
from .middleware import RequestContextMiddleware


def install_api_foundation(
    app: FastAPI,
    *,
    messages_file: str | Path | None = None,
    default_locale: str | None = None,
) -> MessageCatalog:
    messages_file = messages_file or os.getenv("API_MESSAGES_FILE", "config/api-messages.yaml")
    default_locale = default_locale or os.getenv("DEFAULT_LOCALE", "zh-CN")

    catalog = MessageCatalog(messages_file, default_locale=default_locale)
    app.state.message_catalog = catalog
    app.add_middleware(RequestContextMiddleware, default_locale=default_locale)
    install_exception_handlers(app, catalog)
    return catalog
