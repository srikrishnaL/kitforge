"""schemashim check / fix: find and fix tool schemas a provider would reject."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, List, Optional

from . import __version__
from .core import TARGETS, adapt
from .tools import ToolKit


def _load(path: str) -> Any:
    try:
        if path == "-":
            return json.load(sys.stdin)
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except OSError as exc:
        raise SystemExit(f"schemashim: can't read {path}: {exc.strerror or exc}")
    except ValueError as exc:
        raise SystemExit(f"schemashim: {path} is not valid JSON: {exc}")


def _tools(data: Any) -> Optional[list]:
    """The list of tools in ``data``, or None when ``data`` is a bare schema."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        result = data.get("result") if isinstance(data.get("result"), dict) else data
        if isinstance(result.get("tools"), list):
            return result["tools"]
        if isinstance(result.get("tools"), dict):  # mcptest snapshot: {"tools": {name: tool}}
            return [{"name": k, **v} for k, v in result["tools"].items() if isinstance(v, dict)]
        if "name" in data and ("inputSchema" in data or "input_schema" in data):
            return [data]
    return None


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    command: List[str] = []
    if "--" in argv:
        at = argv.index("--")
        argv, command = argv[:at], argv[at + 1:]
    parser = argparse.ArgumentParser(
        prog="schemashim",
        description="Find and fix JSON Schemas that Gemini or OpenAI strict mode reject. "
                    "FILE is a schema, a tool, a list of tools or a tools/list result "
                    "('-' reads stdin).")
    parser.add_argument("--version", action="version", version=f"schemashim {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    c = sub.add_parser("check", help="list what each target would reject; exit 1 if anything")
    c.add_argument("file")
    c.add_argument("--target", choices=TARGETS + ("all",), default="all")
    c.add_argument("--json", action="store_true", help="print the findings as JSON")
    f = sub.add_parser("fix", help="print the adapted schema or tool declarations")
    f.add_argument("file")
    f.add_argument("--target", choices=TARGETS, required=True)
    p = sub.add_parser(
        "proxy", help="run an MCP server with its tool schemas (and names) fixed",
        description="Start the MCP server given after -- and sit between it and the client: "
                    "tool schemas in tools/list are rewritten for the target, and tools/call "
                    "arguments are mapped back. --max-name-length shortens long tool names "
                    "and maps calls back to the real name. Example: schemashim proxy --target "
                    "gemini -- npx -y @modelcontextprotocol/server-everything")
    p.add_argument("--target", choices=TARGETS,
                   help="rewrite tool schemas (and names) for this provider")
    p.add_argument("--max-name-length", type=int, metavar="N",
                   help="shorten tool names longer than N characters (12-64) and replace "
                        "characters model APIs reject; leave room for the prefix your client "
                        "adds, e.g. 64 minus len('mcp__<server>__')")
    p.add_argument("--quiet", action="store_true",
                   help="don't log the changes to stderr")
    args = parser.parse_args(argv)

    if args.command == "proxy":
        if args.target is None and args.max_name_length is None:
            parser.error("proxy needs --target, --max-name-length, or both")
        if args.max_name_length is not None and not 12 <= args.max_name_length <= 64:
            parser.error("--max-name-length must be from 12 to 64")
        if not command:
            parser.error("proxy needs the server command after --, e.g. "
                         "schemashim proxy --target gemini -- npx -y @acme/server")
        from .proxy import run
        return run(command, args.target, quiet=args.quiet,
                   max_name_length=args.max_name_length)
    if command:
        parser.error("unexpected arguments after --")

    data = _load(args.file)
    tools = _tools(data)
    try:
        if args.command == "fix":
            if tools is None:
                out: Any = adapt(data, args.target).schema
            else:
                out = ToolKit(tools, args.target).declarations
            json.dump(out, sys.stdout, indent=2)
            sys.stdout.write("\n")
            return 0

        targets = TARGETS if args.target == "all" else (args.target,)
        findings = []
        for target in targets:
            if tools is None:
                findings += [{"target": target, "tool": None, "path": c.path,
                              "message": c.message} for c in adapt(data, target).changes]
            else:
                for tool in ToolKit(tools, target):
                    findings += [{"target": target, "tool": tool.original_name, "path": c.path,
                                  "message": c.message} for c in tool.changes]
    except (TypeError, ValueError) as exc:
        print(f"schemashim: {exc}", file=sys.stderr)
        return 2

    if args.json:
        json.dump(findings, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        for item in findings:
            where = f"{item['tool']}: " if item["tool"] else ""
            print(f"[{item['target']}] {where}{item['path'] or '(root)'}: {item['message']}")
        if findings:
            n = len(findings)
            print(f"{n} problem{'s' if n != 1 else ''} found; `schemashim fix` rewrites them.")
        else:
            print("OK: accepted as is by " + ", ".join(targets))
    return 1 if findings else 0
