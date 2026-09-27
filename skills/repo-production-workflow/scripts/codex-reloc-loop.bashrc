#!/usr/bin/env bash
# Source from ~/.bashrc: wraps `codex` and the estate codexs aliases with the
# session-relocation resume loop used by scripts/codex-relocate. Interactive
# launches get CODEX_RELOC_LOOP=1 plus a ~/.codex/reloc/<shell-pid> marker
# check; markers older than 900s or missing an epoch are ignored. Sessions
# launched directly by herdr/tmux bypass these functions entirely.

_codex_native() {
  local a daemon=(--no-daemon)
  for a in "$@"; do
    [ "$a" = -- ] && break
    [ "$a" = --no-daemon ] && daemon=()
  done
  CODEX_RELOC_LOOP=1 CODEX_RELOC_SHELL_PID=$BASHPID command codex "${daemon[@]}" "$@"
}

# In-place relocation loop (any profile): a session that ran
# ~/.codex/bin/codex-relocate wrote ~/.codex/reloc/<this-shell-pid> and killed
# its TUI; resume the same thread at the new root in this same pane, reusing
# the launch args (profile/model flags). CODEX_RELOC_LOOP is exported into the
# session env so the agent can tell the loop is armed before killing.
_codex_reloc_loop() {
  local m="$HOME/.codex/reloc/$BASHPID" wt tid epoch note stuck=0
  while [ -f "$m" ]; do
    # Read target, thread and epoch, then the optional continuation note.
    note=""
    { read -r wt tid epoch && { IFS= read -r -d '' note || true; } } < "$m" || break
    rm -f "$m" || stuck=1
    note="${note%$'\n'}"
    [ -n "$wt" ] && [ -n "$tid" ] || break
    # An un-removable marker is processed exactly once — resume if
    # actionable, then stop; looping re-resumed it 1300+ times in 4s.
    # 10# forces base-10 (leading zeros crash arithmetic); {1,18} stays
    # under int64 — wider digits wrap and a bogus marker resumes
    # (measured: 99999999999999999999 resumed).
    # "--" keeps a flag-shaped note from being parsed as an option.
    [[ "$epoch" =~ ^[0-9]{1,18}$ ]] && [ $(( $(date +%s) - 10#$epoch )) -le 900 ] &&
      _codex_native "$@" resume -C "$wt" "$tid" ${note:+-- "$note"}
    [ "$stuck" = 1 ] && break
  done
}
# Interactive TUI launches only: bare `codex`/`codexs`, flags, and `resume`.
# Any other bare token is a non-TUI subcommand (exec, mcp, review, apply,
# app-server, ...) that must never consume a relocation marker; --remote
# sessions can't self-relocate either. Unknown flags-with-values fail safe:
# their value scans as a bare token and bypasses rather than arming.
_codex_is_tui() {
  local a expect_value=0 seen_resume=0
  for a in "$@"; do
    if [ "$expect_value" = 1 ]; then expect_value=0; continue; fi
    case "$a" in
      --remote|--remote=*) return 1 ;;
      -h|--help|-V|--version) return 1 ;;
      -m|--model|-p|--profile|-C|--cd|-c|--config|-s|--sandbox|-a|--ask-for-approval|-i|--image|--add-dir|--enable|--disable|--remote-auth-token-env)
        expect_value=1 ;;
      --*|-*) ;;
      resume) seen_resume=1 ;;
      *) [ "$seen_resume" = 1 ] || return 1 ;;
    esac
  done
  return 0
}
codex() {
  _codex_is_tui "$@" || { command codex "$@"; return; }
  local rargs=() a; for a in "$@"; do [ "$a" = "resume" ] && break; rargs+=("$a"); done
  _codex_native "$@"
  local rc=$?
  _codex_reloc_loop "${rargs[@]}"
  return $rc
}
codexs()        { codex --profile codexs "$@"; }
codexs-high()   { codexs -m swe-2-high "$@"; }
codexs-medium() { codexs -m swe-2-medium "$@"; }
