"""Local deployment must provision the same Skill egress hook as Docker images."""

from pathlib import Path


def test_dev_start_installs_egress_hook_before_services() -> None:
    script = (Path(__file__).resolve().parents[1] / "scripts/dev.sh").read_text(encoding="utf-8")
    install = "uv run python -m muad_egress_site --install"
    assert install in script
    assert script.index("ensure_local_egress_hook || exit 1") < script.index('echo "== 启动服务 =="')


def test_dev_worker_and_runtime_exclude_workspace_from_reload() -> None:
    script = (Path(__file__).resolve().parents[1] / "scripts/dev.sh").read_text(encoding="utf-8")
    assert '[ "$name" = "worker" ] || [ "$name" = "runtime" ]' in script
    # 目录形式（`.data`）而不是通配（`.data/**`）：通配会被 uvicorn 展开成 `.data`
    # 下每个子目录再做 O(n²) 父子比较，本地工作区攒大后 reload 起不来。
    assert 'args+=(--reload-exclude ".data")' in script
