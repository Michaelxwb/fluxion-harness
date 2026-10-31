"""S-05: current surfaces must not claim LangGraph is part of the execution chain."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCANNED_ROOTS = ("docs", ".code-flow/specs", "packages", "apps", "tests")
SCANNED_SUFFIXES = (".py", ".md", ".toml", ".txt", ".yaml", ".yml")
ADR = ROOT / "docs/issues/2026-10-31-execution-core-deframework-adr.md"
EXCLUDED = {
    "docs/17-V1.4评审修订说明.md",
    "docs/issues/2026-10-31-execution-core-deframework-adr.md",
    "tests/contracts/test_dependency_surface.py",
    "tests/contracts/test_langgraph_residue.py",
    "tests/acceptance/test_schema_surface_contract.py",
}


def _findings() -> list[str]:
    hits: list[str] = []
    for root in SCANNED_ROOTS:
        for path in (ROOT / root).rglob("*"):
            if not path.is_file() or path.suffix not in SCANNED_SUFFIXES:
                continue
            relative = str(path.relative_to(ROOT))
            if relative in EXCLUDED or relative.startswith(".code-flow/specs/_session/"):
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            if "langgraph" in content.casefold():
                hits.append(relative)
    return sorted(hits)


def test_s05_current_surfaces_do_not_claim_langgraph() -> None:
    assert _findings() == []


def test_s05_adr_records_the_rejected_checkpointer_option() -> None:
    assert ADR.is_file(), f"missing ADR: {ADR.relative_to(ROOT)}"
    content = ADR.read_text(encoding="utf-8")
    assert "PG Checkpointer" in content
    assert "被否" in content
