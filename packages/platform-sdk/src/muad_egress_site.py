"""Small stdlib-only system-site entrypoint; heavy imports require opt-in."""
from __future__ import annotations

import os
import sys
import sysconfig
from pathlib import Path

SOCKET_ENV = "MUAD_EGRESS_SOCKET"
SITE_FILENAME = "muad_egress.pth"
_STARTUP_TIMEOUT_SECONDS = 2.0
_HOOK: object | None = None
_SOCKET: str | None = None


def ready() -> bool:
    return _HOOK is not None and _SOCKET == os.environ.get(SOCKET_ENV)


def bootstrap() -> None:
    global _HOOK, _SOCKET
    socket_path = os.environ.get(SOCKET_ENV)
    if not socket_path:
        return
    if ready():
        return
    try:
        if _HOOK is not None or not Path(socket_path).is_absolute():
            raise ValueError("invalid invocation socket binding")
        from muad_platform_sdk.egress_ipc_client import IPCAdmissionClient
        from muad_platform_sdk.requests_egress_hook import RequestsEgressHook

        client = IPCAdmissionClient(Path(socket_path), timeout_seconds=_STARTUP_TIMEOUT_SECONDS)
        hook = RequestsEgressHook(client)
        hook.__enter__()
    except Exception:
        # site.py normally ignores import errors. SystemExit must stop startup;
        # the trusted launcher separately catches a missing .pth/module case.
        print("EGRESS_HOOK_STARTUP_FAILED", file=sys.stderr)
        raise SystemExit(78) from None
    _HOOK, _SOCKET = hook, socket_path


def install_site_hook() -> Path:
    destination = Path(sysconfig.get_path("purelib")) / SITE_FILENAME
    destination.write_text("import muad_egress_site; muad_egress_site.bootstrap()\n", encoding="utf-8")
    return destination


if __name__ == "__main__":
    if sys.argv[1:] != ["--install"]:
        raise SystemExit("usage: python -m muad_egress_site --install")
    install_site_hook()
