import pytest

from cimdkit import (
    ClientIdMismatch,
    InvalidClientId,
    InvalidDocument,
    InvalidRedirectUri,
    Policy,
    make_document,
    validate_document,
)

from conftest import CID, good_doc


def test_valid_document():
    meta = validate_document(good_doc(), CID)
    assert meta.client_name == "Example Agent"
    assert meta.token_endpoint_auth_method == "none"
    assert meta.grant_types == ("authorization_code", "refresh_token")
    assert meta.host == "app.example.com"
    assert meta.warnings == ()


def test_unknown_properties_are_ignored_not_rejected():
    # Keycloak rejected Claude Desktop's document for this (keycloak#51236).
    meta = validate_document(good_doc(software_id="x", vendor_extension={"a": 1}), CID)
    assert meta.raw["vendor_extension"] == {"a": 1}


@pytest.mark.parametrize("doc_id", [
    "https://app.example.com/oauth/client.json/",
    "https://app.example.com:443/oauth/client.json",
    "https://APP.example.com/oauth/client.json",
    None,
    ...,
])
def test_client_id_must_match_exactly(doc_id):
    with pytest.raises(ClientIdMismatch) as err:
        validate_document(good_doc(client_id=doc_id), CID)
    assert err.value.revoke_tokens


def test_not_an_object():
    with pytest.raises(InvalidDocument, match="JSON object"):
        validate_document([good_doc()], CID)


def test_client_id_equal_to_own_issuer_is_refused():
    cid = "https://auth.example.com/issuer"
    with pytest.raises(InvalidClientId, match="trusted server"):
        validate_document(good_doc(client_id=cid), cid, Policy(forbidden_client_ids=(cid,)))


@pytest.mark.parametrize("key", ["client_secret", "client_secret_expires_at"])
def test_secrets_are_refused(key):
    with pytest.raises(InvalidDocument, match=key):
        validate_document(good_doc(**{key: "s3cret"}), CID)


@pytest.mark.parametrize("field,value", [
    ("token_endpoint_auth_method", "client_secret_basic"),
    ("token_endpoint_auth_method", "client_secret_post"),
    ("token_endpoint_auth_methods_supported", ["none", "client_secret_jwt"]),
])
def test_shared_secret_methods_are_refused(field, value):
    with pytest.raises(InvalidDocument, match="shared-secret"):
        validate_document(good_doc(**{field: value}), CID)


def test_default_auth_method_is_none_not_client_secret_basic():
    meta = validate_document(good_doc(token_endpoint_auth_method=...), CID)
    assert meta.token_endpoint_auth_method == "none"


def test_client_preference_order_picks_the_method():
    jwks = {"keys": [{"kty": "EC", "crv": "P-256", "x": "a", "y": "b"}]}
    doc = good_doc(token_endpoint_auth_method=...,
                   token_endpoint_auth_methods_supported=["tls_client_auth", "private_key_jwt",
                                                          "none"], jwks=jwks)
    assert validate_document(doc, CID).token_endpoint_auth_method == "private_key_jwt"
    only_none = Policy(auth_methods=("none",))
    assert validate_document(doc, CID, only_none).token_endpoint_auth_method == "none"


def test_single_method_must_be_in_the_list():
    doc = good_doc(token_endpoint_auth_method="none",
                   token_endpoint_auth_methods_supported=["private_key_jwt"])
    with pytest.raises(InvalidDocument, match="must be one of"):
        validate_document(doc, CID)


def test_no_supported_method():
    doc = good_doc(token_endpoint_auth_method="tls_client_auth")
    with pytest.raises(InvalidDocument, match="no supported"):
        validate_document(doc, CID)


def test_private_key_jwt_needs_keys():
    with pytest.raises(InvalidDocument, match="jwks"):
        validate_document(good_doc(token_endpoint_auth_method="private_key_jwt"), CID)


def test_jwks_and_jwks_uri_not_both():
    doc = good_doc(jwks={"keys": []}, jwks_uri="https://app.example.com/jwks")
    with pytest.raises(InvalidDocument, match="both"):
        validate_document(doc, CID)


@pytest.mark.parametrize("key", [
    {"kty": "RSA", "n": "x", "e": "AQAB", "d": "private!"},
    {"kty": "oct", "k": "shared"},
])
def test_private_key_material_is_refused(key):
    with pytest.raises(InvalidDocument, match="private or symmetric"):
        validate_document(good_doc(jwks={"keys": [key]}), CID)


def test_jwks_shape_and_uri_scheme():
    with pytest.raises(InvalidDocument, match="keys"):
        validate_document(good_doc(jwks={"kty": "EC"}), CID)
    with pytest.raises(InvalidDocument, match="https"):
        validate_document(good_doc(jwks_uri="http://app.example.com/jwks"), CID)


def test_grant_and_response_types_are_intersected():
    doc = good_doc(grant_types=["authorization_code", "urn:ietf:params:oauth:grant-type:jwt-bearer"])
    assert validate_document(doc, CID).grant_types == ("authorization_code",)
    with pytest.raises(InvalidDocument, match="grant_types"):
        validate_document(good_doc(grant_types=["implicit"]), CID)
    with pytest.raises(InvalidDocument, match="response_types"):
        validate_document(good_doc(response_types=["token"]), CID)


def test_defaults_for_missing_grant_and_response_types():
    meta = validate_document(good_doc(grant_types=..., response_types=...), CID)
    assert meta.grant_types == ("authorization_code",)
    assert meta.response_types == ("code",)


def test_client_credentials_needs_no_redirects():
    doc = good_doc(grant_types=["client_credentials"], redirect_uris=...,
                   token_endpoint_auth_method="private_key_jwt",
                   jwks_uri="https://app.example.com/jwks")
    policy = Policy(grant_types=("client_credentials",))
    assert validate_document(doc, CID, policy).redirect_uris == ()


def test_authorization_code_needs_redirects():
    with pytest.raises(InvalidDocument, match="redirect_uris is required"):
        validate_document(good_doc(redirect_uris=[]), CID)


@pytest.mark.parametrize("uri,why", [
    ("javascript:alert(1)", "forbidden scheme"),
    ("data:text/html,x", "forbidden scheme"),
    ("http://app.example.com/cb", "loopback"),
    ("https://app.example.com/cb#x", "fragment"),
    ("/relative/cb", "absolute"),
    ("https:nohost", "absolute"),
])
def test_bad_redirect_uris(uri, why):
    with pytest.raises(InvalidDocument, match=why):
        validate_document(good_doc(redirect_uris=[uri]), CID)


def test_redirect_uris_must_be_strings():
    with pytest.raises(InvalidDocument, match="array of strings"):
        validate_document(good_doc(redirect_uris="https://app.example.com/cb"), CID)


def test_native_app_scheme_is_allowed():
    meta = validate_document(good_doc(redirect_uris=["com.example.app:/oauth"]), CID)
    assert meta.check_redirect_uri("com.example.app:/oauth") == "com.example.app:/oauth"


def test_same_origin_policy():
    policy = Policy(same_origin_redirects=True)
    validate_document(good_doc(redirect_uris=["https://APP.example.com/cb"]), CID, policy)
    with pytest.raises(InvalidDocument, match="origin"):
        validate_document(good_doc(redirect_uris=["https://other.example/cb"]), CID, policy)


def test_check_redirect_uri():
    meta = validate_document(good_doc(), CID)
    assert meta.check_redirect_uri("http://127.0.0.1:61234/callback")
    with pytest.raises(InvalidRedirectUri) as err:
        meta.check_redirect_uri("https://evil.example/cb")
    assert err.value.error == "invalid_request"
    with pytest.raises(InvalidRedirectUri, match="required"):
        meta.check_redirect_uri(None)
    single = validate_document(good_doc(redirect_uris=["https://app.example.com/cb"]), CID)
    assert single.check_redirect_uri(None) == "https://app.example.com/cb"


def test_informative_uris_with_odd_schemes_are_dropped_with_a_warning():
    doc = good_doc(logo_uri="javascript:alert(1)", client_uri="https://example.com",
                   policy_uri=42, client_name="  ", scope=["a"])
    meta = validate_document(doc, CID)
    assert meta.logo_uri is None and meta.policy_uri is None and meta.scope is None
    assert meta.client_uri == "https://example.com"
    assert meta.display_name == "app.example.com"
    assert len(meta.warnings) == 4


def test_query_in_client_id_warns():
    cid = "https://app.example.com/c?v=2"
    meta = validate_document(good_doc(client_id=cid), cid)
    assert any("query" in w for w in meta.warnings)
    with pytest.raises(InvalidClientId):
        validate_document(good_doc(client_id=cid), cid, Policy(allow_query=False))


def test_make_document_round_trips():
    doc = make_document(CID, client_name="My CLI", redirect_uris=["http://127.0.0.1/callback"],
                        logo_uri="https://app.example.com/logo.png")
    meta = validate_document(doc, CID)
    assert meta.logo_uri == "https://app.example.com/logo.png"


def test_make_document_refuses_what_servers_would_reject():
    with pytest.raises(InvalidDocument):
        make_document(CID, client_name="x", redirect_uris=["http://app.example.com/cb"])
    with pytest.raises(InvalidDocument):
        make_document(CID, client_name="x", redirect_uris=["http://127.0.0.1/cb"],
                      token_endpoint_auth_method="client_secret_basic")
    with pytest.raises(InvalidClientId):
        make_document("https://app.example.com/", client_name="x", redirect_uris=["x:/cb"])


def test_oauth_error_body():
    try:
        validate_document(good_doc(client_secret="x"), CID)
    except InvalidDocument as exc:
        assert exc.to_oauth_error()["error"] == "invalid_client"
        assert exc.status_code == 400


@pytest.mark.parametrize("host,ok", [
    ("app.example.com", True),
    ("APP.Example.com", True),
    ("deep.sub.trusted.dev", True),
    ("trusted.dev", False),          # *.trusted.dev means subdomains only
    ("eviltrusted.dev", False),
    ("trusted.dev.evil.com", False),
    ("other.example.com", False),
])
def test_allowed_hosts(host, ok):
    policy = Policy(allowed_hosts=("app.example.com", "*.trusted.dev"))
    assert policy.host_allowed(host) is ok
    cid = f"https://{host}/c"
    if ok:
        validate_document(good_doc(client_id=cid), cid, policy)
    else:
        with pytest.raises(InvalidClientId, match="not allowed"):
            validate_document(good_doc(client_id=cid), cid, policy)
