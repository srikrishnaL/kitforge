"""Confirmation tokens: signed, short-lived, single-use, bound to one exact call.

A token is ``<expiry>.<nonce>.<signature>``. The signature is an HMAC-SHA256 over
the action name, a digest of the call's arguments, an optional subject (the user
or session), the expiry and the nonce. Nothing secret is inside the token, and the
action and arguments are not stored in it: they are taken from the call that
presents it, so a token issued for ``delete("staging")`` does not work for
``delete("prod")``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
from typing import Any, Callable, Dict, Mapping, Optional

__all__ = ["TokenSigner", "MemoryNonceStore", "canonical_args"]

_VERSION = b"askfirst.v1"


def canonical_args(args: Optional[Mapping[str, Any]]) -> bytes:
    """A stable byte form of the call's arguments (key order does not matter)."""
    return json.dumps(
        dict(args or {}), sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=repr
    ).encode("utf-8")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


class MemoryNonceStore:
    """Remembers used tokens until they expire, in this process only.

    With several server processes behind a load balancer, pass a store shared
    between them (anything with this ``use`` method), or a token could be used
    once per process.
    """

    def __init__(self, clock: Callable[[], float] = time.time):
        self._used: Dict[str, float] = {}
        self._lock = threading.Lock()
        self._clock = clock

    def use(self, nonce: str, expires_at: float) -> bool:
        """Mark ``nonce`` used. False if it was already used."""
        with self._lock:
            now = self._clock()
            if len(self._used) > 1024:
                self._used = {n: e for n, e in self._used.items() if e > now}
            if nonce in self._used:
                return False
            self._used[nonce] = expires_at
            return True


class TokenSigner:
    """Issues and checks confirmation tokens.

    ``secret`` defaults to random bytes made at start-up, so tokens stop working
    when the server restarts. Pass a fixed secret (32+ random bytes) to keep them
    valid across restarts or between processes.
    """

    def __init__(
        self,
        secret: Optional[bytes] = None,
        *,
        ttl: int = 300,
        store: Optional[Any] = None,
        clock: Callable[[], float] = time.time,
    ):
        if secret is not None and isinstance(secret, str):
            secret = secret.encode("utf-8")
        if secret is not None and len(secret) < 16:
            raise ValueError("secret must be at least 16 bytes")
        if ttl <= 0:
            raise ValueError("ttl must be positive")
        self._secret = secret or secrets.token_bytes(32)
        self.ttl = int(ttl)
        self._clock = clock
        self._store = store if store is not None else MemoryNonceStore(clock)

    def _sign(self, action: str, args: Optional[Mapping[str, Any]], subject: str, exp: int, nonce: str) -> str:
        msg = b"\n".join(
            [
                _VERSION,
                action.encode("utf-8"),
                hashlib.sha256(canonical_args(args)).digest(),
                subject.encode("utf-8"),
                str(exp).encode("ascii"),
                nonce.encode("ascii"),
            ]
        )
        return _b64(hmac.new(self._secret, msg, hashlib.sha256).digest())

    def issue(self, action: str, args: Optional[Mapping[str, Any]] = None, subject: str = "") -> str:
        """A new token for exactly this action, these arguments and this subject."""
        exp = int(self._clock()) + self.ttl
        nonce = _b64(secrets.token_bytes(12))
        return f"{exp}.{nonce}.{self._sign(action, args, subject, exp, nonce)}"

    def check(
        self,
        token: str,
        action: str,
        args: Optional[Mapping[str, Any]] = None,
        subject: str = "",
    ) -> Optional[str]:
        """Check and use up ``token``. Returns None if it is valid, else the reason.

        Reasons: ``"malformed"``, ``"mismatch"`` (signed for another action, other
        arguments, another subject, or by another secret), ``"expired"``, ``"used"``.
        """
        if not isinstance(token, str) or len(token) > 200:
            return "malformed"
        parts = token.strip().split(".")
        if len(parts) != 3 or not parts[0].isdigit() or not parts[1] or not parts[2]:
            return "malformed"
        exp_s, nonce, sig = parts
        exp = int(exp_s)
        try:
            nonce.encode("ascii")
        except UnicodeEncodeError:
            return "malformed"
        if not hmac.compare_digest(self._sign(action, args, subject, exp, nonce), sig):
            return "mismatch"
        if self._clock() >= exp:
            return "expired"
        if not self._store.use(nonce, float(exp)):
            return "used"
        return None
