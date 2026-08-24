from __future__ import annotations

import re
from pathlib import Path

TASK_ROOT = Path(".code-flow/tasks")
RELEASE_REPORT = Path("docs/release/fluxion-v1-release-gate.md")


def _completed_p0_p1_acceptance_rows() -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for path in TASK_ROOT.rglob("*.md"):
        for block in re.split(r"(?=^## TASK-)", path.read_text(encoding="utf-8"), flags=re.MULTILINE):
            if not block.startswith("## TASK-") or not _is_completed_p0_p1(block):
                continue
            rows.extend(_acceptance_rows(block))
    return rows


def _is_completed_p0_p1(block: str) -> bool:
    status = re.search(r"^- \*\*Status\*\*: (\S+)", block, flags=re.MULTILINE)
    priority = re.search(r"^- \*\*Priority\*\*: (\S+)", block, flags=re.MULTILINE)
    return bool(
        status
        and priority
        and status.group(1) == "done"
        and priority.group(1) in {"P0", "P1"}
    )


def _acceptance_rows(block: str) -> list[tuple[str, str]]:
    if "### Acceptance Contract" not in block:
        return []
    section = block.split("### Acceptance Contract", 1)[1].split("\n### ", 1)[0]
    rows: list[tuple[str, str]] = []
    for line in section.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if cells and re.fullmatch(r"[SEB]-[RC]\d+", cells[0]):
            rows.append((cells[0], cells[-1]))
    return rows


def test_p0_p1_acceptance_automation_rate_is_at_least_95_percent() -> None:
    rows = _completed_p0_p1_acceptance_rows()
    assert rows, "no completed P0/P1 acceptance contracts found"
    verified = sum(status == "verified" for _, status in rows)
    assert verified / len(rows) >= 0.95, f"P0/P1 automation rate: {verified}/{len(rows)}"


def test_release_report_covers_all_dfx_dimensions_and_reproducible_commands() -> None:
    report = RELEASE_REPORT.read_text(encoding="utf-8")
    required_ids = {f"DFX-{index:02d}" for index in range(1, 13)}
    required_ids.update(f"DFX-CP-{index:02d}" for index in range(1, 9))
    assert required_ids.issubset(set(re.findall(r"DFX-(?:CP-)?\d{2}", report)))
    for command in (
        "python3 -m pytest backend/tests/benchmarks --benchmark-only",
        "python3 scripts/run_registry_contract_tests.py",
        "python3 -m pytest backend/tests -q",
        "pnpm --filter @fluxion/console build",
        "pnpm --filter @fluxion/chat build",
    ):
        assert command in report
