from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_e01_no_secret_ref_residue_in_code() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/check_secret_ref_residue.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, "SecretRef 残留:\n" + result.stdout[-4000:]
