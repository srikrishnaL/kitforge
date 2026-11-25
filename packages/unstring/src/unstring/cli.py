"""unstring: repair MCP tool-call arguments that clients send as JSON strings."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, List, Optional

from . import __version__
from .core import repair


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    command: List[str] = []
    if "--" in argv:
        at = argv.index("--")
        argv, command = argv[:at], argv[at + 1:]
    parser = argparse.ArgumentParser(
        prog="unstring",
        description="Repair MCP tool-call arguments that arrive as JSON strings "
                    "(\"expected array, received string\") before the server validates them.")
    parser.add_argument("--version", action="version", version=f"unstring {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser(
        "proxy", help="run a stdio MCP server and repair the arguments of every tool call",
        description="Start the MCP server given after -- and sit between it and the client. "
                    "Tool-call arguments that are JSON strings where the tool's schema wants "
                    "an array, object, number or boolean are decoded before the server sees "
                    "them. Example: unstring proxy -- npx -y @acme/server")
    p.add_argument("--wrap", action="store_true",
                   help="also wrap a single value in a list where the schema wants an array")
    p.add_argument("--quiet", action="store_true", help="don't report repairs on stderr")
    p.add_argument("--log", metavar="FILE", help="append repair reports to FILE, not stderr")

    f = sub.add_parser(
        "fix", help="repair one set of arguments against a schema and print the result",
        description="Read tool arguments (JSON) from ARGS or stdin, repair them against the "
                    "schema, print the result. Each repair is listed on stderr. Exit code 0 "
                    "when nothing needed repair, 2 when something was repaired.")
    f.add_argument("--schema", required=True, metavar="FILE|JSON",
                   help="the tool's inputSchema, or a tool object or tools/list result "
                        "(then also give --tool)")
    f.add_argument("--tool", metavar="NAME", help="pick this tool from a tool list")
    f.add_argument("--wrap", action="store_true",
                   help="also wrap a single value in a list where the schema wants an array")
    f.add_argument("args", nargs="?", metavar="ARGS", help="the arguments as JSON (default: stdin)")
    args = parser.parse_args(argv)

    if args.command == "fix":
        return _fix(args, parser)
    if not command:
        parser.error("proxy needs the server command after --, e.g. "
                     "unstring proxy -- npx -y @acme/server")
    from .proxy import run

    log = None
    if args.log:
        try:
            log = open(os.path.expanduser(args.log), "a", encoding="utf-8", buffering=1)
        except OSError as exc:
            parser.error(f"can't open {args.log}: {exc.strerror}")
    try:
        return run(command, wrap=args.wrap, quiet=args.quiet, log=log)
    finally:
        if log is not None:
            log.close()


def _load(text: str, what: str, parser: argparse.ArgumentParser) -> Any:
    try:
        return json.loads(text)
    except ValueError as exc:
        parser.error(f"{what} is not JSON: {exc}")


def _fix(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    raw = args.schema
    if not raw.lstrip().startswith(("{", "[")):
        try:
            with open(os.path.expanduser(raw), encoding="utf-8") as fh:
                raw = fh.read()
        except OSError as exc:
            parser.error(f"can't read {args.schema}: {exc.strerror}")
    schema = _pick(_load(raw, "--schema", parser), args.tool, parser)
    text = args.args if args.args is not None else sys.stdin.read()
    arguments = _load(text, "ARGS", parser)
    result = repair(arguments, schema, wrap=args.wrap)
    for fix in result.fixes:
        print(f"repaired {fix}", file=sys.stderr)
    print(json.dumps(result.value, ensure_ascii=False, indent=2))
    return 2 if result.changed else 0


def _pick(doc: Any, tool: Optional[str], parser: argparse.ArgumentParser) -> Any:
    """Accept an inputSchema, a tool, a list of tools or a tools/list result (or response)."""
    if isinstance(doc, dict) and isinstance(doc.get("result"), dict):
        doc = doc["result"]
    if isinstance(doc, dict) and isinstance(doc.get("tools"), list):
        doc = doc["tools"]
    if isinstance(doc, list):
        names = [t.get("name") for t in doc if isinstance(t, dict)]
        match = [t for t in doc if isinstance(t, dict) and t.get("name") == tool]
        if not match:
            parser.error(f"--tool must name one of: {', '.join(map(str, names))}" if tool is None
                         else f"no tool {tool!r} in the list (have: {', '.join(map(str, names))})")
        doc = match[0]
    if isinstance(doc, dict) and "inputSchema" in doc:
        doc = doc["inputSchema"]
    return doc
