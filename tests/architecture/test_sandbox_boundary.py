from pathlib import Path


def test_agent_runtime_does_not_directly_invoke_subprocess() -> None:
    root = Path(__file__).parents[2] / "apps" / "agent_runtime"
    violations: list[str] = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8").lower()
        if "subprocess" in text or "os.system(" in text:
            violations.append(str(path.relative_to(root)))
    assert not violations, "Agent Runtime must use Capability/Sandbox boundary: " + ", ".join(violations)


def test_framework_core_does_not_implement_local_shell() -> None:
    root = Path(__file__).parents[2] / "framework"
    violations: list[str] = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8").lower()
        if "create_subprocess_exec" in text or "subprocess.popen" in text or "os.system(" in text:
            violations.append(str(path.relative_to(root)))
    assert not violations, "Local shell implementation belongs under adapters/sandbox: " + ", ".join(
        violations
    )
