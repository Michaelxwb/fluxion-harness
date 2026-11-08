"""Legacy session-manager CLI backed by the current execution's session broker."""

from __future__ import annotations

import json
import os
import re
import socket
import sys


def main() -> int:
    args = sys.argv[1:]
    if args == ["--version"]:
        print(json.dumps({"version": 1}))
        return 0
    if args == ["--help"]:
        print(json.dumps({"version": 1, "usage": "session-manager get-state --skill-name <name>"}))
        return 0
    if (
        len(args) != 3
        or args[:2] != ["get-state", "--skill-name"]
        or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", args[2])
    ):
        print(json.dumps({"error": {"code": "invalid_arguments"}}), file=sys.stderr)
        return 2
    endpoint = os.environ.get("MUAD_SESSION_SOCKET")
    if not endpoint:
        print(json.dumps({"error": {"code": "invalid_context"}}), file=sys.stderr)
        return 2
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(120)
            client.connect(endpoint)
            client.sendall(json.dumps({"skillName": args[2]}).encode() + b"\n")
            with client.makefile("rb") as stream:
                response = json.loads(stream.readline(65536))
    except (OSError, ValueError):
        print(json.dumps({"error": {"code": "session_unavailable"}}), file=sys.stderr)
        return 1
    if "error" in response:
        error: dict[str, object] = {"code": response["error"]}
        detail = response.get("detail")
        if isinstance(detail, dict) and detail:
            # 平台侧失败原因（HTTP 状态 / 业务码 / 原文）随 stderr 交给 Skill，再由 Skill 决定怎么呈现。
            error["detail"] = detail
        print(json.dumps({"error": error}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(response))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
