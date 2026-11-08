import json

import pytest

from cimdkit import Response

CID = "https://app.example.com/oauth/client.json"


def good_doc(**overrides):
    doc = {
        "client_id": CID,
        "client_name": "Example Agent",
        "redirect_uris": ["http://127.0.0.1/callback", "https://app.example.com/cb"],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }
    doc.update(overrides)
    return {k: v for k, v in doc.items() if v is not ...}


class FakeNet:
    """Stands in for DNS and HTTPS. Records every request."""

    def __init__(self, status=200, body=None, headers=None, addresses=("93.184.215.14",)):
        self.status = status
        self.body = json.dumps(good_doc()).encode() if body is None else body
        self.headers = {"content-type": "application/json"} if headers is None else headers
        self.addresses = addresses if isinstance(addresses, Exception) else list(addresses)
        self.requests = []
        self.raise_exc = None

    def resolver(self, host, port):
        if isinstance(self.addresses, Exception):
            raise self.addresses
        return self.addresses

    def transport(self, **kw):
        self.requests.append(kw)
        if self.raise_exc:
            raise self.raise_exc
        body = self.body if isinstance(self.body, bytes) else json.dumps(self.body).encode()
        return Response(self.status, dict(self.headers), body[: kw["max_bytes"] + 1])

    def opts(self):
        return {"resolver": self.resolver, "transport": self.transport}


@pytest.fixture
def net():
    return FakeNet()
