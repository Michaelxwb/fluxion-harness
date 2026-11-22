"""Check docs structure, links, and module-document template consistency."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"

REQUIRED = (
    "docs/00-详细设计索引与设计基线.md",
    "docs/01-总体设计说明书.md",
    "docs/configuration-inventory.md",
    "docs/基础文档/README.md",
    "docs/基础模块/README.md",
    "docs/核心功能/README.md",
    "docs/历史/README.md",
    "docs/规划/README.md",
)
CATEGORY_DIRS = {
    "docs/基础文档": 6,
    "docs/基础模块": 6,
    "docs/核心功能": 5,
    "docs/历史": 4,
    "docs/规划": 1,
}
OLD_NAMES = (
    "01-总体架构与部署详细设计.md",
    "02-核心领域与数据库详细设计.md",
    "03-Console-Platform详细设计.md",
    "04-Agent-Runtime详细设计.md",
    "05-IM-Gateway详细设计.md",
    "06-Skill-SDK与Egress-Boundary详细设计.md",
    "07-跨模块接口与协议详细设计.md",
    "08-关键流程时序与状态机详细设计.md",
    "09-DFX安全可靠性测试验收详细设计.md",
    "10-Agent-Worker与异步任务详细设计.md",
    "12-项目平台适配与Session详细设计.md",
    "14-用户范围与能力授权模型详细说明.md",
    "15-Console字段词典与一致性规范.md",
)
LOCAL_LINK = re.compile(r"\]\(([^)]+\.md[^)]*)\)")
FIGURE = re.compile(r"!\[([^\]]+)\]\(([^)]+)\)")
SECTION = re.compile(r"^## (\d+)\. ", re.M)
CHECK_ROOTS = ("docs", "tests", "scripts", ".code-flow/specs", ".code-flow/templates")


def main() -> None:
    errors: list[str] = []

    for relative in REQUIRED:
        if not (ROOT / relative).is_file():
            errors.append(f"missing required doc: {relative}")

    for relative, expected in CATEGORY_DIRS.items():
        directory = ROOT / relative
        if not directory.is_dir():
            errors.append(f"missing category dir: {relative}")
            continue
        count = len([p for p in directory.glob("*.md") if p.name != "README.md"])
        if count != expected:
            errors.append(f"{relative}: expected {expected} docs, found {count}")

    for base in CHECK_ROOTS:
        for path in (ROOT / base).rglob("*"):
            if not path.is_file() or path.suffix not in {".md", ".py", ".yml", ".yaml", ".json"}:
                continue
            if path.resolve() == Path(__file__).resolve():
                continue
            if any(part in {"node_modules", "__pycache__", ".git"} for part in path.parts):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            for old in OLD_NAMES:
                if old in text:
                    errors.append(f"{path.relative_to(ROOT)}: old doc name {old}")
            if path.suffix == ".md" and path.is_relative_to(DOCS):
                for target in LOCAL_LINK.findall(text):
                    target = target.split("#", 1)[0]
                    if not target or target.startswith(("http://", "https://", "mailto:")):
                        continue
                    resolved = (path.parent / target).resolve()
                    if not resolved.exists():
                        errors.append(f"{path.relative_to(ROOT)}: broken link {target}")

    module_docs = list((DOCS / "基础模块").glob("*.md")) + list((DOCS / "核心功能").glob("*.md"))
    for path in module_docs:
        if path.name == "README.md":
            continue
        text = path.read_text(encoding="utf-8")
        if "design_template: module" not in text:
            errors.append(f"{path.relative_to(ROOT)}: missing design_template: module")
        if not re.search(r"^module_kind: (framework|business)$", text, re.M):
            errors.append(f"{path.relative_to(ROOT)}: missing module_kind")
        sections = set(SECTION.findall(text))
        missing = [str(i) for i in range(1, 7) if str(i) not in sections]
        if missing:
            errors.append(f"{path.relative_to(ROOT)}: missing sections {missing}")

    for path in DOCS.rglob("*.md"):
        text = path.read_text(encoding="utf-8")
        for _, relative in FIGURE.findall(text):
            resolved = (path.parent / relative).resolve()
            if not resolved.exists():
                errors.append(f"{path.relative_to(ROOT)}: missing figure {relative}")

    if errors:
        print("FAIL")
        for error in errors:
            print(error)
        sys.exit(1)
    print("PASS: docs structure, links, old names, module template sections and figures")


if __name__ == "__main__":
    main()
