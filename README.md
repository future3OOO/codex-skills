# codex-skills

Codex Git project — the source tree for the Codex estate (`~/.codex/`).

Terminology, matching the sibling projects:

- `codex-skills` / `claude-skills` / `devin-skills` are Git projects.
- The **Codex estate** is the installed copy Codex loads: `~/.codex/`.
- The Claude estate is `~/.claude/`; the Devin estate is `~/.config/devin/`.

The project is the source of truth; the estate is an install artifact.

## Layout

- `AGENTS.md` — global Codex rules (`~/.codex/AGENTS.md`).
- `decisions.md` — tracked project decisions, reasons and delivery status;
  read at start/resume and update before handoff. It is not installed globally.
- `skills/` — custom skills, one directory per skill. Codex-only extras that
  do not exist upstream: `codex-advisor` (the codex-side advisor name),
  `frontend-design`, `setup-pre-commit`, `.system/` is excluded.
- `hooks/` — lifecycle hooks (workflow state lib, intake gate, quality gate,
  discipline re-arm).
- `hooks.json` — managed hook entries; install merges them into the live
  `~/.codex/hooks.json` without dropping entries it does not own (the
  GitNexus hook stays live-managed).
- `scripts/` — `estate_xform.py` + `sync-from-upstream.sh` /
  `sync-to-upstream.sh`.
- `docs/` — historical design docs.
- Tests exist in the repo for CI; `install.sh` excludes them from the estate.

## Developing this estate

Develop Codex skills and runtime in an isolated worktree. Seed two private estates
from one global snapshot: N keeps the unchanged installation; N+1 receives the
candidate changes. Retain extra skills and dependencies; give each estate its own
sessions, caches and workflow state. Bind each at `~/.codex` in its own process
environment; `CODEX_HOME` alone misses home-relative entrypoints. Keep normal tool
access and a read-only path to the global estate.

Refresh N+1 as changes develop. Verify the loaded skills,
hooks, CLI and native state; restart consumers retaining old code before claiming
candidate behavior. Use equivalent real N/N+1 operations to establish correction
and preservation. The same operations support A/B efficiency comparisons: compare
executions, handoffs, context use and latency for equivalent work and correctness.
Use separate test state and reuse applicable proof. Private testing does not
authorize shared installation.

## Test history

The port has independent Git history. Calibration tests replay original Claude
commits and retain their exact captured bytes and digests. CI fetches the pinned
history before either lane; prepare a fresh local checkout the same way:

```bash
git fetch --no-tags https://github.com/future3OOO/claude-skills.git 08074c7e727d26ce62b0a3f80899de76e34818ef:refs/calibration/claude-skills
```

This adds the historical objects without changing the checked-out source.

## Install (project → estate)

For a full project installation:

```bash
./install.sh
```

Backs up touched paths to `~/.codex-backups/<ts>/`, rsyncs `hooks/` and
`skills/` (excluding tests), copies `AGENTS.md`, merges `hooks.json` entries,
and appends `[mcp_servers.gitnexus]` to `config.toml` when absent. Codex
requires hook trust: approve the hooks once via `/hooks` in an interactive
session, or run automation with `--dangerously-bypass-hook-trust`.

### Scoped updates

For authorized shared-estate installation, merge first and use clean, updated `main`. Select only
the reviewed files mapped into the estate; exclude tests and `decisions.md`.
Inspect destination differences first; back up and merge unrelated local edits
and managed config entries instead of overwriting them. For direct file copies,
run from the checkout root (replace the example file list):

```bash
paths=(hooks/lib/workflow_state.py skills/repo-production-workflow/SKILL.md)
estate="${CODEX_HOME:-$HOME/.codex}"
backup="$HOME/.codex-backups/$(date +%Y%m%d-%H%M%S)"
rsync -acR --backup --backup-dir="$backup" -- "${paths[@]}" "$estate/" &&
  for path in "${paths[@]}"; do cmp -- "$path" "$estate/$path" || exit 1; done
```

Back up and remove only explicitly owned obsolete files. Record the source SHA,
selected paths and backup location; run the relevant existing probe against the
installed entrypoint. Leave other estate paths unchanged.

### External tool updates

`./install.sh` does not upgrade GitNexus, Repo Context Forge or SoulForge, and
preserves existing MCP configuration. Update each tool at its own source:

| Tool | Source checkout | Runtime |
| --- | --- | --- |
| GitNexus | `~/projects/GitNexus-dev` | `~/.local/share/gitnexus/current` |
| Repo Context Forge | `~/projects/repo-context-forge` | `~/.local/share/repo-context-forge/current` |
| SoulForge | `~/soulforge` | resolved `~/.local/bin/soulforge` |

Fetch `origin/main`, require a clean local `main`, then fast-forward with
`git merge --ff-only origin/main`. Build/install that exact SHA using the tool's
existing procedure; preserve local changes instead of resetting them. Verify the
installed executable/build matches the fetched SHA, not merely its version label.
Keep GitNexus CLI and Codex/Claude MCP on the same `current` build, and RCF's
plugin/workflow adapters on its `current` producer. Reconnect affected MCP clients
at a safe boundary and verify a real tool call. Preserve active work; an old
connection awaiting reload remains pending, not an updated runtime.

## Syncing with upstream (claude-skills)

Upstream remote: `https://github.com/future3OOO/claude-skills` (`claude`).
The remote is named `claude`, not `upstream`, on purpose: Repo Context Forge's
base detection tries `upstream/main` first, and this repo shares no history
with claude-skills, so an `upstream` remote would resolve a ref that yields
no merge base.
Last synced upstream commit is recorded in `.upstream-sync`.

```bash
./scripts/sync-from-upstream.sh   # upstream -> staged transform
./scripts/sync-to-upstream.sh <paths...>   # selected files -> upstream PR branch
```

`sync-from-upstream.sh` fetches `claude/main`, diffs it against
`.upstream-sync`, runs each changed file through `scripts/estate_xform.py
to-codex`, stages the results, and reports **diverged files** for manual
merge. Any residual `claude`/`devin` hits the transform could not handle are
printed to stderr.

Diverged files (manual merge — sync will not overwrite):

- `AGENTS.md` — codex rules file (own conventions: `$skill` invocation,
  `spawn_agent`/`agent_type` subagent policy, RCF path)
- `config.toml` / `hooks.json` — TOML config and live-managed hook entries
- `README.md`, `install.sh`, `decisions.md`, `mcp_config.json`
- `hooks/lib/hook_input.py` — codex payload dialect (`apply_patch` patch
  headers, `Bash` write-target detection)
- `skills/codex-advisor/` — codex's own advisor skill (upstream's
  `codex-advisor` is the same role under the other harness's name)
- `skills/code-review/SKILL.md` — codex delegate frontmatter
- `skills/production-code/scripts/test_code_quality_gate.py` — captured historical
  calibration literals must retain their original spelling and pinned digests

## Hook event map (Codex)

Codex fires the full Claude event set with the same payload contract
(`tool_name`, `tool_input`, `cwd`, `session_id`, `hook_event_name`,
`tool_use_id`, `source`) and the same output envelope
(`hookSpecificOutput.additionalContext`). Differences that matter:

- Edit surfaces: `apply_patch` carries the patch text in
  `tool_input.command`; writes through shell arrive as `Bash` with the
  command in `tool_input.command`. Gate matchers must cover both.
- `SessionStart` matchers filter `source` directly: `startup|resume|compact|clear`.
- Hooks require trust approval (`/hooks`, or `--dangerously-bypass-hook-trust`
  for automation).

## Upstream Context

Several skills are derived from or influenced by Matt Pocock's public skills
repo: https://github.com/future3OOO/skills
