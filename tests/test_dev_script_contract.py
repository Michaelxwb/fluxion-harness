"""Local deployment must provision the same Skill egress hook as Docker images."""

from pathlib import Path


def test_dev_start_installs_egress_hook_before_services() -> None:
    script = (Path(__file__).resolve().parents[1] / "scripts/dev.sh").read_text(encoding="utf-8")
    install = "uv run python -m muad_egress_site --install"
    assert install in script
    assert script.index("ensure_local_egress_hook || exit 1") < script.index('echo "== 启动服务 =="')
