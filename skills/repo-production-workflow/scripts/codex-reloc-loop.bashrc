#!/usr/bin/env bash
# Source from ~/.bashrc: wraps `codex` and the estate codexs aliases with the
# session-relocation resume loop used by scripts/codex-relocate. Interactive
# launches get CODEX_RELOC_LOOP=1 plus a ~/.codex/reloc/<shell-pid> marker
# check; markers older than 900s or missing an epoch are ignored. Sessions
# launched directly by herdr/tmux bypass these functions entirely.

# In-place relocation loop (any profile): a session that ran
# ~/.codex/bin/codex-relocate wrote ~/.codex/reloc/<this-shell-pid> and killed
# its TUI; resume the same thread at the new root in this same pane, reusing
# the launch args (profile/model flags). CODEX_RELOC_LOOP is exported into the
# session env so the agent can tell the loop is armed before killing.
_codex_reloc_loop() {
  local m="$HOME/.codex/reloc/$$" wt tid epoch
  while [ -f "$m" ]; do
    { read -r wt tid epoch < "$m" && rm -f "$m"; } || break
    [ -n "$wt" ] && [ -n "$tid" ] || break
    [ -z "${epoch:-}" ] && continue
    [ $(( $(date +%s) - epoch )) -gt 900 ] && continue
    CODEX_RELOC_LOOP=1 command codex "$@" resume -C "$wt" "$tid"
  done
}
codex() {
  case "${1:-}" in
    exec|app-server|completion|login|logout|sandbox|import|agents|debug|-h|--help|-V|--version)
      command codex "$@"; return ;;
  esac
  local a; for a in "$@"; do [ "$a" = "--remote" ] && { command codex "$@"; return; }; done
  local rargs=(); for a in "$@"; do [ "$a" = "resume" ] && break; rargs+=("$a"); done
  CODEX_RELOC_LOOP=1 command codex "$@"
  _codex_reloc_loop "${rargs[@]}"
}
_codexs_run() {
  local rargs=(--profile codexs) a
  for a in "$@"; do [ "$a" = "resume" ] && break; rargs+=("$a"); done
  CODEX_RELOC_LOOP=1 command codex --profile codexs "$@"
  _codex_reloc_loop "${rargs[@]}"
}
codexs()        { _codexs_run "$@"; }
codexs-high()   { _codexs_run -m swe-2-high "$@"; }
codexs-medium() { _codexs_run -m swe-2-medium "$@"; }
