"""Command line: ``reattach proxy http://127.0.0.1:3000 --port 8080``."""

from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from . import __version__


def build_app(upstream: str, secret: Optional[str] = None, path: Optional[str] = None,
              timeout: Optional[float] = None, quiet: bool = False, strict: bool = False):
    from .asgi import Reattach
    from .proxy import Proxy

    def log(token: str, inner: str) -> None:
        if not quiet:
            print(f"reattach: reattached session {token[:16]}... to {inner}", file=sys.stderr,
                  flush=True)

    return Reattach(Proxy(upstream, timeout=timeout), secret, path=path, on_reattach=log,
                    strict=strict)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="reattach",
        description="Keep MCP Streamable HTTP sessions alive across server restarts.")
    parser.add_argument("--version", action="version", version=f"reattach {__version__}")
    sub = parser.add_subparsers(dest="command")
    p = sub.add_parser("proxy", help="run a reverse proxy in front of an MCP server")
    p.add_argument("upstream", help="the MCP server's origin, e.g. http://127.0.0.1:3000")
    p.add_argument("--host", default="127.0.0.1", help="address to listen on (default 127.0.0.1)")
    p.add_argument("--port", type=int, default=8080, help="port to listen on (default 8080)")
    p.add_argument("--path", help="only handle MCP sessions on this path (default: every path)")
    p.add_argument("--secret", help="sign session tokens (default: $REATTACH_SECRET)")
    p.add_argument("--timeout", type=float, default=None,
                   help="upstream read timeout in seconds (default: none, for SSE streams)")
    p.add_argument("--quiet", action="store_true", help="do not log reattached sessions")
    p.add_argument("--strict", action="store_true",
                   help="reattach only on 404 (default: also on replies such as 400 'Invalid "
                        "session ID' or a JSON-RPC error 'Session not found')")
    args = parser.parse_args(argv)
    if args.command != "proxy":
        parser.print_help()
        return 2
    try:
        import httpx  # noqa: F401
        import uvicorn
    except ImportError:
        print('reattach proxy needs extra packages: pip install "reattach[proxy]"',
              file=sys.stderr)
        return 2
    app = build_app(args.upstream, args.secret, args.path, args.timeout, args.quiet,
                    args.strict)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
