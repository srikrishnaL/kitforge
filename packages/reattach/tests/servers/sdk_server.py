"""A real MCP Python SDK server (1.x FastMCP or 2.x MCPServer) over Streamable HTTP.

``python sdk_server.py PORT [--bare]`` serves it with uvicorn, wrapped in Reattach unless
``--bare`` is given. Each process start is a "restart": sessions live only in memory.
"""

import os
import sys

try:  # SDK 2.x
    from mcp.server.mcpserver import MCPServer as Server
except ImportError:  # SDK 1.x
    from mcp.server.fastmcp import FastMCP as Server

from reattach import Reattach

BOOT = os.environ.get("BOOT", "0")


def make_server():
    srv = Server("notes")

    @srv.tool()
    def search(q: str):
        """Search the notes."""
        return f"found {q} (boot {BOOT})"

    return srv


def make_app(json_response=False):
    srv = make_server()
    try:  # SDK 2.x
        app = srv.streamable_http_app(json_response=json_response)
    except TypeError:  # SDK 1.x keeps it in settings
        srv.settings.json_response = json_response
        app = srv.streamable_http_app()
    app.mcp_server = srv  # tests reach srv.session_manager to expire sessions
    return app


if __name__ == "__main__":
    import uvicorn

    port = int(sys.argv[1])
    inner = make_app()
    app = inner if "--bare" in sys.argv else Reattach(inner, "test-secret")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
