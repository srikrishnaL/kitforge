import pytest

from cimdkit import InvalidClientId, check_client_id_url, is_client_id_url, redirect_uri_matches
from cimdkit.urls import client_id_host, find_redirect_uri


@pytest.mark.parametrize("url", [
    "https://example.com/client.json",
    "https://example.com:8443/oauth/client",
    "https://[2001:db8::1]/c",
    "https://example.com/c?v=1",
    "https://example.com/a/.well-known/x",
])
def test_valid_client_ids(url):
    assert check_client_id_url(url) == url


@pytest.mark.parametrize("url,why", [
    ("http://example.com/client", "https"),
    ("HTTPS://example.com/client", "https"),
    ("https://example.com", "path"),
    ("https://example.com/", "path"),
    ("https://user:pw@example.com/c", "username"),
    ("https://user@example.com/c", "username"),
    ("https://example.com/a/../c", ". or .."),
    ("https://example.com/./c", ". or .."),
    ("https://example.com/a/%2E%2e/c", ". or .."),
    ("https://example.com/c#frag", "fragment"),
    ("https://example.com/c#", "fragment"),
    ("https://example.com:99999/c", "port"),
    ("https://example.com:abc/c", "port"),
    ("https:///c", "host"),
    ("https://exa mple.com/c", "spaces"),
    ("https://example.com/c\n", "control"),
    ("", "non-empty"),
    (None, "non-empty"),
    ("https://[::1/c", "malformed"),
])
def test_invalid_client_ids(url, why):
    with pytest.raises(InvalidClientId, match=why):
        check_client_id_url(url)


def test_query_can_be_refused():
    with pytest.raises(InvalidClientId, match="query"):
        check_client_id_url("https://example.com/c?x=1", allow_query=False)


def test_is_client_id_url_routes_registered_clients_away():
    assert is_client_id_url("https://example.com/c")
    assert not is_client_id_url("a3f9c2d1")
    assert not is_client_id_url(None)


def test_host_for_consent_screen():
    assert client_id_host("https://App.Example.com:8443/c") == "app.example.com"


@pytest.mark.parametrize("requested,registered,ok", [
    ("https://app.example.com/cb", "https://app.example.com/cb", True),
    ("https://app.example.com/cb/", "https://app.example.com/cb", False),
    ("https://app.example.com:443/cb", "https://app.example.com/cb", False),
    ("https://app.example.com:8443/cb", "https://app.example.com/cb", False),
    # Loopback: any port, for registered with or without a port.
    ("http://127.0.0.1:53123/callback", "http://127.0.0.1/callback", True),
    ("http://127.0.0.1:53123/callback", "http://127.0.0.1:8080/callback", True),
    ("http://[::1]:4000/cb", "http://[::1]/cb", True),
    ("http://localhost:33418/callback", "http://localhost/callback", True),
    # ...but host, path and query must still match.
    ("http://127.0.0.1:5000/other", "http://127.0.0.1/callback", False),
    ("http://127.0.0.1:5000/callback?x=1", "http://127.0.0.1/callback", False),
    ("http://localhost:5000/callback", "http://127.0.0.1/callback", False),
    ("http://127.0.0.2:5000/callback", "http://127.0.0.2/callback", False),
    ("http://evil.com:5000/callback", "http://evil.com/callback", False),
    ("https://127.0.0.1:5000/callback", "https://127.0.0.1/callback", False),
    ("http://user@127.0.0.1:5000/callback", "http://127.0.0.1/callback", False),
    ("http://127.0.0.1:0x50/callback", "http://127.0.0.1/callback", False),
    ("http://127.0.0.1:99999/callback", "http://127.0.0.1/callback", False),
])
def test_redirect_matching(requested, registered, ok):
    assert redirect_uri_matches(requested, registered) is ok


def test_localhost_exception_can_be_turned_off():
    assert not redirect_uri_matches("http://localhost:5/cb", "http://localhost/cb",
                                    allow_localhost=False)
    assert redirect_uri_matches("http://127.0.0.1:5/cb", "http://127.0.0.1/cb",
                                allow_localhost=False)


def test_find_redirect_uri():
    regs = ["https://a.example/cb", "http://127.0.0.1/cb"]
    assert find_redirect_uri("http://127.0.0.1:9/cb", regs) == "http://127.0.0.1/cb"
    assert find_redirect_uri("https://b.example/cb", regs) is None
