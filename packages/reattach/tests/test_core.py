"""Session tokens."""

import json

from reattach.core import MAX_TOKEN, Tokens, _unb64, handshake_params

PARAMS = {"protocolVersion": "2025-06-18", "capabilities": {"roots": {"listChanged": True}},
          "clientInfo": {"name": "c", "version": "1", "title": "C",
                         "icons": [{"src": "data:image/png;base64," + "A" * 9000}]}}


def test_round_trip_keeps_only_what_the_handshake_needs():
    t = Tokens("k")
    tok = t.make(handshake_params(PARAMS))
    assert tok.startswith("ra1.") and len(tok) < 600
    assert t.read(tok) == {"protocolVersion": "2025-06-18",
                           "capabilities": {"roots": {"listChanged": True}},
                           "clientInfo": {"name": "c", "version": "1", "title": "C"}}


def test_tokens_are_unique_and_header_safe():
    t = Tokens("k")
    a, b = t.make(handshake_params(PARAMS)), t.make(handshake_params(PARAMS))
    assert a != b
    assert all(0x21 <= ord(ch) <= 0x7E for ch in a)


def test_bad_tokens_are_rejected():
    t = Tokens("k")
    tok = t.make(handshake_params(PARAMS))
    assert t.read(tok[:-2] + "xx") is None
    assert t.read("ra1.!!!.") is None
    assert t.read("ra1.") is None
    assert t.read("abc") is None
    assert t.read(tok + "é") is None
    assert t.read("ra1." + "A" * MAX_TOKEN) is None
    assert Tokens("other").read(tok) is None
    # a payload that is valid base64 but not our JSON shape
    assert Tokens(None).read("ra1.WzFd.") is None  # [1]


def test_unsigned_tokens_are_read_by_unsigned_instances(monkeypatch):
    monkeypatch.delenv("REATTACH_SECRET", raising=False)
    tok = Tokens().make(handshake_params(PARAMS))
    assert tok.endswith(".") and Tokens().read(tok) is not None


def test_huge_capabilities_drop_experimental_then_give_up():
    params = handshake_params(dict(PARAMS, capabilities={
        "sampling": {}, "experimental": {"x": "y" * 5000}}))
    tok = Tokens("k").make(params)
    assert Tokens("k").read(tok)["capabilities"] == {"sampling": {}}
    params = handshake_params(dict(PARAMS, capabilities={"sampling": {"x": "y" * 5000}}))
    assert Tokens("k").make(params) is None


def test_handshake_params_validation():
    assert handshake_params(None) is None
    assert handshake_params({"capabilities": {}}) is None
    p = handshake_params({"protocolVersion": "2025-03-26", "capabilities": [], "clientInfo": 5})
    assert p == {"protocolVersion": "2025-03-26", "capabilities": {},
                 "clientInfo": {"name": "unknown", "version": "0"}}


def test_payload_is_plain_json():
    tok = Tokens("k").make(handshake_params(PARAMS))
    body = json.loads(_unb64(tok.split(".")[1]))
    assert set(body) == {"n", "p"} and len(_unb64(body["n"])) == 16
