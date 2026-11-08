# cimdkit

OAuth **Client ID Metadata Documents** (CIMD) for Python authorization servers.

MCP clients such as Claude Code and VS Code no longer register with your OAuth server: they
send a URL as their `client_id`, and your server has to fetch that URL, validate the JSON it
finds there and remember it. Doing that safely is fiddly (SSRF, redirects, exact-match rules,
caching, the loopback port exception), and every server is writing it again from scratch.
cimdkit does it in one call, with no dependencies.

```bash
pip install cimdkit
```

Python 3.9+. Works with any web framework and any OAuth library (Authlib, FastMCP's OAuth
proxy, your own).

## Use it in your `/authorize` endpoint

```python
from cimdkit import ClientResolver, CIMDError, Policy, SERVER_METADATA, is_client_id_url

resolver = ClientResolver(Policy(
    forbidden_client_ids=("https://auth.example.com",),  # your own issuer
    auth_methods=("none", "private_key_jwt"),
))

def authorize(params):
    client_id = params["client_id"]
    if not is_client_id_url(client_id):
        return handle_preregistered_client(params)       # your existing clients
    try:
        client = resolver.resolve(client_id)             # fetch + validate + cache
        redirect_uri = client.check_redirect_uri(params.get("redirect_uri"))
    except CIMDError as err:
        # Never redirect on these errors: show them to the user.
        return render_error(err.to_oauth_error(), status=err.status_code)
    return render_consent(name=client.display_name, host=client.host, logo=client.logo_uri,
                          redirect_uri=redirect_uri)

# And advertise support in /.well-known/oauth-authorization-server:
metadata.update(SERVER_METADATA)   # {"client_id_metadata_document_supported": True}
```

At the token endpoint, call `resolver.resolve(client_id)` again (it is served from the cache)
and use `client.token_endpoint_auth_method`: `"none"` means a public PKCE client, so reject any
client authentication it sends; `"private_key_jwt"` means verify its assertion against
`client.jwks` or `client.jwks_uri`. On a `TransientFetchError` answer 503; on any other
`CIMDError` answer `invalid_client`, and if `err.revoke_tokens` is true, revoke the client's
refresh tokens (the document now names a different client).

## What it checks

**The URL** (`check_client_id_url`): https, a path other than `/`, no userinfo, no `.`/`..`
segments (including `%2e`), no fragment, a valid port. Compared as an exact string, never
normalized.

**The fetch** (`fetch_document`):

- Every address the host resolves to must be public. Loopback, private, link-local (including
  the cloud metadata address `169.254.169.254`), CGNAT, multicast and reserved ranges are
  refused, also when hidden as IPv4-mapped IPv6, NAT64 or 6to4.
- The connection goes to the exact address that was checked, with SNI and certificate checks
  for the real host name, so DNS rebinding cannot swap the address between check and connect.
- Redirects are not followed. Only `200` is accepted; `5xx`, `408` and `429` are reported as
  transient.
- `Accept: application/json`, at most 5 KB read, one overall deadline, `application/json` (or
  `application/*+json`) content type, and a JSON object with no duplicate keys.

**The document** (`validate_document`):

- `client_id` must exactly equal the URL it was fetched from.
- No `client_secret`, no shared-secret auth methods (`client_secret_basic`, `_post`, `_jwt`),
  no private key material in `jwks`, not both `jwks` and `jwks_uri`.
- With no auth method declared, the method is `none` (not RFC 7591's `client_secret_basic`).
  With `token_endpoint_auth_methods_supported`, the client's first method that you support is
  chosen.
- `grant_types` and `response_types` are intersected with what you support; an empty result
  is an error.
- `redirect_uris`: absolute, no fragment, no `javascript:`/`data:`/`file:`, plain `http` only
  on loopback. Private-use schemes for native apps (`com.example.app:/cb`) are allowed.
- Unknown properties are ignored, not rejected. Informative URLs (`logo_uri`, `client_uri`...)
  that are not https are dropped with a warning instead of failing the whole sign-in.

**Redirect URIs** (`client.check_redirect_uri`): exact match, except that
`http://127.0.0.1`, `http://[::1]` and `http://localhost` redirects may use any port, as RFC
8252 and RFC 9700 require for native apps and CLIs that listen on a random port.

**Caching** (`ClientResolver`): valid documents are kept for as long as `Cache-Control`
(`s-maxage`, `max-age`, `Age`, `no-store`...) or `Expires` allow, clamped to
`[min_ttl, max_ttl]` (default 0 s to 24 h, 5 minutes when the response says nothing). Errors
and invalid documents are never cached. A transient failure keeps serving the last good
document; a terminal failure drops it.

**Your policy** (`Policy`): supported grant types, response types and auth methods, ids the
client_id must never equal (your issuer, trusted resource servers), `allowed_hosts`
(`"*.example.com"`) to accept only trusted client domains, same-origin redirects, and whether
`localhost` counts as loopback.

## Check your own client's document

If you publish a CIMD for your MCP client, check it the way servers will:

```console
$ cimdkit check https://app.example.com/oauth/client.json --redirect-uri http://127.0.0.1:53682/callback
OK  https://app.example.com/oauth/client.json
  name           Example Agent  (host app.example.com)
  auth method    none
  grant types    authorization_code, refresh_token
  redirect_uri   http://127.0.0.1/callback
  cache          3600 s
```

`--file client.json` checks a local file before you deploy it, and `--json` prints the result
for CI. Or build the document in Python and get an error if any server would reject it:

```python
from cimdkit import make_document

doc = make_document("https://app.example.com/oauth/client.json",
                    client_name="Example Agent",
                    redirect_uris=["http://127.0.0.1/callback"],
                    logo_uri="https://app.example.com/logo.png")
```

## For development

`ClientResolver(allow_loopback=True)` (or `cimdkit check --allow-loopback`) lets a server on
your machine fetch documents from `127.0.0.1`/`::1`. The spec forbids this in production.

## Limits

- It resolves and validates clients. It does not issue codes or tokens, verify
  `private_key_jwt` assertions, or fetch `jwks_uri` and logos for you.
- The fetch connects directly and ignores `HTTPS_PROXY`. Pass your own `transport=` to
  `ClientResolver`/`fetch_document` if your server must use a proxy.
- The cache is in memory, per process. Pass `fetch=` or wrap `resolve` to share one.
- It follows draft-ietf-oauth-client-id-metadata-document as of November 2026. The draft can
  still change.

## Why this exists

MCP's authorization spec made CIMD the default way for clients to identify themselves
([SEP-991](https://github.com/modelcontextprotocol/modelcontextprotocol/issues/991)), and
authorization servers in every language are being asked to support it:

- [modelcontextprotocol/python-sdk#1801](https://github.com/modelcontextprotocol/python-sdk/issues/1801): implement server-side CIMD support
- [authlib/authlib#900](https://github.com/authlib/authlib/issues/900): support Client ID Metadata Document?
- [ory/hydra#4061](https://github.com/ory/hydra/issues/4061), [supabase/auth#2850](https://github.com/supabase/auth/issues/2850), [spring-security#18375](https://github.com/spring-projects/spring-security/issues/18375), [LibreChat#16627](https://github.com/LibreChat-AI/LibreChat/issues/16627), [doorkeeper#1875](https://github.com/doorkeeper-gem/doorkeeper/issues/1875)

The first implementations keep hitting the same bugs, each one a test here:
unknown properties rejected ([keycloak#51236](https://github.com/keycloak/keycloak/issues/51236)),
loopback redirect ports
([claude-code#37747](https://github.com/anthropics/claude-code/issues/37747)), private-use
redirect schemes rejected ([fastmcp#6336](https://github.com/PrefectHQ/fastmcp/issues/6336)), a
malformed port causing a 500 ([fastmcp#5764](https://github.com/PrefectHQ/fastmcp/issues/5764)),
no way to limit CIMD to trusted domains
([fastmcp#6153](https://github.com/PrefectHQ/fastmcp/issues/6153)), and partial client_id URL
validation ([python-sdk#3768](https://github.com/modelcontextprotocol/python-sdk/issues/3768)).

## License

MIT
