# codex-skills

Codex Git project — the source tree for the Codex estate (`~/.codex/`).

Terminology, matching the sibling projects:

- `codex-skills` / `claude-skills` / `devin-skills` are Git projects.
- The **Codex estate** is the installed copy Codex loads: `~/.codex/`.
- The Claude estate is `~/.claude/`; the Devin estate is `~/.config/devin/`.

The project is the source of truth; the estate is an install artifact.

## Layout

- `AGENTS.md` — global Codex rules (`~/.codex/AGENTS.md`).
- `skills/` — custom skills, one directory per skill. Codex-only extras that
  do not exist upstream: `claude-advisor` (the codex-side advisor name),
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

## Install (project → estate)

```bash
./install.sh
```

Backs up touched paths to `~/.codex-backups/<ts>/`, rsyncs `hooks/` and
`skills/` (excluding tests), copies `AGENTS.md`, merges `hooks.json` entries,
and appends `[mcp_servers.gitnexus]` to `config.toml` when absent. Codex
requires hook trust: approve the hooks once via `/hooks` in an interactive
session, or run automation with `--dangerously-bypass-hook-trust`.

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
- `skills/claude-advisor/` — codex's own advisor skill (upstream's
  `codex-advisor` is the same role under the other harness's name)
- `skills/code-review/SKILL.md` — codex delegate frontmatter

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
