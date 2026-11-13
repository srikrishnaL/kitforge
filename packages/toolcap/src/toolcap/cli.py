"""toolcap proxy: page the oversized tool results of any stdio MCP server."""

from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from . import __version__
from .core import Cap


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    command: List[str] = []
    if "--" in argv:
        at = argv.index("--")
        argv, command = argv[:at], argv[at + 1:]
    parser = argparse.ArgumentParser(
        prog="toolcap",
        description="Keep MCP tool results under the client's token limit.")
    parser.add_argument("--version", action="version", version=f"toolcap {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser(
        "proxy", help="run an MCP server with its long tool results paged",
        description="Start the MCP server given after -- and sit between it and the client. "
                    "Tool results over the budget are split into pages the model can follow "
                    "with a cursor. Example: toolcap proxy --max-tokens 20000 -- "
                    "npx -y @playwright/mcp")
    p.add_argument("--max-tokens", type=int, default=None,
                   help="budget for one result (default: TOOLCAP_MAX_TOKENS, else 20000)")
    p.add_argument("--cursor-param", default="cursor",
                   help="name of the parameter added to each tool (default: cursor)")
    p.add_argument("--trim", action="store_true",
                   help="cut long results to fit instead of paging them (adds no parameter)")
    p.add_argument("--quiet", action="store_true", help="don't log cut results to stderr")
    args = parser.parse_args(argv)

    if not command:
        parser.error("proxy needs the server command after --, e.g. "
                     "toolcap proxy -- npx -y @acme/server")
    try:
        cap = Cap(args.max_tokens, cursor_param=args.cursor_param)
    except ValueError as exc:
        parser.error(str(exc))
    from .proxy import run
    return run(command, cap, trim=args.trim, quiet=args.quiet)
