"""MCP server: stdio (for opencode) and HTTP transports over the same tools.

stdio:  python -m ewo.mcp.server
HTTP:   enabled via ``mcp.http_enabled`` in config; serves streamable HTTP on
        ``mcp.http_port``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ewo.config import Config, load_config
from ewo.mcp.tools import EwoApi, register_tools

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer


def build_server(config: Config, api: EwoApi | None = None) -> MCPServer[Any]:
    from mcp.server.mcpserver import MCPServer

    server = MCPServer(name="ewo", instructions="Task/project management for me and my team.")
    register_tools(server, api or EwoApi(config))
    return server


def main() -> None:  # pragma: no cover - transport glue
    config = load_config()
    server = build_server(config)
    if config.mcp.http_enabled:
        import uvicorn

        uvicorn.run(server.streamable_http_app(), host="0.0.0.0", port=config.mcp.http_port)
    else:
        server.run("stdio")


if __name__ == "__main__":  # pragma: no cover
    main()
