"""C-01 fixture: first unknown customer fails retryably with a correctable hint."""

from __future__ import annotations

import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path

from muad_console_platform.infrastructure.skill_validator import validated_package
from muad_skill_sdk.skill_package import SkillPackage

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills/uat-self-correct"


def _zip_directory(directory: Path) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path in sorted(directory.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                archive.write(path, path.relative_to(directory).as_posix())
    return buffer.getvalue()


def _run(customer: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SKILL / "scripts/run.py")],
        input=json.dumps({"customer": customer}),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_self_correct_fixture_loads_and_validates() -> None:
    package = SkillPackage.load(SKILL)
    assert package.manifest.name == "uat-self-correct"
    with validated_package(_zip_directory(SKILL)) as validated:
        assert validated.manifest.name == "uat-self-correct"


def test_unknown_customer_returns_a_retryable_correction_hint() -> None:
    completed = _run("ACME")

    assert completed.returncode == 1
    envelope = json.loads(completed.stderr.strip().splitlines()[-1])
    assert envelope["error"]["code"] == "UAT_CUSTOMER_UNKNOWN"
    assert envelope["error"]["retryable"] is True
    assert "known customers: 1001" in envelope["error"]["message"]


def test_known_customer_succeeds() -> None:
    completed = _run("1001")

    assert completed.returncode == 0
    assert json.loads(completed.stdout) == {
        "ok": True,
        "customer": "1001",
        "assets": ["10.0.0.1", "10.0.0.2"],
    }
