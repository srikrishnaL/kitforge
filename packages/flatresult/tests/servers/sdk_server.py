"""A real MCP Python SDK server (1.x FastMCP or 2.x MCPServer) returning links and resources."""

from mcp.types import EmbeddedResource, ResourceLink, TextResourceContents

try:  # SDK 2.x
    from mcp.server.mcpserver import MCPServer as Server
except ImportError:  # SDK 1.x
    from mcp.server.fastmcp import FastMCP as Server

FRAME = '{"width": 320, "height": 640}'
srv = Server("design")


@srv.resource("file:///design/frame-1.json", mime_type="application/json")
def frame():
    return FRAME


@srv.tool(structured_output=False)
def get_design():
    """Design context for a frame, as a link to it."""
    return [ResourceLink(type="resource_link", uri="file:///design/frame-1.json",
                         name="frame-1", mimeType="application/json")]


@srv.tool(structured_output=False)
def get_notes():
    """Notes, as an embedded resource."""
    return [EmbeddedResource(type="resource", resource=TextResourceContents(
        uri="file:///notes.md", mimeType="text/markdown", text="# Notes"))]


if __name__ == "__main__":
    srv.run()
