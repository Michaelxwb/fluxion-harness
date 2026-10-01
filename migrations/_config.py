"""`migrations/` 下脚本共用的配置读取：**只认 alembic.ini，不读环境变量**。

- `parse_config_args`：解析 `-c/--config <ini>`（未给则返回 None），返回其余 argv。
- `resolve_ini`：未指定时取仓库根的 `migrations/alembic.ini`；指定时按调用方 cwd 解析。
- `load_dsn`：读 ini 里的连接串键；文件缺失或键为空都直接失败（**不做 env 回落**，
  否则会出现"以为在升 A 库、实际升了 B 库"）。
"""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config

DEFAULT_INI = Path("migrations/alembic.ini")


def parse_config_args(argv: list[str]) -> tuple[Path | None, list[str]]:
    """→ (ini 路径或 None, 其余参数)。支持 `-c <ini>` / `--config <ini>` / `--config=<ini>`。"""
    rest: list[str] = []
    ini: Path | None = None
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg in ("-c", "--config"):
            if index + 1 >= len(argv):
                raise SystemExit(f"{arg} 需要一个 ini 路径参数")
            ini = Path(argv[index + 1])
            index += 2
            continue
        if arg.startswith("--config="):
            ini = Path(arg.split("=", 1)[1])
            index += 1
            continue
        rest.append(arg)
        index += 1
    return ini, rest


def resolve_ini(given: Path | None, root: Path) -> Path:
    """未指定 → 仓库根的默认 ini；指定 → 相对调用方 cwd 解析。"""
    return given.resolve() if given is not None else (root / DEFAULT_INI).resolve()


def load_dsn(ini: Path, key: str, *, purpose: str) -> str:
    if not ini.is_file():
        raise SystemExit(f"找不到配置文件：{ini}")
    value = (Config(str(ini)).get_main_option(key) or "").strip()
    if not value:
        raise SystemExit(f"{ini} 的 `{key}` 为空——{purpose}；迁移配置只认该 ini（不读环境变量）")
    return value
