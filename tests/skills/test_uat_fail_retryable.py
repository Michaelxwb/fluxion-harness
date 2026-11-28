"""B-02 threshold fixture: stable retryable failure with no side effects."""

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
SKILL = ROOT / "skills/uat-fail-retryable"


def _zip_directory(directory: Path) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path in sorted(directory.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                archive.write(path, path.relative_to(directory).as_posix())
    return buffer.getvalue()


def test_threshold_fixture_loads_and_validates() -> None:
    package = SkillPackage.load(SKILL)
    assert package.manifest.name == "uat-fail-retryable"
    with validated_package(_zip_directory(SKILL)) as validated:
        assert validated.manifest.name == "uat-fail-retryable"


def test_threshold_fixture_returns_the_same_retryable_error() -> None:
    completed = subprocess.run(
        [sys.executable, str(SKILL / "scripts/run.py")],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 1
    envelope = json.loads(completed.stderr.strip().splitlines()[-1])
    assert envelope == {
        "error": {
            "code": "UAT_ALWAYS_FAIL",
            "message": "uat fail retryable",
            "retryable": True,
        }
    }
