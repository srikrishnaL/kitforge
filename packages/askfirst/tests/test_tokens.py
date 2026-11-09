import pytest

from askfirst import MemoryNonceStore, TokenSigner
from askfirst.tokens import canonical_args


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def make(ttl=60, secret=b"k" * 32):
    clock = Clock()
    return TokenSigner(secret, ttl=ttl, clock=clock), clock


def test_valid_token_works_once():
    s, _ = make()
    tok = s.issue("delete", {"name": "x"})
    assert s.check(tok, "delete", {"name": "x"}) is None
    assert s.check(tok, "delete", {"name": "x"}) == "used"


def test_bound_to_action_args_and_subject():
    s, _ = make()
    tok = s.issue("delete", {"name": "staging"}, subject="alice")
    assert s.check(tok, "delete", {"name": "prod"}, subject="alice") == "mismatch"
    assert s.check(tok, "drop", {"name": "staging"}, subject="alice") == "mismatch"
    assert s.check(tok, "delete", {"name": "staging"}, subject="bob") == "mismatch"
    assert s.check(tok, "delete", {"name": "staging"}) == "mismatch"
    # A mismatch does not use the token up.
    assert s.check(tok, "delete", {"name": "staging"}, subject="alice") is None


def test_argument_order_and_types():
    assert canonical_args({"a": 1, "b": [1, 2]}) == canonical_args({"b": [1, 2], "a": 1})
    assert canonical_args({"a": 1}) != canonical_args({"a": "1"})
    assert canonical_args(None) == canonical_args({})
    s, _ = make()
    tok = s.issue("t", {"b": 2, "a": 1})
    assert s.check(tok, "t", {"a": 1, "b": 2}) is None


def test_expiry():
    s, clock = make(ttl=60)
    tok = s.issue("t")
    clock.t += 59
    assert s.check(s.issue("t"), "t") is None
    clock.t += 1
    assert s.check(tok, "t") == "expired"


def test_tampered_expiry_is_a_mismatch():
    s, clock = make(ttl=60)
    exp, nonce, sig = s.issue("t").split(".")
    forged = f"{int(exp) + 3600}.{nonce}.{sig}"
    clock.t += 120
    assert s.check(forged, "t") == "mismatch"


def test_other_secret_is_a_mismatch():
    a, _ = make(secret=b"a" * 32)
    b, _ = make(secret=b"b" * 32)
    assert b.check(a.issue("t"), "t") == "mismatch"


def test_random_secret_differs_per_signer():
    a, b = TokenSigner(), TokenSigner()
    assert b.check(a.issue("t"), "t") == "mismatch"


@pytest.mark.parametrize("bad", ["", "x", "1.2", "a.b.c", "1..c", "1.b.", "1.é.c", "9" * 300, None, 5])
def test_malformed(bad):
    s, _ = make()
    assert s.check(bad, "t") == "malformed"


def test_whitespace_around_token_is_ignored():
    s, _ = make()
    assert s.check("  " + s.issue("t") + "\n", "t") is None


def test_bad_settings():
    with pytest.raises(ValueError):
        TokenSigner(b"short")
    with pytest.raises(ValueError):
        TokenSigner(ttl=0)
    assert TokenSigner("a-string-secret-of-16+").check is not None


def test_store_prunes_expired_entries():
    clock = Clock()
    store = MemoryNonceStore(clock)
    for i in range(1100):
        assert store.use(f"n{i}", clock.t + 10)
    clock.t += 11
    assert store.use("fresh", clock.t + 10)
    assert len(store._used) == 1
    assert not store.use("fresh", clock.t + 10)


def test_shared_store_blocks_reuse_across_signers():
    store = MemoryNonceStore()
    a = TokenSigner(b"s" * 32, store=store)
    b = TokenSigner(b"s" * 32, store=store)
    tok = a.issue("t")
    assert a.check(tok, "t") is None
    assert b.check(tok, "t") == "used"
