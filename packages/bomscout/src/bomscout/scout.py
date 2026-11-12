"""The questions bomscout answers, as plain methods that return JSON-ready dicts.

The MCP server in :mod:`bomscout.server` is a thin layer over this class, so it can also be
used from scripts and notebooks::

    from bomscout import Scout
    scout = Scout.from_env()
    print(scout.vulnerability_impact("CVE-2021-44228"))
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from .client import DependencyTrack
from .errors import DTError
from . import views

MAX_LIMIT = 500


def _limit(n: int, default: int) -> int:
    try:
        n = int(n)
    except (TypeError, ValueError):
        return default
    return default if n <= 0 else min(n, MAX_LIMIT)


def _choice(value: str, choices: List[str], what: str) -> str:
    try:
        return views.normalize_choice(value, choices, what)
    except ValueError as err:
        raise DTError(str(err)) from None


class Scout:
    """Answers portfolio questions from a Dependency-Track server."""

    def __init__(self, client: DependencyTrack):
        self.dt = client

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None, **kwargs: Any) -> "Scout":
        return cls(DependencyTrack.from_env(env, **kwargs))

    # ------------------------------------------------------------------ portfolio

    def portfolio_overview(self, top: int = 10) -> Dict[str, Any]:
        top = _limit(top, 10)
        metrics = self.dt.get("/v1/metrics/portfolio/current")
        projects, total = self.dt.get_list("/v1/project", {"excludeInactive": True}, limit=MAX_LIMIT)
        ranked = sorted(projects, key=lambda p: -(p.get("lastInheritedRiskScore") or 0))
        out: Dict[str, Any] = {}
        if isinstance(metrics, dict) and metrics:
            keys = ["projects", "vulnerableProjects", "components", "vulnerableComponents"]
            out["portfolio"] = {k: metrics.get(k) for k in keys if metrics.get(k) is not None}
            out["vulnerabilities"] = views.metrics_row(metrics)
            if metrics.get("lastOccurrence"):
                out["metrics_updated"] = metrics["lastOccurrence"]
        else:
            out["note"] = ("Portfolio metrics have not been calculated yet; Dependency-Track "
                           "computes them periodically after BOM uploads.")
        out["active_projects"] = total
        out["riskiest_projects"] = [views.project_row(p) for p in ranked[:top]
                                    if (p.get("lastInheritedRiskScore") or 0) > 0]
        if total > len(projects):
            out["note_projects"] = f"Ranked the first {len(projects)} of {total} projects."
        return out

    def list_projects(self, name: str = "", tag: str = "", include_inactive: bool = False,
                      limit: int = 50) -> Dict[str, Any]:
        limit = _limit(limit, 50)
        params = {"excludeInactive": not include_inactive}
        if tag:
            path = f"/v1/project/tag/{_quote(tag)}"
        else:
            path = "/v1/project"
            if name:
                params["searchText"] = name
        items, total = self.dt.get_list(path, params, limit=limit)
        if tag and name:
            items = [p for p in items if name.lower() in (p.get("name") or "").lower()]
            total = len(items)
        rows = [views.project_row(p) for p in items]
        return _page(rows, total, "projects")

    # ------------------------------------------------------------------ findings

    def project_findings(self, project: str, min_severity: str = "", analysis_state: str = "",
                         include_suppressed: bool = False, limit: int = 50) -> Dict[str, Any]:
        limit = _limit(limit, 50)
        p = self.dt.resolve_project(project)
        raw = self.dt.get(f"/v1/finding/project/{p['uuid']}", {"suppressed": include_suppressed})
        rows = [views.finding_row(f) for f in raw or []]
        summary = views.summarize_findings(rows)
        if min_severity:
            sev = _choice(min_severity, views.SEVERITIES, "severity")
            cut = views.severity_rank(sev)
            rows = [r for r in rows if views.severity_rank(r.get("severity")) <= cut]
        if analysis_state:
            state = _choice(analysis_state, views.ANALYSIS_STATES, "analysis state")
            rows = [r for r in rows if r.get("analysis") == state]
        rows = views.sort_findings(rows)
        out = {"project": views.project_label(p), "project_uuid": p.get("uuid"), **summary}
        out.update(_page(rows, len(rows), "findings", limit))
        return out

    def vulnerability_impact(self, vuln_id: str, source: str = "",
                             max_projects: int = 20) -> Dict[str, Any]:
        max_projects = _limit(max_projects, 20)
        vuln = self.dt.find_vulnerability(vuln_id, source)
        src, vid = vuln.get("source"), vuln.get("vulnId")
        affected = self.dt.get(f"/v1/vulnerability/source/{src}/vuln/{_quote(vid)}/projects",
                               {"excludeInactive": True}) or []
        out: Dict[str, Any] = {"vulnerability": views.vulnerability_view(vuln),
                               "affected_projects": len(affected)}
        impact = []
        for proj in affected[:max_projects]:
            row: Dict[str, Any] = {"project": views.project_label(proj), "project_uuid": proj.get("uuid")}
            try:
                findings = self.dt.get(f"/v1/finding/project/{proj['uuid']}", {"suppressed": True}) or []
            except DTError as err:
                row["error"] = str(err)
                impact.append(row)
                continue
            hits = [f for f in findings if (f.get("vulnerability") or {}).get("uuid") == vuln.get("uuid")]
            row["components"] = [{
                "component": views.coordinates(f.get("component") or {}),
                "component_uuid": (f.get("component") or {}).get("uuid"),
                "analysis": (f.get("analysis") or {}).get("state") or "NOT_SET",
                **({"suppressed": True} if (f.get("analysis") or {}).get("isSuppressed") else {}),
            } for f in hits]
            impact.append(row)
        states = [c["analysis"] for r in impact for c in r.get("components", [])]
        out["remediation"] = {s: states.count(s) for s in views.ANALYSIS_STATES if s in states}
        out["projects"] = impact
        if len(affected) > max_projects:
            out["note"] = (f"Showing analysis for {max_projects} of {len(affected)} affected "
                           "projects; raise max_projects to see more.")
        return out

    # ------------------------------------------------------------------ components

    def project_components(self, project: str, search: str = "", only_outdated: bool = False,
                           only_direct: bool = False, limit: int = 100) -> Dict[str, Any]:
        limit = _limit(limit, 100)
        p = self.dt.resolve_project(project)
        params = {"onlyOutdated": only_outdated, "onlyDirect": only_direct}
        if search:
            params["searchText"] = search
        items, total = self.dt.get_list(f"/v1/component/project/{p['uuid']}", params, limit=limit)
        out = {"project": views.project_label(p), "project_uuid": p.get("uuid")}
        out.update(_page([views.component_row(c) for c in items], total, "components"))
        return out

    def project_licenses(self, project: str) -> Dict[str, Any]:
        p = self.dt.resolve_project(project)
        items, total = self.dt.get_list(f"/v1/component/project/{p['uuid']}", {}, limit=5000)
        out = {"project": views.project_label(p), "project_uuid": p.get("uuid"), "components": total}
        out.update(views.license_summary(items))
        if total > len(items):
            out["note"] = f"Summarized the first {len(items)} of {total} components."
        out["disclaimer"] = "License families are a quick triage aid, not legal advice."
        return out

    def find_component(self, purl: str = "", name: str = "", version: str = "", group: str = "",
                       limit: int = 50) -> Dict[str, Any]:
        limit = _limit(limit, 50)
        if not (purl or name):
            raise DTError("Give a package URL (purl, e.g. pkg:maven/org.apache.logging.log4j/"
                          "log4j-core@2.14.1) or a component name.")
        params = {"purl": purl, "name": name, "version": version, "group": group,
                  "excludeInactiveProjects": True}
        items, total = self.dt.get_list("/v1/component/identity", params, limit=limit)
        rows = [views.component_row(c) for c in items]
        projects = sorted({r["project"] for r in rows if r.get("project")})
        out = {"projects_using_it": projects}
        out.update(_page(rows, total, "components"))
        return out

    # ------------------------------------------------------------------ policy

    def policy_violations(self, project: str = "", violation_type: str = "",
                          include_suppressed: bool = False, limit: int = 50) -> Dict[str, Any]:
        limit = _limit(limit, 50)
        vtype = _choice(violation_type, views.VIOLATION_TYPES, "violation type") if violation_type else ""
        out: Dict[str, Any] = {}
        if project:
            p = self.dt.resolve_project(project)
            out.update(project=views.project_label(p), project_uuid=p.get("uuid"))
            items, total = self.dt.get_list(f"/v1/violation/project/{p['uuid']}",
                                            {"suppressed": include_suppressed}, limit=MAX_LIMIT)
        else:
            items, total = self.dt.get_list("/v1/violation", {"suppressed": include_suppressed},
                                            limit=MAX_LIMIT)
        rows = [views.violation_row(v) for v in items]
        if vtype:
            rows = [r for r in rows if r.get("type") == vtype]
            total = len(rows)
        order = {"FAIL": 0, "WARN": 1, "INFO": 2}
        rows.sort(key=lambda r: (order.get(r.get("state") or "", 3), r.get("type") or "",
                                 r.get("component") or ""))
        out["by_state"] = {s: sum(1 for r in rows if r.get("state") == s) for s in order
                           if any(r.get("state") == s for r in rows)}
        out.update(_page(rows, total, "violations", limit))
        return out

    # ------------------------------------------------------------------ triage (write)

    def record_analysis(self, project: str, component_uuid: str, vulnerability_uuid: str,
                        state: str, justification: str = "", response: str = "",
                        details: str = "", comment: str = "",
                        suppress: Optional[bool] = None) -> Dict[str, Any]:
        p = self.dt.resolve_project(project)
        body: Dict[str, Any] = {
            "project": p["uuid"],
            "component": component_uuid,
            "vulnerability": vulnerability_uuid,
            "analysisState": _choice(state, views.ANALYSIS_STATES, "analysis state"),
        }
        if justification:
            body["analysisJustification"] = _choice(justification, views.JUSTIFICATIONS, "justification")
        if response:
            body["analysisResponse"] = _choice(response, views.RESPONSES, "response")
        if details:
            body["analysisDetails"] = details
        if comment:
            body["comment"] = comment
        if suppress is not None:
            body["isSuppressed"] = bool(suppress)
        result = self.dt.put("/v1/analysis", body) or {}
        comments = result.get("analysisComments") or []
        return {
            "project": views.project_label(p),
            "analysis": result.get("analysisState", body["analysisState"]),
            "justification": result.get("analysisJustification"),
            "response": result.get("analysisResponse"),
            "suppressed": result.get("isSuppressed", body.get("isSuppressed")),
            "latest_comment": views.short((comments[-1] or {}).get("comment"), 200) if comments else None,
        }


def _quote(value: Any) -> str:
    import urllib.parse

    return urllib.parse.quote(str(value), safe="")


def _page(rows: List[Dict[str, Any]], total: int, what: str,
          limit: Optional[int] = None) -> Dict[str, Any]:
    shown = rows if limit is None else rows[:limit]
    out: Dict[str, Any] = {"total": total, "shown": len(shown), what: shown}
    if total > len(shown):
        out["more"] = (f"{total - len(shown)} more {what} not shown; narrow the question "
                       "(filters) or raise limit.")
    return out
