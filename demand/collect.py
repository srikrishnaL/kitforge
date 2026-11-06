#!/usr/bin/env python3
"""Score every idea in ideas.json by real demand, and write scores.json.

Uses the GitHub search API through the `gh` CLI (it must be logged in).
For each idea it measures:

* requests: open issues matching the idea's request query, and the most
  upvoted of them (people blocked today);
* gap: how strong the best existing project is (stars) and whether it is
  still maintained (last push).

score = 55% demand + 45% gap, from 0 to 100. Every number is saved with the
query that produced it, so each pick can be traced back to its evidence.

GitHub search is keyword search, so a score is a shortlist, not a verdict:
before building, open the top requests and the "best existing" repo and
check they really are about the idea (a wrong match inflates the gap).

Usage:  python demand/collect.py [--only ID ...]
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).parent


def gh_search(kind: str, query: str, sort: str, per_page: int = 5) -> dict:
    cmd = ["gh", "api", "-X", "GET", f"search/{kind}", "-f", f"q={query}",
           "-f", f"sort={sort}", "-f", f"per_page={per_page}"]
    for attempt in range(3):
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode == 0:
            return json.loads(res.stdout)
        if "rate limit" in (res.stderr + res.stdout).lower():
            time.sleep(30 * (attempt + 1))
            continue
        raise RuntimeError(f"gh search failed for {query!r}: {res.stderr.strip()}")
    raise RuntimeError("GitHub search rate limit; try again later")


def months_since(iso: str) -> float:
    then = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return (datetime.now(timezone.utc) - then).days / 30.4


def score_idea(idea: dict) -> dict:
    q = idea["request_query"]
    if "is:issue" not in q and "is:pr" not in q:
        q += " is:issue"
    issues = gh_search("issues", q, "reactions")
    time.sleep(2)  # stay well under the search rate limit
    repos = gh_search("repositories", idea["repo_query"], "stars")
    time.sleep(2)

    top_issues = [
        {"title": i["title"], "url": i["html_url"], "reactions": i["reactions"]["total_count"]}
        for i in issues.get("items", [])[:5]
    ]
    top_reactions = max([i["reactions"] for i in top_issues] or [0])
    best = (repos.get("items") or [None])[0]
    best_stars = best["stargazers_count"] if best else 0
    stale = months_since(best["pushed_at"]) if best else None

    demand = 0.5 * min(1.0, math.log10(1 + issues.get("total_count", 0)) / 4) \
        + 0.5 * min(1.0, math.log10(1 + top_reactions) / 2.5)
    gap = 1 - min(1.0, math.log10(1 + best_stars) / 3.5)
    if stale is not None and stale > 12:
        gap = max(gap, 0.7)
    score = round(100 * (0.55 * demand + 0.45 * gap))

    return {
        "id": idea["id"],
        "title": idea["title"],
        "status": idea.get("status", "idea"),
        "score": score,
        "demand": round(demand, 3),
        "gap": round(gap, 3),
        "open_requests": issues.get("total_count", 0),
        "top_requests": top_issues,
        "best_existing": None if not best else {
            "repo": best["full_name"], "url": best["html_url"], "stars": best_stars,
            "months_since_push": round(stale, 1),
        },
        "existing_projects": repos.get("total_count", 0),
        "queries": {"requests": q, "existing": idea["repo_query"]},
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", help="score only these idea ids")
    args = ap.parse_args()

    ideas = json.loads((HERE / "ideas.json").read_text())
    out_path = HERE / "scores.json"
    old = {}
    if out_path.exists():
        old = {s["id"]: s for s in json.loads(out_path.read_text()).get("ideas", [])}

    results = dict(old)
    for idea in ideas:
        if args.only and idea["id"] not in args.only:
            continue
        try:
            results[idea["id"]] = score_idea(idea)
            print(f"{results[idea['id']]['score']:>3}  {idea['id']}", file=sys.stderr)
        except RuntimeError as exc:
            print(f"skip {idea['id']}: {exc}", file=sys.stderr)

    known = {i["id"]: i for i in ideas}
    for r in results.values():  # keep status in sync with ideas.json
        if r["id"] in known:
            r["status"] = known[r["id"]].get("status", "idea")
    ranked = sorted((r for r in results.values() if r["id"] in known),
                    key=lambda r: -r["score"])
    out_path.write_text(json.dumps({
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "method": "score = 55% demand (open requests, top upvotes) + 45% gap (best existing project's stars, staleness)",
        "ideas": ranked,
    }, indent=2) + "\n")
    nxt = next((r for r in ranked if r["status"] == "idea"), None)
    if nxt:
        print(f"next to build: {nxt['id']} (score {nxt['score']})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
