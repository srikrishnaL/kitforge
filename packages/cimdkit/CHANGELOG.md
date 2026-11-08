# Changelog

## 0.1.0 (2026-11-08)

First release.

- `ClientResolver`: resolve a URL `client_id` to validated client metadata, cached by the
  response's HTTP cache headers, keeping the last good document on transient failures and
  dropping it on terminal ones.
- `fetch_document`: https only, no redirects, `Accept: application/json`, 5 KB limit, one
  deadline, SSRF guard on every resolved address (IPv4-mapped, NAT64 and 6to4 included) with
  the connection pinned to the checked address.
- `validate_document`: exact client_id match, no secrets or shared-secret auth methods, no
  private keys, auth method selection with the `none` default, grant and response type
  intersection, redirect URI rules, unknown properties ignored.
- `redirect_uri_matches` / `ClientMetadata.check_redirect_uri`: exact match with the loopback
  any-port exception.
- `Policy`: supported methods and grants, forbidden ids (your issuer), `allowed_hosts`,
  same-origin redirects.
- `make_document` and the `cimdkit check` command for client authors publishing a document.
