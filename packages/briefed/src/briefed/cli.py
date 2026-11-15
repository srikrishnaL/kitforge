"""briefed: deliver an MCP server's instructions to clients that drop them."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
from typing import List, Optional

from . import __version__
from .core import DEFAULT_TOOL_NAME, INLINE_LIMIT, MODES, Brief


def _text(value: Optional[str], parser: argparse.ArgumentParser) -> Optional[str]:
    """``@file`` reads a file, ``-`` is not allowed (stdin is the MCP channel), else literal."""
    if value is None:
        return None
    if value.startswith("@"):
        try:
            with open(os.path.expanduser(value[1:]), encoding="utf-8") as fh:
                return fh.read()
        except OSError as exc:
            parser.error(f"can't read {value[1:]}: {exc.strerror}")
    return value


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    command: List[str] = []
    if "--" in argv:
        at = argv.index("--")
        argv, command = argv[:at], argv[at + 1:]
    parser = argparse.ArgumentParser(
        prog="briefed",
        description="Deliver an MCP server's instructions to clients that drop or cut them, "
                    "by folding them into the tool list.")
    parser.add_argument("--version", action="version", version=f"briefed {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser(
        "proxy", help="run a stdio MCP server with its instructions folded into the tool list",
        description="Start the MCP server given after -- and sit between it and the client. "
                    "The server's instructions (or your own) reach the model through the tool "
                    "list. Example: briefed proxy -- npx -y @acme/server")
    p.add_argument("--mode", choices=MODES, default="tool",
                   help="tool: add a tool that returns the instructions (default); "
                        "inline: put them at the top of one tool's description")
    p.add_argument("--instructions", metavar="TEXT|@FILE",
                   help="use these instructions instead of the server's")
    p.add_argument("--add", metavar="TEXT|@FILE",
                   help="add this text after the server's instructions")
    p.add_argument("--tool-name", default=DEFAULT_TOOL_NAME,
                   help=f"name of the added tool (default: {DEFAULT_TOOL_NAME})")
    p.add_argument("--target", metavar="TOOL",
                   help="inline mode: the tool whose description gets them (default: the first)")
    p.add_argument("--no-hint", action="store_true",
                   help="tool mode: don't add the 'call it first' line to each description")
    p.add_argument("--move", action="store_true",
                   help="also remove the instructions from the initialize result, so clients "
                        "that do read them don't get them twice")

    s = sub.add_parser(
        "show", help="print the instructions a stdio MCP server sends, and how big they are",
        description="Start the MCP server given after --, run the initialize handshake, and "
                    "print its instructions. Exits 1 if it sends none.")
    s.add_argument("--json", action="store_true", help="print a JSON report instead")
    s.add_argument("--timeout", type=float, default=30.0,
                   help="seconds to wait for the initialize reply (default 30)")
    args = parser.parse_args(argv)

    if not command:
        parser.error(f"{args.command} needs the server command after --, e.g. "
                     f"briefed {args.command} -- npx -y @acme/server")
    if args.command == "show":
        return _show(command, args.json, args.timeout)

    try:
        brief = Brief(_text(args.instructions, p), mode=args.mode, tool_name=args.tool_name,
                      target=args.target, hint=not args.no_hint, extra=_text(args.add, p))
    except ValueError as exc:
        parser.error(str(exc))
    from .proxy import run
    return run(command, brief, keep=not args.move)


def _show(command: List[str], as_json: bool, timeout: float) -> int:
    exe = shutil.which(command[0]) or command[0]
    try:
        proc = subprocess.Popen([exe] + command[1:], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE)
    except OSError as exc:
        print(f"briefed: can't start {command[0]}: {exc}", file=sys.stderr)
        return 127
    assert proc.stdin and proc.stdout
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {},
        "clientInfo": {"name": "briefed", "version": __version__}}}
    found: dict = {}

    def read() -> None:
        assert proc.stdout
        for line in iter(proc.stdout.readline, b""):
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if isinstance(msg, dict) and msg.get("id") == 1 and "method" not in msg:
                found["reply"] = msg
                return

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    try:
        proc.stdin.write((json.dumps(init) + "\n").encode())
        proc.stdin.flush()
    except OSError:
        pass
    reader.join(timeout)
    try:
        proc.stdin.close()
    except OSError:
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()

    reply = found.get("reply")
    if reply is None:
        print("briefed: the server did not answer initialize "
              f"within {timeout:g}s", file=sys.stderr)
        return 2
    result = reply.get("result") if isinstance(reply.get("result"), dict) else {}
    text = result.get("instructions") if isinstance(result.get("instructions"), str) else ""
    info = result.get("serverInfo") if isinstance(result.get("serverInfo"), dict) else {}
    report = {"server": info.get("name"), "version": info.get("version"),
              "characters": len(text), "bytes": len(text.encode("utf-8")),
              "instructions": text}
    if as_json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0 if text.strip() else 1
    name = report["server"] or command[0]
    if not text.strip():
        print(f"{name} sends no instructions.")
        return 1
    print(f"{name} sends {len(text):,} characters of instructions:\n")
    print(text)
    print()
    if len(text) > INLINE_LIMIT:
        print(f"Note: over {INLINE_LIMIT:,} characters. Clients that cut long descriptions would "
              "cut these too in inline mode; use the default tool mode.")
    print("Clients that drop instructions will get them with: briefed proxy -- "
          + " ".join(command))
    return 0
