from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast


class OpenAIWireServer:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self._responses = responses
        self._server: asyncio.Server | None = None
        self.requests: list[dict[str, object]] = []
        self.request_headers: list[dict[str, str]] = []

    @property
    def base_url(self) -> str:
        if self._server is None or not self._server.sockets:
            raise RuntimeError("server is not running")
        port = cast(tuple[str, int], self._server.sockets[0].getsockname())[1]
        return f"http://127.0.0.1:{port}/v1"

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)

    async def close(self) -> None:
        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()

    async def _handle(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            header = await reader.readuntil(b"\r\n\r\n")
            body = await reader.readexactly(_content_length(header))
            self.request_headers.append(_headers(header))
            self.requests.append(cast(dict[str, object], json.loads(body)))
            index = len(self.requests) - 1
            payload = self._responses[min(index, len(self._responses) - 1)]
            encoded = json.dumps(payload).encode()
            writer.write(
                b"HTTP/1.1 200 OK\r\n"
                + f"Content-Length: {len(encoded)}\r\n".encode()
                + b"Content-Type: application/json\r\nConnection: close\r\n\r\n"
                + encoded
            )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()


def _content_length(header: bytes) -> int:
    for line in header.decode("ascii").split("\r\n"):
        name, separator, value = line.partition(":")
        if separator and name.lower() == "content-length":
            return int(value.strip())
    return 0


def _headers(header: bytes) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in header.decode("ascii").split("\r\n")[1:]:
        name, separator, value = line.partition(":")
        if separator:
            result[name.lower()] = value.strip()
    return result


@asynccontextmanager
async def openai_wire_server(
    responses: list[dict[str, object]],
) -> AsyncIterator[OpenAIWireServer]:
    server = OpenAIWireServer(responses)
    await server.start()
    try:
        yield server
    finally:
        await server.close()


def openai_tool_call_response(name: str = "lookup") -> dict[str, object]:
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call-lookup-1",
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": '{"query":"fluxion"}',
                            },
                        }
                    ],
                }
            }
        ]
    }


def openai_final_response(content: str) -> dict[str, object]:
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}
