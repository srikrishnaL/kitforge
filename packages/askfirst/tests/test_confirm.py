import asyncio
from types import SimpleNamespace

import pytest

from askfirst import (
    ConfirmationRequired,
    ConfirmationUnavailable,
    Confirmer,
    Declined,
    client_supports_elicitation,
    confirm,
)


def run(coro):
    return asyncio.run(coro)


class Ctx:
    """A fake MCP context. ``answer`` is what elicit returns, or an exception to raise."""

    def __init__(self, answer=None, elicitation=True, style="v2"):
        caps = SimpleNamespace(elicitation=object() if elicitation else None)
        if style == "v2":
            self.client_capabilities = caps
        else:
            self.session = SimpleNamespace(client_params=SimpleNamespace(capabilities=caps))
        self.answer = answer
        self.prompts = []

    async def elicit(self, message, schema):
        self.prompts.append((message, schema))
        if isinstance(self.answer, BaseException):
            raise self.answer
        if self.answer == "hang":
            await asyncio.sleep(10)
        return self.answer


def accept(value=True):
    return SimpleNamespace(action="accept", data=SimpleNamespace(confirm=value))


ASK = dict(action="delete", args={"name": "prod"})


@pytest.mark.parametrize("style", ["v1", "v2"])
def test_elicitation_yes(style):
    ctx = Ctx(accept(), style=style)
    assert run(Confirmer()(ctx, "Delete prod?", **ASK)) == "elicitation"
    message, schema = ctx.prompts[0]
    assert message == "Delete prod?"
    assert list(schema.model_fields) == ["confirm"]


@pytest.mark.parametrize(
    "answer, action",
    [
        (SimpleNamespace(action="decline"), "decline"),
        (SimpleNamespace(action="cancel"), "cancel"),
        (accept(False), "decline"),
        (SimpleNamespace(action="accept", data=None), "decline"),
        (SimpleNamespace(action="accept", data=SimpleNamespace(confirm="yes")), "decline"),
        (SimpleNamespace(action="weird"), "decline"),
    ],
)
def test_elicitation_no(answer, action):
    with pytest.raises(Declined) as err:
        run(Confirmer()(Ctx(answer), "Delete prod?", **ASK))
    assert err.value.action == action
    assert "Nothing was done" in str(err.value)


def test_elicitation_ignores_tokens():
    # On a client that can prompt, a token never replaces the prompt.
    c = Confirmer()
    tok = _token_from(c, None)
    with pytest.raises(Declined):
        run(c(Ctx(SimpleNamespace(action="decline")), "Delete prod?", token=tok, **ASK))


def test_timeout_counts_as_no():
    pytest.importorskip("anyio")
    with pytest.raises(Declined) as err:
        run(Confirmer(timeout=0.05)(Ctx("hang"), "Delete prod?", **ASK))
    assert err.value.action == "timeout"


def _token_from(c, ctx, **kw):
    with pytest.raises(ConfirmationRequired) as err:
        run(c(ctx, "Delete prod?", **{**ASK, **kw}))
    return err.value.token


@pytest.mark.parametrize("ctx", [None, Ctx(elicitation=False), Ctx(elicitation=False, style="v1"), object()])
def test_token_flow(ctx):
    c = Confirmer(ttl=120)
    with pytest.raises(ConfirmationRequired) as err:
        run(c(ctx, "Delete prod?", **ASK))
    e = err.value
    assert e.reason is None and e.expires_in == 120
    text = str(e)
    assert "This call was not run" in text and "Delete prod?" in text
    assert f'confirm_token="{e.token}"' in text and "call delete again" in text
    assert run(c(ctx, "Delete prod?", token=e.token, **ASK)) == "token"


def test_elicitation_failure_falls_back_to_token():
    # e.g. NoBackChannelError on stateless / 2026-07-28 connections
    ctx = Ctx(RuntimeError("no back-channel"))
    c = Confirmer()
    tok = _token_from(c, ctx)
    assert len(ctx.prompts) == 1
    assert run(c(ctx, "Delete prod?", token=tok, **ASK)) == "token"


@pytest.mark.parametrize("reason", ["used", "mismatch", "malformed", "expired"])
def test_bad_token_gets_a_fresh_one(reason):
    now = [1000.0]
    c = Confirmer(clock=lambda: now[0], ttl=60)
    tok = _token_from(c, None)
    if reason == "used":
        run(c(None, "x", token=tok, **ASK))
        kw = {}
    elif reason == "mismatch":
        kw = {"args": {"name": "staging"}}
    elif reason == "malformed":
        tok, kw = "nope", {}
    else:
        now[0] += 61
        kw = {}
    with pytest.raises(ConfirmationRequired) as err:
        run(c(None, "Delete prod?", token=tok, **{**ASK, **kw}))
    assert err.value.reason == reason
    assert err.value.token != tok


def test_token_bound_to_subject():
    c = Confirmer()
    tok = _token_from(c, None, subject="alice")
    with pytest.raises(ConfirmationRequired):
        run(c(None, "x", token=tok, subject="bob", **ASK))


def test_mode_token_never_prompts():
    ctx = Ctx(accept())
    c = Confirmer(mode="token")
    tok = _token_from(c, ctx)
    assert ctx.prompts == []
    assert run(c(ctx, "x", token=tok, **ASK)) == "token"


def test_mode_elicit_refuses_without_prompt():
    c = Confirmer(mode="elicit")
    with pytest.raises(ConfirmationUnavailable):
        run(c(Ctx(elicitation=False), "Delete prod?", **ASK))
    with pytest.raises(ConfirmationUnavailable):
        run(c(Ctx(RuntimeError("boom")), "Delete prod?", **ASK))
    assert run(c(Ctx(accept()), "Delete prod?", **ASK)) == "elicitation"


def test_custom_token_param_name():
    c = Confirmer(token_param="really")
    with pytest.raises(ConfirmationRequired) as err:
        run(c(None, "x", **ASK))
    assert 'really="' in str(err.value)


def test_bad_arguments():
    with pytest.raises(ValueError):
        Confirmer(mode="sometimes")
    with pytest.raises(ValueError):
        run(Confirmer()(None, "x", action=""))


def test_module_level_confirm():
    with pytest.raises(ConfirmationRequired) as err:
        run(confirm(None, "x", **ASK))
    assert run(confirm(None, "x", token=err.value.token, **ASK)) == "token"


def test_capability_detection_edge_cases():
    assert not client_supports_elicitation(None)
    assert not client_supports_elicitation(SimpleNamespace(session=SimpleNamespace(client_params=None)))

    class Raises:
        @property
        def client_capabilities(self):
            raise RuntimeError("outside a request")

    assert not client_supports_elicitation(Raises())
