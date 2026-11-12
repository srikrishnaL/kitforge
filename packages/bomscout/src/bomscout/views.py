"""Turn Dependency-Track API objects into small rows a model can read.

The API returns large objects (a finding carries the full vulnerability description, every
CVSS vector, references...). These functions keep what answers a question and drop the
rest, so a tool result stays a few thousand tokens.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Dict, Iterable, List, Optional

SEVERITIES = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO", "UNASSIGNED"]
ANALYSIS_STATES = ["EXPLOITABLE", "IN_TRIAGE", "FALSE_POSITIVE", "NOT_AFFECTED", "RESOLVED",
                   "NOT_SET"]
JUSTIFICATIONS = ["CODE_NOT_PRESENT", "CODE_NOT_REACHABLE", "REQUIRES_CONFIGURATION",
                  "REQUIRES_DEPENDENCY", "REQUIRES_ENVIRONMENT", "PROTECTED_BY_COMPILER",
                  "PROTECTED_AT_RUNTIME", "PROTECTED_AT_PERIMETER",
                  "PROTECTED_BY_MITIGATING_CONTROL", "NOT_SET"]
RESPONSES = ["CAN_NOT_FIX", "WILL_NOT_FIX", "UPDATE", "ROLLBACK", "WORKAROUND_AVAILABLE",
             "NOT_SET"]
VIOLATION_TYPES = ["SECURITY", "LICENSE", "OPERATIONAL"]

_METRIC_KEYS = ["critical", "high", "medium", "low", "unassigned", "vulnerabilities",
                "findingsTotal", "findingsAudited", "findingsUnaudited", "suppressed",
                "policyViolationsFail", "policyViolationsWarn", "policyViolationsInfo",
                "policyViolationsTotal", "inheritedRiskScore"]


def severity_rank(severity: Optional[str]) -> int:
    s = (severity or "UNASSIGNED").upper()
    return SEVERITIES.index(s) if s in SEVERITIES else len(SEVERITIES)


def normalize_choice(value: str, choices: List[str], what: str) -> str:
    """Accept any case and spaces/dashes ("false positive" -> FALSE_POSITIVE)."""
    v = (value or "").strip().upper().replace(" ", "_").replace("-", "_")
    if v not in choices:
        raise ValueError(f"Unknown {what} {value!r}; use one of: {', '.join(choices)}.")
    return v


def short(text: Optional[str], n: int = 160) -> Optional[str]:
    if not text:
        return None
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def coordinates(c: Dict[str, Any]) -> str:
    """``group/name@version`` (or ``name@version``)."""
    name = c.get("name") or "?"
    if c.get("group"):
        name = f"{c['group']}/{name}"
    return f"{name}@{c['version']}" if c.get("version") else name


def project_label(p: Optional[Dict[str, Any]]) -> Optional[str]:
    if not p:
        return None
    name = p.get("name") or p.get("uuid") or "?"
    return f"{name}@{p['version']}" if p.get("version") else name


def metrics_row(m: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not m:
        return None
    return {k: m[k] for k in _METRIC_KEYS if m.get(k) not in (None, 0, 0.0)} or {"vulnerabilities": 0}


def project_row(p: Dict[str, Any]) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "project": project_label(p),
        "uuid": p.get("uuid"),
        "classifier": p.get("classifier"),
    }
    if p.get("active") is False:
        row["active"] = False
    if p.get("isLatest"):
        row["latest"] = True
    tags = [t.get("name") for t in p.get("tags") or [] if isinstance(t, dict) and t.get("name")]
    if tags:
        row["tags"] = tags
    if p.get("lastInheritedRiskScore") is not None:
        row["risk_score"] = p["lastInheritedRiskScore"]
    m = p.get("metrics")
    if m:
        row["vulnerabilities"] = {s.lower(): m.get(s.lower(), 0) for s in SEVERITIES[:4]}
        if m.get("policyViolationsTotal"):
            row["policy_violations"] = m["policyViolationsTotal"]
    if p.get("lastBomImport"):
        row["last_bom_import"] = p["lastBomImport"]
    return {k: v for k, v in row.items() if v is not None}


def aliases(v: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    own = v.get("vulnId")
    for a in v.get("aliases") or []:
        if not isinstance(a, dict):
            continue
        for key in ("cveId", "ghsaId", "osvId", "snykId", "sonatypeId", "vulnDbId"):
            val = a.get(key)
            if val and val != own and val not in out:
                out.append(val)
    return out


def score(v: Dict[str, Any]) -> Optional[float]:
    for key in ("cvssV3BaseScore", "cvssV4Score", "cvssV2BaseScore"):
        if v.get(key) is not None:
            return v[key]
    return None


def finding_row(f: Dict[str, Any], *, with_project: bool = False) -> Dict[str, Any]:
    c = f.get("component") or {}
    v = f.get("vulnerability") or {}
    a = f.get("analysis") or {}
    row: Dict[str, Any] = {
        "vuln": v.get("vulnId"),
        "source": v.get("source"),
        "severity": v.get("severity") or "UNASSIGNED",
        "cvss": score(v),
        "epss": v.get("epssScore"),
        "aliases": aliases(v) or None,
        "title": short(v.get("title") or v.get("description"), 120),
        "component": coordinates(c),
        "purl": c.get("purl"),
        "analysis": a.get("state") or "NOT_SET",
        "suppressed": True if a.get("isSuppressed") else None,
        "component_uuid": c.get("uuid"),
        "vulnerability_uuid": v.get("uuid"),
    }
    if with_project:
        row["project"] = c.get("projectName") and (
            f"{c['projectName']}@{c['projectVersion']}" if c.get("projectVersion") else c["projectName"])
        row["project_uuid"] = c.get("project")
    return {k: val for k, val in row.items() if val is not None}


def sort_findings(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(rows, key=lambda r: (severity_rank(r.get("severity")), -(r.get("cvss") or 0),
                                       -(r.get("epss") or 0), r.get("vuln") or ""))


def summarize_findings(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    rows = list(rows)
    by_sev = Counter(r.get("severity", "UNASSIGNED") for r in rows)
    by_state = Counter(r.get("analysis", "NOT_SET") for r in rows)
    return {
        "by_severity": {s: by_sev[s] for s in SEVERITIES if by_sev[s]},
        "by_analysis": {s: by_state[s] for s in ANALYSIS_STATES if by_state[s]},
    }


def vulnerability_view(v: Dict[str, Any]) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "vuln": v.get("vulnId"),
        "source": v.get("source"),
        "severity": v.get("severity"),
        "cvss_v3": v.get("cvssV3BaseScore"),
        "cvss_v3_vector": v.get("cvssV3Vector"),
        "cvss_v4": v.get("cvssV4Score"),
        "epss": v.get("epssScore"),
        "epss_percentile": v.get("epssPercentile"),
        "aliases": aliases(v) or None,
        "title": v.get("title"),
        "description": short(v.get("description"), 1200),
        "recommendation": short(v.get("recommendation"), 600),
        "cwes": [f"CWE-{c['cweId']}" for c in v.get("cwes") or [] if isinstance(c, dict) and c.get("cweId")] or None,
        "published": v.get("published"),
        "patched_versions": v.get("patchedVersions"),
        "vulnerable_versions": v.get("vulnerableVersions"),
        "references": short(v.get("references"), 600),
        "uuid": v.get("uuid"),
    }
    return {k: val for k, val in row.items() if val not in (None, "", [])}


def license_of(c: Dict[str, Any]) -> str:
    resolved = c.get("resolvedLicense") or {}
    return (resolved.get("licenseId") or resolved.get("name") or c.get("licenseExpression")
            or c.get("license") or "(none)")


def component_row(c: Dict[str, Any]) -> Dict[str, Any]:
    meta = c.get("repositoryMeta") or {}
    latest = meta.get("latestVersion")
    row: Dict[str, Any] = {
        "component": coordinates(c),
        "purl": c.get("purl"),
        "license": license_of(c),
        "latest_version": latest if latest and latest != c.get("version") else None,
        "internal": True if c.get("isInternal") or c.get("internal") else None,
        "uuid": c.get("uuid"),
    }
    m = c.get("metrics") or {}
    vulns = {s.lower(): m.get(s.lower()) for s in SEVERITIES[:4] if m.get(s.lower())}
    if vulns:
        row["vulnerabilities"] = vulns
    proj = c.get("project")
    if isinstance(proj, dict):
        row["project"] = project_label(proj)
        row["project_uuid"] = proj.get("uuid")
    return {k: v for k, v in row.items() if v is not None}


# SPDX ids that usually need a closer look in commercial software. A summary, not legal advice.
COPYLEFT_STRONG = ("GPL-", "AGPL-", "SSPL-", "OSL-", "EUPL-", "CPAL-", "RPL-", "SleepyCat")
COPYLEFT_WEAK = ("LGPL-", "MPL-", "EPL-", "CDDL-", "CPL-", "MS-RL", "APSL-")


_FAMILIES = ["permissive", "weak copyleft", "strong copyleft"]


def _family_of_id(spdx_id: str) -> int:
    if spdx_id.startswith(COPYLEFT_STRONG):
        return 2
    if spdx_id.startswith(COPYLEFT_WEAK):
        return 1
    return 0


def license_family(lic: str) -> str:
    """Rough family of an SPDX id or expression.

    ``A OR B`` counts as the more permissive choice (you may pick either); ``A AND B`` as the
    more restrictive one. ``WITH`` exceptions (e.g. Classpath-exception-2.0) lower a strong
    copyleft license to weak.
    """
    if lic == "(none)":
        return "unknown"
    expr = " ".join(lic.replace("(", " ").replace(")", " ").split())
    best = None
    for alternative in expr.split(" OR "):
        worst = 0
        for part in alternative.split(" AND "):
            spdx_id, _, exception = part.partition(" WITH ")
            fam = _family_of_id(spdx_id.strip())
            if exception and fam == 2:
                fam = 1
            worst = max(worst, fam)
        best = worst if best is None else min(best, worst)
    return _FAMILIES[best or 0]


def license_summary(components: List[Dict[str, Any]], examples: int = 3) -> Dict[str, Any]:
    groups: Dict[str, List[str]] = defaultdict(list)
    for c in components:
        groups[license_of(c)].append(coordinates(c))
    rows = []
    for lic, comps in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        rows.append({"license": lic, "family": license_family(lic), "components": len(comps),
                     "examples": sorted(comps)[:examples]})
    fam = Counter()
    for r in rows:
        fam[r["family"]] += r["components"]
    order = ["strong copyleft", "weak copyleft", "unknown", "permissive"]
    return {"by_family": {f: fam[f] for f in order if fam[f]}, "licenses": rows}


def violation_row(v: Dict[str, Any]) -> Dict[str, Any]:
    cond = v.get("policyCondition") or {}
    policy = cond.get("policy") or {}
    analysis = v.get("analysis") or {}
    comp = v.get("component") or {}
    condition = " ".join(str(x) for x in (cond.get("subject"), cond.get("operator"), cond.get("value")) if x)
    proj = v.get("project") if isinstance(v.get("project"), dict) else comp.get("project")
    row = {
        "type": v.get("type"),
        "policy": policy.get("name"),
        "state": policy.get("violationState"),
        "condition": condition or None,
        "component": coordinates(comp) if comp else None,
        "project": project_label(proj) if isinstance(proj, dict) else None,
        "analysis": analysis.get("analysisState") or "NOT_SET",
        "suppressed": True if analysis.get("isSuppressed") or analysis.get("suppressed") else None,
        "text": short(v.get("text"), 160),
        "uuid": v.get("uuid"),
    }
    return {k: val for k, val in row.items() if val is not None}
