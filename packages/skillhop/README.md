# skillhop

Install the skills that MCP servers serve into the agent you actually use. MCP servers can now
ship Agent Skills (`SKILL.md` folders) over the
[Skills extension, SEP-2640](https://modelcontextprotocol.io/seps/2640-skills-extension), but
most agents (Claude Desktop and claude.ai, Cursor, Gemini CLI, GitHub Copilot, goose,
OpenCode...) don't load them from the server yet.

skillhop fetches a server's skills, checks every file against the size and SHA-256 digest the
server published, and writes them into your agent's skills folder (or a zip for claude.ai),
with a lock file so `skillhop check` can tell you when the server changes them.

```bash
pip install skillhop        # or: uvx skillhop ...
```

Python 3.9+, one dependency (PyYAML). Works with any server that implements the extension
over stdio or Streamable HTTP, on the 2026-07-28 protocol or the older `initialize` one.
Tested against FastMCP 4.1 (`SkillsExtension`) and the official TypeScript SDK 2.4
(`@modelcontextprotocol/server/ext/skills`).

## Example

```bash
# A server from your Claude Code / Cursor / VS Code / Claude Desktop config, by name:
skillhop ls docs

# ...or a URL, or a command:
skillhop ls https://mcp.example.com/mcp -H "Authorization: Bearer ${DOCS_TOKEN}"
skillhop ls --cmd "npx -y @acme/mcp-server"

# Install two skills into this project's .claude/skills
skillhop add docs pdf-processing acme/billing/refunds

# Into Cursor and Codex (both read .agents/skills), or your home folder with --global
skillhop add docs pdf-processing -a cursor -a codex
skillhop add docs --all -a gemini-cli --global

# For claude.ai or Claude Desktop: write pdf-processing.zip, then upload it in
# Claude's Skills settings
skillhop add docs pdf-processing --zip ./skill-zips

# Later: did the server change anything? (exit code 1 if so; good in CI)
skillhop check
skillhop update
```

`skillhop add` shows what you are about to install (files, scripts the agent could run,
frontmatter it will drop) and asks before writing anything:

```
  pdf-processing  from docs (skill://pdf-processing/SKILL.md)
    Extract, fill, and assemble PDF documents
    4 files, 1,316 bytes: SKILL.md, templates/invoice.md, scripts/extract.py, assets/logo.png
    scripts the agent may run (read them first): scripts/extract.py
    will remove from frontmatter: allowed-tools: "Bash(python:*)"

  into /home/me/project/.claude/skills
  Skills are instructions for your agent from a third party. Install only from servers you trust.

Install 1 skill? [y/N]
```

Read a skill before installing it with `skillhop show docs pdf-processing`.

## What it checks

Everything the extension asks a host to check, before any file is written:

- **Integrity.** Each file must match the `size` and `sha256` digest in the skill's entry, and
  `SKILL.md`'s frontmatter must equal the entry's `frontmatter`. One mismatch and nothing is
  installed. Files are written to a temporary folder and moved into place at the end.
- **The entry itself.** The URI must end in `/SKILL.md`, its last segment must be the skill's
  `name` (lowercase, digits, single hyphens), `resources` must list `SKILL.md` and stay within
  512 files and 16 MiB. File URIs must stay inside the skill folder: `..`, `%2e%2e`, `%2F`,
  backslashes, drive letters, Windows reserved names and paths that differ only in case are
  refused. Invalid entries are skipped with a warning.
- **No shadowing.** A folder skillhop didn't install is never replaced. Two skills with the
  same name (`acme/billing/refunds` and `acme/support/refunds`) must be picked by path, and the
  second one installed with `--as support-refunds`.
- **No permission grants.** `allowed-tools` and `hooks` are removed from the frontmatter
  (`--keep-permissions` keeps them). Files are written without the executable bit.
- **Origin stays visible.** `(from MCP server docs)` is added to the description, so the model
  can tell it is not one of your own skills (`--no-origin` to skip).
- **Dynamic skills** (`"resources": "dynamic"`) cannot be verified, so they need
  `--allow-dynamic`; their files are found with `resources/directory/read` when the server
  supports it.

## Commands

| Command | |
| --- | --- |
| `skillhop servers` | MCP servers found in Claude Code, Cursor, VS Code, Gemini CLI, Windsurf, Codex and Claude Desktop configs |
| `skillhop ls SERVER [--json]` | skills the server lists, with file counts and sizes |
| `skillhop show SERVER SKILL` | print the verified `SKILL.md` |
| `skillhop add SERVER SKILL... \| --all` | install (`--as NAME`, `--zip DIR`, `--yes`, `--force`, `--keep-permissions`, `--no-origin`, `--allow-dynamic`) |
| `skillhop status` | installed skills and whether you edited them |
| `skillhop check [NAME...]` | ask the servers whether installed skills changed: exit 0 none, 1 changed or gone, 2 error |
| `skillhop update [NAME...]` | reinstall the ones that changed (your edits are kept unless `--force`) |
| `skillhop remove NAME...` | delete skills skillhop installed |

`SERVER` is a URL, a server name from your MCP client configs, or `--cmd "COMMAND"`. A skill
is a name, a path such as `acme/billing/refunds`, or a full URI such as
`skill://acme/billing/refunds/SKILL.md` (for skills a server serves but doesn't list).
`-H "Name: value"` adds an HTTP header and `-e NAME=value` an environment variable for
`--cmd`; `${VAR}` and `${VAR:-default}` are read from your environment.

Where skills go (`-a/--agent`, repeatable; default `claude-code`; `--dir PATH` for anything else):

| Agent | Project folder | With `--global` |
| --- | --- | --- |
| `claude-code` | `.claude/skills` | `~/.claude/skills` |
| `codex`, `agents` | `.agents/skills` | `~/.agents/skills` |
| `cursor` | `.agents/skills` | `~/.cursor/skills` |
| `gemini-cli` | `.agents/skills` | `~/.gemini/skills` |
| `github-copilot` | `.agents/skills` | `~/.copilot/skills` |
| `goose` | `.goose/skills` | `~/.config/goose/skills` |
| `opencode` | `.agents/skills` | `~/.config/opencode/skills` |

Each skills folder gets a `.skillhop.json` lock file: the server, the skill URI, the digests
the server published and the hashes of what was written. Secrets are not saved: a header or
variable is stored only as a `${VAR}` template; a literal value is left out, and `check` asks
you to pass it again. For servers found by name in your client config, only the name is saved.

## From Python

```python
from skillhop import ServerSpec, entries, fetch, install, skills_dir

spec = ServerSpec(label="docs", url="https://mcp.example.com/mcp",
                  headers={"Authorization": "Bearer ${DOCS_TOKEN}"})
with spec.connect() as client:
    skills, warnings = entries(client, ["pdf-processing"])
    install(fetch(client, skills[0]), skills_dir("claude-code"), spec.save())
```

## Limits

- An installed skill is a copy. Your agent loads it as a local skill, without the per-server
  tagging and approval a host with native support would give it; that is why skillhop asks
  first, drops permission fields and tags the description. Install only from servers you trust.
- OAuth sign-in is not built in: pass a token with `-H "Authorization: Bearer ..."`.
- The old HTTP+SSE transport is not supported (the extension needs Streamable HTTP or stdio).
- Requests that need user input (`input_required`) are not answered; skillhop stops with an
  error.
- Frontmatter is compared after normalizing YAML types (`yes`/`true`, `1.0`/`1`, dates), so a
  server that renders YAML a little differently is not flagged as tampered.
- Agent folder paths change; check your agent's docs and use `--dir` if yours differs.

## Why this exists

The Skills extension went Final on 2026-09-13 and servers ship it (Hugging Face, Cloudflare,
n8n, Outline, Tableau...), but host support is patchy. These requests ask for it:

- [anthropics/claude-ai-mcp#1160](https://github.com/anthropics/claude-ai-mcp/issues/1160):
  Claude chat, Claude Desktop and Cowork can't load skills served over MCP (28 upvotes).
- [openai/codex#48000](https://github.com/openai/codex/issues/48000): support SEP-2640 in Codex.
- [anthropics/claude-code#106550](https://github.com/anthropics/claude-code/issues/106550):
  add support for skills over MCP.
- [aaif-goose/goose#12068](https://github.com/aaif-goose/goose/issues/12068),
  [microsoft/vscode#338300](https://github.com/microsoft/vscode/issues/338300): the same for
  goose and GitHub Copilot.

Existing tools read served skills but don't install them for other agents: `mcpc skills-get`
prints one file, and FastMCP's `download_skill` targets its own pre-SEP manifest format without
digest checks. skillhop is the missing step: verified install, any agent, and a way to notice
changes.

MIT licensed. Part of [kitforge](https://github.com/srikrishnaL/kitforge).
