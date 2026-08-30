"""fluxion-dod CLI（Phase 6 TASK-004 / FEAT-P6-04，design §3.4 形态 B / D4 选型）。

`fluxion-dod verify`：运行 Final DoD 14 项验收（RULE-P6-04：14/14 全过才允许
Release，任一失败阻断）。

编排：
- pytest 全验收套件（`-m "dod or chaos_runtime or chaos_workflow or chaos_storage
  or scale"`）——dod 测试承载 DoD 1-14 的轻量/静态 verifier 与映射断言，
  chaos/scale 套件承载 DoD 1-6 的完整故障注入语义；
- 退出码：0=14/14 全过 / 非 0=存在失败（Release 门禁可消费）。

DoD 14 项 → 验收套件映射（详细证据见任务文件 Acceptance Evidence）：
  1-2  CONSIST-01/02   tests/dod（轻量对拍）+ tests/scale（满负载）+ chaos S-02
  3    REC-01          chaos S-02（kill Runtime 进程恢复 P95≤30s）+ dod 轻量
  4    REC-02          chaos S-03（workflow backend 重启恢复 P95≤60s）
  5    REL-01          chaos S-04（PG 断连 RPO=0）+ dod 轻量
  6    REL-02          chaos S-03/E-03（无重复 side effect）
  7    TRACE-01        tests/dod（span 采样完整率 ≥99%）
  8    SEC-01          tests/dod（tenant escape=0 正反断言）
  9    UX-01           tests/dod（前端维护套件 + chat-nfr 真浏览器）
  10-13 LEGACY-01..04  scripts/static_scan/scan_legacy.py（四类扫描 =0）
  14   DEL-01          tests/dod（active pinned hard-delete 拒绝）
"""

from __future__ import annotations

import subprocess
import sys
from typing import Annotated

import typer

app = typer.Typer(no_args_is_help=True, help="Final DoD 自动化验收（Release 门禁）")

_MARK_EXPR = "dod or chaos_runtime or chaos_workflow or chaos_storage or scale"


@app.callback()
def _callback() -> None:
    """fluxion-dod：Final DoD 14 项验收套件入口（FEAT-P6-04）。"""


@app.command("verify")
def verify_command(
    pytest_args: Annotated[
        list[str] | None,
        typer.Argument(help="透传给 pytest 的附加参数（如 -k 选择器）"),
    ] = None,
) -> None:
    """运行 Final DoD 14 项验收（退出码 0=14/14 全过）。"""
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-m",
        _MARK_EXPR,
        "backend/tests/dod",
        "backend/tests/chaos",
        "backend/tests/scale",
        "-q",
        *(pytest_args or []),
    ]
    typer.echo(f"=== fluxion-dod verify（pytest -m '{_MARK_EXPR}'）===")
    result = subprocess.run(command, check=False)

    if result.returncode != 0:
        typer.echo(
            f"[FAIL] Final DoD 验收存在失败（exit={result.returncode}）——"
            "RULE-P6-04：任一 DoD 失败阻断 Release",
            err=True,
        )
        raise typer.Exit(result.returncode)
    typer.echo("[OK] Final DoD 14/14 全过——Release 门禁通过")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
