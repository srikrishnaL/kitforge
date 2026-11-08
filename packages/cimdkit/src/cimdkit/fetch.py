"""Fetch a Client ID Metadata Document the way the spec requires, safely.

* https only, no redirects followed, ``Accept: application/json``;
* every address the host resolves to must be public (no loopback, private,
  link-local, CGNAT, multicast or reserved ranges, IPv4-mapped and NAT64
  forms included), and the connection goes to the address that was checked,
  so DNS rebinding cannot swap it afterwards;
* at most ``max_bytes`` (5 KB by default) are read, under one overall deadline;
* the body must be a JSON object with no duplicate keys.
"""

from __future__ import annotations

import http.client
import ipaddress
import json
import socket
import ssl
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Mapping, Optional, Sequence

from .errors import BlockedAddress, FetchError, TransientFetchError
from .urls import _split, _split_netloc, check_client_id_url

DEFAULT_MAX_BYTES = 5 * 1024
DEFAULT_TIMEOUT = 5.0
USER_AGENT = "cimdkit (+https://github.com/srikrishnaL/kitforge)"

_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_TRANSIENT_STATUS = {408, 425, 429, 500, 502, 503, 504}

Resolver = Callable[[str, int], Sequence[str]]


@dataclass
class Response:
    status: int
    headers: Dict[str, str]
    body: bytes


Transport = Callable[..., Response]


@dataclass
class FetchResult:
    document: dict
    headers: Mapping[str, str] = field(default_factory=dict)
    address: str = ""


def is_public_address(addr: str) -> bool:
    """False for any special-use address (RFC 6890) an attacker could aim us at."""
    try:
        ip = ipaddress.ip_address(addr.split("%", 1)[0])
    except ValueError:
        return False
    if ip.version == 6:
        if ip.ipv4_mapped is not None:
            return is_public_address(str(ip.ipv4_mapped))
        if ip in _NAT64:
            return is_public_address(str(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)))
        if ip.sixtofour is not None and not is_public_address(str(ip.sixtofour)):
            return False
        if ip.teredo is not None:
            return False
    return bool(ip.is_global) and not ip.is_multicast


def _is_loopback(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr.split("%", 1)[0])
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_loopback


def system_resolver(host: str, port: int) -> List[str]:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(info[4][0] for info in infos))


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS to a fixed IP, with SNI and certificate checks for the real host name."""

    def __init__(self, host: str, port: int, address: str, timeout: float,
                 context: ssl.SSLContext) -> None:
        super().__init__(host, port, timeout=timeout, context=context)
        self._address = address
        self._ctx = context

    def connect(self) -> None:  # pragma: no cover - exercised by the TLS test
        sock = socket.create_connection((self._address, self.port), self.timeout)
        self.sock = self._ctx.wrap_socket(sock, server_hostname=self.host)


def https_transport(*, host: str, port: int, address: str, target: str,
                    headers: Mapping[str, str], timeout: float, max_bytes: int,
                    ssl_context: Optional[ssl.SSLContext] = None) -> Response:
    """Default transport: one GET over a pinned TLS connection, read with a deadline."""
    deadline = time.monotonic() + timeout
    ctx = ssl_context or ssl.create_default_context()
    conn = _PinnedHTTPSConnection(host, port, address, timeout, ctx)
    try:
        conn.request("GET", target, headers=dict(headers))
        resp = conn.getresponse()
        got: Dict[str, str] = {}
        for k, v in resp.getheaders():
            k = k.lower()
            got[k] = f"{got[k]}, {v}" if k in got else v
        length = got.get("content-length", "")
        if resp.status == 200 and length.isdigit() and int(length) > max_bytes:
            raise FetchError(f"metadata document is {length} bytes; the limit is {max_bytes}")
        chunks: List[bytes] = []
        size = 0
        while size <= max_bytes:
            if time.monotonic() > deadline:
                raise TransientFetchError("timed out reading the metadata document")
            chunk = resp.read(min(1024, max_bytes + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
        return Response(resp.status, got, b"".join(chunks))
    finally:
        conn.close()


def _no_duplicates(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise FetchError(f"metadata document repeats the property {key!r}")
        out[key] = value
    return out


def _is_json_type(content_type: str) -> bool:
    mime = content_type.split(";", 1)[0].strip().lower()
    return mime == "application/json" or (mime.startswith("application/") and mime.endswith("+json"))


def fetch_document(client_id: str, *, timeout: float = DEFAULT_TIMEOUT,
                   max_bytes: int = DEFAULT_MAX_BYTES, allow_loopback: bool = False,
                   require_json_content_type: bool = True,
                   resolver: Optional[Resolver] = None,
                   transport: Optional[Transport] = None,
                   ssl_context: Optional[ssl.SSLContext] = None,
                   user_agent: str = USER_AGENT) -> FetchResult:
    """Fetch and parse the document at ``client_id``. It is not validated here.

    ``allow_loopback`` lets a development server fetch from 127.0.0.1/::1; the
    spec forbids that in production. ``resolver`` and ``transport`` are hooks
    for tests and custom networking.
    """
    check_client_id_url(client_id)
    _, netloc, rest = _split(client_id)
    host, port_s, _ = _split_netloc(netloc)
    port = int(port_s) if port_s else 443
    bare = host[1:-1] if host.startswith("[") else host

    try:
        ipaddress.ip_address(bare)
        addresses: Sequence[str] = [bare]
    except ValueError:
        try:
            addresses = (resolver or system_resolver)(bare, port)
        except (OSError, UnicodeError) as exc:
            raise TransientFetchError(f"could not resolve {bare}: {exc}") from None
    if not addresses:
        raise TransientFetchError(f"{bare} has no addresses")
    for addr in addresses:
        if is_public_address(addr) or (allow_loopback and _is_loopback(addr)):
            continue
        raise BlockedAddress(
            f"{bare} resolves to {addr}, a private or special-use address; refusing to fetch it")

    headers = {"Accept": "application/json", "User-Agent": user_agent, "Host": netloc}
    try:
        resp = (transport or https_transport)(
            host=bare, port=port, address=addresses[0], target=rest, headers=headers,
            timeout=timeout, max_bytes=max_bytes, ssl_context=ssl_context)
    except (FetchError, TransientFetchError):
        raise
    except ssl.SSLCertVerificationError as exc:
        raise FetchError(f"TLS certificate for {bare} is not valid: {exc.verify_message}") from None
    except (OSError, http.client.HTTPException) as exc:
        raise TransientFetchError(f"could not fetch {client_id}: {exc}") from None

    if resp.status != 200:
        if 300 <= resp.status < 400:
            raise FetchError(f"{client_id} answered {resp.status}; redirects are not followed")
        if resp.status in _TRANSIENT_STATUS:
            raise TransientFetchError(f"{client_id} answered {resp.status}")
        raise FetchError(f"{client_id} answered {resp.status}, not 200")
    if len(resp.body) > max_bytes:
        raise FetchError(f"metadata document is larger than {max_bytes} bytes")
    ctype = resp.headers.get("content-type", "")
    if require_json_content_type and not _is_json_type(ctype):
        raise FetchError(f"metadata document is served as {ctype or 'no content type'!r}, "
                         "not application/json")
    try:
        doc = json.loads(resp.body.decode("utf-8"), object_pairs_hook=_no_duplicates)
    except FetchError:
        raise
    except (UnicodeDecodeError, ValueError) as exc:
        raise FetchError(f"metadata document is not valid JSON: {exc}") from None
    if not isinstance(doc, dict):
        raise FetchError("metadata document must be a JSON object")
    return FetchResult(doc, resp.headers, addresses[0])
