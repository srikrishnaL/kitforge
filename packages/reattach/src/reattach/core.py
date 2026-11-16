"""Session tokens: the session id the client keeps, which carries what is needed to rebuild it.

A token looks like ``ra1.<payload>.<signature>``. The payload is base64url JSON with a random
nonce and the client's ``initialize`` params (protocol version, capabilities, client name), so
any process can replay the handshake for it, with no shared store. The signature is an HMAC
over the payload when a secret is set, and empty otherwise.

A session id is not a credential (the MCP spec says servers must not use it for auth), and the
128-bit nonce keeps tokens as hard to guess as the SDK's own uuid4 ids. The secret only stops
clients from handing the server made-up handshake params through a token.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from typing import Any, Dict, Optional

PREFIX = "ra1."
MAX_TOKEN = 4096  # header values this long are still accepted by every common server/proxy
_CLIENT_INFO_KEYS = ("name", "version", "title")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _key(secret: Optional[str]) -> Optional[bytes]:
    if secret is None:
        secret = os.environ.get("REATTACH_SECRET") or None
    return secret.encode("utf-8") if secret else None


def handshake_params(params: Any) -> Optional[Dict[str, Any]]:
    """The parts of ``initialize`` params worth replaying, or None if they are not usable."""
    if not isinstance(params, dict) or not isinstance(params.get("protocolVersion"), str):
        return None
    caps = params.get("capabilities")
    info = params.get("clientInfo")
    out: Dict[str, Any] = {
        "protocolVersion": params["protocolVersion"],
        "capabilities": caps if isinstance(caps, dict) else {},
        # icons and other display fields can be large data: URIs, and the server only needs
        # the name and version to tell clients apart
        "clientInfo": ({k: info[k] for k in _CLIENT_INFO_KEYS if isinstance(info.get(k), str)}
                       if isinstance(info, dict) else {"name": "unknown", "version": "0"}),
    }
    out["clientInfo"].setdefault("name", "unknown")
    out["clientInfo"].setdefault("version", "0")
    return out


class Tokens:
    """Make and read session tokens. ``secret=None`` reads ``REATTACH_SECRET`` from the env."""

    def __init__(self, secret: Optional[str] = None) -> None:
        self.key = _key(secret)

    def _sign(self, payload: str) -> str:
        if self.key is None:
            return ""
        return _b64(hmac.new(self.key, payload.encode("ascii"), hashlib.sha256).digest()[:18])

    def make(self, params: Dict[str, Any]) -> Optional[str]:
        """A new token for a session opened with ``params``, or None if it would be too long."""
        for drop_experimental in (False, True):
            p = params
            if drop_experimental:
                caps = {k: v for k, v in params.get("capabilities", {}).items()
                        if k != "experimental"}
                p = dict(params, capabilities=caps)
            body = {"n": _b64(os.urandom(16)), "p": p}
            payload = _b64(json.dumps(body, separators=(",", ":"), ensure_ascii=True,
                                      sort_keys=True).encode("ascii"))
            token = PREFIX + payload + "." + self._sign(payload)
            if len(token) <= MAX_TOKEN:
                return token
        return None

    def read(self, token: str) -> Optional[Dict[str, Any]]:
        """The handshake params in ``token``, or None if it is not a valid token of ours."""
        if not token.startswith(PREFIX) or len(token) > MAX_TOKEN:
            return None
        payload, _, sig = token[len(PREFIX):].partition(".")
        if not token.isascii():
            return None
        if self.key is not None and not hmac.compare_digest(sig, self._sign(payload)):
            return None
        try:
            body = json.loads(_unb64(payload))
        except ValueError:
            return None
        if not isinstance(body, dict) or not isinstance(body.get("n"), str):
            return None
        return handshake_params(body.get("p"))
