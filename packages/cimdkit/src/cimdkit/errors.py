"""Errors raised while resolving a Client ID Metadata Document.

Every error carries the OAuth error code to return (``invalid_client``) and
whether the failure is *transient* (network trouble: retry later, keep any
cached metadata, answer 503) or *terminal* (the document is missing, wrong or
rejected: drop cached metadata, answer ``invalid_client``).
"""

from __future__ import annotations

from typing import Dict


class CIMDError(Exception):
    """Base class. The message is safe to show to the person signing in."""

    #: OAuth error code for the token endpoint / authorization error page.
    error = "invalid_client"
    #: True for network problems: retry later and keep cached metadata.
    transient = False
    #: True when the spec says the client's refresh tokens should be revoked.
    revoke_tokens = False

    @property
    def status_code(self) -> int:
        """HTTP status the spec suggests at the token endpoint."""
        return 503 if self.transient else 400

    def to_oauth_error(self) -> Dict[str, str]:
        """``{"error": ..., "error_description": ...}`` for a JSON error body."""
        return {"error": self.error, "error_description": str(self)}


class InvalidClientId(CIMDError):
    """The client_id is not a valid Client Identifier URL, or is not allowed."""


class InvalidDocument(CIMDError):
    """The document was fetched but breaks the spec or this server's policy."""


class ClientIdMismatch(InvalidDocument):
    """The document's ``client_id`` is not exactly the URL it was fetched from."""

    revoke_tokens = True


class InvalidRedirectUri(CIMDError):
    """The redirect_uri in an authorization request is not registered.

    Never redirect to it: show the error to the user instead.
    """

    error = "invalid_request"


class FetchError(CIMDError):
    """The document could not be used: wrong status, too large, not JSON..."""


class BlockedAddress(FetchError):
    """The host resolves to a private or special-use address (SSRF guard)."""


class TransientFetchError(CIMDError):
    """Timeout, DNS or connection failure, or a 5xx/429 answer. Try again later."""

    transient = True
