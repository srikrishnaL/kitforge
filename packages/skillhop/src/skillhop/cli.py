"""skillhop command line."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, TextIO

from . import __version__
from .install import (AGENTS, Status, compare, entries, fetch, install, local_hashes,
                      read_lock, remove, skills_dir, to_zip)
from .rpc import Client, McpError, TransportError
from .servers import ServerNotFound, ServerSpec, all_configured, parse_pairs, resolve
from .skills import PERMISSION_FIELDS, Skill, SkillError, parse_entry, summarize


class Abort(Exception):
    pass


def _server_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--cmd", metavar="COMMAND", help='run a stdio server, e.g. --cmd "npx -y @acme/mcp"')
    p.add_argument("--header", "-H", action="append", default=[], metavar="NAME: VALUE",
                   help="HTTP header for URL servers; ${VAR} is read from the environment")
    p.add_argument("--env", "-e", action="append", default=[], metavar="NAME=VALUE",
                   help="environment variable for --cmd servers")
    p.add_argument("--label", help="name to show for the server (default: config name or host)")
    p.add_argument("--timeout", type=float, default=30.0, help="seconds per request (default 30)")
    p.add_argument("--verbose", "-v", action="store_true", help="show the server's stderr")


def _target_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--agent", "-a", action="append", choices=sorted(AGENTS), metavar="AGENT",
                   help=f"where to install: {', '.join(AGENTS)} (repeatable; default claude-code)")
    p.add_argument("--global", "-g", dest="global_", action="store_true",
                   help="use the agent's folder in your home directory instead of this project")
    p.add_argument("--dir", "-d", action="append", type=Path, metavar="PATH",
                   help="install into this skills folder instead (repeatable)")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="skillhop",
        description="Install the skills that MCP servers serve (SEP-2640 Skills extension) into "
                    "Claude Code, Codex, Cursor, Gemini CLI, Copilot, goose and OpenCode, verified "
                    "against the server's digests.")
    ap.add_argument("--version", action="version", version=f"skillhop {__version__}")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("ls", help="list the skills a server serves")
    p.add_argument("server", nargs="?", help="URL or a server name from your MCP client config")
    p.add_argument("--json", action="store_true", help="print the raw entries as JSON")
    _server_args(p)

    p = sub.add_parser("show", help="print a skill's SKILL.md (verified) without installing it")
    p.add_argument("args", nargs="+", metavar="[SERVER] SKILL")
    _server_args(p)

    p = sub.add_parser("add", help="install skills from a server")
    p.add_argument("args", nargs="*", metavar="[SERVER] SKILL",
                   help="server (URL or config name; omit with --cmd), then skill names, paths or URIs")
    p.add_argument("--all", action="store_true", help="install every skill the server lists")
    p.add_argument("--as", dest="rename", metavar="NAME", help="install the skill under another name")
    p.add_argument("--yes", "-y", action="store_true", help="don't ask for confirmation")
    p.add_argument("--force", action="store_true",
                   help="replace a folder that skillhop did not install, or that you edited")
    p.add_argument("--keep-permissions", action="store_true",
                   help=f"keep {', '.join(PERMISSION_FIELDS)} in the frontmatter (removed by default)")
    p.add_argument("--no-origin", action="store_true",
                   help="don't add '(from MCP server X)' to the skill's description")
    p.add_argument("--allow-dynamic", action="store_true",
                   help='install skills with resources "dynamic" (content cannot be verified)')
    p.add_argument("--zip", type=Path, metavar="FOLDER",
                   help="write <name>.zip files for Claude.ai / Claude Desktop upload instead")
    _server_args(p)
    _target_args(p)

    p = sub.add_parser("status", help="list installed skills and whether you edited them")
    _target_args(p)

    p = sub.add_parser("check", help="ask the servers whether installed skills changed (exit 1 if so)")
    p.add_argument("names", nargs="*")
    p.add_argument("--header", "-H", action="append", default=[], metavar="NAME: VALUE")
    p.add_argument("--env", "-e", action="append", default=[], metavar="NAME=VALUE")
    p.add_argument("--timeout", type=float, default=30.0)
    p.add_argument("--verbose", "-v", action="store_true")
    _target_args(p)

    p = sub.add_parser("update", help="reinstall installed skills that changed on the server")
    p.add_argument("names", nargs="*")
    p.add_argument("--yes", "-y", action="store_true")
    p.add_argument("--force", action="store_true", help="replace skills you edited")
    p.add_argument("--header", "-H", action="append", default=[], metavar="NAME: VALUE")
    p.add_argument("--env", "-e", action="append", default=[], metavar="NAME=VALUE")
    p.add_argument("--timeout", type=float, default=30.0)
    p.add_argument("--verbose", "-v", action="store_true")
    _target_args(p)

    p = sub.add_parser("remove", help="delete skills that skillhop installed")
    p.add_argument("names", nargs="+")
    _target_args(p)

    sub.add_parser("servers", help="list the MCP servers found in your client configs")
    return ap


# --- helpers -----------------------------------------------------------------------------

def _dirs(args: argparse.Namespace) -> List[Path]:
    if args.dir:
        return [d.expanduser() for d in args.dir]
    seen: List[Path] = []
    for agent in args.agent or ["claude-code"]:
        d = skills_dir(agent, args.global_)
        if d not in seen:
            seen.append(d)
    return seen


def _spec(args: argparse.Namespace, server: Optional[str]) -> ServerSpec:
    return resolve(server, args.cmd, parse_pairs(args.header, ":", "--header"),
                   parse_pairs(args.env, "=", "--env"), args.label)


def _split_server(args: argparse.Namespace, items: Sequence[str]):
    items = list(items)
    if args.cmd:
        return None, items
    if not items:
        raise Abort("say which server: a URL, a server name from your MCP config, or --cmd")
    return items[0], items[1:]


def _connect(spec: ServerSpec, args: argparse.Namespace) -> Client:
    return spec.connect(timeout=args.timeout, show_stderr=args.verbose)


def _confirm(question: str, yes: bool, out: TextIO) -> bool:
    if yes:
        return True
    if not sys.stdin.isatty():
        raise Abort("not a terminal, so skillhop can't ask; read the summary above and rerun with --yes")
    out.write(f"{question} [y/N] ")
    out.flush()
    return sys.stdin.readline().strip().lower() in ("y", "yes")


def _describe(skill: Skill, label: str, out: TextIO, removed: Sequence[str] = ()) -> None:
    info = summarize(skill)
    out.write(f"\n  {skill.name}  from {label} ({skill.uri})\n")
    out.write(f"    {skill.description}\n")
    if skill.dynamic:
        out.write("    content is generated on the server (\"dynamic\"): it cannot be verified\n")
    else:
        files = ", ".join(f.path for f in skill.files[:6]) + (" ..." if len(skill.files) > 6 else "")
        out.write(f"    {info['files']} files, {info['bytes']:,} bytes: {files}\n")
    if info["scripts"]:
        out.write(f"    scripts the agent may run (read them first): {', '.join(info['scripts'])}\n")
    if info["nested"]:
        out.write(f"    contains nested skills: {', '.join(info['nested'])}\n")
    for k in removed:
        if k in skill.frontmatter:
            out.write(f"    will remove from frontmatter: {k}: {json.dumps(skill.frontmatter[k])}\n")


def _print_problems(problems: Sequence[str], err: TextIO) -> None:
    for p in problems:
        err.write(f"skillhop: warning: {p}\n")


# --- commands ----------------------------------------------------------------------------

def cmd_ls(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    spec = _spec(args, args.server)
    with _connect(spec, args) as client:
        raw = client.list_skills()
        if args.json:
            out.write(json.dumps(raw, indent=2) + "\n")
            return 0
        if client.skills_extension is None:
            err.write(f"skillhop: warning: {spec.label} does not declare the Skills extension\n")
        if not raw:
            out.write(f"{spec.label} lists no skills (a server may still serve skills by URI)\n")
            return 0
        for e in raw:
            try:
                s = parse_entry(e)
            except SkillError as exc:
                out.write(f"  ! invalid entry: {exc}\n")
                continue
            size = "dynamic" if s.dynamic else f"{len(s.files)} files, {s.total_size:,} bytes"
            out.write(f"  {s.skill_path:<32} {size}\n      {s.description}\n")
    return 0


def cmd_show(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    server, wanted = _split_server(args, args.args)
    if len(wanted) != 1:
        raise Abort("show takes one skill")
    spec = _spec(args, server)
    with _connect(spec, args) as client:
        skills, problems = entries(client, wanted)
        _print_problems(problems, err)
        f = fetch(client, skills[0], tag_origin=False, keep_permissions=True, allow_dynamic=True)
    if skills[0].dynamic:
        err.write("skillhop: note: dynamic skill, content not verified\n")
    out.write(f.files["SKILL.md"].decode("utf-8"))
    return 0


def cmd_add(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    server, wanted = _split_server(args, args.args)
    if not wanted and not args.all:
        raise Abort("name the skills to install, or pass --all (see them with `skillhop ls`)")
    if args.rename and (args.all or len(wanted) != 1):
        raise Abort("--as works with exactly one skill")
    spec = _spec(args, server)
    dirs = [] if args.zip else _dirs(args)
    with _connect(spec, args) as client:
        skills, problems = entries(client, wanted, all_=args.all)
        _print_problems(problems, err)
        if not skills:
            out.write(f"{spec.label} lists no skills\n")
            return 0
        removed = () if args.keep_permissions else PERMISSION_FIELDS
        for s in skills:
            _describe(s, spec.label, out, removed)
        where = str(args.zip) if args.zip else ", ".join(str(d) for d in dirs)
        out.write(f"\n  into {where}\n")
        out.write("  Skills are instructions for your agent from a third party. Install only from "
                  "servers you trust.\n\n")
        if not _confirm(f"Install {len(skills)} skill{'s' if len(skills) != 1 else ''}?", args.yes, out):
            out.write("Nothing installed.\n")
            return 1
        options = {"tag_origin": not args.no_origin, "keep_permissions": args.keep_permissions,
                   "allow_dynamic": args.allow_dynamic}
        failed = 0
        for s in skills:
            try:
                f = fetch(client, s, rename=args.rename, **options)
            except (SkillError, McpError, TransportError) as exc:
                err.write(f"skillhop: {s.name}: not installed: {exc}\n")
                failed += 1
                continue
            if args.zip:
                args.zip.mkdir(parents=True, exist_ok=True)
                path = args.zip / f"{f.name}.zip"
                path.write_bytes(to_zip(f))
                out.write(f"wrote {path}\n")
                continue
            for d in dirs:
                try:
                    target = install(f, d, spec.save(), force=args.force, options=options)
                except SkillError as exc:
                    err.write(f"skillhop: {f.name}: not installed in {d}: {exc}\n")
                    failed += 1
                    continue
                note = f" (removed {', '.join(f.removed)})" if f.removed else ""
                out.write(f"installed {f.name} -> {target}{note}\n")
    return 1 if failed else 0


def cmd_status(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    any_ = False
    for d in _dirs(args):
        lock = read_lock(d)
        for name, rec in sorted(lock["skills"].items()):
            any_ = True
            folder = d / name
            if not folder.is_dir():
                state = "missing"
            elif local_hashes(folder) != rec.get("files"):
                state = "edited locally"
            else:
                state = "as installed"
            out.write(f"  {name:<28} {rec['server'].get('label')} {rec['uri']}  [{state}]  {folder}\n")
    if not any_:
        out.write("no skills installed by skillhop here\n")
    return 0


def _check(args: argparse.Namespace, err: TextIO):
    """Yield (dir, name, record, Status, fresh Skill or None, client or None) per installed skill."""
    headers = parse_pairs(args.header, ":", "--header")
    env = parse_pairs(args.env, "=", "--env")
    results = []
    clients: Dict[str, Any] = {}
    try:
        for d in _dirs(args):
            lock = read_lock(d)
            for name, rec in sorted(lock["skills"].items()):
                if args.names and name not in args.names:
                    continue
                folder = d / name
                edited = folder.is_dir() and local_hashes(folder) != rec.get("files")
                key = json.dumps(rec["server"], sort_keys=True)
                try:
                    if key not in clients:
                        clients[key] = None
                        spec = ServerSpec.load(rec["server"], headers, env)
                        clients[key] = (spec, spec.connect(timeout=args.timeout,
                                                           show_stderr=args.verbose))
                    if clients[key] is None:
                        raise TransportError("could not connect (see above)")
                    spec, client = clients[key]
                    try:
                        fresh = parse_entry(client.get_skill(rec["uri"]))
                    except McpError as exc:
                        if exc.code == -32602:
                            results.append((d, name, rec, Status(name, "gone",
                                            f"{rec['uri']} is no longer served"), None, None))
                            continue
                        raise
                    diff = compare(rec, fresh)
                    if diff and not diff.startswith("dynamic"):
                        st = Status(name, "changed", diff + ("; you also edited it locally" if edited
                                                             else ""), edited)
                    elif edited:
                        st = Status(name, "edited", "you changed files after installing")
                    else:
                        st = Status(name, "ok", diff)
                    results.append((d, name, rec, st, fresh, (spec, client)))
                except (ServerNotFound, TransportError, McpError, SkillError) as exc:
                    results.append((d, name, rec, Status(name, "error", str(exc)), None, None))
        return results, clients
    except BaseException:
        for c in clients.values():
            if c:
                c[1].close()
        raise


def cmd_check(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    results, clients = _check(args, err)
    try:
        if not results:
            out.write("no skills installed by skillhop here\n")
            return 0
        for d, name, rec, st, _, _ in results:
            out.write(f"  {name:<28} {st.state:<8} {st.detail}\n")
        if any(r[3].state == "error" for r in results):
            return 2
        return 1 if any(r[3].state in ("changed", "gone") for r in results) else 0
    finally:
        for c in clients.values():
            if c:
                c[1].close()


def cmd_update(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    results, clients = _check(args, err)
    try:
        todo = [r for r in results if (r[3].state == "changed" and (args.force or not r[3].edited))
                or (args.force and r[3].state == "edited")]
        for d, name, rec, st, _, _ in results:
            if st.state in ("error", "gone"):
                err.write(f"skillhop: {name}: {st.state}: {st.detail}\n")
            elif st.state == "changed" and st.edited and not args.force:
                err.write(f"skillhop: {name}: changed on the server, but you edited it; "
                          "not updated (pass --force to replace your edits)\n")
        if not todo:
            out.write("everything is up to date\n")
            return 2 if any(r[3].state == "error" for r in results) else 0
        for d, name, rec, st, fresh, (spec, _client) in todo:
            removed = () if rec.get("options", {}).get("keep_permissions") else PERMISSION_FIELDS
            _describe(fresh, spec.label, out, removed)
            out.write(f"    {st.detail}\n")
        out.write("\n")
        if not _confirm(f"Update {len(todo)} skill{'s' if len(todo) != 1 else ''}?", args.yes, out):
            out.write("Nothing updated.\n")
            return 1
        failed = 0
        for d, name, rec, st, fresh, (spec, client) in todo:
            opts = rec.get("options") or {}
            try:
                f = fetch(client, fresh, rename=name if name != fresh.name else None,
                          tag_origin=opts.get("tag_origin", True),
                          keep_permissions=opts.get("keep_permissions", False),
                          allow_dynamic=opts.get("allow_dynamic", False))
                f.name = name
                install(f, d, rec["server"], force=True, options=opts)
                out.write(f"updated {name} ({st.detail})\n")
            except (SkillError, McpError, TransportError) as exc:
                err.write(f"skillhop: {name}: not updated: {exc}\n")
                failed += 1
        return 1 if failed else 0
    finally:
        for c in clients.values():
            if c:
                c[1].close()


def cmd_remove(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    failed = 0
    for d in _dirs(args):
        for name in args.names:
            try:
                out.write(f"removed {remove(d, name)}\n")
            except SkillError as exc:
                err.write(f"skillhop: {exc}\n")
                failed += 1
    return 1 if failed else 0


def cmd_servers(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    found = all_configured()
    if not found:
        out.write("no MCP servers found in Claude Code, Cursor, VS Code, Gemini CLI, Windsurf, "
                  "Codex or Claude Desktop configs\n")
    for name, client, path in found:
        out.write(f"  {name:<28} {client}  ({path})\n")
    return 0


COMMANDS = {"ls": cmd_ls, "show": cmd_show, "add": cmd_add, "status": cmd_status,
            "check": cmd_check, "update": cmd_update, "remove": cmd_remove, "servers": cmd_servers}


def main(argv: Optional[Sequence[str]] = None, out: TextIO = sys.stdout, err: TextIO = sys.stderr) -> int:
    args = build_parser().parse_args(argv)
    try:
        return COMMANDS[args.command](args, out, err)
    except (Abort, SkillError, ServerNotFound, TransportError, McpError) as exc:
        err.write(f"skillhop: {exc}\n")
        return 2
    except KeyboardInterrupt:
        err.write("skillhop: interrupted\n")
        return 130


if __name__ == "__main__":
    sys.exit(main())
