"""Client Identifier URL checks and loopback-aware redirect_uri matching."""

from __future__ import annotations

import re
from typing import Iterable, Optional, Tuple

from .errors import InvalidClientId

#: Hosts that get the RFC 8252 / RFC 9700 "any port" exception for http redirects.
LOOPBACK_IPS = ("127.0.0.1", "[::1]")

_DOT_SEGMENT = re.compile(r"^(?:\.|%2e){1,2}$", re.IGNORECASE)


def is_client_id_url(value: object) -> bool:
    """True if ``value`` should be treated as a Client Identifier URL.

    Use it to route between pre-registered clients (random ids) and CIMD
    clients: the spec asks servers never to issue ids starting with https://.
    """
    return isinstance(value, str) and value.startswith("https://")


def _split_netloc(netloc: str) -> Tuple[str, Optional[str], Optional[str]]:
    """Split ``user@host:port`` into (host, port, userinfo) without normalizing."""
    userinfo = None
    if "@" in netloc:
        userinfo, _, netloc = netloc.rpartition("@")
    if netloc.startswith("["):
        end = netloc.find("]")
        if end == -1:
            raise ValueError("unclosed IPv6 bracket")
        host, rest = netloc[: end + 1], netloc[end + 1 :]
        if rest and not rest.startswith(":"):
            raise ValueError("junk after IPv6 address")
        port = rest[1:] if rest else None
    elif ":" in netloc:
        host, _, port = netloc.partition(":")
    else:
        host, port = netloc, None
    return host, port, userinfo


def _split(url: str) -> Tuple[str, str, str]:
    """Split an absolute URL into (scheme, netloc, rest) exactly as written."""
    scheme, sep, after = url.partition("://")
    if not sep:
        raise ValueError("not an absolute URL with an authority")
    cut = len(after)
    for ch in "/?#":
        i = after.find(ch)
        if i != -1:
            cut = min(cut, i)
    return scheme, after[:cut], after[cut:]


def _valid_port(port: Optional[str]) -> bool:
    return port is None or (port.isdigit() and port.isascii() and 0 < int(port) < 65536)


def check_client_id_url(value: object, *, allow_query: bool = True) -> str:
    """Raise :class:`InvalidClientId` unless ``value`` is a valid Client Identifier URL.

    Rules (draft-ietf-oauth-client-id-metadata-document, section 3): https
    scheme, no userinfo, a path other than ``/``, no ``.`` or ``..`` segments,
    no fragment, and (SHOULD) no query. The URL is not normalized: the server
    must compare it as an exact string.
    """
    if not isinstance(value, str) or not value:
        raise InvalidClientId("client_id must be a non-empty string")
    if any(ord(c) <= 0x20 or ord(c) == 0x7F for c in value):
        raise InvalidClientId("client_id contains spaces or control characters")
    if not value.startswith("https://"):
        raise InvalidClientId("client_id URL must start with https://")
    if "#" in value:
        raise InvalidClientId("client_id URL must not contain a fragment")
    try:
        _, netloc, rest = _split(value)
        host, port, userinfo = _split_netloc(netloc)
    except ValueError as exc:
        raise InvalidClientId(f"client_id URL is malformed: {exc}") from None
    if userinfo is not None:
        raise InvalidClientId("client_id URL must not contain a username or password")
    if not host:
        raise InvalidClientId("client_id URL has no host")
    if not _valid_port(port):
        raise InvalidClientId("client_id URL has an invalid port")
    path, _, query = rest.partition("?")
    if path in ("", "/"):
        raise InvalidClientId("client_id URL must have a path other than /")
    if any(_DOT_SEGMENT.match(seg) for seg in path.split("/")):
        raise InvalidClientId("client_id URL must not contain . or .. path segments")
    if "?" in rest and not allow_query:
        raise InvalidClientId("client_id URL must not contain a query")
    return value


def client_id_host(client_id: str) -> str:
    """The host to show on the consent screen (the spec asks servers to show it)."""
    _, netloc, _ = _split(client_id)
    host, _, _ = _split_netloc(netloc)
    return host.lower()


def is_loopback_redirect(uri: str, *, allow_localhost: bool = True) -> bool:
    """True for ``http://127.0.0.1``, ``http://[::1]`` (and ``http://localhost``) URIs."""
    try:
        scheme, netloc, _ = _split(uri)
        host, port, userinfo = _split_netloc(netloc)
    except ValueError:
        return False
    if scheme != "http" or userinfo is not None or not _valid_port(port):
        return False
    hosts = LOOPBACK_IPS + (("localhost",) if allow_localhost else ())
    return host.lower() in hosts


def redirect_uri_matches(requested: str, registered: str, *, allow_localhost: bool = True) -> bool:
    """Exact string match, except that loopback http redirects may use any port.

    Native apps and CLIs (Claude Code, VS Code, MCP Inspector) listen on a random
    port, so ``http://127.0.0.1:53123/callback`` matches a registered
    ``http://127.0.0.1/callback`` or ``http://127.0.0.1:8080/callback``
    (RFC 8252 section 7.3, RFC 9700 section 4.1.3). Host, path and query
    must still match exactly.
    """
    if requested == registered:
        return True
    if not (is_loopback_redirect(requested, allow_localhost=allow_localhost)
            and is_loopback_redirect(registered, allow_localhost=allow_localhost)):
        return False
    _, r_netloc, r_rest = _split(requested)
    _, g_netloc, g_rest = _split(registered)
    r_host = _split_netloc(r_netloc)[0].lower()
    g_host = _split_netloc(g_netloc)[0].lower()
    return r_host == g_host and r_rest == g_rest


def find_redirect_uri(requested: str, registered: Iterable[str], *,
                      allow_localhost: bool = True) -> Optional[str]:
    """The registered URI that ``requested`` matches, or None."""
    for uri in registered:
        if redirect_uri_matches(requested, uri, allow_localhost=allow_localhost):
            return uri
    return None
