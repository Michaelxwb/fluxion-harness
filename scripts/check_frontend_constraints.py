#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APPS = [
    ROOT / "frontend/apps/console",
    ROOT / "frontend/apps/chat",
]
FORBIDDEN = {"antd", "@ant-design/icons", "@mui/material", "@mui/icons-material"}
ADAPTER = "@douyinfe/semi-ui/react19-adapter"
SEMI_PREFIX = "@douyinfe/semi-"


def check_package(app: Path, errors: list[str]) -> None:
    package = app / "package.json"
    if not package.exists():
        return
    data = json.loads(package.read_text(encoding="utf-8"))
    deps = {}
    deps.update(data.get("dependencies", {}))
    deps.update(data.get("devDependencies", {}))
    bad = sorted(FORBIDDEN.intersection(deps))
    if bad:
        errors.append(f"{package.relative_to(ROOT)} 禁止依赖: {', '.join(bad)}")
    for required in ("@douyinfe/semi-ui", "@douyinfe/semi-icons"):
        if required not in deps:
            errors.append(f"{package.relative_to(ROOT)} 缺少 Semi Design 依赖: {required}")


def check_main(app: Path, errors: list[str]) -> None:
    candidates = [app / "src/main.tsx", app / "src/main.ts", app / "src/main.jsx"]
    main = next((p for p in candidates if p.exists()), None)
    if main is None:
        return
    lines = main.read_text(encoding="utf-8").splitlines()
    imports = [(i, line.strip()) for i, line in enumerate(lines) if line.strip().startswith("import ")]
    adapter_positions = [i for i, line in imports if ADAPTER in line]
    if not adapter_positions:
        errors.append(f"{main.relative_to(ROOT)} 缺少 React19 Semi Adapter 导入")
        return
    adapter_pos = adapter_positions[0]
    semi_positions = [
        i for i, line in imports
        if SEMI_PREFIX in line and ADAPTER not in line
    ]
    if semi_positions and adapter_pos > min(semi_positions):
        errors.append(
            f"{main.relative_to(ROOT)} 必须在任何 Semi 组件导入前加载 {ADAPTER}"
        )


def main() -> int:
    errors: list[str] = []
    for app in APPS:
        check_package(app, errors)
        check_main(app, errors)
    if errors:
        for err in errors:
            print(f"[frontend-constraint] {err}", file=sys.stderr)
        return 1
    print("Fluxion 前端 Semi Design 约束检查通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
