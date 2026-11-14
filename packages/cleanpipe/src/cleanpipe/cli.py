"""Command line: ``cleanpipe [options] -- <server command>`` and ``cleanpipe check -- <server command>``."""

from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from . import __version__
from .check import check
from .pipe import Pipe

USAGE = """\
cleanpipe [--log FILE | --drop] [--quiet] -- <server command> [args...]
       cleanpipe check [--timeout S] [--json] -- <server command> [args...]"""

DESCRIPTION = """\
Run a stdio MCP server so that only JSON-RPC messages reach the client on stdout. Banners,
log lines and print() output the server writes to stdout go to stderr instead (or to --log,
or nowhere with --drop), so the client's handshake doesn't fail. `check` reports whether a
server writes such output, for its maintainers and CI."""


def _split_argv(argv: List[str]):
    """Options before `--` (or before the first non-option word), and the server command."""
    if "--" in argv:
        i = argv.index("--")
        return argv[:i], argv[i + 1:]
    opts: List[str] = []
    i = 0
    while i < len(argv) and argv[i].startswith("-"):
        opts.append(argv[i])
        if argv[i] in ("--log", "--timeout") and i + 1 < len(argv):
            opts.append(argv[i + 1])
            i += 1
        i += 1
    return opts, argv[i:]


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    mode = "run"
    if argv and argv[0] == "check":
        mode = "check"
        argv = argv[1:]
    opts, command = _split_argv(argv)

    parser = argparse.ArgumentParser(prog="cleanpipe", usage=USAGE, description=DESCRIPTION)
    parser.add_argument("--version", action="version", version=f"cleanpipe {__version__}")
    if mode == "run":
        parser.add_argument("--log", metavar="FILE",
                            help="append the non-JSON-RPC output to FILE instead of stderr")
        parser.add_argument("--drop", action="store_true", help="discard the non-JSON-RPC output")
        parser.add_argument("--quiet", action="store_true",
                            help="don't print cleanpipe's own notes on stderr")
    else:
        parser.add_argument("--timeout", type=float, default=10.0,
                            help="seconds to wait for each reply (default 10)")
        parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args(opts)
    if not command:
        parser.error("give the server command after --, e.g. cleanpipe -- npx -y some-mcp-server")

    if mode == "check":
        return _check(command, args)

    if args.log and args.drop:
        parser.error("use --log or --drop, not both")
    log = None
    try:
        if args.log:
            try:
                log = open(args.log, "ab")
            except OSError as exc:
                parser.error(f"can't open {args.log}: {exc}")
        return Pipe(command, noise=log, drop=args.drop, quiet=args.quiet).run()
    finally:
        if log is not None:
            log.close()


def _check(command: List[str], args) -> int:
    report = check(command, timeout=args.timeout)
    if args.json:
        import json
        print(json.dumps({"clean": report.clean, "handshake": report.handshake,
                          "tools": report.tools, "noise": report.noise,
                          "exit_code": report.exit_code, "error": report.error}, indent=2))
    else:
        if report.handshake:
            tools = "" if report.tools is None else f", {report.tools} tool(s)"
            print(f"handshake: ok{tools}")
        if report.error:
            print(f"error: {report.error}")
        if report.noise:
            print(f"stdout noise: {len(report.noise)} line(s) that are not JSON-RPC "
                  "(clients may fail the handshake; log to stderr instead):")
            for line in report.noise[:20]:
                print(f"  | {line[:200]}")
            if len(report.noise) > 20:
                print(f"  ... and {len(report.noise) - 20} more")
        elif report.handshake:
            print("stdout: clean")
    if report.error:
        return 2
    return 0 if report.clean else 1
