"""Demand traceability. Code-stage deferral never implies requirement review passed.

Run with MUAD_EGRESS_INVENTORY_STAGE=code before task finish; the default is review.
Source/environment freshness remains the native CodeFlow gate's responsibility.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from typing import cast

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
LIVE_REL = ".code-flow/tasks/2026-11-06/egress-rate-sdk"
ID = re.compile(r"^(?:[SEB]-\d+|[\w-]+#RULE-[\w-]+)$")


def _dir() -> Path:
    live = ROOT / LIVE_REL
    if live.is_dir():
        return live
    archived = sorted(ROOT.glob(".code-flow/tasks/archived/*/egress-rate-sdk"))
    assert len(archived) == 1, "egress-rate-sdk requirement directory is missing or ambiguous"
    return archived[0]


def _mapping(value: object) -> dict[str, object]:
    assert isinstance(value, dict), "expected an object in acceptance metadata"
    return cast(dict[str, object], value)


def _records(value: object) -> list[dict[str, object]]:
    assert isinstance(value, list), "expected a list in acceptance metadata"
    return [_mapping(item) for item in value]


def _section(text: str, title: str, level: int = 3) -> str:
    marker = "#" * level + " " + title + "\n"
    assert marker in text, f"missing section {title}"
    return re.split(r"\n#{1," + str(level) + r"} ", text.split(marker, 1)[1], maxsplit=1)[0]


def _rows(text: str) -> dict[str, list[str]]:
    result = {}
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if ID.fullmatch(cells[0]):
            assert cells[0] not in result, f"duplicate row {cells[0]}"
            result[cells[0]] = cells
    return result


def _refs(block: str, field: str) -> set[str]:
    match = re.search(r"^- \*\*" + field + r"\*\*: (.+)$", block, re.M)
    assert match, f"missing field {field}"
    return {value.strip() for value in match[1].split(",") if ID.fullmatch(value.strip())}


def _tasks(text: str) -> dict[str, str]:
    matches = list(re.finditer(r"^## (TASK-\d+):", text, re.M))
    return {match[1]: text[match.start():matches[i + 1].start() if i + 1 < len(matches) else len(text)]
            for i, match in enumerate(matches)}


def _command(value: object, directory: Path) -> list[str]:
    command = json.loads(value) if isinstance(value, str) else value
    assert isinstance(command, list) and all(isinstance(item, str) for item in command)
    relative = directory.relative_to(ROOT).as_posix() if directory.is_relative_to(ROOT) else LIVE_REL
    return [item.replace(LIVE_REL, relative) for item in command]


@lru_cache(maxsize=4)
def _collected(files: tuple[str, ...]) -> set[str]:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", *files],
        cwd=ROOT, capture_output=True, text=True, timeout=90,
    )
    assert result.returncode == 0, "test collection failed: " + result.stdout[-2000:] + result.stderr[-500:]
    return {line.strip() for line in result.stdout.splitlines() if "::test_" in line}


def _case_errors(
    sid: str, row: list[str], item: dict[str, object], tasks: dict[str, str], directory: Path,
) -> list[str]:
    errors = []
    owner = row[4]
    if item.get("owner") != owner or owner not in tasks:
        return [f"{sid}: manifest/coverage owner differs or is missing"]
    contract = _rows(_section(tasks[owner], "Acceptance Contract"))
    evidence = _rows(_section(tasks[owner], "Acceptance Evidence"))
    for label, rows in (("contract", contract), ("evidence", evidence)):
        if sid not in rows or rows[sid][-1] != "verified":
            errors.append(f"{sid}: owner {owner} {label} row missing or not verified")
    for key, expected in (("level", row[2]), ("boundary", row[3]), ("cwd", row[7])):
        if item.get(key) != expected:
            errors.append(f"{sid}: manifest {key} differs")
    command = _command(row[6], directory)
    if command != _command(item.get("command"), directory):
        errors.append(f"{sid}: manifest command differs")
    if sid in contract and (contract[sid][1:3] != row[2:4]
                            or _command(contract[sid][-2], directory) != command):
        errors.append(f"{sid}: owner contract boundary/level/command differs")
    if row[5] != "verified" or item.get("status") != "verified":
        errors.append(f"{sid}: coverage/manifest status not verified")
    run = _mapping(item.get("evidence", {}))
    if run.get("status") != "passed" or run.get("exit_code") != 0 or not run.get("run_id"):
        errors.append(f"{sid}: executed acceptance evidence missing")
    elif _command(run.get("command"), directory) != command:
        errors.append(f"{sid}: executed command differs")
    return errors


def _rule_errors(context: dict[str, object], tasks: dict[str, str], stage: str) -> list[str]:
    errors = []
    owners: dict[str, list[str]] = {}
    for owner, block in tasks.items():
        for ref in _refs(block, "Rule-Owner-Refs") if "**Rule-Owner-Refs**:" in block else ():
            owners.setdefault(ref, []).append(owner)
    required = []
    for binding in _records(context.get("bindings")):
        for rule in _records(binding.get("rules")):
            if rule.get("enforcement") == "required":
                required.append((str(binding["spec_id"]) + "#" + str(rule["ref"]), rule))
    if len(required) != 9 or set(owners) != {ref for ref, _ in required}:
        errors.append("required rules: Context/owner coverage differs (expected nine rules)")
    for ref, rule in required:
        if len(owners.get(ref, [])) != 1:
            errors.append(f"{ref}: owner missing or duplicated")
            continue
        owner = owners[ref][0]
        deferred = stage == "code" and rule.get("verifier_stage") == "review"
        expected = "e2e_deferred" if deferred else "verified"
        for title in ("Acceptance Contract", "Acceptance Evidence"):
            rows = _rows(_section(tasks[owner], title))
            if ref not in rows or rows[ref][-1] != expected:
                errors.append(f"{ref}: owner {owner} {title} row missing or not {expected}")
        statuses = _mapping(rule.get("stage_status"))
        status = _mapping(statuses.get(str(rule.get("verifier_stage")), {}))
        if not deferred and status.get("status") != "verified":
            errors.append(f"{ref}: Context verifier not verified")
    return errors


def inventory_errors(directory: Path, *, stage: str = "review") -> list[str]:
    assert stage in {"code", "review"}
    text = (directory / "egress-rate-sdk.md").read_text(encoding="utf-8")
    manifest = _mapping(json.loads((directory / ".acceptance-manifest.json").read_text()))
    context = _mapping(yaml.safe_load((directory / "spec-context.yml").read_text()))
    source = (directory / "egress-rate-sdk.scenarios.md").read_text().split("---", 2)[1]
    canonical = {str(item["id"]): item for item in _records(_mapping(yaml.safe_load(source))["scenarios"])}
    coverage = _rows(_section(text, "Acceptance Coverage", 2))
    tasks = _tasks(text)
    scenarios = {str(item["id"]): item for item in _records(manifest["scenarios"])}
    errors = []
    design = (directory / "egress-rate-sdk.design.md").read_text()
    design_ids = set(re.findall(r"\| ([SEB]-\d+) \|", design))
    if set(canonical) != design_ids:
        errors.append("scenarios: design/canonical coverage differs")
    if len(canonical) != 14 or set(canonical) != set(coverage) or set(canonical) != set(scenarios):
        errors.append("scenarios: canonical/coverage/manifest differs (expected fourteen including B-05)")
    for sid in sorted(set(coverage) & set(scenarios)):
        if sid not in canonical or canonical[sid].get("owner") != coverage[sid][4]:
            errors.append(f"{sid}: canonical owner differs")
        errors.extend(_case_errors(sid, coverage[sid], scenarios[sid], tasks, directory))
    for owner, block in tasks.items():
        contract = _rows(_section(block, "Acceptance Contract"))
        for ref in _refs(block, "Acceptance-Refs"):
            if ref not in contract:
                errors.append(f"{owner}: Acceptance-Refs contract missing {ref}")
    errors.extend(_rule_errors(context, tasks, stage))
    errors.extend(_checklist_errors(context, tasks, stage))
    errors.extend(_check_errors(manifest, directory))
    return errors


def _checklist_errors(context: dict[str, object], tasks: dict[str, str], stage: str) -> list[str]:
    review_refs = {
        str(binding["spec_id"]) + "#" + str(rule["ref"])
        for binding in _records(context["bindings"]) for rule in _records(binding["rules"])
        if rule.get("verifier_stage") == "review"
    }
    errors = []
    for owner, block in tasks.items():
        review_bound = bool(_refs(block, "Spec-Refs") & review_refs)
        for line in _section(block, "Checklist").splitlines():
            if not line.startswith("- [ ]"):
                continue
            if stage == "code" and review_bound and "[deferred-review]" in line:
                continue
            errors.append(f"{owner}: unfinished checklist {line}")
    return errors


def _check_errors(manifest: dict[str, object], directory: Path) -> list[str]:
    errors = []
    checks = {str(item["id"]): item for item in _records(manifest["checks"])}
    references = set()
    for check in checks.values():
        references.update(str(item) for item in cast(list[object], check.get("test_ids", [])))
    references = {value.replace(LIVE_REL, _command([LIVE_REL], directory)[0]) for value in references}
    files = tuple(sorted({value.split("::")[0] for value in references}))
    collected = _collected(files)
    for ref in sorted(references - collected):
        errors.append(f"registered test ID missing: {ref}")
    for item in _records(manifest["scenarios"]):
        sid = str(item["id"])
        check = checks.get(str(item.get("check")))
        if check is None or not check.get("test_ids"):
            errors.append(f"{sid}: registered check/tests missing")
        elif (_command(check["command"], directory) != _command(item["command"], directory)
              or check.get("cwd") != item.get("cwd") or check.get("timeout") != item.get("timeout")):
            errors.append(f"{sid}: check command/cwd/timeout differs")
    return errors


def test_egress_rate_sdk_inventory() -> None:
    errors = inventory_errors(_dir(), stage=os.environ.get("MUAD_EGRESS_INVENTORY_STAGE", "review"))
    assert errors == [], "\n".join(errors)


@pytest.mark.parametrize("mutation", ["status", "evidence", "test_id", "manifest"])
def test_inventory_detects_four_negative_controls(tmp_path: Path, mutation: str) -> None:
    directory = tmp_path / "requirement"
    shutil.copytree(_dir(), directory)
    baseline = inventory_errors(directory, stage="code")
    assert baseline == [], "baseline must pass before negative controls:\n" + "\n".join(baseline)
    task = directory / "egress-rate-sdk.md"
    manifest = directory / ".acceptance-manifest.json"
    original_task, original_manifest = task.read_bytes(), manifest.read_bytes()
    if mutation in {"status", "evidence"}:
        lines = task.read_text().splitlines()
        index = next(i for i, line in enumerate(lines) if line.startswith("| S-01 |"))
        if mutation == "status":
            lines[index] = lines[index].replace("| verified |", "| planned |")
        else:
            owner = _tasks(task.read_text())["TASK-002"]
            row = next(line for line in _section(owner, "Acceptance Evidence").splitlines()
                       if line.startswith("| S-01 |"))
            lines.remove(row)
        task.write_text("\n".join(lines) + "\n")
    else:
        data = json.loads(manifest.read_text())
        if mutation == "test_id":
            data["checks"][0]["test_ids"][0] = (
                "tests/sdk/test_request_budget.py::test_fabricated_egress_check"
            )
        else:
            data["scenarios"][0]["owner"] = "TASK-999"
        manifest.write_text(json.dumps(data))
    changed = inventory_errors(directory, stage="code")
    assert set(changed) - set(baseline), f"negative control not detected: {mutation}"
    assert any("S-01" in error or "fabricated" in error for error in set(changed) - set(baseline))
    task.write_bytes(original_task)
    manifest.write_bytes(original_manifest)
    assert inventory_errors(directory, stage="code") == baseline
    assert task.read_bytes() == original_task and manifest.read_bytes() == original_manifest
