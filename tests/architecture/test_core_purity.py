from pathlib import Path

FORBIDDEN_IMPORT_FRAGMENTS = (
    "integrations.mss",
    "integrations.channels.wecom",
    "wecom",
)


def test_framework_core_does_not_import_project_integrations() -> None:
    root = Path(__file__).parents[2] / "framework"
    violations: list[str] = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8").lower()
        for fragment in FORBIDDEN_IMPORT_FRAGMENTS:
            if f"import {fragment}" in text or f"from {fragment}" in text:
                violations.append(f"{path.relative_to(root)} -> {fragment}")
    assert not violations, "Core purity violations:\n" + "\n".join(violations)
