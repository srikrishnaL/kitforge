"""A small, dependency-free client for the Dependency-Track REST API (v4.x).

Only the read endpoints bomscout needs, plus the one write (``PUT /api/v1/analysis``).
Every error is turned into a :class:`DTError` whose text tells the model, and the person
behind it, what went wrong and how to fix it (wrong URL, bad API key, a missing team
permission, an unknown project).
"""

from __future__ import annotations

import json
import os
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from .errors import DTError

#: A transport takes (method, url, headers, body) and returns (status, headers, body).
#: Tests pass a fake one; the default uses urllib.
Transport = Callable[[str, str, Dict[str, str], Optional[bytes]], Tuple[int, Dict[str, str], bytes]]

UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

# The team permission each endpoint needs (from the @PermissionRequired annotations of the
# Dependency-Track 4.x API server), so a 403 can say exactly what to grant.
_PERMISSIONS = [
    ("/v1/finding", "VIEW_VULNERABILITY"),
    ("/v1/analysis", "VULNERABILITY_ANALYSIS"),
    ("/v1/violation", "VIEW_POLICY_VIOLATION"),
    ("/v1/vulnerability", "VIEW_PORTFOLIO"),
    ("/v1/", "VIEW_PORTFOLIO"),
]

# Ordered guesses for the source of a vulnerability id when none is given.
_SOURCE_BY_PREFIX = [
    ("CVE-", "NVD"),
    ("GHSA-", "GITHUB"),
    ("SNYK-", "SNYK"),
    ("SONATYPE-", "OSSINDEX"),
    ("INT-", "INTERNAL"),
]
SOURCES = ["NVD", "GITHUB", "OSV", "SNYK", "OSSINDEX", "VULNDB", "NPM", "RETIREJS", "TRIVY",
           "INTERNAL"]


def _urllib_transport(timeout: float, context: Optional[ssl.SSLContext]) -> Transport:
    def send(method: str, url: str, headers: Dict[str, str], body: Optional[bytes]):
        req = urllib.request.Request(url, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=context) as resp:
                return resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read()
        except urllib.error.HTTPError as err:
            return err.code, {k.lower(): v for k, v in (err.headers or {}).items()}, err.read() or b""

    return send


def guess_sources(vuln_id: str) -> List[str]:
    """Sources to try for ``vuln_id``, most likely first."""
    upper = vuln_id.upper()
    first = [src for prefix, src in _SOURCE_BY_PREFIX if upper.startswith(prefix)]
    return first + [s for s in SOURCES if s not in first]


class DependencyTrack:
    """Dependency-Track API client.

    Args:
        url: Base URL of the API server, e.g. ``https://dtrack.example.com`` (the frontend URL
            works when the frontend and API share a host). A trailing ``/api`` is fine.
        api_key: An API key of a Dependency-Track team (Administration > Access Management >
            Teams). bomscout needs VIEW_PORTFOLIO, VIEW_VULNERABILITY and
            VIEW_POLICY_VIOLATION; VULNERABILITY_ANALYSIS only for recording an analysis.
        timeout: Seconds per request.
        verify: ``True`` (default), ``False`` to skip TLS verification, or a CA bundle path.
        transport: Replace the HTTP layer (used by tests).
        page_size: Items per page when walking paginated lists.
    """

    def __init__(self, url: str, api_key: str, *, timeout: float = 30.0,
                 verify: Any = True, transport: Optional[Transport] = None,
                 page_size: int = 100):
        if not url:
            raise DTError("Dependency-Track is not configured: set DT_URL to the API server's "
                          "URL and DT_API_KEY to a team API key, then restart the MCP server.")
        if not re.match(r"^https?://", url):
            url = "https://" + url
        base = url.rstrip("/")
        if base.endswith("/api"):
            base = base[: -len("/api")]
        self.base = base
        self.api_key = api_key
        self.page_size = page_size
        if transport is None:
            context = None
            if base.startswith("https://"):
                if verify is False:
                    context = ssl.create_default_context()
                    context.check_hostname = False
                    context.verify_mode = ssl.CERT_NONE
                elif isinstance(verify, str):
                    context = ssl.create_default_context(cafile=verify)
            transport = _urllib_transport(timeout, context)
        self._send = transport

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None, **kwargs: Any) -> "DependencyTrack":
        """Build a client from ``DT_URL``, ``DT_API_KEY`` (or the ``DEPENDENCY_TRACK_*``
        spellings), ``DT_TIMEOUT``, ``DT_CA_BUNDLE`` and ``DT_INSECURE``."""
        env = os.environ if env is None else env
        url = env.get("DT_URL") or env.get("DEPENDENCY_TRACK_URL") or ""
        key = env.get("DT_API_KEY") or env.get("DEPENDENCY_TRACK_API_KEY") or ""
        verify: Any = True
        if env.get("DT_CA_BUNDLE"):
            verify = env["DT_CA_BUNDLE"]
        if (env.get("DT_INSECURE") or "").lower() in ("1", "true", "yes"):
            verify = False
        timeout = float(env.get("DT_TIMEOUT") or 30)
        kwargs.setdefault("timeout", timeout)
        kwargs.setdefault("verify", verify)
        return cls(url, key, **kwargs)

    # ------------------------------------------------------------------ requests

    def request(self, method: str, path: str, params: Optional[Mapping[str, Any]] = None,
                body: Any = None) -> Tuple[Any, Dict[str, str]]:
        """Send one request to ``/api<path>`` and return (parsed JSON, lowercased headers)."""
        query = {k: _param(v) for k, v in (params or {}).items() if v is not None and v != ""}
        url = f"{self.base}/api{path}"
        if query:
            url += "?" + urllib.parse.urlencode(query)
        headers = {"Accept": "application/json", "User-Agent": "bomscout"}
        if self.api_key:
            headers["X-Api-Key"] = self.api_key
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        try:
            status, resp_headers, raw = self._send(method, url, headers, data)
        except DTError:
            raise
        except Exception as err:  # DNS, refused connection, TLS, timeout
            reason = getattr(err, "reason", None) or err
            raise DTError(f"Could not reach Dependency-Track at {self.base}: {reason}. Check "
                          "DT_URL (it must point at the API server) and the network.") from None
        if status >= 400:
            raise self._error(status, method, path, raw)
        text = raw.decode("utf-8", "replace").strip() if raw else ""
        if not text:
            return None, resp_headers
        try:
            return json.loads(text), resp_headers
        except ValueError:
            if "<html" in text[:500].lower():
                raise DTError(f"{self.base}/api{path} returned an HTML page, not JSON. DT_URL "
                              "probably points at the Dependency-Track frontend; set it to the "
                              "API server's URL.") from None
            return text, resp_headers

    def get(self, path: str, params: Optional[Mapping[str, Any]] = None) -> Any:
        return self.request("GET", path, params)[0]

    def put(self, path: str, body: Any) -> Any:
        return self.request("PUT", path, body=body)[0]

    def get_list(self, path: str, params: Optional[Mapping[str, Any]] = None,
                 limit: Optional[int] = None) -> Tuple[List[Any], int]:
        """Walk a paginated list. Returns (items, total). Stops after ``limit`` items."""
        items: List[Any] = []
        total: Optional[int] = None
        page = 1
        size = self.page_size if limit is None else max(1, min(self.page_size, limit))
        while True:
            p = dict(params or {})
            p.update(pageNumber=page, pageSize=size)
            data, headers = self.request("GET", path, p)
            batch = data if isinstance(data, list) else []
            items.extend(batch)
            count = headers.get("x-total-count")
            if count is not None and str(count).isdigit():
                total = int(count)
            if len(batch) < size or (total is not None and len(items) >= total):
                break
            if limit is not None and len(items) >= limit:
                break
            page += 1
            if page > 1000:  # a server that ignores paging; never loop forever
                break
        if limit is not None:
            items = items[:limit]
        return items, (total if total is not None else len(items))

    def _error(self, status: int, method: str, path: str, raw: bytes) -> DTError:
        detail = raw.decode("utf-8", "replace").strip()[:300] if raw else ""
        if "<html" in detail.lower():
            detail = ""
        if status == 401:
            return DTError("Dependency-Track rejected the API key (HTTP 401). Check DT_API_KEY: "
                           "it must be a team API key from Administration > Access Management > "
                           "Teams.", status=status)
        if status == 403:
            perm = next(p for prefix, p in _PERMISSIONS if path.startswith(prefix))
            return DTError(f"The API key's team lacks the {perm} permission needed for {method} "
                           f"/api{path} (HTTP 403). Add it to the team in Administration > Access "
                           "Management > Teams.", status=status)
        if status == 404:
            return DTError(f"Not found in Dependency-Track: /api{path}" +
                           (f" ({detail})" if detail else ""), status=status)
        return DTError(f"Dependency-Track returned HTTP {status} for {method} /api{path}" +
                       (f": {detail}" if detail else ""), status=status)

    # ------------------------------------------------------------------ helpers

    def version(self) -> Dict[str, Any]:
        """``GET /api/version`` (no API key needed)."""
        data = self.get("/version")
        return data if isinstance(data, dict) else {}

    def resolve_project(self, ref: str) -> Dict[str, Any]:
        """Find one project by UUID, ``name@version``, or name (latest version).

        A name with several versions and none marked latest raises :class:`DTError` listing
        the versions, so the model can ask again with ``name@version``.
        """
        ref = (ref or "").strip()
        if not ref:
            raise DTError("Give a project: its UUID, its name, or name@version.")
        if UUID_RE.match(ref):
            try:
                return self.get(f"/v1/project/{ref}")
            except DTError as err:
                if err.status == 404:
                    raise DTError(f"No project with UUID {ref}. Use list_projects to find it.",
                                  status=404) from None
                raise
        name, version = ref, None
        head, sep, tail = ref.rpartition("@")
        if sep and head and tail:  # "app@1.2", "@scope/app@1.2"; not "@scope/app"
            name, version = head, tail
        if version:
            try:
                return self.get("/v1/project/lookup", {"name": name, "version": version})
            except DTError as err:
                if err.status != 404:
                    raise
                # a project name that itself contains "@": fall through to a name search
                name, version = ref, None
        matches, _ = self.get_list("/v1/project", {"name": name, "excludeInactive": "false"},
                                   limit=200)
        exact = [p for p in matches if p.get("name") == name]
        if not exact:
            raise DTError(f"No project named {name!r}. Use list_projects to search by part of "
                          "the name.", status=404)
        if len(exact) == 1:
            return self.get(f"/v1/project/{exact[0]['uuid']}")
        latest = [p for p in exact if p.get("isLatest")]
        active = [p for p in exact if p.get("active", True)]
        pick = latest[0] if len(latest) == 1 else (active[0] if len(active) == 1 else None)
        if pick is None:
            versions = ", ".join(sorted(str(p.get("version") or "(no version)") for p in exact))
            raise DTError(f"{name!r} has several versions ({versions}) and none is marked "
                          f"latest. Ask again as {name}@<version>.")
        return self.get(f"/v1/project/{pick['uuid']}")

    def find_vulnerability(self, vuln_id: str, source: str = "") -> Dict[str, Any]:
        """Look up a vulnerability by id (CVE-..., GHSA-..., ...), guessing the source."""
        vuln_id = (vuln_id or "").strip()
        if not vuln_id:
            raise DTError("Give a vulnerability id, e.g. CVE-2021-44228 or GHSA-jfh8-c2jp-5v3q.")
        sources = [source.upper()] if source else guess_sources(vuln_id)
        quoted = urllib.parse.quote(vuln_id, safe="")
        for src in sources:
            try:
                data = self.get(f"/v1/vulnerability/source/{src}/vuln/{quoted}")
            except DTError as err:
                if err.status == 404:
                    continue
                raise
            if isinstance(data, dict) and data:
                return data
        tried = ", ".join(sources)
        raise DTError(f"Dependency-Track has no vulnerability {vuln_id} (sources tried: {tried}). "
                      "It only knows vulnerabilities its analyzers have seen in some project or "
                      "mirrored from a feed.", status=404)


def _param(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)
