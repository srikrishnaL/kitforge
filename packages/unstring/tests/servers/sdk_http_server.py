"""A real MCP Python SDK server (1.x FastMCP or 2.x MCPServer) with typed tool parameters.

``python sdk_http_server.py PORT`` serves it over Streamable HTTP with uvicorn.
"""

import json
import sys
from typing import List

try:  # SDK 2.x
    from mcp.server.mcpserver import MCPServer as Server
except ImportError:  # SDK 1.x
    from mcp.server.fastmcp import FastMCP as Server


def make_server():
    srv = Server("pages")

    @srv.tool()
    def create_pages(titles: List[str], position: int):
        """Create one page per title at a position."""
        return json.dumps({"titles": titles, "position": position})

    return srv


def make_app(json_response=False):
    srv = make_server()
    try:  # SDK 2.x
        return srv.streamable_http_app(json_response=json_response)
    except TypeError:  # SDK 1.x keeps it in settings
        srv.settings.json_response = json_response
        return srv.streamable_http_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(make_app(), host="127.0.0.1", port=int(sys.argv[1]), log_level="warning")
