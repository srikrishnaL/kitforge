"""flatresult: make MCP tool results readable by clients that only show text blocks."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, List, Optional

from . import __version__
from .core import DEFAULT_MAX_TEXT, flatten


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    command: List[str] = []
    if "--" in argv:
        at = argv.index("--")
        argv, command = argv[:at], argv[at + 1:]
    parser = argparse.ArgumentParser(
        prog="flatresult",
        description="Rewrite MCP tool results (structuredContent without text, resource_link, "
                    "embedded resources, audio) into text and image blocks every client shows.")
    parser.add_argument("--version", action="version", version=f"flatresult {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser(
        "proxy", help="run a stdio MCP server and flatten the result of every tool call",
        description="Start the MCP server given after -- and sit between it and the client. "
                    "Tool results whose data is only in structuredContent, resource_link "
                    "blocks, embedded resources or audio get text (or image) blocks the "
                    "client can show. Linked resources are read from the server and put "
                    "inline. Example: flatresult proxy -- npx -y mcp-remote "
                    "https://mcp.example.com/mcp")
    _common(p)
    p.add_argument("--no-inline", action="store_true",
                   help="don't read linked resources; turn each link into a text line")
    p.add_argument("--max-links", type=int, default=5, metavar="N",
                   help="read at most N linked resources per result (default 5)")
    p.add_argument("--timeout", type=float, default=10.0, metavar="SECONDS",
                   help="send the result without link contents if the server has not "
                        "answered resources/read by then (default 10)")
    p.add_argument("--quiet", action="store_true", help="don't report rewrites on stderr")
    p.add_argument("--log", metavar="FILE", help="append rewrite reports to FILE, not stderr")

    c = sub.add_parser(
        "check", help="flatten one tool result (JSON) and print it",
        description="Read a tools/call result, or a whole JSON-RPC response, from FILE or "
                    "stdin, print the flattened result and list each rewrite on stderr. Exit "
                    "code 0 when every client can already read it, 2 when it was rewritten.")
    _common(c)
    c.add_argument("file", nargs="?", metavar="FILE", help="the result as JSON (default: stdin)")
    args = parser.parse_args(argv)

    if args.command == "check":
        return _check(args, parser)
    if not command:
        parser.error("proxy needs the server command after --, e.g. "
                     "flatresult proxy -- npx -y @acme/server")
    if args.max_links < 0:
        parser.error("--max-links must be 0 or more")
    from .proxy import run

    log = None
    if args.log:
        try:
            log = open(os.path.expanduser(args.log), "a", encoding="utf-8", buffering=1)
        except OSError as exc:
            parser.error(f"can't open {args.log}: {exc.strerror}")
    try:
        return run(command, keep=args.keep, inline=not args.no_inline,
                   max_links=args.max_links, max_text=args.max_text, timeout=args.timeout,
                   quiet=args.quiet, log=log)
    finally:
        if log is not None:
            log.close()


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--keep", action="store_true",
                   help="keep the original blocks and add the text after them (for clients "
                        "that skip unknown blocks instead of failing on them)")
    p.add_argument("--max-text", type=int, default=DEFAULT_MAX_TEXT, metavar="CHARS",
                   help=f"cut resource text put inline after CHARS characters "
                        f"(default {DEFAULT_MAX_TEXT:,})")


def _check(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    if args.file:
        try:
            with open(os.path.expanduser(args.file), encoding="utf-8") as fh:
                text = fh.read()
        except OSError as exc:
            parser.error(f"can't read {args.file}: {exc.strerror}")
    else:
        text = sys.stdin.read()
    try:
        doc: Any = json.loads(text)
    except ValueError as exc:
        parser.error(f"input is not JSON: {exc}")
    if isinstance(doc, dict) and isinstance(doc.get("result"), dict) and "jsonrpc" in doc:
        doc = doc["result"]
    flat = flatten(doc, keep=args.keep, max_text=args.max_text)
    for change in flat.changes:
        print(f"rewrote {change}", file=sys.stderr)
    print(json.dumps(flat.result, ensure_ascii=False, indent=2))
    return 2 if flat.changed else 0
