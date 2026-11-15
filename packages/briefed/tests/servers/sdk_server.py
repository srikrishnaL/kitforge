"""A real MCP Python SDK server (1.x FastMCP or 2.x MCPServer) that sends instructions."""

from typing import Annotated

from pydantic import Field

try:  # SDK 2.x
    from mcp.server.mcpserver import MCPServer as Server
except ImportError:  # SDK 1.x
    from mcp.server.fastmcp import FastMCP as Server

INSTRUCTIONS = "Call search before get. Prices are in cents. Use has_price to filter tradeable bonds."
srv = Server("bonds", instructions=INSTRUCTIONS)


@srv.tool()
def search(q: Annotated[str, Field(description="Issuer name or ticker")]):
    """Search bonds by issuer or ticker."""
    return f"found {q}"


@srv.tool()
def get(bond_id: Annotated[str, Field(description="Bond id from search")]):
    """Get one bond by its id."""
    return f"bond {bond_id}"


if __name__ == "__main__":
    srv.run()
