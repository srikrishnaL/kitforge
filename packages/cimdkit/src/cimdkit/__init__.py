"""cimdkit: OAuth Client ID Metadata Documents (CIMD) for authorization servers.

MCP clients identify themselves with a URL ``client_id`` instead of
registering. cimdkit fetches that URL safely, validates the document against
draft-ietf-oauth-client-id-metadata-document, caches it by its HTTP headers and
matches redirect URIs, including the loopback any-port rule.
"""

from .document import ClientMetadata, Policy, make_document, validate_document
from .errors import (
    BlockedAddress,
    CIMDError,
    ClientIdMismatch,
    FetchError,
    InvalidClientId,
    InvalidDocument,
    InvalidRedirectUri,
    TransientFetchError,
)
from .fetch import DEFAULT_MAX_BYTES, FetchResult, Response, fetch_document, is_public_address
from .resolver import ClientResolver, cache_ttl
from .urls import check_client_id_url, is_client_id_url, redirect_uri_matches

__version__ = "0.1.0"

#: Add these to your RFC 8414 authorization server metadata.
SERVER_METADATA = {"client_id_metadata_document_supported": True}

__all__ = [
    "BlockedAddress",
    "CIMDError",
    "ClientIdMismatch",
    "ClientMetadata",
    "ClientResolver",
    "DEFAULT_MAX_BYTES",
    "FetchError",
    "FetchResult",
    "InvalidClientId",
    "InvalidDocument",
    "InvalidRedirectUri",
    "Policy",
    "Response",
    "SERVER_METADATA",
    "TransientFetchError",
    "cache_ttl",
    "check_client_id_url",
    "fetch_document",
    "is_client_id_url",
    "is_public_address",
    "make_document",
    "redirect_uri_matches",
    "validate_document",
]
