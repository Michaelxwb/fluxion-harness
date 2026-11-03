"""Business requests through the execution-bound Runtime/Worker platform access."""

import json
import os
import socket
from typing import Any, cast


def platform_request(
    platform: str, method: str, path: str, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    endpoint = os.environ.get("MUAD_PLATFORM_SOCKET")
    if not endpoint:
        raise RuntimeError("PLATFORM_EXECUTION_CONTEXT_REQUIRED")
    request = (
        json.dumps({"platform": platform, "method": method, "path": path, "payload": payload or {}}).encode()
        + b"\n"
    )
    if len(request) > 1024 * 1024:
        raise RuntimeError("PLATFORM_REQUEST_TOO_LARGE")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(120)
        client.connect(endpoint)
        client.sendall(request)
        with client.makefile("rb") as stream:
            response = stream.readline(2 * 1024 * 1024)
    data = json.loads(response)
    if "error" in data:
        raise RuntimeError(data["error"])
    result = data["data"]
    if not isinstance(result, dict):
        raise RuntimeError("PLATFORM_RESPONSE_INVALID")
    return cast(dict[str, Any], result)
