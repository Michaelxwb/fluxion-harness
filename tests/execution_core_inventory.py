"""Execution-core-deframework inventory: cross-check task file, manifest, context and盘面."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
REQUIREMENT = "execution-core-deframework"
LIVE = ROOT / ".code-flow/tasks/2026-10-31" / REQUIREMENT
ARCHIVED = ROOT / ".code-flow/tasks/archived"


def requirement_dir(root: Path = ROOT) -> Path:
    live = root / ".code-flow/tasks/2026-10-31" / REQUIREMENT
    if live.is_dir():
        return live
    found = sorted(path for path in (root / ".code-flow/tasks/archived").rglob(REQUIREMENT) if path.is_dir())
    assert found, f"missing requirement directory: {REQUIREMENT}"
    return found[-1]


def _table_after(text: str, heading: str) -> list[list[str]]:
    start = text.index(heading)
    rows: list[list[str]] = []
    for line in text[start:].splitlines():
        stripped = line.strip()
        if stripped.startswith("|"):
            rows.append([cell.strip() for cell in stripped.strip("|").split("|")])
        elif rows:
            break
    return rows[2:] if len(rows) >= 2 else []


def _sections(text: str) -> dict[str, str]:
    parts = re.split(r"(?m)^## (TASK-\d+):", text)
    return {parts[index]: parts[index + 1] for index in range(1, len(parts), 2)}


def problems(task_file: Path, manifest_path: Path, context_path: Path, root: Path = ROOT) -> list[str]:
    text = task_file.read_text(encoding="utf-8")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    found: list[str] = []

    coverage_rows = {
        row[0]: row
        for row in _table_after(text, "## Acceptance Coverage")
        if row and re.fullmatch(r"[SEB]-\d+", row[0])
    }
    scenarios = {scenario["id"]: scenario for scenario in manifest.get("scenarios", [])}
    if set(coverage_rows) != set(scenarios):
        found.append(f"coverage/manifest id mismatch: {sorted(set(coverage_rows) ^ set(scenarios))}")

    sections = _sections(text)
    for scenario_id, row in coverage_rows.items():
        owner = row[4]
        scenario = scenarios.get(scenario_id)
        if owner not in sections:
            found.append(f"{scenario_id}: unknown owner {owner}")
            continue
        if scenario is None:
            continue
        if scenario.get("owner") != owner:
            found.append(f"{scenario_id}: manifest owner {scenario.get('owner')} != coverage owner {owner}")
        if scenario.get("status") != "verified" and scenario_id != "S-07":
            found.append(f"{scenario_id}: manifest status {scenario.get('status')}")
        command = [str(part) for part in scenario.get("command") or []]
        test_paths = [part for part in command if part.startswith("tests/")]
        if not test_paths:
            found.append(f"{scenario_id}: command has no tests/ path")
        for path in test_paths:
            if not (root / path).is_file():
                found.append(f"{scenario_id}: missing test file {path}")
        evidence = sections[owner].split("### Acceptance Evidence", 1)[-1]
        if not re.search(rf"(?m)^\|\s*{re.escape(scenario_id)}\s*\|", evidence):
            found.append(f"{scenario_id}: missing evidence table row in {owner}")

    for owner, body in sections.items():
        contract = body.split("### Acceptance Contract", 1)[-1].split("### Acceptance Evidence", 1)[0]
        declared = re.search(r"- \*\*Acceptance-Refs\*\*:\s*(.+)", body)
        if declared is None:
            continue
        for reference in (item.strip() for item in declared.group(1).split(",")):
            if re.fullmatch(r"[SEB]-\d+", reference) and not re.search(
                rf"(?m)^\|\s*{re.escape(reference)}\s*\|", contract
            ):
                found.append(f"{owner}: missing contract row for {reference}")

    context = yaml.safe_load(context_path.read_text(encoding="utf-8"))
    for binding in context.get("bindings", []):
        for rule in binding.get("rules", []):
            if rule.get("enforcement") != "required":
                continue
            stages = {name: value.get("status") for name, value in rule.get("stage_status", {}).items()}
            for stage in ("design", "plan"):
                if stages.get(stage) not in {"applied", "verified"}:
                    found.append(f"{binding['spec_id']}#{rule['ref']}: {stage} status {stages.get(stage)}")
    return found


def _paths(root: Path = ROOT) -> tuple[Path, Path, Path]:
    directory = requirement_dir(root)
    return (
        directory / f"{REQUIREMENT}.md",
        directory / ".acceptance-manifest.json",
        directory / "spec-context.yml",
    )


def test_inventory_is_clean() -> None:
    task_file, manifest_path, context_path = _paths()
    assert problems(task_file, manifest_path, context_path) == []


def _copy_to(tmp_path: Path) -> tuple[Path, Path, Path]:
    task_file, manifest_path, context_path = _paths()
    copied = []
    for source in (task_file, manifest_path, context_path):
        target = tmp_path / source.name
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        copied.append(target)
    return copied[0], copied[1], copied[2]


def test_inventory_flags_unknown_owner(tmp_path: Path) -> None:
    task_file, manifest_path, context_path = _copy_to(tmp_path)
    text = task_file.read_text(encoding="utf-8").replace("| S-01 |", "| S-01 | TASK-999 |", 1)
    task_file.write_text(text, encoding="utf-8")
    assert any("S-01" in item and "owner" in item for item in problems(task_file, manifest_path, context_path))


def test_inventory_flags_missing_evidence_row(tmp_path: Path) -> None:
    task_file, manifest_path, context_path = _copy_to(tmp_path)
    text = task_file.read_text(encoding="utf-8")
    head, separator, tail = text.partition("### Acceptance Evidence")
    tail = re.sub(r"(?m)^\|\s*S-04\s*\|.*\n", "", tail, count=1)
    task_file.write_text(head + separator + tail, encoding="utf-8")
    assert any("S-04" in item and "evidence" in item for item in problems(task_file, manifest_path, context_path))


def test_inventory_flags_forged_test_path(tmp_path: Path) -> None:
    task_file, manifest_path, context_path = _copy_to(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["scenarios"][0]["command"] = ["uv", "run", "pytest", "-q", "tests/forged_missing.py"]
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    assert any("missing test file" in item for item in problems(task_file, manifest_path, context_path))


def test_inventory_flags_manifest_owner_drift(tmp_path: Path) -> None:
    task_file, manifest_path, context_path = _copy_to(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["scenarios"][0]["owner"] = "TASK-999"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    assert any("manifest owner" in item for item in problems(task_file, manifest_path, context_path))
