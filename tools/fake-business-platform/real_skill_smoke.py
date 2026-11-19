#!/usr/bin/env python3
"""Run the **real** intranet `daily-duty-report-mssp` skill against the fake MSSP.

This is the manual bridge between the two worlds:

- the business package is the user's real `/Users/jahan/share/daily-duty-report-mssp`
  (override with ``--skill-dir``); scripts, scene rules and xlsx generation are not copied or
  rewritten;
- only the platform base URLs are pointed at the local Fake MSSP, and a temporary
  ``session-manager`` returns a fake session state file;
- the report lands in ``.data/real-mssp-skill/<company>/<date>/`` so it can be inspected.

Usage:
  python3 tools/fake-business-platform/real_skill_smoke.py --company-id 1001
  python3 tools/fake-business-platform/real_skill_smoke.py --company-id 1002 --date 2026-11-18
  python3 tools/fake-business-platform/real_skill_smoke.py --company-id 2101 --keep-skill

The script starts Fake MSSP on an ephemeral port unless ``--base-url`` is supplied. It never
touches the intranet and never enables WeCom writes; by default the skill runs with
``--no-wecom-push``. Use ``--with-wecom-query`` to exercise the read-only group query.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SERVER = Path(__file__).with_name("server.py")
DEFAULT_SKILL = Path("/Users/jahan/share/daily-duty-report-mssp")
DEFAULT_OUTPUT = ROOT / ".data/real-mssp-skill"


def _start_fake(*extra: str) -> tuple[subprocess.Popen[str], dict[str, Any]]:
    process = subprocess.Popen(
        [
            sys.executable,
            str(SERVER),
            "--platform",
            "mssp",
            "--host",
            "127.0.0.1",
            "--port",
            "0",
            *extra,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    line = process.stdout.readline()
    if not line:
        stderr = process.stderr.read() if process.stderr is not None else ""
        process.kill()
        raise SystemExit(f"fake MSSP did not start: {stderr}")
    return process, json.loads(line)


def _session_manager(work: Path) -> Path:
    state = {
        "platforms": {
            "mssp": {"cookies": [{"name": "session", "value": "real-skill-smoke"}]},
            "xdr": {"cookies": [{"name": "session", "value": "real-skill-smoke"}]},
        }
    }
    state_path = work / "session-state.json"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    bin_dir = work / "bin"
    bin_dir.mkdir()
    manager = bin_dir / "session-manager"
    manager.write_text(
        f'#!/bin/sh\necho \'{{"sessionStateFile":"{state_path}"}}\'\n',
        encoding="utf-8",
    )
    manager.chmod(0o755)
    return bin_dir


def _copy_skill(source: Path, work: Path) -> Path:
    target = work / source.name
    shutil.copytree(source, target)
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skill-dir", type=Path, default=DEFAULT_SKILL)
    parser.add_argument("--company-id", default="1001")
    parser.add_argument("--date", default="2026-11-18")
    parser.add_argument("--base-url", default=None, help="Use an already running Fake MSSP")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--with-wecom-query", action="store_true")
    parser.add_argument("--keep-skill", action="store_true", help="Keep the temporary skill copy")
    parser.add_argument("--timeout", type=float, default=180.0)
    args = parser.parse_args(argv)

    source = args.skill_dir.expanduser().resolve()
    if not (source / "scripts/run.py").is_file():
        parser.error(f"real skill not found: {source}")
    args.output_root.mkdir(parents=True, exist_ok=True)
    run_dir = args.output_root / str(args.company_id) / str(args.date)
    run_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="real-mssp-skill-"))
    process: subprocess.Popen[str] | None = None
    started = time.monotonic()
    try:
        base_url = args.base_url
        if base_url is None:
            process, ready = _start_fake()
            base_url = str(ready["baseUrl"])
            print(f"Fake MSSP: {base_url}", flush=True)
        skill = _copy_skill(source, work) if args.keep_skill else _copy_skill(source, work)
        bin_dir = _session_manager(work)
        env = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}",
            "SKILL_OUTPUT_DIR": str(run_dir),
            "DAILY_DUTY_SUMMARY_MSSP_BASE_URL": base_url,
            "DAILY_DUTY_SUMMARY_XDR_BASE_URL": base_url,
        }
        command = [
            sys.executable,
            "scripts/run.py",
            "--company-id",
            str(args.company_id),
            "--date",
            str(args.date),
        ]
        if not args.with_wecom_query:
            command.append("--no-wecom-push")
        print("Command:", " ".join(command), flush=True)
        completed = subprocess.run(
            command,
            cwd=skill,
            env=env,
            capture_output=True,
            text=True,
            timeout=args.timeout,
        )
        stdout_path = run_dir / "stdout.txt"
        stderr_path = run_dir / "stderr.txt"
        stdout_path.write_text(completed.stdout, encoding="utf-8")
        stderr_path.write_text(completed.stderr, encoding="utf-8")
        print(f"exit={completed.returncode} elapsed={time.monotonic() - started:.1f}s")
        if completed.stdout:
            print("--- stdout (report) ---")
            print(completed.stdout)
        if completed.stderr:
            print("--- stderr (diagnostics) ---")
            print(completed.stderr)
        print("--- files ---")
        for path in sorted(run_dir.rglob("*")):
            if path.is_file() and path.name not in {"stdout.txt", "stderr.txt"}:
                print(path)
        if completed.returncode != 0:
            print(f"report failed; raw stdout/stderr kept in {run_dir}", file=sys.stderr)
        return completed.returncode
    finally:
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if not args.keep_skill:
            shutil.rmtree(work, ignore_errors=True)
        else:
            print(f"temporary skill copy kept: {work}")


if __name__ == "__main__":
    raise SystemExit(main())
