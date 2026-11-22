"""从总体设计 Markdown 生成配图预览页。"""
from __future__ import annotations

import re
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT.parent / "01-总体设计说明书.md"
IMAGE = re.compile(r"!\[([^\]]+)\]\(([^)]+)\)")


def main() -> None:
    text = DOC.read_text(encoding="utf-8")
    images = IMAGE.findall(text)
    parts = [
        "<!doctype html><html lang=\"zh-CN\"><meta charset=\"utf-8\">",
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">",
        "<title>Fluxion Harness 总体设计配图预览</title>",
        "<style>body{margin:0;background:#f4f6f8;color:#26384b;font:16px/1.8 -apple-system,BlinkMacSystemFont,'PingFang SC',sans-serif}main{max-width:960px;margin:24px auto;padding:36px 46px;background:white}h1{font-size:26px;margin:0 0 12px}h2{font-size:21px;margin:36px 0 12px}p{margin:12px 0}a{color:#276f9c}img{display:block;width:100%;height:auto;margin:18px 0}.note{color:#587086;font-size:14px}section{border-top:1px solid #e5eaf0;margin-top:30px;padding-top:8px}nav{display:flex;gap:18px;flex-wrap:wrap}@media(max-width:700px){main{margin:0;padding:24px 18px}}</style>",
        "<main><h1>Fluxion Harness 总体设计配图预览</h1>",
        "<p class=\"note\">按正文阅读宽度展示，图名按文字描述引用。源文件与生成脚本见 <code>01-总体设计图/</code>。</p>",
        "<nav><a href=\"../01-总体设计说明书.md\">总体设计 Markdown</a><a href=\"图目录.md\">图目录</a></nav>",
    ]
    for name, relative in images:
        stem = Path(relative).stem
        caption = f"**图：{name}**"
        following = text.split(caption, 1)[1].strip() if caption in text else ""
        paragraph = re.split(r"\n\n|\n!\[|\n##", following, maxsplit=1)[0].lstrip("> ")
        paragraph = paragraph.replace("`", "").replace("**", "")
        parts.append(
            f"<section id=\"{escape(stem)}\"><h2>{escape(name)}</h2>"
            f"<img src=\"{escape(stem)}.svg\" alt=\"{escape(name)}\">"
            f"<p>{escape(paragraph)}</p></section>"
        )
    parts.append("</main></html>")
    (ROOT / "配图预览.html").write_text("\n".join(parts) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
