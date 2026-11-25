# reattach outreach drafts

Drafts for Sri to post, or not. Nothing here has been posted. All four issues were open on
2026-11-25 (checked with GitHub search); read the thread again before posting, and skip it if the cause turned out to be
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

## Added 2026-11-25 (need reattach 0.2.0, so post only after it is on PyPI)

These servers answer a lost session with something other than `404`, so no client can
recover. reattach 0.2.0 recognises those replies. Each maintainer will likely fix the server
itself; offer reattach only as a stopgap for people running it today.

### YosefHayim/ebay-mcp#228 (400 instead of 404 for an unknown session)

https://github.com/YosefHayim/ebay-mcp/issues/228

> Until the 404 change lands: anyone running the Docker image can put
> [reattach](https://github.com/srikrishnaL/kitforge/tree/main/packages/reattach) (MIT) in
> front of it. It's a small reverse proxy that recognises the `400 No valid session ID
> provided` / `invalid_session` replies as a lost session, replays `initialize` +
> `notifications/initialized` from the client's original handshake, and retries the request,
> so clients don't even need to handle the 404:
>
> ```bash
> pip install "reattach[proxy]"
> REATTACH_SECRET=... reattach proxy http://127.0.0.1:3000 --port 8080
> ```
>
> It forwards every path (so the OAuth metadata endpoints keep working) and the request's own
> `Authorization` header.

### gradusnikov/eclipse-chatgpt-plugin#189 (Session not found after restart or idle sweep)

https://github.com/gradusnikov/eclipse-chatgpt-plugin/issues/189

> For conditions 1, 2 and 4 (restart, idle sweep, client that never re-initializes), a
> workaround that needs no plugin change:
> [reattach](https://github.com/srikrishnaL/kitforge/tree/main/packages/reattach) (MIT) is a
> local reverse proxy. Point the client at it instead of `localhost:8124`; when the server
> answers `Session not found: <uuid>`, it replays the client's `initialize` on a new session
> and retries the call, so the client never notices.
>
> ```bash
> pip install "reattach[proxy]"
> reattach proxy http://localhost:8124 --port 8125
> # client URL: http://localhost:8125/mcp/eclipse-ide
> ```
>
> It doesn't help with condition 3 (the 3 s async timeout cutting long tool calls).
