"""An in-memory fake of the Dependency-Track 4.x REST API, for tests.

Objects use the field names of the real API server (checked against the Dependency-Track
4.14.6 build): Project, Component, Finding (component / vulnerability / analysis /
attribution maps), PolicyViolation, PortfolioMetrics. Lists are paginated with
pageNumber / pageSize and an X-Total-Count header, like the real server.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from typing import Any, Dict, List, Optional, Set

KEY = "odt_test_key"
ALL_PERMS = {"VIEW_PORTFOLIO", "VIEW_VULNERABILITY", "VIEW_POLICY_VIOLATION", "VULNERABILITY_ANALYSIS"}

P_SHOP = "11111111-1111-4111-8111-111111111111"
P_SHOP_OLD = "11111111-1111-4111-8111-000000000001"
P_API = "22222222-2222-4222-8222-222222222222"
P_WEB = "33333333-3333-4333-8333-333333333333"
P_RETIRED = "44444444-4444-4444-8444-444444444444"

C_LOG4J = "aaaaaaaa-0000-4000-8000-000000000001"
C_LODASH = "aaaaaaaa-0000-4000-8000-000000000002"
C_JACKSON = "aaaaaaaa-0000-4000-8000-000000000003"
C_LOG4J_API = "aaaaaaaa-0000-4000-8000-000000000004"
C_MYSQL = "aaaaaaaa-0000-4000-8000-000000000005"

V_LOG4SHELL = "bbbbbbbb-0000-4000-8000-000000000001"
V_LODASH = "bbbbbbbb-0000-4000-8000-000000000002"
V_JACKSON = "bbbbbbbb-0000-4000-8000-000000000003"


def project(uuid, name, version, *, risk=0.0, latest=False, active=True, tags=(), metrics=None):
    p = {"uuid": uuid, "name": name, "version": version, "classifier": "APPLICATION",
         "active": active, "isLatest": latest, "tags": [{"name": t} for t in tags],
         "lastInheritedRiskScore": risk, "lastBomImport": 1762300000000}
    if metrics:
        p["metrics"] = metrics
    return p


def vuln(uuid, vuln_id, source, severity, *, cvss=None, epss=None, title=None, aliases=(), **extra):
    v = {"uuid": uuid, "vulnId": vuln_id, "source": source, "severity": severity,
         "severityRank": ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO", "UNASSIGNED"].index(severity),
         "cvssV3BaseScore": cvss, "epssScore": epss, "title": title,
         "description": extra.pop("description", f"{vuln_id} description " * 30),
         "aliases": list(aliases), "cwes": [{"cweId": 502, "name": "Deserialization of Untrusted Data"}]}
    v.update(extra)
    return v


def component(uuid, group, name, version, purl, *, license_id=None, license_name=None,
              latest=None, project_uuid=None, internal=False):
    c = {"uuid": uuid, "group": group, "name": name, "version": version, "purl": purl,
         "isInternal": internal}
    if license_id:
        c["resolvedLicense"] = {"licenseId": license_id, "name": license_id}
    elif license_name:
        c["license"] = license_name
    if latest:
        c["repositoryMeta"] = {"latestVersion": latest}
    if project_uuid:
        c["_project"] = project_uuid
    return c


class FakeDT:
    def __init__(self, *, perms: Optional[Set[str]] = None, version: str = "4.14.6",
                 latest_endpoint: bool = True):
        self.perms = ALL_PERMS if perms is None else perms
        self.version = version
        self.calls: List[tuple] = []
        self.puts: List[Dict[str, Any]] = []
        self.projects = [
            project(P_SHOP, "shop", "2.0", risk=95.0, latest=True, tags=["payments", "java"],
                    metrics={"critical": 1, "high": 1, "medium": 0, "low": 0, "policyViolationsTotal": 2}),
            project(P_SHOP_OLD, "shop", "1.9", risk=120.0, tags=["java"]),
            project(P_API, "orders-api", "5.1", risk=40.0, latest=True, tags=["java"]),
            project(P_WEB, "@acme/web", "3.0.0", risk=0.0, latest=True, tags=["frontend"]),
            project(P_RETIRED, "legacy-batch", "0.9", risk=10.0, active=False),
        ]
        self.vulns = {
            V_LOG4SHELL: vuln(V_LOG4SHELL, "CVE-2021-44228", "NVD", "CRITICAL", cvss=10.0, epss=0.944,
                              title=None, aliases=[{"cveId": "CVE-2021-44228", "ghsaId": "GHSA-jfh8-c2jp-5v3q"}],
                              recommendation="Upgrade to 2.17.1 or later.", cvssV3Vector="CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:C/C:H/I:H/A:H",
                              epssPercentile=0.999, published="2021-12-10T10:15:09Z"),
            V_LODASH: vuln(V_LODASH, "GHSA-35jh-r3h4-6jhm", "GITHUB", "HIGH", cvss=7.2, epss=0.02,
                           title="Command Injection in lodash",
                           aliases=[{"cveId": "CVE-2021-23337", "ghsaId": "GHSA-35jh-r3h4-6jhm"}]),
            V_JACKSON: vuln(V_JACKSON, "CVE-2020-36518", "NVD", "HIGH", cvss=7.5, epss=0.004,
                            title=None),
        }
        self.components = {
            P_SHOP: [
                component(C_LOG4J, "org.apache.logging.log4j", "log4j-core", "2.14.1",
                          "pkg:maven/org.apache.logging.log4j/log4j-core@2.14.1", license_id="Apache-2.0",
                          latest="2.24.3"),
                component(C_LOG4J_API, "org.apache.logging.log4j", "log4j-api", "2.14.1",
                          "pkg:maven/org.apache.logging.log4j/log4j-api@2.14.1", license_id="Apache-2.0"),
                component(C_JACKSON, "com.fasterxml.jackson.core", "jackson-databind", "2.12.0",
                          "pkg:maven/com.fasterxml.jackson.core/jackson-databind@2.12.0", license_id="Apache-2.0"),
                component(C_MYSQL, "mysql", "mysql-connector-java", "8.0.28",
                          "pkg:maven/mysql/mysql-connector-java@8.0.28",
                          license_name="GPL-2.0-only WITH Universal-FOSS-exception-1.0"),
            ],
            P_API: [
                component("aaaaaaaa-0000-4000-8000-0000000000a1", "org.apache.logging.log4j", "log4j-core", "2.14.1",
                          "pkg:maven/org.apache.logging.log4j/log4j-core@2.14.1", license_id="Apache-2.0"),
                component("aaaaaaaa-0000-4000-8000-0000000000a2", "", "netty", "4.1.0",
                          "pkg:maven/io.netty/netty@4.1.0", license_id="AGPL-3.0-only"),
            ],
            P_WEB: [
                component(C_LODASH, None, "lodash", "4.17.20", "pkg:npm/lodash@4.17.20", license_id="MIT"),
                component("aaaaaaaa-0000-4000-8000-0000000000b1", None, "left-pad", "1.3.0",
                          "pkg:npm/left-pad@1.3.0"),
                component("aaaaaaaa-0000-4000-8000-0000000000b2", None, "dual", "1.0.0",
                          "pkg:npm/dual@1.0.0", license_name="(MIT OR GPL-3.0-only)"),
            ],
        }
        # (project, component, vulnerability) -> analysis
        self.findings = {
            P_SHOP: [(C_LOG4J, V_LOG4SHELL, {"state": "EXPLOITABLE", "isSuppressed": False}),
                     (C_JACKSON, V_JACKSON, None)],
            P_API: [("aaaaaaaa-0000-4000-8000-0000000000a1", V_LOG4SHELL, {"state": "NOT_AFFECTED", "isSuppressed": True})],
            P_WEB: [(C_LODASH, V_LODASH, {"state": "IN_TRIAGE", "isSuppressed": False})],
        }
        self.metrics = {"critical": 2, "high": 2, "medium": 0, "low": 0, "unassigned": 0,
                        "vulnerabilities": 4, "projects": 4, "vulnerableProjects": 3,
                        "components": 9, "vulnerableComponents": 3, "findingsTotal": 4,
                        "findingsAudited": 3, "findingsUnaudited": 1, "suppressed": 1,
                        "policyViolationsFail": 1, "policyViolationsWarn": 1,
                        "policyViolationsTotal": 2, "inheritedRiskScore": 265.0,
                        "lastOccurrence": 1762900000000}
        self.violations = {
            P_SHOP: [
                {"uuid": "cccccccc-0000-4000-8000-000000000001", "type": "LICENSE", "timestamp": 1762900000000,
                 "policyCondition": {"subject": "LICENSE_GROUP", "operator": "IS", "value": "Copyleft",
                                     "policy": {"name": "No copyleft", "violationState": "WARN"}},
                 "component": {"uuid": C_MYSQL, "name": "mysql-connector-java", "version": "8.0.28", "group": "mysql"},
                 "project": {"uuid": P_SHOP, "name": "shop", "version": "2.0"}},
                {"uuid": "cccccccc-0000-4000-8000-000000000002", "type": "SECURITY", "timestamp": 1762900000000,
                 "policyCondition": {"subject": "SEVERITY", "operator": "IS", "value": "CRITICAL",
                                     "policy": {"name": "No critical vulns", "violationState": "FAIL"}},
                 "component": {"uuid": C_LOG4J, "name": "log4j-core", "version": "2.14.1", "group": "org.apache.logging.log4j"},
                 "project": {"uuid": P_SHOP, "name": "shop", "version": "2.0"},
                 "analysis": {"analysisState": "APPROVED", "isSuppressed": False}},
            ],
        }
        self.latest_endpoint = latest_endpoint

    # ------------------------------------------------------------------ transport

    def __call__(self, method, url, headers, body):
        parsed = urllib.parse.urlparse(url)
        assert parsed.scheme == "https" and parsed.netloc == "dtrack.example.com", url
        path = parsed.path
        q = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        self.calls.append((method, path, q))
        if path == "/api/version":
            return self._json({"version": self.version, "application": "Dependency-Track"})
        if headers.get("X-Api-Key") != KEY:
            return 401, {}, b""
        route = path[len("/api"):]
        data = json.loads(body) if body else None
        try:
            result = self.route(method, route, q, data)
        except LookupError as err:
            return 404, {"content-type": "text/plain"}, str(err).encode()
        except PermissionError:
            return 403, {}, b""
        if isinstance(result, tuple):  # (items, paginate)
            items = result[0]
            return self._page(items, q)
        return self._json(result)

    def need(self, perm):
        if perm not in self.perms:
            raise PermissionError(perm)

    def route(self, method, path, q, data):
        m = lambda pattern: re.fullmatch(pattern, path)  # noqa: E731
        if method == "PUT" and path == "/v1/analysis":
            self.need("VULNERABILITY_ANALYSIS")
            self.puts.append(data)
            analysis = {"analysisState": data["analysisState"],
                        "analysisJustification": data.get("analysisJustification", "NOT_SET"),
                        "analysisResponse": data.get("analysisResponse", "NOT_SET"),
                        "analysisDetails": data.get("analysisDetails"),
                        "analysisComments": [{"comment": "Analysis: NOT_SET → " + data["analysisState"]}]
                        + ([{"comment": data["comment"]}] if data.get("comment") else []),
                        "isSuppressed": bool(data.get("isSuppressed", False))}
            return analysis
        if method != "GET":
            raise LookupError(path)
        if path.startswith("/v1/finding") or path.startswith("/v1/analysis"):
            self.need("VIEW_VULNERABILITY")
        elif path.startswith("/v1/violation"):
            self.need("VIEW_POLICY_VIOLATION")
        else:
            self.need("VIEW_PORTFOLIO")

        if path == "/v1/metrics/portfolio/current":
            return self.metrics
        if path == "/v1/project":
            ps = self.projects
            if q.get("excludeInactive") == "true":
                ps = [p for p in ps if p["active"]]
            if q.get("name"):
                ps = [p for p in ps if p["name"] == q["name"]]
            if q.get("searchText"):
                ps = [p for p in ps if q["searchText"].lower() in p["name"].lower()]
            return ps, True
        if path == "/v1/project/lookup":
            for p in self.projects:
                if p["name"] == q.get("name") and p["version"] == q.get("version"):
                    return p
            raise LookupError("The project could not be found.")
        if mm := m(r"/v1/project/latest/(.+)"):
            if not self.latest_endpoint:
                raise LookupError("HTTP 404 Not Found")
            name = urllib.parse.unquote(mm.group(1))
            for p in self.projects:
                if p["name"] == name and p["isLatest"]:
                    return p
            raise LookupError("The project could not be found.")
        if mm := m(r"/v1/project/tag/(.+)"):
            tag = urllib.parse.unquote(mm.group(1))
            ps = [p for p in self.projects if any(t["name"] == tag for t in p["tags"])]
            if q.get("excludeInactive") == "true":
                ps = [p for p in ps if p["active"]]
            return ps, True
        if mm := m(r"/v1/project/([0-9a-f-]{36})"):
            return self.project(mm.group(1))
        if mm := m(r"/v1/finding/project/([0-9a-f-]{36})"):
            self.project(mm.group(1))
            rows = [self.finding(mm.group(1), c, v, a) for c, v, a in self.findings.get(mm.group(1), [])]
            if q.get("suppressed") != "true":
                rows = [r for r in rows if not r["analysis"].get("isSuppressed")]
            return rows
        if path == "/v1/finding":
            return [], True
        if mm := m(r"/v1/vulnerability/source/([A-Z]+)/vuln/([^/]+)/projects"):
            v = self.vuln_by_id(mm.group(1), urllib.parse.unquote(mm.group(2)))
            hits = [self.project(p) for p, fs in self.findings.items() if any(x[1] == v["uuid"] for x in fs)]
            return [{"uuid": p["uuid"], "name": p["name"], "version": p["version"], "active": p["active"]} for p in hits]
        if mm := m(r"/v1/vulnerability/source/([A-Z]+)/vuln/([^/]+)"):
            return self.vuln_by_id(mm.group(1), urllib.parse.unquote(mm.group(2)))
        if mm := m(r"/v1/component/project/([0-9a-f-]{36})"):
            p = self.project(mm.group(1))
            cs = [self.public_component(c) for c in self.components.get(p["uuid"], [])]
            if q.get("onlyOutdated") == "true":
                cs = [c for c in cs if c.get("repositoryMeta")]
            if q.get("searchText"):
                cs = [c for c in cs if q["searchText"].lower() in c["name"].lower()]
            return cs, True
        if path == "/v1/component/identity":
            out = []
            for puuid, cs in self.components.items():
                for c in cs:
                    purl_q = q.get("purl")
                    if purl_q and not (c["purl"] == purl_q or c["purl"].startswith(purl_q + "@")):
                        continue
                    if q.get("name") and c["name"] != q["name"]:
                        continue
                    if q.get("version") and c["version"] != q["version"]:
                        continue
                    pc = self.public_component(c)
                    p = self.project(puuid)
                    pc["project"] = {"uuid": p["uuid"], "name": p["name"], "version": p["version"]}
                    out.append(pc)
            return out, True
        if mm := m(r"/v1/violation/project/([0-9a-f-]{36})"):
            self.project(mm.group(1))
            return self.violations.get(mm.group(1), []), True
        if path == "/v1/violation":
            return [v for vs in self.violations.values() for v in vs], True
        raise LookupError(path)

    # ------------------------------------------------------------------ helpers

    def project(self, uuid):
        for p in self.projects:
            if p["uuid"] == uuid:
                return p
        raise LookupError("The project could not be found.")

    def vuln_by_id(self, source, vuln_id):
        for v in self.vulns.values():
            if v["source"] == source and v["vulnId"] == vuln_id:
                return v
        raise LookupError("The vulnerability could not be found.")

    def public_component(self, c):
        return {k: v for k, v in c.items() if not k.startswith("_")}

    def finding(self, project_uuid, comp_uuid, vuln_uuid, analysis):
        comp = next(c for c in self.components[project_uuid] if c["uuid"] == comp_uuid)
        v = self.vulns[vuln_uuid]
        return {
            "component": {"uuid": comp["uuid"], "name": comp["name"], "group": comp["group"],
                          "version": comp["version"], "purl": comp["purl"], "project": project_uuid},
            "vulnerability": dict(v),
            "analysis": dict(analysis or {"isSuppressed": False}),
            "attribution": {"analyzerIdentity": "INTERNAL_ANALYZER", "attributedOn": 1762800000000},
            "matrix": f"{project_uuid}:{comp_uuid}:{vuln_uuid}",
        }

    def _json(self, obj):
        return 200, {"content-type": "application/json"}, json.dumps(obj).encode()

    def _page(self, items, q):
        size = int(q.get("pageSize", 100))
        number = int(q.get("pageNumber", 1))
        chunk = items[(number - 1) * size: number * size]
        status, headers, body = self._json(chunk)
        headers["x-total-count"] = str(len(items))
        return status, headers, body
