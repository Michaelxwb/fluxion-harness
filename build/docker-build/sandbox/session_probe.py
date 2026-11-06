#!/usr/bin/env python3
"""Pre-flight test for bwrap + session-manager.

This is a test-only image entrypoint. It emulates the Runtime's session broker
with a Unix socket, puts the real session_cli.py behind a `session-manager`
launcher, then runs a client inside bwrap. The client must be able to:

1. connect to the broker through MUAD_SESSION_SOCKET;
2. read sessionStateFile returned by the broker;
3. print the cookie from that state file.

It does not call a real business platform. It validates the transport and file
visibility that the real session-manager path depends on.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path("/tmp/muad-session-probe")
SOCKET_PATH = ROOT / "session.sock"
STATE_PATH = ROOT / "state.json"
SKILL_NAME = "sandbox-session-probe"

CLIENT = r'''
import json
import subprocess

out = subprocess.check_output(
    ["session-manager", "get-state", "--skill-name", "sandbox-session-probe"],
    text=True,
)
data = json.loads(out)
with open(data["sessionStateFile"], encoding="utf-8") as stream:
    state = json.load(stream)
cookie = state["platforms"]["demo"]["cookies"][0]["value"]
print("session_ok", cookie)
'''


def broker() -> None:
    try:
        SOCKET_PATH.unlink()
    except FileNotFoundError:
        pass
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(SOCKET_PATH))
    server.listen(1)
    try:
        conn, _ = server.accept()
        with conn:
            request = json.loads(conn.makefile("rb").readline(65536))
            if request != {"skillName": SKILL_NAME}:
                response = {"error": "invalid_context"}
            else:
                STATE_PATH.write_text(
                    json.dumps(
                        {
                            "version": 1,
                            "skillName": SKILL_NAME,
                            "platforms": {
                                "demo": {
                                    "cookies": [
                                        {"name": "sid", "value": "probe-cookie"}
                                    ]
                                }
                            },
                        }
                    ),
                    encoding="utf-8",
                )
                response = {
                    "version": 1,
                    "status": "ready",
                    "skillName": SKILL_NAME,
                    "sessionStateFile": str(STATE_PATH),
                }
            conn.sendall((json.dumps(response) + "\n").encode())
    finally:
        server.close()


def main() -> int:
    ROOT.mkdir(parents=True, exist_ok=True)
    work = ROOT / "work"
    work.mkdir(parents=True, exist_ok=True)
    (ROOT / "client.py").write_text(CLIENT, encoding="utf-8")

    launcher = ROOT / "session-manager"
    launcher.write_text(
        "#!/bin/sh\n"
        "exec /opt/python3.12/bin/python -I "
        "/opt/muad-sandbox/session_cli.py \"$@\"\n",
        encoding="utf-8",
    )
    launcher.chmod(0o755)

    thread = threading.Thread(target=broker, daemon=True)
    thread.start()

    command = [
        "bwrap",
        "--unshare-all",
        "--die-with-parent",
        "--clearenv",
        "--setenv",
        "PATH",
        f"/opt/python3.12/bin:/usr/local/bin:/usr/bin:/bin:{ROOT}",
        "--setenv",
        "MUAD_SESSION_SOCKET",
        str(SOCKET_PATH),
        "--ro-bind",
        "/usr",
        "/usr",
        "--ro-bind",
        "/lib",
        "/lib",
        "--ro-bind-try",
        "/lib64",
        "/lib64",
        "--ro-bind",
        "/bin",
        "/bin",
        "--ro-bind",
        "/etc",
        "/etc",
        "--ro-bind",
        "/opt",
        "/opt",
        "--dev",
        "/dev",
        "--size",
        "67108864",
        "--tmpfs",
        "/tmp",
        "--bind",
        str(ROOT),
        str(ROOT),
        "--bind",
        str(work),
        "/work",
        "--chdir",
        "/work",
        "python",
        str(ROOT / "client.py"),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    print("bwrap_rc", result.returncode)
    print("stdout", result.stdout.strip())
    print("stderr", result.stderr.strip())
    ok = result.returncode == 0 and "probe-cookie" in result.stdout
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
