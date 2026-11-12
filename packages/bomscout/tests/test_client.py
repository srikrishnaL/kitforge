import pytest
from fakedt import KEY, P_API, P_SHOP, P_SHOP_OLD, P_WEB, FakeDT

from bomscout import DependencyTrack, DTError
from bomscout.client import guess_sources


def make(fake, url="https://dtrack.example.com", key=KEY, **kw):
    return DependencyTrack(url, key, transport=fake, **kw)


@pytest.mark.parametrize("url", ["https://dtrack.example.com", "https://dtrack.example.com/",
                                 "https://dtrack.example.com/api", "dtrack.example.com/api/"])
def test_base_url_is_normalized(fake, url):
    assert make(fake, url).version()["version"] == "4.14.6"
    assert fake.calls[-1][1] == "/api/version"


def test_unconfigured_client_explains_what_to_set():
    with pytest.raises(DTError, match="set DT_URL"):
        DependencyTrack("", "")


def test_api_key_header_and_401(fake):
    with pytest.raises(DTError, match="rejected the API key") as err:
        make(fake, key="wrong").get("/v1/project")
    assert err.value.status == 401


def test_403_names_the_missing_permission(fake):
    fake.perms = {"VIEW_PORTFOLIO"}
    with pytest.raises(DTError, match="VIEW_VULNERABILITY permission"):
        make(fake).get(f"/v1/finding/project/{P_SHOP}")
    with pytest.raises(DTError, match="VIEW_POLICY_VIOLATION"):
        make(fake).get("/v1/violation")
    with pytest.raises(DTError, match="VULNERABILITY_ANALYSIS"):
        make(fake).put("/v1/analysis", {"analysisState": "NOT_SET"})


def test_html_answer_means_frontend_url():
    def transport(method, url, headers, body):
        return 200, {}, b"<!DOCTYPE html><html><head><title>Dependency-Track</title></html>"

    with pytest.raises(DTError, match="frontend"):
        DependencyTrack("https://dt.example.com", KEY, transport=transport).get("/v1/project")


def test_unreachable_server():
    def transport(method, url, headers, body):
        raise OSError("[Errno 111] Connection refused")

    with pytest.raises(DTError, match="Could not reach Dependency-Track at https://dt.example.com.*refused"):
        DependencyTrack("https://dt.example.com", KEY, transport=transport).get("/v1/project")


def test_other_http_errors_keep_the_server_text():
    def transport(method, url, headers, body):
        return 500, {}, b"Database unavailable"

    with pytest.raises(DTError, match="HTTP 500 .*Database unavailable") as err:
        DependencyTrack("https://dt.example.com", KEY, transport=transport).get("/v1/project")
    assert err.value.status == 500


def test_get_list_walks_pages_and_stops_at_limit(fake):
    client = make(fake, page_size=2)
    items, total = client.get_list("/v1/project", {})
    assert total == 5 and len(items) == 5
    pages = [c[2]["pageNumber"] for c in fake.calls if c[1] == "/api/v1/project"]
    assert pages == ["1", "2", "3"]
    fake.calls.clear()
    items, total = client.get_list("/v1/project", {}, limit=3)
    assert len(items) == 3 and total == 5


def test_get_list_without_total_header_stops_on_short_page():
    pages = {1: [1, 2], 2: [3]}

    def transport(method, url, headers, body):
        import json
        import urllib.parse

        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
        return 200, {}, json.dumps(pages.get(int(q["pageNumber"]), [])).encode()

    items, total = DependencyTrack("https://x.example", KEY, transport=transport, page_size=2).get_list("/v1/x")
    assert items == [1, 2, 3] and total == 3


def test_booleans_are_sent_as_lowercase_and_empty_params_dropped(fake):
    make(fake).get("/v1/project", {"excludeInactive": True, "name": "", "tag": None})
    assert fake.calls[-1][2] == {"excludeInactive": "true"}


@pytest.mark.parametrize("ref,uuid", [
    (P_SHOP, P_SHOP),
    ("shop", P_SHOP),  # two versions, one marked latest
    ("shop@1.9", P_SHOP_OLD),
    ("orders-api", P_API),
    ("@acme/web", P_WEB),  # npm-style scoped name, no version
    ("@acme/web@3.0.0", P_WEB),
])
def test_resolve_project(fake, ref, uuid):
    assert make(fake).resolve_project(ref)["uuid"] == uuid


def test_resolve_project_errors(fake):
    client = make(fake)
    with pytest.raises(DTError, match="No project named 'nope'"):
        client.resolve_project("nope")
    with pytest.raises(DTError, match="No project with UUID"):
        client.resolve_project("99999999-9999-4999-8999-999999999999")
    with pytest.raises(DTError, match="Give a project"):
        client.resolve_project("  ")


def test_resolve_project_with_several_versions_and_no_latest(fake):
    for p in fake.projects:
        p["isLatest"] = False
        p["active"] = True
    with pytest.raises(DTError, match=r"several versions \(1.9, 2.0\).*shop@<version>"):
        make(fake).resolve_project("shop")


def test_resolve_project_falls_back_when_name_contains_at(fake):
    fake.projects[3]["name"] = "team@acme"
    fake.projects[3]["version"] = None
    assert make(fake).resolve_project("team@acme")["uuid"] == P_WEB


def test_guess_sources():
    assert guess_sources("GHSA-35jh-r3h4-6jhm")[0] == "GITHUB"
    assert guess_sources("cve-2021-44228")[0] == "NVD"
    assert guess_sources("PYSEC-2021-1")[0] == "NVD"  # unknown prefix: try all, NVD first
    assert len(set(guess_sources("X"))) == len(guess_sources("X"))


def test_find_vulnerability_tries_sources(fake):
    client = make(fake)
    v = client.find_vulnerability("GHSA-35jh-r3h4-6jhm")
    assert v["source"] == "GITHUB"
    tried = [c[1] for c in fake.calls if "/vulnerability/" in c[1]]
    assert tried == ["/api/v1/vulnerability/source/GITHUB/vuln/GHSA-35jh-r3h4-6jhm"]
    with pytest.raises(DTError, match="no vulnerability CVE-1999-0001"):
        client.find_vulnerability("CVE-1999-0001")
    with pytest.raises(DTError, match="no vulnerability"):
        client.find_vulnerability("CVE-2021-44228", source="github")


def test_from_env_spellings():
    fake = FakeDT()
    c = DependencyTrack.from_env({"DEPENDENCY_TRACK_URL": "https://dtrack.example.com",
                                  "DEPENDENCY_TRACK_API_KEY": KEY}, transport=fake)
    assert c.get("/v1/project", {"pageSize": 1})
    c = DependencyTrack.from_env({"DT_URL": "https://dtrack.example.com/api", "DT_API_KEY": KEY,
                                  "DT_TIMEOUT": "5"}, transport=fake)
    assert c.base == "https://dtrack.example.com"


def test_insecure_and_ca_bundle_build_real_transports(tmp_path):
    # No request is sent; this only checks the settings are accepted.
    DependencyTrack.from_env({"DT_URL": "https://dt.example.com", "DT_API_KEY": "k", "DT_INSECURE": "1"})
    with pytest.raises(OSError):
        DependencyTrack.from_env({"DT_URL": "https://dt.example.com", "DT_API_KEY": "k",
                                  "DT_CA_BUNDLE": str(tmp_path / "missing.pem")})
