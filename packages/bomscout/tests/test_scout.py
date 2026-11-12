import json

import pytest
from fakedt import C_JACKSON, C_LOG4J, P_SHOP, V_JACKSON, V_LOG4SHELL

from bomscout import DTError
from bomscout.views import license_family


def test_portfolio_overview(scout):
    out = scout.portfolio_overview(top=2)
    assert out["vulnerabilities"]["critical"] == 2
    assert out["portfolio"]["vulnerableProjects"] == 3
    assert out["active_projects"] == 4  # the inactive project is left out
    names = [p["project"] for p in out["riskiest_projects"]]
    assert names == ["shop@1.9", "shop@2.0"]
    assert out["riskiest_projects"][1]["vulnerabilities"] == {"critical": 1, "high": 1, "medium": 0, "low": 0}
    json.dumps(out)


def test_portfolio_overview_before_metrics_exist(scout, fake):
    fake.metrics = None
    out = scout.portfolio_overview()
    assert "not been calculated" in out["note"]


def test_list_projects_search_tag_and_inactive(scout):
    assert scout.list_projects(name="SHO")["total"] == 2
    out = scout.list_projects(tag="java", limit=1)
    assert out["total"] == 3 and out["shown"] == 1 and "2 more projects" in out["more"]
    assert scout.list_projects(tag="java", name="orders")["total"] == 1
    assert scout.list_projects()["total"] == 4
    assert scout.list_projects(include_inactive=True)["total"] == 5


def test_project_findings_sorted_and_summarized(scout):
    out = scout.project_findings("shop")
    assert out["project"] == "shop@2.0" and out["project_uuid"] == P_SHOP
    assert [f["vuln"] for f in out["findings"]] == ["CVE-2021-44228", "CVE-2020-36518"]
    log4shell = out["findings"][0]
    assert log4shell["component"] == "org.apache.logging.log4j/log4j-core@2.14.1"
    assert log4shell["analysis"] == "EXPLOITABLE" and log4shell["cvss"] == 10.0
    assert log4shell["aliases"] == ["GHSA-jfh8-c2jp-5v3q"]
    assert log4shell["component_uuid"] == C_LOG4J and log4shell["vulnerability_uuid"] == V_LOG4SHELL
    assert "description" not in log4shell and len(log4shell["title"]) <= 120
    assert out["by_severity"] == {"CRITICAL": 1, "HIGH": 1}
    assert out["by_analysis"] == {"EXPLOITABLE": 1, "NOT_SET": 1}


def test_project_findings_filters(scout):
    assert scout.project_findings("shop", min_severity="critical")["total"] == 1
    untriaged = scout.project_findings("shop", analysis_state="not set")
    assert [f["vuln"] for f in untriaged["findings"]] == ["CVE-2020-36518"]
    assert scout.project_findings("orders-api")["total"] == 0
    assert scout.project_findings("orders-api", include_suppressed=True)["findings"][0]["suppressed"] is True
    with pytest.raises(DTError, match="Unknown severity 'urgent'"):
        scout.project_findings("shop", min_severity="urgent")


def test_vulnerability_impact(scout):
    out = scout.vulnerability_impact("CVE-2021-44228")
    v = out["vulnerability"]
    assert v["severity"] == "CRITICAL" and v["recommendation"].startswith("Upgrade")
    assert v["cwes"] == ["CWE-502"] and v["aliases"] == ["GHSA-jfh8-c2jp-5v3q"]
    assert len(v["description"]) <= 1200
    assert out["affected_projects"] == 2
    by_project = {p["project"]: p["components"] for p in out["projects"]}
    assert by_project["shop@2.0"][0]["analysis"] == "EXPLOITABLE"
    assert by_project["orders-api@5.1"][0] == {
        "component": "org.apache.logging.log4j/log4j-core@2.14.1",
        "component_uuid": "aaaaaaaa-0000-4000-8000-0000000000a1",
        "analysis": "NOT_AFFECTED", "suppressed": True}
    assert out["remediation"] == {"EXPLOITABLE": 1, "NOT_AFFECTED": 1}


def test_vulnerability_impact_caps_projects(scout):
    out = scout.vulnerability_impact("CVE-2021-44228", max_projects=1)
    assert len(out["projects"]) == 1 and "1 of 2" in out["note"]


def test_vulnerability_impact_keeps_going_when_one_project_fails(scout, fake):
    real = fake.route

    def route(method, path, q, data):
        if path == f"/v1/finding/project/{P_SHOP}":
            raise PermissionError("VIEW_VULNERABILITY")
        return real(method, path, q, data)

    fake.route = route
    out = scout.vulnerability_impact("CVE-2021-44228")
    errors = [p for p in out["projects"] if "error" in p]
    assert len(errors) == 1 and "VIEW_VULNERABILITY" in errors[0]["error"]
    assert out["remediation"] == {"NOT_AFFECTED": 1}


def test_project_components(scout):
    out = scout.project_components("shop@2.0")
    assert out["total"] == 4
    log4j = out["components"][0]
    assert log4j["latest_version"] == "2.24.3" and log4j["license"] == "Apache-2.0"
    assert "latest_version" not in out["components"][1]
    assert scout.project_components("shop", only_outdated=True)["total"] == 1
    assert scout.project_components("shop", search="jackson")["components"][0]["uuid"] == C_JACKSON


def test_project_licenses(scout):
    out = scout.project_licenses("@acme/web")
    assert out["components"] == 3
    fams = {r["license"]: r["family"] for r in out["licenses"]}
    assert fams == {"MIT": "permissive", "(none)": "unknown", "(MIT OR GPL-3.0-only)": "permissive"}
    assert out["by_family"] == {"unknown": 1, "permissive": 2}
    shop = scout.project_licenses("shop")
    assert shop["by_family"] == {"weak copyleft": 1, "permissive": 3}


@pytest.mark.parametrize("lic,family", [
    ("Apache-2.0", "permissive"), ("GPL-3.0-only", "strong copyleft"), ("AGPL-3.0-or-later", "strong copyleft"),
    ("LGPL-2.1-only", "weak copyleft"), ("MPL-2.0", "weak copyleft"), ("(none)", "unknown"),
    ("MIT AND GPL-2.0-only", "strong copyleft"), ("MIT OR GPL-2.0-only", "permissive"),
    ("(LGPL-2.1-only OR GPL-3.0-only) AND MIT", "weak copyleft"),
    ("GPL-2.0-only WITH Classpath-exception-2.0", "weak copyleft"),
])
def test_license_family(lic, family):
    assert license_family(lic) == family


def test_find_component_across_portfolio(scout):
    out = scout.find_component(purl="pkg:maven/org.apache.logging.log4j/log4j-core@2.14.1")
    assert out["projects_using_it"] == ["orders-api@5.1", "shop@2.0"]
    assert scout.find_component(purl="pkg:maven/org.apache.logging.log4j/log4j-core")["total"] == 2
    assert scout.find_component(name="lodash", version="4.17.20")["projects_using_it"] == ["@acme/web@3.0.0"]
    with pytest.raises(DTError, match="package URL"):
        scout.find_component()


def test_policy_violations(scout):
    out = scout.policy_violations("shop")
    assert [v["state"] for v in out["violations"]] == ["FAIL", "WARN"]
    fail = out["violations"][0]
    assert fail["policy"] == "No critical vulns" and fail["condition"] == "SEVERITY IS CRITICAL"
    assert fail["component"] == "org.apache.logging.log4j/log4j-core@2.14.1"
    assert fail["project"] == "shop@2.0" and fail["analysis"] == "APPROVED"
    assert out["by_state"] == {"FAIL": 1, "WARN": 1}
    lic = scout.policy_violations(violation_type="license")
    assert lic["total"] == 1 and lic["violations"][0]["type"] == "LICENSE"
    with pytest.raises(DTError, match="violation type"):
        scout.policy_violations(violation_type="bad")


def test_record_analysis_sends_the_api_fields(scout, fake):
    out = scout.record_analysis("shop", C_JACKSON, V_JACKSON, "not affected",
                                justification="code-not-reachable", response="will not fix",
                                details="Only used in tests.", comment="Checked by Sri", suppress=True)
    assert fake.puts == [{
        "project": P_SHOP, "component": C_JACKSON, "vulnerability": V_JACKSON,
        "analysisState": "NOT_AFFECTED", "analysisJustification": "CODE_NOT_REACHABLE",
        "analysisResponse": "WILL_NOT_FIX", "analysisDetails": "Only used in tests.",
        "comment": "Checked by Sri", "isSuppressed": True}]
    assert out["analysis"] == "NOT_AFFECTED" and out["suppressed"] is True
    assert out["latest_comment"] == "Checked by Sri"


def test_record_analysis_leaves_unset_fields_out(scout, fake):
    scout.record_analysis(P_SHOP, C_JACKSON, V_JACKSON, "IN_TRIAGE")
    assert fake.puts[-1] == {"project": P_SHOP, "component": C_JACKSON,
                             "vulnerability": V_JACKSON, "analysisState": "IN_TRIAGE"}
    with pytest.raises(DTError, match="Unknown analysis state 'fixed'"):
        scout.record_analysis(P_SHOP, C_JACKSON, V_JACKSON, "fixed")
    assert len(fake.puts) == 1


def test_limits_are_clamped(scout):
    assert scout.list_projects(limit=0)["total"] == 4
    assert scout.list_projects(limit="x")["shown"] == 4
