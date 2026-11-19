"""一次性重建 docs/configuration-inventory.csv：镜像 tests/test_configuration_inventory.py 的 AST 扫描，
保留既有行的 category/reason，按 (path,symbol,kind) 重新对齐 line/default，补新增、删失效。"""
from __future__ import annotations

import ast
import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "docs/configuration-inventory.csv"

CLASSES = {
    "SharedSettings", "AgentPolicy", "BudgetPolicy", "EgressPolicy", "SnipSettings",
    "ToolResultSettings", "MicroSettings", "SummarySettings", "MemorySettings",
    "CompactionSettings", "AgentSettings", "TaskSettings", "MemoryPolicySettings",
    "LearningSettings", "ArtifactSettings", "AuthSettings", "LocaleSettings",
    "ImSettings", "McpSettings", "CapacitySettings", "ModelPoolSettings", "WebchatSettings",
}

DEFAULT_REASON = {
    "python-constant": "协议标识、枚举、schema、文本或实现常量",
    "shared-setting": "启动期环境配置（§2.4 environment）",
    "policy-field": "业务策略默认（平台设置来源）",
    "resource-field": "资源级配置（§2.4 business-resource）",
}


def scan() -> dict[tuple[str, int, str, str], str]:
    """(path, line, symbol, default) -> kind。

    与 tests/test_configuration_inventory.py 的扫描保持一致：按**每一次出现**采集，
    同名符号在不同枚举/schema 中重复声明时各自成行（不能被覆盖）。
    """
    found: dict[tuple[str, int, str, str], str] = {}
    for base in ("apps", "packages"):
        for path in (ROOT / base).rglob("*.py"):
            # 统一 POSIX 分隔符：测试在 Linux 上相对 ROOT 生成 `apps/...`，Windows 打包的
            # CSV 若留反斜杠会整体错配。以源码事实（POSIX 相对路径）为准。
            rel = path.relative_to(ROOT).as_posix()
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    for target in targets:
                        if isinstance(target, ast.Name) and target.id.strip("_").isupper():
                            found[(rel, node.lineno, target.id, ast.unparse(node.value))] = (
                                "python-constant"
                            )
                if isinstance(node, ast.ClassDef) and node.name in CLASSES:
                    kind = "shared-setting" if node.name == "SharedSettings" else "policy-field"
                    for body in node.body:
                        if (
                            isinstance(body, ast.AnnAssign)
                            and isinstance(body.target, ast.Name)
                            and body.value is not None
                            and body.target.id != "model_config"
                        ):
                            found[(rel, body.lineno, body.target.id, ast.unparse(body.value))] = kind
    return found


def infer_category(kind: str) -> str:
    return {
        "python-constant": "code",
        "shared-setting": "environment",
        "policy-field": "business",
        "resource-field": "business-resource",
    }[kind]


def main() -> None:
    with CATALOG.open(newline="", encoding="utf-8") as stream:
        old_rows = list(csv.DictReader(stream))
    # 保留既有分类：按符号名匹配（重名符号共享同一 category/reason），本次未出现则淘汰。
    meta: dict[str, tuple[str, str]] = {}
    for r in old_rows:
        meta.setdefault(r["symbol"], (r["category"], r["reason"]))

    actual = scan()
    out: list[dict[str, str]] = []
    # 非本脚本扫描的 kind（如 resource-field）原样保留，避免误删。
    for r in old_rows:
        if r["kind"] not in {"python-constant", "shared-setting", "policy-field"}:
            row = {k: r[k] for k in ("path", "line", "symbol", "default", "kind", "category", "reason")}
            row["path"] = row["path"].replace("\\", "/")
            out.append(row)
    for (path, line, symbol, default), kind in sorted(actual.items()):
        category, reason = meta.get(symbol, (infer_category(kind), DEFAULT_REASON[kind]))
        out.append(
            {
                "path": path,
                "line": str(line),
                "symbol": symbol,
                "default": default,
                "kind": kind,
                "category": category,
                "reason": reason,
            }
        )
    with CATALOG.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=["path", "line", "symbol", "default", "kind", "category", "reason"],
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(out)
    print(f"rows: {len(old_rows)} -> {len(out)}")


if __name__ == "__main__":
    main()
