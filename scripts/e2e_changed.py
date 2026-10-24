#!/usr/bin/env python3
"""Plan or run the E2E changed profile from scenario `trigger_paths`.

- Loads `e2e/scenarios/*.scenarios.md` (0.8 frontmatter).
- Compares `trigger_paths` with `git diff --name-only <base>`.
- Smoke scenarios are always selected (unless --no-smoke); non-smoke E2E only
  when a trigger path matches a changed file.
- Maps `design_refs` (e.g. `console-auth#S-15`) to a domain config and grep.

Usage:
  uv run python scripts/e2e_changed.py --base origin/main --plan
  uv run python scripts/e2e_changed.py --base origin/main --run --no-smoke
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
SCENARIO_DIR = ROOT / "e2e" / "scenarios"

DOMAIN_CONFIG = {
    "console-auth": "console-auth",
    "overview-dashboard": "overview-dashboard",
    "model-management": "model-management",
    "agent-management": "agent",
    "skill-management": "skill",
    "mcp-management": "mcp",
    "project-platform": "platform",
    "user-identity": "user-identity",
    "settings": "settings",
    "task-schedule": "task-schedule-e2e",
    "audit-observability": "audit-observability",
    "run-observability": "run-observability-e2e",
    "webchat": "webchat-e2e",
}


def _frontmatter(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end == -1:
            raise SystemExit(f"{path}: frontmatter 未闭合")
        text = text[3:end]
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise SystemExit(f"{path}: 场景源不是 mapping")
    return data


def load_scenarios() -> list[dict[str, Any]]:
    scenarios: list[dict[str, Any]] = []
    for path in sorted(SCENARIO_DIR.glob("*.scenarios.md")):
        for raw in _frontmatter(path).get("scenarios") or []:
            item = dict(raw)
            item["_source"] = path.name
            scenarios.append(item)
    return scenarios


def changed_files(base: str) -> list[str]:
    try:
        output = subprocess.check_output(
            ["git", "diff", "--name-only", base], cwd=ROOT, text=True
        )
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"git diff 失败（base={base}）：{exc}") from exc
    return [line.strip() for line in output.splitlines() if line.strip()]


def _matches(changed: list[str], patterns: list[str]) -> bool:
    return any(
        fnmatch.fnmatch(path, pattern)
        for pattern in patterns
        for path in changed
    )


def _refs(scenario: dict[str, Any]) -> list[tuple[str, str]]:
    """design_refs → [(domain, test_id)]；忽略无法识别的 ref。"""
    refs: list[tuple[str, str]] = []
    for ref in scenario.get("design_refs") or []:
        if "#" not in str(ref):
            continue
        domain, test_id = str(ref).split("#", 1)
        domain = domain.strip()
        test_id = test_id.strip()
        if domain in DOMAIN_CONFIG and re.fullmatch(r"[A-Z]+(?:-[A-Z]+)?-\d+[a-z]?", test_id):
            refs.append((domain, test_id))
    return refs


def plan(scenarios: list[dict[str, Any]], changed: list[str], include_smoke: bool) -> list[dict[str, Any]]:
    by_config: dict[str, dict[str, Any]] = {}
    for scenario in scenarios:
        if str(scenario.get("level", "")).lower() != "e2e":
            continue
        smoke = bool(scenario.get("smoke"))
        if smoke and not include_smoke:
            continue
        if not smoke and not _matches(changed, [str(p) for p in scenario.get("trigger_paths") or []]):
            continue
        refs = _refs(scenario)
        if not refs:
            continue
        for domain, test_id in refs:
            config = DOMAIN_CONFIG[domain]
            entry = by_config.setdefault(config, {"config": config, "greps": set(), "scenarios": set()})
            entry["greps"].add("@smoke" if smoke else test_id)
            entry["scenarios"].add(str(scenario.get("id")))
    result = []
    for config in sorted(by_config):
        entry = by_config[config]
        greps = sorted(entry["greps"])
        result.append({
            "config": config,
            "grep": "|".join(greps),
            "scenarios": sorted(entry["scenarios"]),
        })
    return result


def run(entries: list[dict[str, Any]], build: bool) -> int:
    if build:
        subprocess.run(
            ["npm", "--prefix", "apps/console-platform/frontend", "run", "build"],
            cwd=ROOT,
            check=True,
        )
    failed: list[str] = []
    for entry in entries:
        command = [
            "npm", "--prefix", "e2e", "test", "--",
            "--config", f"playwright.{entry['config']}.config.ts",
            "--grep", entry["grep"],
        ]
        print(f"== changed {entry['config']}: {entry['grep']} ({','.join(entry['scenarios'])})", flush=True)
        code = subprocess.run(command, cwd=ROOT).returncode
        if code != 0:
            failed.append(entry["config"])
    if failed:
        print(f"changed profile 失败：{', '.join(failed)}", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="", help="git diff base（默认 origin/main，缺失回落 HEAD~1）")
    parser.add_argument("--plan", action="store_true", help="只输出计划，不执行")
    parser.add_argument("--run", action="store_true", help="执行计划")
    parser.add_argument("--no-smoke", action="store_true", help="跳过 smoke（smoke job 已跑）")
    parser.add_argument("--no-build", action="store_true", help="不重复构建前端")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    base = args.base
    if not base:
        base = "origin/main" if subprocess.run(
            ["git", "rev-parse", "--verify", "origin/main"], cwd=ROOT,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        ).returncode == 0 else "HEAD~1"

    scenarios = load_scenarios()
    changed = changed_files(base)
    entries = plan(scenarios, changed, include_smoke=not args.no_smoke)

    if args.json:
        print(json.dumps({"base": base, "changed_files": changed, "entries": entries}, ensure_ascii=False))
    else:
        print(f"base={base} changed={len(changed)} scenarios={sum(len(e['scenarios']) for e in entries)}")
        for entry in entries:
            print(f"  {entry['config']}: grep={entry['grep']} scenarios={','.join(entry['scenarios'])}")

    if args.plan or not args.run:
        return 0
    return run(entries, build=not args.no_build)


if __name__ == "__main__":
    raise SystemExit(main())
