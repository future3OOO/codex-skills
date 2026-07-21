#!/usr/bin/env bash
set -euo pipefail

skill_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
wrapper="$skill_dir/scripts/ask-claude-advisor.sh"
tmp_dir="$(mktemp -d)"

cleanup() {
  rm -rf "$tmp_dir"
}
trap cleanup EXIT

stub_dir="$tmp_dir/bin"
mkdir -p "$stub_dir"

cat > "$stub_dir/claude" <<'STUB'
#!/usr/bin/env bash
set -euo pipefail

{
  printf 'ARGV_BEGIN\n'
  for arg in "$@"; do
    printf '%s\n' "$arg"
  done
  printf 'ARGV_END\n'
} > "${CLAUDE_STUB_ARGV:?}"

cat > "${CLAUDE_STUB_PROMPT:?}"
if [[ "${CLAUDE_STUB_EMPTY:-0}" == "1" ]]; then
  exit 0
fi
if [[ -n "${CLAUDE_STUB_FAIL:-}" ]]; then
  exit "$CLAUDE_STUB_FAIL"
fi
if [[ "${CLAUDE_STUB_STALE_RESUME:-0}" == "1" ]] && printf '%s\n' "$@" | grep -Fx -- '--resume' >/dev/null; then
  printf 'No conversation found with session ID: stale-test-session\n' >&2
  exit 1
fi
printf 'CLAUDE_STUB_OUTPUT\n'
STUB
chmod +x "$stub_dir/claude"

cat > "$stub_dir/codex" <<'STUB'
#!/usr/bin/env bash
set -euo pipefail

{
  printf 'ARGV_BEGIN\n'
  for arg in "$@"; do
    printf '%s\n' "$arg"
  done
  printf 'ARGV_END\n'
} > "${CODEX_STUB_ARGV:?}"

cat > "${CODEX_STUB_PROMPT:?}"
printf 'CODEX_STUB_OUTPUT\n'
STUB
chmod +x "$stub_dir/codex"

export PATH="$stub_dir:$PATH"
export CLAUDE_ADVISOR_STATE_DIR="$tmp_dir/state"
export CLAUDE_STUB_ARGV="$tmp_dir/claude.argv"
export CLAUDE_STUB_PROMPT="$tmp_dir/claude.prompt"
export CODEX_STUB_ARGV="$tmp_dir/codex.argv"
export CODEX_STUB_PROMPT="$tmp_dir/codex.prompt"

stdout_file="$tmp_dir/stdout"
stderr_file="$tmp_dir/stderr"

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}

assert_contains() {
  local file="$1"
  local needle="$2"
  if ! grep -F -- "$needle" "$file" >/dev/null; then
    printf 'Expected %s to contain:\n%s\n\nActual:\n' "$file" "$needle" >&2
    sed -n '1,220p' "$file" >&2
    exit 1
  fi
}

assert_contains_block() {
  local file="$1"
  local needle="$2"
  local actual
  actual="$(cat "$file")"
  if [[ "$actual" != *"$needle"* ]]; then
    printf 'Expected %s to contain block:\n%s\n\nActual:\n' "$file" "$needle" >&2
    sed -n '1,260p' "$file" >&2
    exit 1
  fi
}

assert_not_contains() {
  local file="$1"
  local needle="$2"
  if grep -F -- "$needle" "$file" >/dev/null; then
    printf 'Expected %s not to contain:\n%s\n\nActual:\n' "$file" "$needle" >&2
    sed -n '1,220p' "$file" >&2
    exit 1
  fi
}

run_advisor() {
  : > "$CLAUDE_STUB_ARGV"
  : > "$CLAUDE_STUB_PROMPT"
  : > "$CODEX_STUB_ARGV"
  : > "$CODEX_STUB_PROMPT"
  : > "$stdout_file"
  : > "$stderr_file"
  set +e
  "$wrapper" "$@" >"$stdout_file" 2>"$stderr_file"
  local status=$?
  set -e
  return "$status"
}

run_advisor_with_stdin() {
  local stdin_value="$1"
  shift
  : > "$CLAUDE_STUB_ARGV"
  : > "$CLAUDE_STUB_PROMPT"
  : > "$CODEX_STUB_ARGV"
  : > "$CODEX_STUB_PROMPT"
  : > "$stdout_file"
  : > "$stderr_file"
  set +e
  printf '%s' "$stdin_value" | "$wrapper" "$@" >"$stdout_file" 2>"$stderr_file"
  local status="${PIPESTATUS[1]}"
  set -e
  return "$status"
}

session_id_for_slug() {
  local slug="$1"
  local cwd_path="$2"
  local cwd_key
  cwd_path="$(cd "$cwd_path" && pwd -P)"
  cwd_key="$(printf '%s' "$cwd_path" | cksum | cut -d ' ' -f1)"
  cat "$CLAUDE_ADVISOR_STATE_DIR/${cwd_key}-${slug}.sid"
}

run_advisor --slug cass --cwd "$skill_dir" -- "Question: first call"
assert_contains "$stdout_file" "CLAUDE_STUB_OUTPUT"
assert_not_contains "$stdout_file" "claude_advisor_session"
assert_contains "$CLAUDE_STUB_PROMPT" "Advisor mode. Do not create files. Do not edit files. Do not write plan artifacts. Stdout only."
assert_contains "$CLAUDE_STUB_PROMPT" "/tdd and /improve-codebase-architecture"
assert_contains "$CLAUDE_STUB_PROMPT" "Do not invoke heavyweight repo execution skills"
assert_contains "$CLAUDE_STUB_PROMPT" "Preflight remains Codex-owned"
assert_contains "$CLAUDE_STUB_PROMPT" "live git/PR context and diff collected by this wrapper"
assert_contains "$CLAUDE_STUB_ARGV" "read-only; cite file:line"
assert_contains "$CLAUDE_STUB_ARGV" "flag uncertainty; no orders; stdout only"
assert_contains "$CLAUDE_STUB_ARGV" "Bash(gh issue view:*)"
assert_contains "$CLAUDE_STUB_ARGV" "Bash(gh pr view:*)"
assert_contains "$CLAUDE_STUB_ARGV" "Bash(gh run view:*)"
assert_contains "$CLAUDE_STUB_ARGV" "Edit Write NotebookEdit"
assert_not_contains "$CLAUDE_STUB_ARGV" "MultiEdit"
assert_contains "$stderr_file" "claude_advisor_session"
assert_contains "$stderr_file" "raw_slug=cass"
assert_contains "$stderr_file" "normalized_slug=cass"
assert_contains "$stderr_file" "mode=create"
assert_contains "$stderr_file" "phase=none"
assert_contains "$stderr_file" "warnings=none"
assert_contains "$stderr_file" "claude_advisor_complete status=0 provider=claude"
first_sid="$(session_id_for_slug cass "$skill_dir")"
assert_contains "$CLAUDE_STUB_ARGV" "--session-id"
assert_contains "$CLAUDE_STUB_ARGV" "$first_sid"

export CLAUDE_STUB_STALE_RESUME=1
run_advisor --slug cass --cwd "$skill_dir" -- "Question: stale resume"
recovered_sid="$(session_id_for_slug cass "$skill_dir")"
[[ "$recovered_sid" != "$first_sid" ]] || fail "stale resume did not rotate the session id"
assert_contains "$stdout_file" "CLAUDE_STUB_OUTPUT"
assert_not_contains "$stdout_file" "No conversation found"
assert_contains "$stderr_file" "claude_advisor_session_recovery reason=stale-resume"
unset CLAUDE_STUB_STALE_RESUME
first_sid="$recovered_sid"

export CLAUDE_STUB_EMPTY=1
if run_advisor --slug empty-output --cwd "$skill_dir" -- "Question: empty output"; then
  fail "empty Claude output should fail closed"
fi
assert_contains "$stderr_file" "error: claude advisor returned empty output"
assert_not_contains "$stderr_file" "claude_advisor_complete"
unset CLAUDE_STUB_EMPTY

export CLAUDE_STUB_FAIL=7
if run_advisor --slug failed-provider --cwd "$skill_dir" -- "Question: provider failure"; then
  fail "failed Claude command should fail closed"
fi
assert_contains "$stderr_file" "error: claude advisor command failed (exit 7)"
assert_not_contains "$stderr_file" "claude_advisor_complete"
unset CLAUDE_STUB_FAIL

git_tmp="$tmp_dir/git-worktree"
mkdir -p "$git_tmp"
git -C "$git_tmp" init -q
git -C "$git_tmp" config user.email "test@example.com"
git -C "$git_tmp" config user.name "Test User"
printf 'x\n' > "$git_tmp/file.txt"
git -C "$git_tmp" add file.txt
git -C "$git_tmp" commit -q -m init
git -C "$git_tmp" branch base
printf 'x\ny\n' > "$git_tmp/file.txt"
git -C "$git_tmp" add file.txt
git -C "$git_tmp" commit -q -m feature
git -C "$git_tmp" branch --set-upstream-to=base >/dev/null

run_advisor --slug generic-diff --cwd "$git_tmp" -- "Question: generic advice"
assert_not_contains "$CLAUDE_STUB_PROMPT" "PR/base diff ref:"

run_advisor --slug preflight-diff --phase preflight-advice --cwd "$git_tmp" -- "Question: scope advice"
assert_not_contains "$CLAUDE_STUB_PROMPT" "PR/base diff ref:"

run_advisor --slug explicit-diff --cwd "$git_tmp" --base-ref base -- "Question: explicit diff"
assert_contains "$CLAUDE_STUB_PROMPT" "PR/base diff ref: base...HEAD"
assert_contains "$CLAUDE_STUB_PROMPT" "+y"

run_advisor --slug precommit-diff --phase precommit-challenge --cwd "$git_tmp" -- "Question: challenge diff"
assert_contains "$CLAUDE_STUB_PROMPT" "PR/base diff ref: base...HEAD"
assert_contains "$CLAUDE_STUB_PROMPT" "+y"

run_advisor --provider codex --slug cass --phase precommit-challenge --cwd "$git_tmp" --base-ref HEAD -- "Question: codex challenge"
assert_contains "$stdout_file" "CODEX_STUB_OUTPUT"
assert_contains "$stderr_file" "codex_advisor_session"
assert_contains "$stderr_file" "claude_advisor_complete status=0 provider=codex"
assert_contains "$stderr_file" "provider=codex"
assert_contains "$stderr_file" "phase=precommit-challenge"
assert_contains "$CODEX_STUB_ARGV" "exec"
assert_contains "$CODEX_STUB_ARGV" "--sandbox"
assert_contains "$CODEX_STUB_ARGV" "read-only"
assert_contains "$CODEX_STUB_ARGV" "-C"
assert_contains "$CODEX_STUB_ARGV" "$git_tmp"
assert_contains "$CODEX_STUB_ARGV" "--ephemeral"
assert_contains "$CODEX_STUB_ARGV" "-"
assert_contains "$CODEX_STUB_PROMPT" "Checkpoint Interface: precommit-challenge"
assert_contains "$CODEX_STUB_PROMPT" "Wrapper-provided live git/PR context and diff:"
[[ ! -s "$CLAUDE_STUB_ARGV" ]] || fail "codex provider invoked claude"

CLAUDE_ADVISOR_PROVIDER=codex run_advisor --slug env-codex --cwd "$skill_dir" -- "Question: env provider"
assert_contains "$stdout_file" "CODEX_STUB_OUTPUT"
assert_contains "$stderr_file" "provider=codex"

if run_advisor --provider codex --slug cass --cwd "$skill_dir" --write -- "Task: invalid"; then
  fail "codex provider with --write should fail closed"
fi
assert_contains "$stderr_file" "error: codex provider only supports read-only advisor mode"

if run_advisor --provider codex --slug cass --cwd "$skill_dir" --full-tools -- "Task: invalid"; then
  fail "codex provider with --full-tools should fail closed"
fi
assert_contains "$stderr_file" "error: codex provider only supports read-only advisor mode"

run_advisor_with_stdin "repo packet context" --slug cass --cwd "$skill_dir" -- "Question: stdin"
assert_contains "$CLAUDE_STUB_PROMPT" "Additional context from stdin:"
assert_contains "$CLAUDE_STUB_PROMPT" "repo packet context"

run_advisor --slug cass --cwd "$skill_dir" -- "Question: second call"
second_sid="$(session_id_for_slug cass "$skill_dir")"
[[ "$first_sid" == "$second_sid" ]] || fail "stable slug did not reuse the stored session id"
assert_contains "$stderr_file" "mode=resume"
assert_contains "$CLAUDE_STUB_ARGV" "--resume"
assert_contains "$CLAUDE_STUB_ARGV" "$first_sid"

other_cwd="$tmp_dir/other-cwd"
mkdir -p "$other_cwd"
run_advisor --slug cass --cwd "$other_cwd" -- "Question: same slug other cwd"
other_cwd_sid="$(session_id_for_slug cass "$other_cwd")"
[[ "$other_cwd_sid" != "$first_sid" ]] || fail "same slug reused sid across cwd"
assert_contains "$CLAUDE_STUB_ARGV" "--session-id"
assert_contains "$CLAUDE_STUB_ARGV" "$other_cwd_sid"

run_advisor --slug other-task --cwd "$skill_dir" -- "Question: other call"
other_sid="$(session_id_for_slug other-task "$skill_dir")"
[[ "$other_sid" != "$first_sid" ]] || fail "different slugs reused the same session id"

run_advisor --slug "../foo" --cwd "$skill_dir" -- "Question: path-shaped slug"
find "$CLAUDE_ADVISOR_STATE_DIR" -maxdepth 1 -name '*-.._foo.sid' | grep -q . || fail "path-shaped slug did not normalize inside the state directory"

run_advisor --slug cass --fresh --cwd "$skill_dir" -- "Question: fresh call"
fresh_sid="$(session_id_for_slug cass "$skill_dir")"
[[ "$fresh_sid" != "$first_sid" ]] || fail "--fresh did not rotate the stored session id"
assert_contains "$stderr_file" "mode=fresh"
assert_contains "$CLAUDE_STUB_ARGV" "--session-id"
assert_contains "$CLAUDE_STUB_ARGV" "$fresh_sid"

run_advisor --slug pre-commit --cwd "$skill_dir" -- "Question: warned slug"
assert_contains "$stderr_file" "raw_slug=pre-commit"
assert_contains "$stderr_file" "normalized_slug=pre-commit"
assert_contains "$stderr_file" "warnings=phase-slug:pre-commit"
assert_contains "$stdout_file" "CLAUDE_STUB_OUTPUT"

run_advisor --slug cass --phase preflight-advice --cwd "$skill_dir" -- "Question: before editing"
assert_contains "$stderr_file" "phase=preflight-advice"
assert_contains "$CLAUDE_STUB_PROMPT" "Checkpoint Interface: preflight-advice"
assert_contains "$CLAUDE_STUB_PROMPT" "packet covers the PRD slice, correct seams, and correct surface area"
assert_contains "$CLAUDE_STUB_PROMPT" "PRD slice outcomes"
assert_contains "$CLAUDE_STUB_PROMPT" "Repo Context Forge packet target surface, coverage plan, and skipped high-ranked targets"
assert_contains "$CLAUDE_STUB_PROMPT" "packet-scoped GitNexus findings"
assert_contains "$CLAUDE_STUB_PROMPT" "TDD hypothesis or planned first failing behavior test"
assert_contains "$CLAUDE_STUB_PROMPT" "one concrete next action before editing"
assert_not_contains "$CLAUDE_STUB_PROMPT" "Wrapper-provided live git/PR context and diff is required"
assert_contains_block "$CLAUDE_STUB_PROMPT" "Checkpoint Interface: preflight-advice

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
- implementation hypothesis"

run_advisor --slug cass --phase precommit-challenge --cwd "$skill_dir" --base-ref HEAD -- "Question: before commit"
assert_contains "$stderr_file" "phase=precommit-challenge"
assert_contains "$CLAUDE_STUB_PROMPT" "Checkpoint Interface: precommit-challenge"
assert_contains "$CLAUDE_STUB_PROMPT" "implementation satisfies the PRD slice and production contract"
assert_contains "$CLAUDE_STUB_PROMPT" "wrapper-provided live diff"
assert_contains "$CLAUDE_STUB_PROMPT" "TDD red/green proof"
assert_contains "$CLAUDE_STUB_PROMPT" "one concrete next action before commit or push"
assert_contains "$CLAUDE_STUB_PROMPT" "Challenge output:"
assert_contains "$CLAUDE_STUB_PROMPT" "Verdict: commit-ready, fix-before-commit, or context-mismatch"
assert_contains "$CLAUDE_STUB_PROMPT" "PRD reconciliation: implemented, missing, extra, and unproven outcomes"
assert_contains "$CLAUDE_STUB_PROMPT" "Reviewer coverage: Greptile/Cubic/CodeRabbit/Devin/human findings, when present"
assert_contains "$CLAUDE_STUB_PROMPT" "TDD check"
assert_contains "$CLAUDE_STUB_PROMPT" "Module shape: public Interface, test surface, deep Module pressure, and any shallow unnecessary helper/service/manager/wrapper split"
assert_contains_block "$CLAUDE_STUB_PROMPT" "Challenge output:
- Verdict: commit-ready, fix-before-commit, or context-mismatch
- PRD reconciliation: implemented, missing, extra, and unproven outcomes
- Reviewer coverage: Greptile/Cubic/CodeRabbit/Devin/human findings, when present
- TDD check
- Module shape: public Interface, test surface, deep Module pressure, and any shallow unnecessary helper/service/manager/wrapper split
- Minimality/bloat
- Regression risk
- Action"

assert_contains "$CLAUDE_STUB_PROMPT" "Verdicts and focus tags are summary metadata only"
assert_contains "$CLAUDE_STUB_PROMPT" "intended Module"
assert_contains "$CLAUDE_STUB_PROMPT" "public Interface"
assert_contains "$CLAUDE_STUB_PROMPT" "hidden Implementation complexity"
assert_contains "$CLAUDE_STUB_PROMPT" "existing reuse path"
assert_contains "$CLAUDE_STUB_PROMPT" "shallow Module debt"
assert_contains "$CLAUDE_STUB_PROMPT" "test surface"
assert_contains "$CLAUDE_STUB_PROMPT" "no-change surfaces"

run_advisor --slug cass --cwd "$skill_dir" --write -- "Task: write mode"
assert_contains "$CLAUDE_STUB_ARGV" "claude-fable-5"
assert_contains "$CLAUDE_STUB_ARGV" "--permission-mode"
assert_contains "$CLAUDE_STUB_ARGV" "acceptEdits"
assert_contains "$CLAUDE_STUB_ARGV" "Edit Write NotebookEdit"
assert_not_contains "$CLAUDE_STUB_ARGV" "MultiEdit"
assert_contains "$CLAUDE_STUB_ARGV" "Bash(git commit:*)"

if run_advisor --slug cass --phase preflight-advice --cwd "$skill_dir" --write -- "Task: invalid"; then
  fail "--phase with --write should fail closed"
fi
assert_contains "$stderr_file" "error: --phase is only valid for read-only advisor mode"

run_advisor --slug cass --cwd "$git_tmp" --full-tools -- "Task: full tools"
assert_contains "$CLAUDE_STUB_ARGV" "--permission-mode"
assert_contains "$CLAUDE_STUB_ARGV" "bypassPermissions"
assert_contains "$CLAUDE_STUB_ARGV" "--tools"
assert_contains "$CLAUDE_STUB_ARGV" "default"

if run_advisor --slug cass --phase precommit-challenge --cwd "$git_tmp" --full-tools -- "Task: invalid"; then
  fail "--phase with --full-tools should fail closed"
fi
assert_contains "$stderr_file" "error: --phase is only valid for read-only advisor mode"

printf 'ok\n'
