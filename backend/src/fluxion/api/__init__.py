from fluxion.api.runtime import create_app

__all__ = ["create_app"]
from fluxion.api.console import create_app as create_console_app
from fluxion.api.runtime import create_app as create_runtime_app

__all__ = ["create_console_app", "create_runtime_app"]
