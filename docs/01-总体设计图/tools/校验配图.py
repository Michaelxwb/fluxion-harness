# ruff: noqa: E501
"""校验总体设计中的图片引用、图题、读图说明与 SVG 基本边界。"""
from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT.parent / "01-总体设计说明书.md"
IMAGE = re.compile(r"!\[([^\]]+)\]\(([^)]+)\)")




def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def check_svg(path: Path) -> ET.Element:
    if not path.is_file():
        fail(f"图片不存在: {path.name}")
    try:
        root = ET.fromstring(path.read_text(encoding="utf-8"))
    except ET.ParseError as exc:
        fail(f"图片不是有效 SVG: {path.name}: {exc}")
    if not root.tag.endswith("svg"):
        fail(f"根节点不是 svg: {path.name}")
    view = root.get("viewBox", "")
    if not view or len(view.split()) != 4:
        fail(f"缺少有效 viewBox: {path.name}")
    _, _, width, height = (float(value) for value in view.split())
    for node in root.iter("{http://www.w3.org/2000/svg}text"):
        try:
            x, y = float(node.get("x", "0")), float(node.get("y", "0"))
        except ValueError:
            fail(f"文本坐标不是数字: {path.name}: {node.text}")
        if x < 0 or y < 0 or x > width or y > height:
            fail(f"文本超出画布: {path.name}: {node.text} ({x},{y})")
    return root


def main() -> None:
    text = DOC.read_text(encoding="utf-8")
    images = IMAGE.findall(text)
    if not images:
        fail("正文没有图片引用")
    names = [name for name, _ in images]
    if len(names) != len(set(names)):
        fail("正文存在重复图名")

    for name, relative in images:
        path = DOC.parent / relative
        check_svg(path)
        caption = f"**图：{name}**"
        if caption not in text:
            fail(f"缺少图题: {name}")
        after = text.split(caption, 1)[1].lstrip()
        if not after.startswith(">"):
            fail(f"图题后缺少读图说明: {name}")
        layout = ROOT / "tools" / "布局数据" / f"{path.stem}.json"
        mermaid = ROOT / f"{path.stem}.mmd"
        if not layout.is_file():
            fail(f"缺少布局数据: {layout.name}")
        if not mermaid.is_file():
            fail(f"缺少 Mermaid 结构对照: {mermaid.name}")

    referenced = {DOC.parent.joinpath(relative).resolve() for _, relative in images}
    for svg in ROOT.glob("*.svg"):
        if svg.resolve() not in referenced:
            fail(f"存在正文未引用的图: {svg.name}")

    print(f"PASS: {len(images)} 张图均有引用、图题、读图说明与布局数据")


if __name__ == "__main__":
    main()
