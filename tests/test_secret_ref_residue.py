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


def test_schema_assertion_exception_does_not_exempt_production_code(
    tmp_path: Path,
) -> None:
    assertion = tmp_path / "tests/acceptance/test_mcp_schema_constraints.py"
    assertion.parent.mkdir(parents=True)
    assertion.write_text('assert "auth_secret_ref" not in columns\n', encoding="utf-8")
    production = tmp_path / "apps/mcp.py"
    production.parent.mkdir()
    production.write_text('auth_secret_ref = "legacy"\n', encoding="utf-8")
    scanner = tmp_path / "scripts/check_secret_ref_residue.py"
    scanner.parent.mkdir()
    scanner.write_text((ROOT / "scripts/check_secret_ref_residue.py").read_text(), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(scanner)], capture_output=True, text=True, check=False,
    )

    assert result.returncode == 1
    assert "apps/mcp.py:1:" in result.stdout
    assert "tests/acceptance/" not in result.stdout
