"""An MCP Python SDK server (1.x FastMCP or 2.x MCPServer) that print()s to stdout."""

try:
    from mcp.server.mcpserver import MCPServer as Server
except ImportError:
    from mcp.server.fastmcp import FastMCP as Server

print("Loading demo server...", flush=True)  # the classic mistake: stdout is the protocol channel

srv = Server("printer")


@srv.tool()
def add(a: int, b: int):
    """Add two numbers."""
    print(f"debug: adding {a} and {b}", flush=True)
    return a + b


if __name__ == "__main__":
    srv.run()
