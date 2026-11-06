"""login-mssp-platform 旧入口：不改代码，只用 session-manager 获取平台登录态。"""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

PLATFORM = "mssp"
SKILL_NAME = "login-mssp-platform"


def get_session_state() -> dict[str, Any]:
    output = subprocess.check_output(
        ["session-manager", "get-state", "--skill-name", SKILL_NAME],
        text=True,
    )
    return json.loads(output)


def main() -> int:
    session = get_session_state()
    summary = next(item for item in session["platforms"] if item["platform"] == PLATFORM)
    with open(session["sessionStateFile"], encoding="utf-8") as handle:
        state = json.load(handle)
    platform = state["platforms"][PLATFORM]
    print(
        json.dumps(
            {
                "ok": True,
                "platform": summary["platform"],
                "platforms": session["platforms"],
                "expiresAt": summary["expiresAt"],
                "sessionStateFile": session["sessionStateFile"],
                "cookieNames": sorted({cookie["name"] for cookie in platform["cookies"]}),
                "cookieValuesReadable": bool(platform["cookies"])
                and all(bool(cookie.get("value")) for cookie in platform["cookies"]),
                "baseUrl": platform["requestProfile"]["baseUrl"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
