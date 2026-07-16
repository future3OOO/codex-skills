#!/usr/bin/env bash
set -euo pipefail

usage() {
  printf 'Usage: %s [--provider claude|codex] [--slug name] [--phase preflight-advice|precommit-challenge] [--cwd path] [--fresh] [--budget words] [--base-ref ref] [--model model] [--fallback-model model] [--codex-model model] [--write] [--full-tools] -- "question"\n' "$0" >&2
}

provider="${CLAUDE_ADVISOR_PROVIDER:-${ADVISOR_PROVIDER:-claude}}"
slug="default"
phase=""
cwd="$PWD"
fresh=0
budget="300"
base_ref=""
advisor_model="${CLAUDE_ADVISOR_MODEL:-claude-fable-5}"
advisor_fallback_model="${CLAUDE_ADVISOR_FALLBACK_MODEL:-claude-fable-5}"
codex_model="${CODEX_ADVISOR_MODEL:-}"
write_mode=0
full_tools=0

new_session_id() {
  if [[ -r /proc/sys/kernel/random/uuid ]]; then
    cat /proc/sys/kernel/random/uuid
  else
    uuidgen
  fi
}

phase_slug_warning() {
  local normalized_slug="$1"
  local lower_slug
  lower_slug="$(printf '%s' "$normalized_slug" | tr '[:upper:]' '[:lower:]')"
  local phase_pattern='(^|[_.-])(pre-edit|preedit|pre-commit|precommit|post-edit|postedit|post-commit|postcommit|review|challenge|final|preflight)([_.-]|$)'

  if [[ "$lower_slug" =~ $phase_pattern ]]; then
    printf 'phase-slug:%s' "${BASH_REMATCH[2]}"
  else
    printf 'none'
  fi
}

resolve_task_session() {
  local raw_slug="$1"
  local fresh_session="$2"
  local advisor_phase="$3"
  local resolved_normalized_slug="${raw_slug//[^A-Za-z0-9_.-]/_}"
  local resolved_cwd_key
  resolved_cwd_key="$(printf '%s' "$session_cwd" | cksum | cut -d ' ' -f1)"
  local resolved_sid_file="$state_dir/${resolved_cwd_key}-${resolved_normalized_slug}.sid"
  local resolved_sid=""
  local resolved_mode=""
  local resolved_warning_state

  resolved_warning_state="$(phase_slug_warning "$resolved_normalized_slug")"

  if [[ "$fresh_session" -eq 1 ]]; then
    resolved_sid="$(new_session_id)"
    printf '%s\n' "$resolved_sid" > "$resolved_sid_file"
    resolved_mode="fresh"
    session_args=(--session-id "$resolved_sid")
  elif [[ ! -s "$resolved_sid_file" ]]; then
    resolved_sid="$(new_session_id)"
    printf '%s\n' "$resolved_sid" > "$resolved_sid_file"
    resolved_mode="create"
    session_args=(--session-id "$resolved_sid")
  else
    resolved_sid="$(cat "$resolved_sid_file")"
    resolved_mode="resume"
    session_args=(--resume "$resolved_sid")
  fi

  sid="$resolved_sid"
  sid_file="$resolved_sid_file"
  normalized_slug="$resolved_normalized_slug"
  session_mode="$resolved_mode"
  warning_state="$resolved_warning_state"
  phase_display="${advisor_phase:-none}"
}

build_phase_prompt() {
  local advisor_phase="$1"
  local next_action_target=""

  case "$advisor_phase" in
    "")
      return 0
      ;;
    preflight-advice)
      cat <<'EOF'

Checkpoint Interface: preflight-advice

Use this as the post-Repo Context Forge / post-GitNexus / pre-production-preflight checkpoint before edits. Challenge whether the Repo Context Forge + GitNexus packet covers the PRD slice, correct seams, and correct surface area before production preflight:
- task contract
- PRD slice outcomes
- Repo Context Forge packet target surface, coverage plan, and skipped high-ranked targets
- packet-scoped GitNexus findings
- intended Module / public Interface / hidden Implementation complexity
- existing reuse path
- new Seam justification, or why the existing Module should be deepened
- touched shallow Module debt
- TDD hypothesis or planned first failing behavior test
- test surface and no-change surfaces
- ordering / idempotency / data-loss risks
- implementation hypothesis
EOF
      next_action_target="before editing"
      ;;
    precommit-challenge)
      cat <<'EOF'

Checkpoint Interface: precommit-challenge

Use this as the post-edit / post-proof / pre-commit checkpoint. Challenge whether the implementation satisfies the PRD slice and production contract without extra behavior or no-change surface drift:
- exact PRD, issue, or reviewer finding
- branch / base / head context
- wrapper-provided live diff
- TDD red/green proof
- verification outcomes
- Module / Interface / Implementation
- existing reuse path
- shallow Module debt
- test surface and no-change surfaces
- skipped or weak proof
- extra behavior beyond the PRD
- commit-readiness hypothesis

Challenge output:
- Verdict: commit-ready, fix-before-commit, or context-mismatch
- PRD reconciliation: implemented, missing, extra, and unproven outcomes
- Reviewer coverage: Greptile/Cubic/CodeRabbit/Devin/human findings, when present
- TDD check
- Module shape: public Interface, test surface, deep Module pressure, and any shallow unnecessary helper/service/manager/wrapper split
- Minimality/bloat
- Regression risk
- Action
EOF
      next_action_target="before commit or push"
      ;;
    *)
      printf 'error: unsupported advisor phase: %s\n' "$advisor_phase" >&2
      exit 2
      ;;
  esac

  cat <<EOF

Shared rubric: challenge intended Module, public Interface, hidden Implementation complexity, reuse path, shallow Module debt, test surface, and no-change surfaces. Say whether the work deepens an existing Module, creates a real Seam, or risks shallow helper/service/manager/wrapper complexity.

Give full advice first. Then compact summary metadata only: suggested verdict, focus tags, and one concrete next action ${next_action_target}. Verdicts and focus tags are summary metadata only; do not let them restrict critical advice.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --provider)
      provider="${2:?missing --provider value}"
      shift 2
      ;;
    --slug)
      slug="${2:?missing --slug value}"
      shift 2
      ;;
    --phase)
      phase="${2:?missing --phase value}"
      shift 2
      ;;
    --cwd)
      cwd="${2:?missing --cwd value}"
      shift 2
      ;;
    --fresh)
      fresh=1
      shift
      ;;
    --budget)
      budget="${2:?missing --budget value}"
      shift 2
      ;;
    --base-ref)
      base_ref="${2:?missing --base-ref value}"
      shift 2
      ;;
    --model)
      advisor_model="${2:?missing --model value}"
      shift 2
      ;;
    --fallback-model)
      advisor_fallback_model="${2:?missing --fallback-model value}"
      shift 2
      ;;
    --codex-model)
      codex_model="${2:?missing --codex-model value}"
      shift 2
      ;;
    --write)
      write_mode=1
      shift
      ;;
    --full-tools)
      write_mode=1
      full_tools=1
      shift
      ;;
    --)
      shift
      break
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      break
      ;;
  esac
done

if [[ $# -eq 0 ]]; then
  usage
  exit 2
fi

case "$phase" in
  ""|preflight-advice|precommit-challenge)
    ;;
  *)
    printf 'error: unsupported advisor phase: %s\n' "$phase" >&2
    exit 2
    ;;
esac

case "$provider" in
  claude|codex)
    ;;
  *)
    printf 'error: unsupported advisor provider: %s\n' "$provider" >&2
    exit 2
    ;;
esac

if [[ -n "$phase" && "$write_mode" -eq 1 ]]; then
  printf 'error: --phase is only valid for read-only advisor mode\n' >&2
  exit 2
fi

if [[ "$provider" == "codex" && ( "$write_mode" -eq 1 || "$full_tools" -eq 1 ) ]]; then
  printf 'error: codex provider only supports read-only advisor mode\n' >&2
  exit 2
fi

question="$*"
session_cwd="$(cd "$cwd" && pwd -P)"
state_dir="${CLAUDE_ADVISOR_STATE_DIR:-$HOME/.codex/claude-advisor}"
sid=""
sid_file=""
normalized_slug=""
session_mode=""
warning_state=""
phase_display=""
session_args=()
if [[ "$provider" == "claude" ]]; then
  mkdir -p "$state_dir"
  resolve_task_session "$slug" "$fresh" "$phase"

  printf 'claude_advisor_session raw_slug=%q normalized_slug=%q mode=%s sid_prefix=%s phase=%s warnings=%s\n' \
    "$slug" "$normalized_slug" "$session_mode" "${sid:0:8}" "$phase_display" "$warning_state" >&2
  printf 'claude_advisor_model model=%q\n' "$advisor_model" >&2
  printf 'claude_advisor_fallback_model model=%q\n' "$advisor_fallback_model" >&2
else
  sid="$(new_session_id)"
  normalized_slug="${slug//[^A-Za-z0-9_.-]/_}"
  session_mode="ephemeral"
  warning_state="$(phase_slug_warning "$normalized_slug")"
  phase_display="${phase:-none}"
  printf 'codex_advisor_session raw_slug=%q normalized_slug=%q mode=%s sid_prefix=%s phase=%s warnings=%s provider=codex\n' \
    "$slug" "$normalized_slug" "$session_mode" "${sid:0:8}" "$phase_display" "$warning_state" >&2
  printf 'codex_advisor_model model=%q\n' "${codex_model:-default}" >&2
fi

stdin_context=""
if [[ ! -t 0 ]]; then
  stdin_context="$(cat)"
fi

cd "$cwd"

worktree_root=""
if [[ "$full_tools" -eq 1 ]]; then
  if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    printf 'error: --full-tools requires --cwd to be inside the delegated git worktree\n' >&2
    exit 2
  fi
  worktree_root="$(git rev-parse --show-toplevel)"
  cd "$worktree_root"
fi

git_context=""
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  branch="$(git branch --show-current 2>/dev/null || true)"
  head_sha="$(git rev-parse HEAD 2>/dev/null || true)"
  upstream="$(git rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null || true)"
  status_short="$(git status --short --branch 2>/dev/null || true)"
  pr_context=""
  pr_base=""

  if command -v gh >/dev/null 2>&1; then
    pr_context="$(gh pr view --json number,url,state,isDraft,headRefName,headRefOid,baseRefName,baseRefOid --jq '"number=\(.number) url=\(.url) state=\(.state) draft=\(.isDraft) head=\(.headRefName) headOid=\(.headRefOid) base=\(.baseRefName) baseOid=\(.baseRefOid)"' 2>/dev/null || true)"
    pr_base="$(gh pr view --json baseRefName --jq '.baseRefName' 2>/dev/null || true)"
  fi

  diff_ref="$base_ref"
  if [[ -z "$diff_ref" && -n "$pr_base" && "$pr_base" != "null" ]]; then
    if git rev-parse --verify "origin/$pr_base" >/dev/null 2>&1; then
      diff_ref="origin/$pr_base"
    else
      diff_ref="$pr_base"
    fi
  fi
  if [[ -z "$diff_ref" && -n "$upstream" ]]; then
    diff_ref="$upstream"
  fi

  staged_context=""
  if ! git diff --cached --quiet --no-ext-diff 2>/dev/null; then
    staged_context="$(printf 'Staged diff stat:\n'; git diff --cached --stat --no-ext-diff; printf '\nStaged diff:\n'; git diff --cached --no-ext-diff)"
  fi

  unstaged_context=""
  if ! git diff --quiet --no-ext-diff 2>/dev/null; then
    unstaged_context="$(printf 'Unstaged diff stat:\n'; git diff --stat --no-ext-diff; printf '\nUnstaged diff:\n'; git diff --no-ext-diff)"
  fi

  pr_diff_context=""
  if [[ -z "$staged_context$unstaged_context" && -n "$diff_ref" ]]; then
    pr_diff_context="$(printf 'PR/base diff ref: %s...HEAD\n' "$diff_ref"; git diff --stat --no-ext-diff "$diff_ref"...HEAD 2>/dev/null || true; printf '\nPR/base diff:\n'; git diff --no-ext-diff "$diff_ref"...HEAD 2>/dev/null || true)"
  fi

  git_context="$(cat <<EOF
Live git/PR context collected by wrapper:
cwd=$PWD
branch=$branch
HEAD=$head_sha
upstream=$upstream
PR=$pr_context
status:
$status_short

$staged_context
$unstaged_context
$pr_diff_context
EOF
)"
fi

if [[ "$full_tools" -eq 1 ]]; then
  mode_prompt="Full-tools execution mode. You have full Claude tool access for the delegated task, but only inside this delegated git worktree: ${worktree_root}. The Codex agent must have created or selected this worktree from the intended base, such as current main head or current PR head. Do not work in temp directories, do not edit files outside this worktree, and do not use files outside this worktree except for explicit read-only context supplied by the prompt. Do not commit or push unless the task explicitly grants that authority. Keep changes directly traceable to the task. Stdout must summarize changed files, commands run, verification, and blockers. Answer in <=${budget} words."
  append_prompt="Full-tools execution mode: use tools only inside the delegated git worktree ${worktree_root}. Do not work in temp directories or edit outside the worktree. Do not commit or push unless the task explicitly grants that authority. Keep edits task-scoped; report changed files, commands, verification, and blockers."
  permission_args=(--permission-mode bypassPermissions)
  tool_args=(--tools default)
elif [[ "$write_mode" -eq 1 ]]; then
  mode_prompt="Execution mode. You may edit files in the target cwd only when the requested task requires it. Keep changes minimal and directly traceable to the task. Do not commit, push, delete files, install packages, change secrets, or write plan artifacts. Stdout must summarize changed files, verification run, and any blockers. Answer in <=${budget} words."
  append_prompt="Execution mode: writes are permitted in the target cwd only for the requested task. Keep edits minimal; cite changed files; flag uncertainty; do not commit, push, delete files, install packages, change secrets, or write plan artifacts."
  permission_args=(--permission-mode acceptEdits)
  tool_args=(
    --allowed-tools
    "Read Grep Glob Edit Write NotebookEdit Bash(git diff:*) Bash(git status:*) Bash(git branch:*) Bash(git rev-parse:*) Bash(git grep:*) Bash(rg:*) Bash(ls:*) Bash(sed:*) Bash(cat:*) Bash(npm test:*) Bash(npm run:*) Bash(pnpm test:*) Bash(pnpm run:*) Bash(pytest:*) Bash(cargo test:*)"
    --disallowed-tools
    "Bash(git commit:*) Bash(git push:*) Bash(git reset:*) Bash(git checkout:*) Bash(git clean:*) Bash(rm:*) Bash(sudo:*) Bash(curl:*) Bash(wget:*)"
  )
else
  mode_prompt="Advisor mode. Do not create files. Do not edit files. Do not write plan artifacts. Stdout only. Answer in <=${budget} words."
  append_prompt="Advisor mode: read-only; cite file:line when using repo evidence; flag uncertainty; no orders; stdout only."
  permission_args=()
  tool_args=(
    --allowed-tools
    "Read Grep Glob Bash(git diff:*) Bash(git status:*) Bash(git branch:*) Bash(git rev-parse:*) Bash(gh issue view:*) Bash(gh pr view:*) Bash(gh run view:*) Bash(rg:*) Bash(ls:*) Bash(sed:*) Bash(cat:*)"
    --disallowed-tools
    "Edit Write NotebookEdit"
  )
fi

phase_prompt="$(build_phase_prompt "$phase")"

prompt="${mode_prompt}

You may use /tdd and /improve-codebase-architecture as read-only rubric references. Do not invoke heavyweight repo execution skills, bootstrap scripts, or /production-preflight as a separate workflow unless explicitly asked. Preflight remains Codex-owned; report missing preflight/module-shape evidence instead of generating a substitute preflight.

For review or pre-commit challenge requests, use the live git/PR context and diff collected by this wrapper as the evidence base. Critique Codex's claim against that evidence, not against Codex's prose summary. If the wrapper-provided diff shows no relevant change, say that directly and explain what exact evidence is missing or inconsistent.
"

if [[ -n "$phase_prompt" ]]; then
  prompt="${prompt}

${phase_prompt}"
fi

prompt="${prompt}

${question}"

if [[ -n "$git_context" ]]; then
  prompt="${prompt}

Wrapper-provided live git/PR context and diff:
${git_context}"
fi

if [[ -n "$stdin_context" ]]; then
  prompt="${prompt}

Additional context from stdin:
${stdin_context}"
fi

advisor_output=""
advisor_status=0
if [[ "$provider" == "codex" ]]; then
  codex_args=(exec --sandbox read-only -C "$session_cwd" --ephemeral)
  if [[ -n "$codex_model" ]]; then
    codex_args+=(--model "$codex_model")
  fi
  set +e
  advisor_output="$(printf '%s' "$prompt" | codex "${codex_args[@]}" -)"
  advisor_status=$?
  set -e
else
  run_claude_advisor() {
    printf '%s' "$prompt" | claude -p \
      --model "$advisor_model" \
      --fallback-model "$advisor_fallback_model" \
      --output-format text \
      --append-system-prompt "$append_prompt" \
      ${permission_args[@]+"${permission_args[@]}"} \
      ${tool_args[@]+"${tool_args[@]}"} \
      "$@"
  }
  capture_claude_advisor() {
    local stderr_file
    stderr_file="$(mktemp)"
    set +e
    advisor_output="$(run_claude_advisor "$@" 2>"$stderr_file")"
    advisor_status=$?
    set -e
    advisor_stderr="$(<"$stderr_file")"
    rm -f "$stderr_file"
  }
  advisor_stderr=""
  capture_claude_advisor "${session_args[@]}"
  if [[ "$session_mode" == "resume" && ( "$advisor_output" == *"No conversation found with session ID:"* || "$advisor_stderr" == *"No conversation found with session ID:"* ) ]]; then
    sid="$(new_session_id)"
    printf '%s\n' "$sid" > "$sid_file"
    printf 'claude_advisor_session_recovery reason=stale-resume sid_prefix=%s\n' "${sid:0:8}" >&2
    capture_claude_advisor --session-id "$sid"
  fi
  if [[ -n "$advisor_stderr" ]]; then
    printf '%s\n' "$advisor_stderr" >&2
  fi
fi

if [[ "$advisor_status" -ne 0 ]]; then
  printf 'error: %s advisor command failed (exit %s)\n' "$provider" "$advisor_status" >&2
  exit "$advisor_status"
fi
if [[ -z "$(printf '%s' "$advisor_output" | tr -d '[:space:]')" ]]; then
  printf 'error: %s advisor returned empty output\n' "$provider" >&2
  exit 1
fi
printf '%s\n' "$advisor_output"
