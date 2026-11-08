"""``cimdkit check``: test a Client ID Metadata Document the way a server will."""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import List, Optional

from . import __version__
from .document import Policy, validate_document
from .errors import CIMDError
from .fetch import DEFAULT_MAX_BYTES, fetch_document
from .resolver import cache_ttl


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cimdkit",
        description="Check an OAuth Client ID Metadata Document (CIMD) as an authorization "
                    "server would: fetch rules, spec rules and redirect URIs.")
    p.add_argument("--version", action="version", version=f"cimdkit {__version__}")
    sub = p.add_subparsers(dest="command", required=True)
    c = sub.add_parser("check", help="fetch (or read) and validate a metadata document")
    c.add_argument("client_id", help="the Client Identifier URL")
    c.add_argument("--file", help="validate this local JSON file instead of fetching the URL")
    c.add_argument("--redirect-uri", action="append", default=[],
                   help="also check that this redirect_uri would be accepted (repeatable)")
    c.add_argument("--auth-method", action="append",
                   help="auth methods the server supports (default: none, private_key_jwt)")
    c.add_argument("--allow-loopback", action="store_true",
                   help="allow fetching from 127.0.0.1 / ::1 (development only)")
    c.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    c.add_argument("--timeout", type=float, default=5.0)
    c.add_argument("--json", action="store_true", help="print the result as JSON")
    return p


def _check(args: argparse.Namespace) -> int:
    policy = Policy(auth_methods=tuple(args.auth_method)) if args.auth_method else Policy()
    headers = {}
    try:
        if args.file:
            with open(args.file, encoding="utf-8") as fh:
                doc = json.load(fh)
        else:
            result = fetch_document(args.client_id, timeout=args.timeout,
                                    max_bytes=args.max_bytes,
                                    allow_loopback=args.allow_loopback)
            doc, headers = result.document, dict(result.headers)
        meta = validate_document(doc, args.client_id, policy)
        bad = [u for u in args.redirect_uri if meta.match_redirect_uri(u) is None]
    except CIMDError as exc:
        kind = "transient" if exc.transient else "invalid_client"
        if args.json:
            print(json.dumps({"ok": False, "kind": kind, "error": str(exc)}))
        else:
            print(f"FAIL ({kind}): {exc}", file=sys.stderr)
        return 1
    except (OSError, ValueError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    ttl = cache_ttl(headers, now=time.time()) if headers else None
    if args.json:
        print(json.dumps({
            "ok": not bad, "client_id": meta.client_id, "client_name": meta.client_name,
            "host": meta.host, "redirect_uris": list(meta.redirect_uris),
            "grant_types": list(meta.grant_types), "response_types": list(meta.response_types),
            "token_endpoint_auth_method": meta.token_endpoint_auth_method,
            "cache_seconds": ttl, "warnings": list(meta.warnings),
            "rejected_redirect_uris": bad,
        }, indent=2))
    else:
        print(f"{'OK' if not bad else 'FAIL'}  {meta.client_id}")
        print(f"  name           {meta.display_name}  (host {meta.host})")
        print(f"  auth method    {meta.token_endpoint_auth_method}")
        print(f"  grant types    {', '.join(meta.grant_types)}")
        for uri in meta.redirect_uris:
            print(f"  redirect_uri   {uri}")
        if ttl is not None:
            print(f"  cache          {int(ttl)} s")
        for w in meta.warnings:
            print(f"  warning        {w}")
        for uri in bad:
            print(f"  rejected       {uri} is not a registered redirect_uri")
    return 1 if bad else 0


def main(argv: Optional[List[str]] = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "check":
        return _check(args)
    return 2  # pragma: no cover
