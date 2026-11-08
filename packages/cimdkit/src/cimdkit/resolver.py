"""Resolve a client_id to validated metadata, with spec-compliant caching."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Callable, Dict, Mapping, Optional

from .document import ClientMetadata, Policy, validate_document
from .errors import CIMDError, InvalidClientId, TransientFetchError
from .fetch import FetchResult, fetch_document
from .urls import check_client_id_url, client_id_host


def _directives(cache_control: str) -> Dict[str, Optional[str]]:
    out: Dict[str, Optional[str]] = {}
    for part in cache_control.split(","):
        name, eq, value = part.strip().partition("=")
        if name:
            out[name.strip().lower()] = value.strip().strip('"') if eq else None
    return out


def _http_date(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).timestamp()
    except (TypeError, ValueError, IndexError, OverflowError):
        return None


def cache_ttl(headers: Mapping[str, str], *, now: float, default_ttl: float = 300,
              min_ttl: float = 0, max_ttl: float = 86400) -> float:
    """Seconds to keep a document, from its HTTP cache headers (RFC 9111).

    ``no-store``/``no-cache`` mean 0 (unless ``min_ttl`` says otherwise),
    ``s-maxage`` beats ``max-age`` (an authorization server is a shared cache),
    ``Age`` is subtracted, ``Expires`` is used only without either, and
    ``default_ttl`` applies when nothing is said. The result is clamped to
    ``[min_ttl, max_ttl]``.
    """
    headers = {k.lower(): v for k, v in headers.items()}
    cc = _directives(headers.get("cache-control", ""))
    if "no-store" in cc or "no-cache" in cc or "private" in cc:
        ttl: float = 0
    else:
        raw = cc.get("s-maxage") if "s-maxage" in cc else cc.get("max-age")
        if raw is not None or "s-maxage" in cc or "max-age" in cc:
            ttl = int(raw) if raw and raw.isdigit() else 0
            age = headers.get("age", "")
            if age.isdigit():
                ttl -= int(age)
        elif "expires" in headers:
            expires = _http_date(headers["expires"])
            date = _http_date(headers.get("date")) or now
            ttl = expires - date if expires is not None else 0
        else:
            ttl = default_ttl
    return max(min_ttl, min(max_ttl, ttl))


@dataclass
class _Entry:
    metadata: ClientMetadata
    expires: float


class ClientResolver:
    """Fetch, validate and cache Client ID Metadata Documents.

    >>> resolver = ClientResolver(Policy(forbidden_client_ids=("https://auth.example.com",)))
    >>> client = resolver.resolve(request.args["client_id"])  # doctest: +SKIP
    >>> client.check_redirect_uri(request.args.get("redirect_uri"))  # doctest: +SKIP

    * A valid document is cached for as long as its cache headers allow,
      clamped to ``[min_ttl, max_ttl]``. Invalid documents and errors are never cached.
    * On a transient failure (timeout, 5xx) a previously valid document is
      kept and returned, as the spec asks; it is retried after ``retry_after``.
    * On a terminal failure the cached document is dropped and the error raised.
      Check ``error.revoke_tokens`` to see if the client's refresh tokens must go.

    Extra keyword arguments go to :func:`cimdkit.fetch_document`
    (``timeout``, ``max_bytes``, ``allow_loopback``...). Thread-safe.
    """

    def __init__(self, policy: Optional[Policy] = None, *,
                 fetch: Optional[Callable[[str], FetchResult]] = None,
                 default_ttl: float = 300, min_ttl: float = 0, max_ttl: float = 86400,
                 retry_after: float = 30, clock: Callable[[], float] = time.time,
                 **fetch_options) -> None:
        self.policy = policy or Policy()
        self._fetch = fetch or (lambda cid: fetch_document(cid, **fetch_options))
        self.default_ttl, self.min_ttl, self.max_ttl = default_ttl, min_ttl, max_ttl
        self.retry_after = retry_after
        self._clock = clock
        self._cache: Dict[str, _Entry] = {}
        self._lock = threading.Lock()

    def cached(self, client_id: str) -> Optional[ClientMetadata]:
        """The cached metadata, fresh or stale, without fetching."""
        with self._lock:
            entry = self._cache.get(client_id)
        return entry.metadata if entry else None

    def invalidate(self, client_id: str) -> None:
        with self._lock:
            self._cache.pop(client_id, None)

    def resolve(self, client_id: str, *, force: bool = False) -> ClientMetadata:
        """Return validated metadata for ``client_id`` or raise a :class:`CIMDError`."""
        now = self._clock()
        with self._lock:
            entry = self._cache.get(client_id)
        if entry and not force and now < entry.expires:
            return entry.metadata
        try:
            # Check the URL and host policy before any network traffic.
            check_client_id_url(client_id, allow_query=self.policy.allow_query)
            if not self.policy.host_allowed(client_id_host(client_id)):
                raise InvalidClientId(
                    f"client_id host {client_id_host(client_id)!r} is not allowed here")
            result = self._fetch(client_id)
            metadata = validate_document(result.document, client_id, self.policy)
        except TransientFetchError:
            if entry is None:
                raise
            with self._lock:
                entry.expires = now + self.retry_after
            return entry.metadata
        except CIMDError:
            self.invalidate(client_id)
            raise
        ttl = cache_ttl(result.headers, now=now, default_ttl=self.default_ttl,
                        min_ttl=self.min_ttl, max_ttl=self.max_ttl)
        with self._lock:
            if ttl > 0:
                self._cache[client_id] = _Entry(metadata, now + ttl)
            else:
                self._cache.pop(client_id, None)
        return metadata
