"""Decide which packages to release.

    python release_plan.py <tag>     # a pushed tag: release that one package
    python release_plan.py           # a push to main: every package whose version has no tag yet

Prints a JSON list of {"tag", "pkg", "ver", "dir", "kind"} for the release job's matrix.
Existing tags are read with `git tag`, so check out with fetch-depth 0.
"""

import json
import subprocess
import sys
from pathlib import Path

try:
    import tomllib
except ImportError:  # pragma: no cover - Python < 3.11
    tomllib = None

ROOT = Path(__file__).resolve().parents[2]


def packages():
    for d in sorted((ROOT / "packages").iterdir()):
        if (d / "pyproject.toml").is_file():
            data = tomllib.loads((d / "pyproject.toml").read_text(encoding="utf-8"))["project"]
            yield d, "python", data["name"], data["version"]
        elif (d / "package.json").is_file():
            data = json.loads((d / "package.json").read_text(encoding="utf-8"))
            if data.get("private"):
                continue
            yield d, "node", data["name"], data["version"]


def entry(d, kind, name, ver):
    tag_name = name.lstrip("@").replace("/", "-")
    return {"tag": f"{tag_name}-v{ver}", "pkg": name, "ver": ver,
            "dir": str(d.relative_to(ROOT)), "kind": kind}


def main(argv):
    if argv:
        tag = argv[0]
        for d, kind, name, ver in packages():
            for alias in {name.lstrip("@").replace("/", "-"), d.name}:
                if tag == f"{alias}-v{ver}":
                    e = entry(d, kind, name, ver)
                    e["tag"] = tag
                    return [e]
        sys.exit(f"tag {tag} does not match any package name and version under packages/")
    tags = set(subprocess.run(["git", "tag"], cwd=ROOT, capture_output=True, text=True,
                              check=True).stdout.split())
    return [e for e in (entry(*p) for p in packages()) if e["tag"] not in tags]


if __name__ == "__main__":
    print(json.dumps(main(sys.argv[1:])))
