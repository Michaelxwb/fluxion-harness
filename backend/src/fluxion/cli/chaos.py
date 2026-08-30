"""fluxion-chaos CLI（Phase 6 TASK-002 / FEAT-P6-02，design §3.4 形态 B）。

`fluxion-chaos run --group runtime|workflow|storage`：运行对应组 Chaos 套件
（等价 `pytest -m chaos_<group>`，D1 进程级故障注入）。

- 退出码：0=全过 / 非 0=存在失败（Release 门禁可消费）；
- stdout 透传 pytest 输出；错误文案中文。
"""

from __future__ import annotations

import subprocess
import sys
from typing import Annotated

import typer

app = typer.Typer(no_args_is_help=True, help="Chaos 故障注入套件（进程级，FEAT-P6-02）")

_GROUPS = ("runtime", "workflow", "storage")


@app.callback()
def _callback() -> None:
    """fluxion-chaos：进程级故障注入套件（D1 选型）。"""


@app.command("run")
def run_command(
    group: Annotated[str, typer.Option("--group")] = "runtime",
) -> None:
    """运行 Chaos 组套件（等价 pytest -m chaos_<group>）。"""
    if group not in _GROUPS:
        typer.echo(
            f"error: 未知 group {group!r}（可选：{' / '.join(_GROUPS)}）", err=True
        )
        raise typer.Exit(2)

    marker = f"chaos_{group}"
    typer.echo(f"=== fluxion-chaos：运行 chaos_{group} 组（pytest -m {marker}）===")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-m", marker, "-q", "backend/tests/chaos"],
        check=False,
    )
    if result.returncode != 0:
        typer.echo(f"[FAIL] chaos_{group} 组存在失败（exit={result.returncode}）", err=True)
        raise typer.Exit(result.returncode)
    typer.echo(f"[OK] chaos_{group} 组全过")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
