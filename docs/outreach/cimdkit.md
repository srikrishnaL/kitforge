# cimdkit outreach drafts

Drafts for Sri to post, or not. Nothing here has been posted.

## modelcontextprotocol/python-sdk#1801 (server-side CIMD support)

https://github.com/modelcontextprotocol/python-sdk/issues/1801

> In case it helps while this is open: I put the server side of CIMD into a small
> dependency-free package, [cimdkit](https://github.com/srikrishnaL/kitforge/tree/main/packages/cimdkit)
> (`pip install cimdkit`). `ClientResolver.resolve(client_id)` fetches the document (https only,
> no redirects, 5 KB cap, SSRF guard on every resolved address with the connection pinned to
> the checked IP), validates it against the draft (exact `client_id` match, no secrets or
> shared-secret auth methods, `none` as the default method, grant/response type
> intersection) and caches it by its `Cache-Control` headers. `client.check_redirect_uri()`
> handles the loopback any-port rule. It could back a `ClientRegistrationProvider`-style hook
> here, or serve as a reference for the edge cases; happy to help if a PR would be welcome.

## authlib/authlib#900 (Support Client ID Metadata Document?)

https://github.com/authlib/authlib/issues/900

> For anyone who needs this before Authlib supports the draft: I wrote
> [cimdkit](https://github.com/srikrishnaL/kitforge/tree/main/packages/cimdkit), a small
> dependency-free library for the provider side. It resolves a URL `client_id` to validated
> metadata (safe fetch, exact-match and credential rules, auth-method selection, HTTP
> caching) and you plug the result into your own `query_client`. `pip install cimdkit`. It
> covers what MCP and ATProto clients send today; feedback on what Authlib would need from it
> is welcome.

## PrefectHQ/fastmcp#6153 (limit CIMD to trusted domains)

https://github.com/PrefectHQ/fastmcp/issues/6153

> Not a fix for OAuthProxy itself, but in case the matching rules are useful: cimdkit's
> `Policy(allowed_hosts=("claude.ai", "*.example.com"))` refuses other client_id hosts before
> any DNS lookup or fetch, with `*.` meaning subdomains only (so `eviltrusted.dev` and
> `trusted.dev.evil.com` don't match). Code and tests:
> https://github.com/srikrishnaL/kitforge/tree/main/packages/cimdkit
