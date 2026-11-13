"""A real FastMCP (4.1.2+) server serving a skills folder through its Skills extension."""

import sys
from pathlib import Path

from fastmcp import FastMCP
from fastmcp.server.providers.skills import SkillsDirectoryProvider
from fastmcp.skills.extension import SkillsExtension

mcp = FastMCP("fastmcp-skills")
provider = SkillsDirectoryProvider(roots=[Path(sys.argv[1])], experimental_skills=True)
mcp.add_provider(provider)
mcp.add_extension(SkillsExtension(providers=[provider]))

if __name__ == "__main__":
    if len(sys.argv) > 2:
        mcp.run(transport="http", host="127.0.0.1", port=int(sys.argv[2]), show_banner=False)
    else:
        mcp.run(show_banner=False)
