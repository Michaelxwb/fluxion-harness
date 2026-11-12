"""System-site startup guard tests. Linux/image boundaries are tested separately."""
import importlib
import os
import subprocess
import sys
from pathlib import Path


def test_trusted_launcher_blocks_business_when_site_hook_is_missing(tmp_path):
    marker = tmp_path / "business-started"
    script = tmp_path / "business.py"
    script.write_text(f"from pathlib import Path\nPath({str(marker)!r}).write_text('started')\n")
    launcher = Path(__file__).parents[2] / "packages/platform-sdk/src/muad_egress_launch.py"
    result = subprocess.run([sys.executable, "-E", "-s", "-B", str(launcher), str(script)],
                            env={**os.environ, "MUAD_EGRESS_SOCKET": "/tmp/missing-egress-test.sock"},
                            capture_output=True, text=True)
    assert result.returncode == 78
    assert "EGRESS_HOOK_NOT_READY" in result.stderr
    assert not marker.exists()


def test_site_bootstrap_without_policy_is_inert():
    bootstrap = importlib.import_module("muad_egress_site")
    assert not bootstrap.ready()
