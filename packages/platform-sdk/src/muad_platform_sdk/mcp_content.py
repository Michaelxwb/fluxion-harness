"""MCP content conversion: text/resources/images/unsupported, with bounded media."""

from __future__ import annotations

import base64
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any, Protocol

from .mcp_limits import McpLimits


class McpContentTooLarge(RuntimeError):
    def __init__(self, message: str) -> None:
        self.code = "MCP_CONTENT_TOO_LARGE"
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class McpArtifact:
    media_type: str
    data: bytes
    filename: str | None = None
    uri: str | None = None
    artifact_id: str | None = None


@dataclass(frozen=True, slots=True)
class McpResult:
    text: str
    artifacts: tuple[McpArtifact, ...] = ()
    unsupported: tuple[str, ...] = ()


class McpArtifactWriter(Protocol):
    async def write(self, artifact: McpArtifact) -> str:
        """Persist bytes and return an artifact id/description."""


async def convert_content(
    content: Any,
    *,
    limits: McpLimits,
    writer: McpArtifactWriter | None = None,
) -> McpResult:
    if not isinstance(content, list):
        raise McpContentTooLarge("MCP content must be a list")
    if len(content) > limits.max_content_items:
        raise McpContentTooLarge("MCP content item limit exceeded")
    text_parts: list[str] = []
    artifacts: list[McpArtifact] = []
    unsupported: list[str] = []
    total = 0
    for item in content:
        if not isinstance(item, Mapping):
            unsupported.append("invalid")
            continue
        kind = item.get("type")
        if kind == "text":
            value = item.get("text")
            if isinstance(value, str):
                total += len(value.encode("utf-8"))
                text_parts.append(value)
        elif kind == "resource":
            resource = item.get("resource")
            if not isinstance(resource, Mapping):
                unsupported.append("resource")
                continue
            uri = resource.get("uri")
            if isinstance(resource.get("text"), str):
                total += len(str(resource["text"]).encode("utf-8"))
                text_parts.append(f"[resource: {uri}]\n{resource['text']}")
            elif isinstance(resource.get("blob"), str):
                data = _bounded_blob(resource["blob"], limits)
                total += len(data)
                artifacts.append(
                    McpArtifact(
                        media_type=str(resource.get("mimeType") or "application/octet-stream"),
                        data=data,
                        uri=str(uri) if uri is not None else None,
                    )
                )
                text_parts.append(f"[resource: {uri}]（二进制资源已转为产物，可用 read_attachment 读取）")
        elif kind == "image":
            raw = item.get("data")
            if not isinstance(raw, str):
                unsupported.append("image")
                continue
            data = _bounded_blob(raw, limits)
            total += len(data)
            media_type = str(item.get("mimeType") or "application/octet-stream")
            artifacts.append(McpArtifact(media_type=media_type, data=data))
            text_parts.append(f"[image: {media_type}, {len(data)} bytes]")
        else:
            unsupported.append(str(kind))
    if total > limits.max_total_bytes:
        raise McpContentTooLarge("MCP total content limit exceeded")
    if writer is not None and artifacts:
        resolved: list[McpArtifact] = []
        for artifact in artifacts:
            artifact_id = await writer.write(artifact)
            resolved.append(replace(artifact, artifact_id=artifact_id))
            text_parts.append(f"[artifact: {artifact_id}]")
        artifacts = resolved
    if unsupported:
        text_parts.append("[unsupported MCP content: " + ", ".join(unsupported) + "]")
    return McpResult("\n".join(text_parts), tuple(artifacts), tuple(unsupported))


def _bounded_blob(value: str, limits: McpLimits) -> bytes:
    try:
        data = base64.b64decode(value, validate=True)
    except ValueError as exc:
        raise McpContentTooLarge("MCP media is not valid base64") from exc
    if len(data) > limits.max_artifact_bytes:
        raise McpContentTooLarge("MCP artifact limit exceeded")
    return data


__all__ = [
    "McpArtifact",
    "McpArtifactWriter",
    "McpContentTooLarge",
    "McpResult",
    "convert_content",
]
