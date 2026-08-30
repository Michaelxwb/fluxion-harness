#!/usr/bin/env python3
"""四类 legacy 静态扫描（Phase 6 TASK-004 / FEAT-P6-04，D5 选型）。

对应 Final DoD 10-13（NFR-P6-LEGACY-01..04）：
- ``plugin_type``：dead PluginType——枚举值在 plugins loader 分派与 provider 实现中
  均无引用（NFR-P6-LEGACY-01 =0）；
- ``spec_json_get``：runtime raw ``spec_json.get``/``spec_json[`` 违规（须经定义
  模型类型化读取，NFR-P6-LEGACY-02 =0；注释中的提及不计）；
- ``summarize``：pseudo ``_summarize`` 符号残留（真实现为 Summarizer SPI，
  NFR-P6-LEGACY-03 =0）；
- ``legacy_path``：permanent legacy product compatibility path——运行代码中的
  legacy 兼容分支标识（NFR-P6-LEGACY-04 =0；注释/文档提及与白名单不计）。

用法：
  python scripts/static_scan/scan_legacy.py [--kind plugin_type|spec_json_get|summarize|legacy_path|all] [--json]
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_SRC = _REPO / "backend" / "src" / "fluxion"

# ---------------------------------------------------------------------------
# 1. dead PluginType（NFR-P6-LEGACY-01）
#


# HOOK 白名单：ADR-EXT-001 保留类型——经 HookRegistryProtocol 独立装配（Phase 5
# 注入），不经 PluginLoader provider 分派；非 dead 类型
_PLUGIN_TYPE_WHITELIST = {"HOOK"}


def scan_plugin_type() -> list[str]:
    """PluginType 枚举值在 loader 分派表 + provider 实现中均无引用 → dead。"""
    contracts = (_SRC / "plugins" / "contracts.py").read_text(encoding="utf-8")
    enum_match = re.search(
        r"class PluginType\(StrEnum\):(.*?)(?=\nclass |\Z)", contracts, re.DOTALL
    )
    if enum_match is None:
        return ["PluginType 枚举缺失（扫描失效）"]
    members = re.findall(r"^    (\w+) = ", enum_match.group(1), re.MULTILINE)

    # 除 contracts.py 定义处外的全部源码引用——AST 属性访问（PluginType.X），
    # review P1-5：文本正则会把注释/字符串里的提及算作已使用（逃逸）
    violations: list[str] = []
    for member in members:
        if member in _PLUGIN_TYPE_WHITELIST:
            continue
        used = False
        for path in _SRC.rglob("*.py"):
            if path.name == "contracts.py" and path.parent.name == "plugins":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Attribute)
                    and node.attr == member
                    and isinstance(node.value, ast.Name)
                    and node.value.id == "PluginType"
                ):
                    used = True
                    break
            if used:
                break
        if not used:
            violations.append(f"PluginType.{member} 无任何 loader 分派/provider 引用")
    return violations


# ---------------------------------------------------------------------------
# 2. runtime raw spec_json.get（NFR-P6-LEGACY-02）
#


def scan_spec_json_get() -> list[str]:
    """backend/src/fluxion/runtime/ 内 raw `spec_json.get(` / `spec_json[` 违规。

    注释中的提及不计（AST 定位调用/下标表达式）；review P1-5：追踪别名
    （`sj = x.spec_json; sj.get(...)` / `sj[...]` 同样违规）。
    """
    violations: list[str] = []

    def _is_spec_json_expr(expr: ast.expr, aliases: dict[str, bool]) -> bool:
        if isinstance(expr, ast.Attribute) and expr.attr == "spec_json":
            return True
        if isinstance(expr, ast.Name):
            return aliases.get(expr.id, False)
        return False

    for path in sorted((_SRC / "runtime").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        # 逐作用域收集 spec_json 别名（Assign: name = <...>.spec_json）
        aliases: dict[str, bool] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if (
                        isinstance(target, ast.Name)
                        and isinstance(node.value, ast.Attribute)
                        and node.value.attr == "spec_json"
                    ):
                        aliases[target.id] = True
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and _is_spec_json_expr(node.func.value, aliases)
            ):
                violations.append(f"{path.name}:{node.lineno} raw spec_json.get()")
            if isinstance(node, ast.Subscript) and _is_spec_json_expr(node.value, aliases):
                violations.append(f"{path.name}:{node.lineno} raw spec_json[...]")
    return violations


# ---------------------------------------------------------------------------
# 3. pseudo _summarize（NFR-P6-LEGACY-03）
#

def scan_summarize() -> list[str]:
    r"""pseudo ``_summarize`` 符号（**确切名**：定义/绑定/调用，含方法前缀）残留。

    review P1-5：原正则负向后顾 `(?<![\w.])` 漏掉 `self._summarize(`；AST 化
    精确匹配 ``_summarize``（旧伪摘要函数的确切名）——真实现 Summarizer SPI
    的派生命名（如 `_summarize_records`/`default_summarizer_registry`）合法。
    注释/字符串不计。
    """
    violations: list[str] = []
    for path in sorted(_SRC.rglob("*.py")):
        relative = path.relative_to(_SRC).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == "_summarize"
            ):
                violations.append(f"{relative}:{node.lineno} pseudo _summarize 定义")
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "_summarize":
                        violations.append(f"{relative}:{node.lineno} pseudo _summarize 绑定")
            if isinstance(node, ast.Call):
                func = node.func
                name = getattr(func, "attr", None) or getattr(func, "id", None)
                if name == "_summarize":
                    violations.append(f"{relative}:{node.lineno} pseudo _summarize 调用")
    return violations


# ---------------------------------------------------------------------------
# 4. permanent legacy compatibility path（NFR-P6-LEGACY-04）
#

# 白名单：非「permanent legacy 兼容路径」的合法 legacy 出现——
# - 迁移工具文件（one-time migration 语义：rollover/cleanup/旧模型迁移，非运行
#   时的永久兼容分支）
_LEGACY_WHITELIST_FILES = {
    "agents/migration.py",              # 旧 capability 模型一次性迁移工具
    "services/migration_rollover.py",   # FEAT-P6-03 one-time Rollover 引擎
}


def scan_legacy_path() -> list[str]:
    """运行代码中的 permanent legacy 兼容分支标识（def/class 命名 + LEGACY_ 常量
    + legacy_* 标识符绑定）。注释与字符串不计。
    """
    violations: list[str] = []
    for path in sorted(_SRC.rglob("*.py")):
        relative = path.relative_to(_SRC).as_posix()
        if relative in _LEGACY_WHITELIST_FILES:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            name = getattr(node, "name", None)
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                and name
                and "legacy" in name.lower()
            ):
                violations.append(f"{relative}:{node.lineno} legacy 命名定义 {name}")
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and "legacy" in target.id.lower():
                        violations.append(f"{relative}:{node.lineno} legacy 标识符绑定 {target.id}")
    return violations


# ---------------------------------------------------------------------------
# CLI
#

SCANNERS: dict[str, object] = {
    "plugin_type": scan_plugin_type,
    "spec_json_get": scan_spec_json_get,
    "summarize": scan_summarize,
    "legacy_path": scan_legacy_path,
}


def run_scan(kind: str) -> dict[str, object]:
    if kind == "all":
        results = {name: scanner() for name, scanner in SCANNERS.items()}  # type: ignore[operator]
    else:
        results = {kind: SCANNERS[kind]()}  # type: ignore[operator]
    return {
        "violations": {name: items for name, items in results.items()},
        "passed": all(not items for items in results.values()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="legacy 静态扫描（DoD 10-13）")
    parser.add_argument(
        "--kind",
        default="all",
        choices=["all", *SCANNERS],
        help="扫描类别（默认 all）",
    )
    parser.add_argument("--json", action="store_true", help="机器可读输出")
    args = parser.parse_args()

    result = run_scan(args.kind)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"=== legacy 静态扫描（{args.kind}）===")
        for name, items in result["violations"].items():
            status = "OK" if not items else "FAIL"
            print(f"  [{status}] {name}: {len(items)} 违规")
            for item in items:
                print(f"    - {item}")
        print(f"判定: {'[OK] 全过' if result['passed'] else '[FAIL] 存在违规'}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
