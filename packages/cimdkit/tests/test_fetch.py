import socket

import pytest

from cimdkit import (
    BlockedAddress,
    FetchError,
    InvalidClientId,
    TransientFetchError,
    fetch_document,
    is_public_address,
)

from conftest import CID, FakeNet


@pytest.mark.parametrize("addr", [
    "127.0.0.1", "10.1.2.3", "172.16.0.1", "192.168.1.1", "169.254.169.254",  # cloud metadata
    "100.64.0.1", "0.0.0.0", "224.0.0.1", "255.255.255.255", "192.0.2.1", "198.18.0.1",
    "::1", "::", "fe80::1", "fc00::1", "ff02::1",
    "::ffff:127.0.0.1", "::ffff:169.254.169.254",  # IPv4-mapped
    "64:ff9b::a9fe:a9fe",  # NAT64 of 169.254.169.254
    "2002:7f00:1::",  # 6to4 of 127.0.0.1
    "2001:0:4136:e378:8000:63bf:3fff:fdd2",  # Teredo
    "not-an-ip",
])
def test_special_use_addresses_are_not_public(addr):
    assert not is_public_address(addr)


@pytest.mark.parametrize("addr", ["93.184.215.14", "8.8.8.8", "2606:4700:4700::1111"])
def test_public_addresses(addr):
    assert is_public_address(addr)


def test_fetch_sends_spec_headers_and_pins_the_checked_address(net):
    result = fetch_document(CID, **net.opts())
    assert result.document["client_id"] == CID
    req = net.requests[0]
    assert req["host"] == "app.example.com" and req["port"] == 443
    assert req["address"] == "93.184.215.14"
    assert req["target"] == "/oauth/client.json"
    assert req["headers"]["Accept"] == "application/json"
    assert req["max_bytes"] == 5120


def test_port_and_query_are_kept():
    net = FakeNet()
    fetch_document("https://app.example.com:8443/c?v=1", **net.opts())
    req = net.requests[0]
    assert req["port"] == 8443 and req["target"] == "/c?v=1"
    assert req["headers"]["Host"] == "app.example.com:8443"


def test_invalid_client_id_is_never_fetched(net):
    with pytest.raises(InvalidClientId):
        fetch_document("http://app.example.com/c", **net.opts())
    assert net.requests == []


def test_private_address_is_blocked_before_connecting():
    net = FakeNet(addresses=["169.254.169.254"])
    with pytest.raises(BlockedAddress, match="169.254.169.254"):
        fetch_document(CID, **net.opts())
    assert net.requests == []


def test_any_private_address_in_the_answer_blocks():
    # A DNS answer mixing public and private addresses is a rebinding trick.
    net = FakeNet(addresses=["93.184.215.14", "10.0.0.5"])
    with pytest.raises(BlockedAddress):
        fetch_document(CID, **net.opts())


def test_ip_literal_hosts_are_checked_without_dns():
    net = FakeNet(addresses=OSError("must not resolve"))
    with pytest.raises(BlockedAddress):
        fetch_document("https://127.0.0.1/c", **net.opts())
    with pytest.raises(BlockedAddress):
        fetch_document("https://[::1]:8443/c", **net.opts())


def test_loopback_allowed_only_when_asked():
    net = FakeNet(addresses=["127.0.0.1"])
    net.body = {"client_id": "https://localhost:8443/c"}
    with pytest.raises(BlockedAddress):
        fetch_document("https://localhost:8443/c", **net.opts())
    result = fetch_document("https://localhost:8443/c", allow_loopback=True, **net.opts())
    assert result.address == "127.0.0.1"
    net.addresses = ["10.0.0.1"]
    with pytest.raises(BlockedAddress):  # loopback only, not private ranges
        fetch_document("https://localhost:8443/c", allow_loopback=True, **net.opts())


def test_dns_failure_is_transient():
    net = FakeNet(addresses=socket.gaierror("Name or service not known"))
    with pytest.raises(TransientFetchError) as err:
        fetch_document(CID, **net.opts())
    assert err.value.status_code == 503
    net.addresses = []
    with pytest.raises(TransientFetchError, match="no addresses"):
        fetch_document(CID, **net.opts())


@pytest.mark.parametrize("status", [301, 302, 307, 308])
def test_redirects_are_not_followed(status):
    net = FakeNet(status=status, headers={"location": "https://elsewhere.example/c"})
    with pytest.raises(FetchError, match="redirects are not followed"):
        fetch_document(CID, **net.opts())
    assert len(net.requests) == 1


@pytest.mark.parametrize("status,transient", [
    (404, False), (410, False), (401, False), (204, False),
    (500, True), (502, True), (503, True), (429, True), (408, True),
])
def test_non_200_statuses(status, transient):
    net = FakeNet(status=status)
    with pytest.raises((FetchError, TransientFetchError)) as err:
        fetch_document(CID, **net.opts())
    assert err.value.transient is transient


@pytest.mark.parametrize("exc", [ConnectionRefusedError(), socket.timeout("timed out"), OSError()])
def test_network_errors_are_transient(exc):
    net = FakeNet()
    net.raise_exc = exc
    with pytest.raises(TransientFetchError):
        fetch_document(CID, **net.opts())


def test_size_limit():
    net = FakeNet(body=b'{"client_id": "' + b"x" * 6000 + b'"}')
    with pytest.raises(FetchError, match="larger than 5120"):
        fetch_document(CID, **net.opts())
    fetch_document(CID, max_bytes=10000, **FakeNet().opts())


@pytest.mark.parametrize("ctype,ok", [
    ("application/json", True),
    ("application/json; charset=utf-8", True),
    ("application/oauth-client+json", True),
    ("text/html", False),
    ("text/plain", False),
    ("", False),
])
def test_content_type(ctype, ok):
    net = FakeNet(headers={"content-type": ctype} if ctype else {})
    if ok:
        fetch_document(CID, **net.opts())
    else:
        with pytest.raises(FetchError, match="application/json"):
            fetch_document(CID, **net.opts())
        fetch_document(CID, require_json_content_type=False, **net.opts())


@pytest.mark.parametrize("body,why", [
    (b"<html>login</html>", "not valid JSON"),
    (b"\xff\xfe{}", "not valid JSON"),
    (b'\xef\xbb\xbf{"client_id": "x"}', "not valid JSON"),
    (b"[1, 2]", "JSON object"),
    (b'"https://app.example.com/oauth/client.json"', "JSON object"),
    (b'{"client_id": "a", "client_id": "b"}', "repeats the property 'client_id'"),
])
def test_bad_bodies(body, why):
    with pytest.raises(FetchError, match=why):
        fetch_document(CID, **FakeNet(body=body).opts())
