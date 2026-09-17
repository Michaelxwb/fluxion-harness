from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from muad_common import SharedSettings

from .catalog import MessageCatalog
from .handlers import install_exception_handlers
from .middleware import RequestContextMiddleware


def install_api_foundation(
    app: FastAPI,
    *,
    settings: SharedSettings | None = None,
    messages_file: str | Path | None = None,
    default_locale: str | None = None,
) -> MessageCatalog:
    settings = settings or SharedSettings()
    resolved_messages_file = messages_file or settings.api_messages_file
    resolved_locale = default_locale or settings.default_locale

    catalog = MessageCatalog(resolved_messages_file, default_locale=resolved_locale)
    app.state.message_catalog = catalog
    app.add_middleware(RequestContextMiddleware, default_locale=resolved_locale)
    install_exception_handlers(app, catalog)
    return catalog
