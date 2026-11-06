"""mss-report-skill 最小改造入口。

改造点：
1. 报告写 ``SKILL_OUTPUT_DIR``（不写包根目录或 /tmp）；
2. cache/logs/companies 写 ``SKILL_DATA_DIR``（跨执行持久）；
3. 平台地址来自 ``session-manager`` state 的 ``requestProfile.baseUrl``；
4. 不维护计划任务/后台循环，调度交给平台。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PLATFORM = "mssp"
SKILL_NAME = "mss-report-skill"
PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def required_dir(name: str) -> Path:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"{name} is required; run the skill through the platform executor")
    return Path(value)


def load_session_profile() -> dict[str, Any]:
    output = subprocess.check_output(
        ["session-manager", "get-state", "--skill-name", SKILL_NAME],
        text=True,
    )
    session = json.loads(output)
    summary = next(item for item in session["platforms"] if item["platform"] == PLATFORM)
    with open(session["sessionStateFile"], encoding="utf-8") as handle:
        state = json.load(handle)
    platform = state["platforms"][PLATFORM]
    return {
        "platform": summary["platform"],
        "expiresAt": summary["expiresAt"],
        "baseUrl": platform["requestProfile"]["baseUrl"],
        "cookies": platform["cookies"],
    }


def load_common_config() -> dict[str, Any]:
    return json.loads((PACKAGE_ROOT / "common_config.json").read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    data_dir = required_dir("SKILL_DATA_DIR")
    output_dir = required_dir("SKILL_OUTPUT_DIR")
    profile = load_session_profile()
    common = load_common_config()
    generated_at = datetime.now(UTC).isoformat()

    write_json(data_dir / "cache" / "last-run.json", {"lastRunAt": generated_at})
    write_json(
        data_dir / "companies" / "companies.json",
        {"companies": [{"id": "demo-company", "platform": profile["platform"]}]},
    )
    log_dir = data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "run.log").write_text(
        f"{generated_at} platform={profile['platform']} base_url={profile['baseUrl']}\n",
        encoding="utf-8",
    )

    report = {
        "platform": profile["platform"],
        "baseUrl": profile["baseUrl"],
        "expiresAt": profile["expiresAt"],
        "cookieCount": len(profile["cookies"]),
        "generatedAt": generated_at,
    }
    write_json(output_dir / "reports" / "mssp-report.json", report)
    print(
        json.dumps(
            {
                "ok": True,
                "platform": report["platform"],
                "baseUrl": report["baseUrl"],
                "commonConfigBaseUrl": common["baseUrl"],
                "dataDir": str(data_dir),
                "outputDir": str(output_dir),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
