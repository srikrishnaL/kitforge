import pytest

from cimdkit import (
    ClientIdMismatch,
    ClientResolver,
    FetchError,
    FetchResult,
    TransientFetchError,
    cache_ttl,
)

from conftest import CID, good_doc

NOW = 1_800_000_000.0


class Clock:
    def __init__(self):
        self.t = NOW

    def __call__(self):
        return self.t


class Origin:
    """A fake client host whose answers can be changed between fetches."""

    def __init__(self):
        self.doc = good_doc()
        self.headers = {"cache-control": "max-age=600"}
        self.fail = None
        self.calls = 0

    def __call__(self, client_id):
        self.calls += 1
        if self.fail:
            raise self.fail
        return FetchResult(dict(self.doc), dict(self.headers))


@pytest.mark.parametrize("headers,expected", [
    ({}, 300),
    ({"cache-control": "max-age=600"}, 600),
    ({"Cache-Control": "public, max-age=600"}, 600),
    ({"cache-control": "max-age=600, s-maxage=60"}, 60),
    ({"cache-control": "max-age=600", "age": "100"}, 500),
    ({"cache-control": "max-age=60", "age": "100"}, 0),
    ({"cache-control": "max-age=999999999"}, 86400),
    ({"cache-control": "max-age=0"}, 0),
    ({"cache-control": 'max-age="120"'}, 120),
    ({"cache-control": "max-age=abc"}, 0),
    ({"cache-control": "no-store"}, 0),
    ({"cache-control": "no-cache, max-age=600"}, 0),
    ({"cache-control": "private, max-age=600"}, 0),
    ({"expires": "Fri, 15 Jan 2027 08:00:00 GMT", "date": "Fri, 15 Jan 2027 07:50:00 GMT"}, 600),
    ({"expires": "0"}, 0),
    ({"expires": "Fri, 15 Jan 2027 08:00:00 GMT", "cache-control": "max-age=5"}, 5),
])
def test_cache_ttl(headers, expected):
    assert cache_ttl(headers, now=NOW) == expected


def test_cache_ttl_floor_for_rate_limiting():
    assert cache_ttl({"cache-control": "no-store"}, now=NOW, min_ttl=30) == 30


def test_valid_document_is_cached_until_it_expires():
    origin, clock = Origin(), Clock()
    r = ClientResolver(fetch=origin, clock=clock)
    assert r.resolve(CID).client_name == "Example Agent"
    clock.t += 599
    r.resolve(CID)
    assert origin.calls == 1
    clock.t += 2
    origin.doc = good_doc(client_name="Renamed")
    assert r.resolve(CID).client_name == "Renamed"
    assert origin.calls == 2


def test_no_store_is_fetched_every_time():
    origin = Origin()
    origin.headers = {"cache-control": "no-store"}
    r = ClientResolver(fetch=origin, clock=Clock())
    r.resolve(CID)
    r.resolve(CID)
    assert origin.calls == 2
    assert r.cached(CID) is None


def test_transient_failure_keeps_the_last_good_document():
    origin, clock = Origin(), Clock()
    r = ClientResolver(fetch=origin, clock=clock, retry_after=30)
    r.resolve(CID)
    clock.t += 700
    origin.fail = TransientFetchError("503")
    assert r.resolve(CID).client_name == "Example Agent"
    clock.t += 10
    r.resolve(CID)  # inside retry_after: no new fetch
    assert origin.calls == 2
    clock.t += 30
    r.resolve(CID)
    assert origin.calls == 3


def test_transient_failure_without_cache_raises():
    origin = Origin()
    origin.fail = TransientFetchError("timeout")
    with pytest.raises(TransientFetchError):
        ClientResolver(fetch=origin, clock=Clock()).resolve(CID)


def test_terminal_failure_drops_the_cache():
    origin, clock = Origin(), Clock()
    r = ClientResolver(fetch=origin, clock=clock)
    r.resolve(CID)
    clock.t += 700
    origin.fail = FetchError("404")
    with pytest.raises(FetchError):
        r.resolve(CID)
    assert r.cached(CID) is None


def test_mismatch_says_to_revoke_tokens_and_is_not_cached():
    origin, clock = Origin(), Clock()
    r = ClientResolver(fetch=origin, clock=clock)
    r.resolve(CID)
    clock.t += 700
    origin.doc = good_doc(client_id="https://attacker.example/c")
    with pytest.raises(ClientIdMismatch) as err:
        r.resolve(CID)
    assert err.value.revoke_tokens
    assert r.cached(CID) is None
    origin.doc = good_doc()
    r.resolve(CID)
    assert origin.calls == 3


def test_invalid_document_is_never_cached():
    origin = Origin()
    origin.doc = good_doc(client_secret="x")
    r = ClientResolver(fetch=origin, clock=Clock())
    for _ in range(2):
        with pytest.raises(Exception):
            r.resolve(CID)
    assert origin.calls == 2


def test_force_and_invalidate():
    origin = Origin()
    r = ClientResolver(fetch=origin, clock=Clock())
    r.resolve(CID)
    r.resolve(CID, force=True)
    r.invalidate(CID)
    r.resolve(CID)
    assert origin.calls == 3


def test_fetch_options_reach_fetch_document(net):
    r = ClientResolver(clock=Clock(), max_bytes=4096, **net.opts())
    assert r.resolve(CID).client_id == CID
    assert net.requests[0]["max_bytes"] == 4096


def test_disallowed_host_is_refused_before_fetching():
    from cimdkit import InvalidClientId, Policy
    origin = Origin()
    r = ClientResolver(Policy(allowed_hosts=("*.trusted.dev",)), fetch=origin, clock=Clock())
    with pytest.raises(InvalidClientId):
        r.resolve(CID)
    with pytest.raises(InvalidClientId):
        r.resolve("https://app.example.com:99999/c")  # malformed port: an error, not a crash
    assert origin.calls == 0
