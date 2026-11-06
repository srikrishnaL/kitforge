"""Command line: ``mcptest lint|tools|call|snapshot|diff``.

The server comes after ``--`` (a command to start over stdio) or from
``--url`` (a running Streamable HTTP server)::

    mcptest lint -- python server.py
    mcptest lint --url http://localhost:8000/mcp
    mcptest snapshot -o mcp-tools.json -- npx -y @acme/mcp-server
    mcptest diff mcp-tools.json -- python server.py
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List, Optional, Sequence

from . import __version__
from . import snapshot as snap
from .client import MCPClient, MCPError, MCPTransportError
from .lint import RULES, SEVERITIES, at_or_above, count_by_severity, lint_tools


def _split(argv: Sequence[str]):
    argv = list(argv)
    if "--" in argv:
        i = argv.index("--")
        return argv[:i], argv[i + 1:]
    return argv, []


def _add_server_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--url", help="Streamable HTTP URL of a running server")
    p.add_argument("--header", action="append", default=[], metavar="NAME:VALUE",
                   help="HTTP header to send, e.g. 'Authorization: Bearer ...' (repeatable)")
    p.add_argument("--env", action="append", default=[], metavar="NAME=VALUE",
                   help="environment variable for a stdio server (repeatable)")
    p.add_argument("--timeout", type=float, default=30.0, help="seconds per reply (default 30)")


def _connect(args: argparse.Namespace, command: List[str]) -> MCPClient:
    if args.url and command:
        raise SystemExit("mcptest: give --url or a command after --, not both")
    if args.url:
        headers: Dict[str, str] = {}
        for h in args.header:
            name, _, value = h.partition(":")
            headers[name.strip()] = value.strip()
        return MCPClient.http(args.url, headers=headers, timeout=args.timeout)
    if command:
        env = dict(e.split("=", 1) for e in args.env if "=" in e)
        return MCPClient.stdio(command, env=env, timeout=args.timeout)
    raise SystemExit("mcptest: no server given; add --url URL or -- COMMAND ...")


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mcptest",
        description="Test and lint MCP servers. Put the server command after --.",
    )
    p.add_argument("--version", action="version", version=f"mcptest {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    lint = sub.add_parser("lint", help="check tool names, descriptions and schemas")
    _add_server_args(lint)
    lint.add_argument("--ignore", default="", help="comma-separated rule codes to skip")
    lint.add_argument("--fail-on", choices=SEVERITIES, default="error",
                      help="exit 1 if a finding is at this level or worse (default: error)")
    lint.add_argument("--format", choices=("text", "json"), default="text")
    lint.add_argument("--rules", action="store_true", help="list the rules and exit")

    tools = sub.add_parser("tools", help="print the server's tools as JSON")
    _add_server_args(tools)

    call = sub.add_parser("call", help="call one tool and print the result")
    _add_server_args(call)
    call.add_argument("tool")
    call.add_argument("arguments", nargs="?", default="{}", help="JSON object of arguments")

    snapshot = sub.add_parser("snapshot", help="write the tool schemas to a JSON snapshot")
    _add_server_args(snapshot)
    snapshot.add_argument("-o", "--output", default="-", help="file to write (default: stdout)")

    diff = sub.add_parser("diff", help="compare a snapshot with another snapshot or a live server")
    _add_server_args(diff)
    diff.add_argument("old", help="the earlier snapshot file")
    diff.add_argument("new", nargs="?", help="the later snapshot file (omit to use the live server)")
    diff.add_argument("--format", choices=("text", "json"), default="text")
    diff.add_argument("--allow-breaking", action="store_true",
                      help="exit 0 even when there are breaking changes")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    own, command = _split(sys.argv[1:] if argv is None else argv)
    args = _parser().parse_args(own)
    try:
        return _run(args, command)
    except (MCPTransportError, MCPError) as exc:
        print(f"mcptest: {exc}", file=sys.stderr)
        return 2


def _run(args: argparse.Namespace, command: List[str]) -> int:
    if args.cmd == "lint":
        if args.rules:
            for code, text in RULES.items():
                print(f"{code}  {text}")
            return 0
        with _connect(args, command) as client:
            tools = client.list_tools()
            name = client.server_info.get("name") or "server"
        ignore = [c.strip() for c in args.ignore.split(",") if c.strip()]
        findings = lint_tools(tools, ignore=ignore)
        if args.format == "json":
            print(json.dumps({"server": name, "tools": len(tools),
                              "findings": [f.as_dict() for f in findings]}, indent=2))
        else:
            for f in findings:
                print(f)
            counts = count_by_severity(findings)
            size = len(json.dumps(tools))
            print(
                f"\n{name}: {len(tools)} tools, about {size // 4:,} tokens of definitions; "
                f"{counts['error']} errors, {counts['warning']} warnings, {counts['info']} info"
            )
        return 1 if at_or_above(findings, args.fail_on) else 0

    if args.cmd == "tools":
        with _connect(args, command) as client:
            print(json.dumps(client.list_tools(), indent=2, ensure_ascii=False))
        return 0

    if args.cmd == "call":
        try:
            arguments: Any = json.loads(args.arguments)
        except ValueError as exc:
            raise SystemExit(f"mcptest: arguments must be a JSON object: {exc}") from None
        if not isinstance(arguments, dict):
            raise SystemExit("mcptest: arguments must be a JSON object")
        with _connect(args, command) as client:
            result = client.call_tool(args.tool, arguments)
        print(json.dumps(result.raw, indent=2, ensure_ascii=False))
        return 1 if result.is_error else 0

    if args.cmd == "snapshot":
        with _connect(args, command) as client:
            data = snap.normalize(client.list_tools())
        if args.output == "-":
            sys.stdout.write(snap.dumps(data))
        else:
            snap.save(args.output, data)
            print(f"wrote {len(data['tools'])} tools to {args.output}", file=sys.stderr)
        return 0

    if args.cmd == "diff":
        old = snap.load(args.old)
        if args.new:
            new = snap.load(args.new)
        else:
            with _connect(args, command) as client:
                new = snap.normalize(client.list_tools())
        changes = snap.diff(old, new)
        if args.format == "json":
            print(json.dumps([c.as_dict() for c in changes], indent=2))
        elif not changes:
            print("no changes")
        else:
            for c in changes:
                print(c)
            n = len(snap.breaking(changes))
            print(f"\n{n} breaking, {len(changes) - n} compatible")
        return 1 if snap.breaking(changes) and not args.allow_breaking else 0

    raise AssertionError(args.cmd)  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
