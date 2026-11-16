# reattach outreach drafts

Drafts for Sri to post, or not. Nothing here has been posted. Both issues were open on
2026-11-16; read the thread again before posting, and skip it if the cause turned out to be
something else (reattach only helps when the server forgot the session: restart, redeploy,
idle timeout, or a request reaching another replica).

## zerodha/kite-mcp-server#83 (Session not found after login on the hosted server)

https://github.com/zerodha/kite-mcp-server/issues/83

> If the hosted server runs more than one instance, or restarts between the login and the
> next call, the session id the client holds no longer exists where the request lands, and
> most clients never re-initialize after the 404. One way to rule that out without changing
> the Go code: [reattach](https://github.com/srikrishnaL/kitforge/tree/main/packages/reattach)
> (MIT) is a small reverse proxy that gives clients a session id carrying their handshake and
> replays `initialize` on whichever instance gets the request, so the client never sees the
> 404:
>
> ```bash
> pip install "reattach[proxy]"
> REATTACH_SECRET=... reattach proxy http://127.0.0.1:8080 --port 9090
> ```
>
> It forwards every path (OAuth discovery included) and the request's own `Authorization`.
> Happy to help if you try it.

## zai-org/feedback#1374 (client never re-initializes an expired session after a server restart)

https://github.com/zai-org/feedback/issues/1374

> Until the client handles the 404, server owners can hide it on their side. I wrote
> [reattach](https://github.com/srikrishnaL/kitforge/tree/main/packages/reattach) (MIT, no
> dependencies) for exactly this: ASGI middleware (or a reverse proxy for non-Python
> servers) that replays `initialize` + `notifications/initialized` when the server no longer
> knows a session, then retries the request once.
>
> ```python
> from reattach import Reattach
> app = Reattach(mcp.streamable_http_app())
> ```
>
> `pip install reattach`. Tested with the MCP Python SDK client against a server that is
> killed and restarted mid-session: the next tool call just works.
