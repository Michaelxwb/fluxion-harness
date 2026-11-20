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
        print(json.dumps({"version": 2, "usage": [
            "session-manager list-platforms",
            "session-manager get-state --platform <key>",
            "session-manager refresh --platform <key>",
        ], "notes": {
            "list-platforms": "checks actual login state; reports per-platform availability",
            "get-state": "returns the execution-scoped session state file",
            "refresh": "refreshes one platform session explicitly",
        }}))
        return 0
    command: str
    platform: str | None = None
    refresh = False
    if args == ["list-platforms"]:
        command = "list-platforms"
    elif len(args) == 3 and args[1] == "--platform" and args[2]:
        command, platform = args[0], args[2]
        if command not in {"get-state", "refresh"}:
            command = ""
        refresh = command == "refresh"
    elif (
        len(args) == 3
        and args[:2] == ["get-state", "--skill-name"]
        and re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", args[2])
    ):
        command, platform = "get-state", None
        legacy_name = args[2]
    else:
        command = ""
    if not command:
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
            request = {"skillName": locals().get("legacy_name", os.environ.get("MUAD_SESSION_NAME", ""))}
            if command == "list-platforms":
                request["list"] = True
            elif platform is not None:
                request.update({"platform": platform, "refresh": refresh})
            else:
                request["legacy"] = True
            client.sendall(json.dumps(request).encode() + b"\n")
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
