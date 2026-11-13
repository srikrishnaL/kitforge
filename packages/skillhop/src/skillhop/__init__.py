"""skillhop: install the skills MCP servers serve (SEP-2640) into your agent's skills folder.

>>> from skillhop import ServerSpec, entries, fetch, install, skills_dir
>>> spec = ServerSpec(label="docs", url="https://example.com/mcp")
>>> with spec.connect() as client:
...     skills, _ = entries(client, ["pdf-processing"])
...     install(fetch(client, skills[0]), skills_dir("claude-code"), spec.save())

Every file is checked against the size and SHA-256 digest the server published, and SKILL.md's
frontmatter against the entry, before anything is written.
"""

__version__ = "0.1.0"

from .install import AGENTS, compare, entries, fetch, install, read_lock, remove, skills_dir, to_zip  # noqa: E402
from .rpc import Client, McpError, TransportError  # noqa: E402
from .servers import ServerNotFound, ServerSpec, find_configured  # noqa: E402
from .skills import Skill, SkillError, parse_entry, verify  # noqa: E402

__all__ = [
    "AGENTS", "Client", "McpError", "ServerNotFound", "ServerSpec", "Skill", "SkillError",
    "TransportError", "compare", "entries", "fetch", "find_configured", "install", "parse_entry",
    "read_lock", "remove", "skills_dir", "to_zip", "verify",
]
