from __future__ import annotations

import os
from pathlib import Path

from mcp.server import MCPServer

server = MCPServer("fluxion-product-stdio-fixture")


@server.tool()
def lookup(query: str) -> dict[str, str]:
    call_log = os.environ.get("MCP_TEST_CALL_LOG")
    if call_log:
        Path(call_log).write_text(query, encoding="utf-8")
    return {"answer": f"MCP {query} result", "transport": "stdio"}


def main() -> None:
    pid_file = os.environ.get("MCP_TEST_PID_FILE")
    if pid_file:
        Path(pid_file).write_text(str(os.getpid()), encoding="ascii")
    server.run("stdio")


if __name__ == "__main__":
    main()
