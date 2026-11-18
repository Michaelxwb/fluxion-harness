"""`MEDIA:` 兼容层（FEAT-10）：只解析最终助手文本里的独立行。

主契约仍是结构化 attachments 与 `SKILL_OUTPUT_DIR`。这里只负责把旧 Skill/Agent
在正文末尾写下的 `MEDIA: path` 转成当前 Run 的 `OUTPUT` 产物；解析器只接收**最终
助手文本**，工具/Skill/插件 stdout 与流式块永远不会经过它。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .output_service import SkillOutputArtifactRegistrar
from .tools import AttachmentToolError, resolve_workspace_file

#: 一条消息最多兼容解析多少个附件；超出的引用同样生成可见失败说明。
MAX_MEDIA_ITEMS = 10

#: 独立行：最多 3 个前导空格，`MEDIA:` 后是完整路径；非行首/行内出现不匹配。
_MEDIA_LINE = re.compile(r"^ {0,3}MEDIA:[ \t]*(\S.*?)\s*$")
#: Markdown 围栏代码块（``` 或 ~~~）；围栏内的 `MEDIA:` 不是指令。
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
#: 工作区在沙箱内的固定挂载前缀；模型常按沙箱视角输出绝对路径。
_WORKSPACE_MOUNT_PREFIX = "/workspace/"


@dataclass(frozen=True, slots=True)
class ParsedMedia:
    text: str
    paths: tuple[str, ...]
    errors: tuple[str, ...]


def parse_final_media(text: str) -> ParsedMedia:
    """提取独立 `MEDIA:` 行；从正文移除指令，并把格式错误变成可见说明。"""
    kept: list[str] = []
    paths: list[str] = []
    errors: list[str] = []
    fence: str | None = None
    for line in text.splitlines():
        marker = _FENCE.match(line)
        if fence is not None:
            kept.append(line)
            if marker is not None and marker.group(1)[0] == fence:
                fence = None
            continue
        if marker is not None:
            fence = marker.group(1)[0]
            kept.append(line)
            continue
        match = _MEDIA_LINE.match(line)
        if match is None:
            kept.append(line)
            continue
        path = _clean_path(match.group(1))
        if not path:
            errors.append("未能附加文件：MEDIA 行缺少路径。")
            continue
        if len(paths) >= MAX_MEDIA_ITEMS:
            errors.append(f"未能附加文件「{path}」：一次消息最多 {MAX_MEDIA_ITEMS} 个附件。")
            continue
        paths.append(path)
    cleaned = "\n".join(kept).strip()
    return ParsedMedia(text=cleaned, paths=tuple(paths), errors=tuple(errors))


def _clean_path(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'", "`"}:
        text = text[1:-1].strip()
    if text.startswith(_WORKSPACE_MOUNT_PREFIX):
        text = text[len(_WORKSPACE_MOUNT_PREFIX) :]
    return text


class FinalMediaRegistrar:
    """把最终文本中的 `MEDIA:` 引用登记到当前 Run；失败只产生可见说明，不阻断回答。"""

    def __init__(
        self,
        *,
        workspace_dir: Path,
        registrar: SkillOutputArtifactRegistrar,
    ) -> None:
        self._workspace_dir = workspace_dir
        self._registrar = registrar

    async def apply(self, text: str) -> str:
        parsed = parse_final_media(text)
        if not parsed.paths and not parsed.errors:
            return text
        notes = list(parsed.errors)
        registered = 0
        for path in parsed.paths:
            try:
                output = resolve_workspace_file(self._workspace_dir, path)
                await self._registrar.register(output)
            except AttachmentToolError as exc:
                notes.append(f"未能附加文件「{path}」：{exc.message}")
            except OSError:
                notes.append(f"未能附加文件「{path}」：文件不可读。")
            else:
                registered += 1
        cleaned = parsed.text
        if notes:
            cleaned = "\n\n".join(part for part in (cleaned, "\n".join(notes)) if part)
        elif registered and not cleaned:
            # 成功附件不能配空气泡：给一句朴素说明，正文里也不再出现 MEDIA 指令。
            cleaned = "已生成附件。"
        return cleaned


__all__ = ["FinalMediaRegistrar", "MAX_MEDIA_ITEMS", "ParsedMedia", "parse_final_media"]
