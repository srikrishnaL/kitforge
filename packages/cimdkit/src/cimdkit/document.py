"""Validate a fetched Client ID Metadata Document against the spec and a server policy."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Iterable, List, Mapping, Optional, Sequence, Tuple

from .errors import ClientIdMismatch, InvalidClientId, InvalidDocument, InvalidRedirectUri
from .urls import (
    _split,
    check_client_id_url,
    client_id_host,
    find_redirect_uri,
    is_loopback_redirect,
)

#: Shared-secret methods are forbidden: the document is public.
SYMMETRIC_AUTH_METHODS = frozenset({"client_secret_post", "client_secret_basic", "client_secret_jwt"})

#: JWK members that only appear in private or symmetric keys.
PRIVATE_JWK_MEMBERS = frozenset({"d", "p", "q", "dp", "dq", "qi", "oth", "k"})

#: Schemes never accepted in redirect_uris.
FORBIDDEN_REDIRECT_SCHEMES = frozenset({"javascript", "data", "vbscript", "file", "about", "blob"})

INFORMATIVE_URIS = ("client_uri", "logo_uri", "policy_uri", "tos_uri")


@dataclass(frozen=True)
class Policy:
    """What this authorization server supports and allows.

    ``auth_methods`` is in the server's terms; the client's own order of
    preference (``token_endpoint_auth_methods_supported``) picks among them.
    """

    grant_types: Tuple[str, ...] = ("authorization_code", "refresh_token")
    response_types: Tuple[str, ...] = ("code",)
    auth_methods: Tuple[str, ...] = ("none", "private_key_jwt")
    #: Ids the client_id must never equal: your issuer, trusted resource servers.
    forbidden_client_ids: Tuple[str, ...] = ()
    #: Require https redirect_uris to share the client_id's origin.
    same_origin_redirects: bool = False
    #: Accept http://localhost as a loopback redirect (127.0.0.1 and [::1] always are).
    allow_localhost: bool = True
    #: Accept a query in the client_id URL (the spec says SHOULD NOT, not MUST NOT).
    allow_query: bool = True
    #: Only accept client_ids on these hosts: "app.example.com" or "*.example.com".
    #: Empty means any public host.
    allowed_hosts: Tuple[str, ...] = ()

    def host_allowed(self, host: str) -> bool:
        if not self.allowed_hosts:
            return True
        host = host.lower().rstrip(".")
        for pattern in self.allowed_hosts:
            pattern = pattern.lower().rstrip(".")
            if pattern.startswith("*."):
                if host.endswith(pattern[1:]) and host != pattern[2:]:
                    return True
            elif host == pattern:
                return True
        return False


@dataclass(frozen=True)
class ClientMetadata:
    """A validated client. ``raw`` keeps every property, including unknown ones."""

    client_id: str
    redirect_uris: Tuple[str, ...]
    grant_types: Tuple[str, ...]
    response_types: Tuple[str, ...]
    token_endpoint_auth_method: str
    client_name: Optional[str] = None
    client_uri: Optional[str] = None
    logo_uri: Optional[str] = None
    policy_uri: Optional[str] = None
    tos_uri: Optional[str] = None
    scope: Optional[str] = None
    jwks: Optional[Mapping[str, Any]] = None
    jwks_uri: Optional[str] = None
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)
    #: Things that were dropped or look wrong but did not fail validation.
    warnings: Tuple[str, ...] = ()
    allow_localhost: bool = field(default=True, repr=False)

    @property
    def host(self) -> str:
        """Show this next to the client name on the consent screen."""
        return client_id_host(self.client_id)

    @property
    def display_name(self) -> str:
        return self.client_name or self.host

    def match_redirect_uri(self, redirect_uri: str) -> Optional[str]:
        """The registered redirect URI that ``redirect_uri`` matches, or None."""
        return find_redirect_uri(redirect_uri, self.redirect_uris,
                                 allow_localhost=self.allow_localhost)

    def check_redirect_uri(self, redirect_uri: Optional[str]) -> str:
        """Return the redirect_uri to use, or raise :class:`InvalidRedirectUri`.

        With no redirect_uri in the request, the only registered one is used
        (RFC 6749 section 3.1.2.3); with several it is an error.
        """
        if redirect_uri is None:
            if len(self.redirect_uris) == 1:
                return self.redirect_uris[0]
            raise InvalidRedirectUri("redirect_uri is required: the client registers several")
        if self.match_redirect_uri(redirect_uri) is None:
            raise InvalidRedirectUri(
                f"redirect_uri {redirect_uri!r} is not listed in the client's metadata document")
        return redirect_uri


def _str_list(doc: Mapping[str, Any], key: str) -> Optional[List[str]]:
    if key not in doc:
        return None
    value = doc[key]
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise InvalidDocument(f"{key} must be an array of strings")
    return value


def _opt_str(doc: Mapping[str, Any], key: str) -> Optional[str]:
    value = doc.get(key)
    if value is not None and not isinstance(value, str):
        raise InvalidDocument(f"{key} must be a string")
    return value


def _origin(url: str) -> str:
    scheme, netloc, _ = _split(url)
    return f"{scheme}://{netloc.lower()}"


def _check_redirect(uri: str, client_id: str, policy: Policy) -> None:
    scheme, colon, _ = uri.partition(":")
    if not colon or not scheme or not scheme[0].isalpha():
        raise InvalidDocument(f"redirect_uri {uri!r} is not an absolute URI")
    if "#" in uri:
        raise InvalidDocument(f"redirect_uri {uri!r} must not contain a fragment")
    lower = scheme.lower()
    if lower in FORBIDDEN_REDIRECT_SCHEMES:
        raise InvalidDocument(f"redirect_uri {uri!r} uses a forbidden scheme")
    if lower == "http":
        if not is_loopback_redirect(uri, allow_localhost=policy.allow_localhost):
            raise InvalidDocument(f"redirect_uri {uri!r}: http is only allowed on loopback")
    elif lower == "https":
        try:
            _split(uri)
        except ValueError:
            raise InvalidDocument(f"redirect_uri {uri!r} is not an absolute URI") from None
        if policy.same_origin_redirects and _origin(uri) != _origin(client_id):
            raise InvalidDocument(f"redirect_uri {uri!r} is not on the client_id's origin")
    # Any other scheme is a private-use scheme for native apps (com.example.app:/cb).


def _check_jwks(jwks: Any) -> None:
    if not isinstance(jwks, dict) or not isinstance(jwks.get("keys"), list):
        raise InvalidDocument('jwks must be an object with a "keys" array')
    for key in jwks["keys"]:
        if not isinstance(key, dict):
            raise InvalidDocument("each key in jwks must be an object")
        leaked = PRIVATE_JWK_MEMBERS.intersection(key)
        if leaked:
            raise InvalidDocument(
                "jwks contains private or symmetric key material "
                f"({', '.join(sorted(leaked))}); publish public keys only")


def _choose_auth_method(doc: Mapping[str, Any], policy: Policy) -> str:
    single = _opt_str(doc, "token_endpoint_auth_method")
    several = _str_list(doc, "token_endpoint_auth_methods_supported")
    declared = ([single] if single else []) + (several or [])
    bad = SYMMETRIC_AUTH_METHODS.intersection(declared)
    if bad:
        raise InvalidDocument(
            f"shared-secret auth method {sorted(bad)[0]!r} is not allowed in a public document")
    if single and several is not None and single not in several:
        raise InvalidDocument(
            "token_endpoint_auth_method must be one of token_endpoint_auth_methods_supported")
    if several is not None:
        candidates = several
    elif single:
        candidates = [single]
    else:
        # The RFC 7591 default (client_secret_basic) must NOT be applied.
        candidates = ["none"]
    for method in candidates:
        if method in policy.auth_methods:
            return method
    raise InvalidDocument(
        f"no supported token endpoint auth method: client offers {candidates}, "
        f"this server supports {list(policy.auth_methods)}")


def _intersect(doc_values: Optional[List[str]], default: Sequence[str],
               supported: Sequence[str], key: str) -> Tuple[str, ...]:
    values = default if doc_values is None else doc_values
    common = tuple(dict.fromkeys(v for v in values if v in supported))
    if not common:
        raise InvalidDocument(
            f"none of the client's {key} {list(values)} are supported (server: {list(supported)})")
    return common


def validate_document(doc: Any, client_id: str, policy: Optional[Policy] = None) -> ClientMetadata:
    """Check a parsed document fetched from ``client_id`` and return the client.

    Raises :class:`InvalidClientId`, :class:`ClientIdMismatch` or
    :class:`InvalidDocument`. Unknown properties are ignored, as the spec and
    RFC 7591 require.
    """
    policy = policy or Policy()
    check_client_id_url(client_id, allow_query=policy.allow_query)
    if client_id in policy.forbidden_client_ids:
        raise InvalidClientId("client_id must not equal the identifier of a trusted server")
    if not policy.host_allowed(client_id_host(client_id)):
        raise InvalidClientId(f"client_id host {client_id_host(client_id)!r} is not allowed here")
    if not isinstance(doc, dict):
        raise InvalidDocument("the metadata document must be a JSON object")

    doc_id = doc.get("client_id")
    if doc_id != client_id:
        raise ClientIdMismatch(
            f"document client_id {doc_id!r} does not exactly match the URL it was fetched "
            f"from {client_id!r}")

    for key in ("client_secret", "client_secret_expires_at"):
        if key in doc:
            raise InvalidDocument(f"{key} must not appear in a public metadata document")

    method = _choose_auth_method(doc, policy)

    jwks = doc.get("jwks")
    jwks_uri = _opt_str(doc, "jwks_uri")
    if jwks is not None and jwks_uri is not None:
        raise InvalidDocument("jwks and jwks_uri must not both be present")
    if jwks is not None:
        _check_jwks(jwks)
    if jwks_uri is not None and not jwks_uri.startswith("https://"):
        raise InvalidDocument("jwks_uri must be an https URL")
    if method == "private_key_jwt" and jwks is None and jwks_uri is None:
        raise InvalidDocument("private_key_jwt needs jwks or jwks_uri in the document")

    grant_types = _intersect(_str_list(doc, "grant_types"), ["authorization_code"],
                             policy.grant_types, "grant_types")
    response_types = _intersect(_str_list(doc, "response_types"), ["code"],
                                policy.response_types, "response_types")

    redirect_uris = _str_list(doc, "redirect_uris") or []
    for uri in redirect_uris:
        _check_redirect(uri, client_id, policy)
    if "authorization_code" in grant_types and not redirect_uris:
        raise InvalidDocument("redirect_uris is required for the authorization_code grant")

    warnings: List[str] = []
    informative = {}
    for key in INFORMATIVE_URIS:
        value = doc.get(key)
        if value is None:
            continue
        if isinstance(value, str) and value.startswith("https://"):
            informative[key] = value
        else:
            warnings.append(f"{key} ignored: only https URLs are shown to users")

    name = doc.get("client_name")
    if name is not None and not (isinstance(name, str) and name.strip()):
        warnings.append("client_name ignored: not a non-empty string")
        name = None

    scope = doc.get("scope")
    if scope is not None and not isinstance(scope, str):
        warnings.append("scope ignored: not a string")
        scope = None

    if "?" in client_id:
        warnings.append("client_id contains a query; the spec says it SHOULD NOT")

    return ClientMetadata(
        client_id=client_id,
        redirect_uris=tuple(redirect_uris),
        grant_types=grant_types,
        response_types=response_types,
        token_endpoint_auth_method=method,
        client_name=name,
        scope=scope,
        jwks=MappingProxyType(jwks) if jwks is not None else None,
        jwks_uri=jwks_uri,
        raw=MappingProxyType(dict(doc)),
        warnings=tuple(warnings),
        allow_localhost=policy.allow_localhost,
        **informative,
    )


def make_document(client_id: str, *, client_name: str, redirect_uris: Iterable[str],
                  grant_types: Iterable[str] = ("authorization_code", "refresh_token"),
                  response_types: Iterable[str] = ("code",),
                  token_endpoint_auth_method: str = "none",
                  **extra: Any) -> dict:
    """Build a metadata document for a client to publish, and check it first.

    Raises the same errors as :func:`validate_document` if the result would be
    rejected by a compliant authorization server.
    """
    doc = {
        "client_id": client_id,
        "client_name": client_name,
        "redirect_uris": list(redirect_uris),
        "grant_types": list(grant_types),
        "response_types": list(response_types),
        "token_endpoint_auth_method": token_endpoint_auth_method,
    }
    doc.update(extra)
    permissive = Policy(
        grant_types=tuple(doc["grant_types"]),
        response_types=tuple(doc["response_types"]),
        auth_methods=(token_endpoint_auth_method,),
    )
    validate_document(doc, client_id, permissive)
    return doc
