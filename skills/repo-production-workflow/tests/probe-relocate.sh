#!/usr/bin/env bash
# Retained attack probes for codex-relocate + codex-reloc-loop.bashrc.
# Usage: probe-relocate.sh <self|guards|flags|ws> <script> [loop-file]
set -u
HERE="$(cd "$(dirname "$0")" && pwd -P)"
CASE="$1"; SCRIPT="$(readlink -f "$2")"; LOOP="${3:-$HERE/../scripts/codex-reloc-loop.bashrc}"
case "$CASE" in self|guards|flags|ws) ;; *) echo "unknown case: $CASE" >&2; exit 2 ;; esac
WTD="$(mktemp -d /tmp/reloc-probe.XXXXXX)"
mkdir -p "$WTD/home/.codex/reloc"
# Isolate HOME: _trust_dir now persists trust into ~/.codex/config.toml —
# probes must never write trust entries into the real user config.
export HOME="$WTD/home"
fail=0
say() { printf '%s\n' "$*"; }
stubdir=""; mkstub() {
  stubdir=$(mktemp -d)
  printf '#!/bin/bash\necho "CODEX-INVOKED: $*"\n' > "$stubdir/codex"; chmod +x "$stubdir/codex"
}
chain() { # chain <wt> <tid> [flag]  (run from caller cwd)
  bash -c 'python3 "$@"' _ "$HERE/reloc-stubchain.py" "$SCRIPT" "$@" 2>&1
}
cleanup() { [ -n "$stubdir" ] && rm -rf "$stubdir"; rm -rf "$WTD"; }

case "$CASE" in
self)
  mkdir -p "$WTD/wtA"
  out=$(chain "$WTD/wtA" stubtid flag)
  echo "$out" | grep -q '"sigterm_delivered_to_host": true' || { say "NO-KILL: $out"; fail=1; }
  # Deferral is behavioral, not textual: the SIGTERM must land well after the
  # relocating process exits so its turn closes first (immediate kill = bug).
  echo "$out" | python3 -c "import sys,json; d=json.loads([l for l in sys.stdin if l.startswith('{')][-1])['sigterm_delay']; sys.exit(0 if d is not None and d >= 5 else 1)" \
    || { say "KILL-NOT-DEFERRED: $out"; fail=1; }
  echo "$out" | grep -q '"marker_exists": true' || { say "NO-MARKER: $out"; fail=1; }
  mc=$(echo "$out" | python3 -c "import sys,json; print(json.loads([l for l in sys.stdin if l.startswith('{')][-1])['marker_content'])" 2>/dev/null)
  echo "$mc" | grep -qE "^$WTD/wtA stubtid [0-9]+$" || { say "MARKER-CONTENT-BAD: '$mc'"; fail=1; }
  out2=$(chain "$WTD/wtA" stubtid)
  echo "$out2" | grep -q '"sigterm_delivered_to_host": false' || { say "KILLED-WITHOUT-LOOP: $out2"; fail=1; }
  echo "$out2" | grep -q '"marker_exists": false' || { say "MARKER-WITHOUT-LOOP: $out2"; fail=1; }
  echo "$out2" | grep -q "resume -C $WTD/wtA stubtid" || { say "NO-MANUAL-HINT: $out2"; fail=1; }
  [ "$fail" -eq 0 ] && say "SELF-PATH-OK" || say "SELF-PATH-FAILING RELOC_SELF_PATH_MISMATCH"
  ;;
guards)
  mkdir -p "$WTD/wtRooted"
  out=$(cd "$WTD/wtRooted" && chain "$WTD/wtRooted" stubtid flag)
  echo "$out" | grep -q '"sigterm_delivered_to_host": false' && \
  echo "$out" | grep -q '"marker_exists": false' && \
  echo "$out" | grep -q '"exit_code": [1-9]' && \
  echo "$out" | grep -q "already rooted" \
    || { say "GUARD-ALREADY-ROOTED-MISSING: $out"; fail=1; }
  mkstub
  res=$(STUBDIR="$stubdir" LOOPFILE="$LOOP" bash -c '
    export PATH="$STUBDIR:$PATH"; source "$LOOPFILE"
    echo "/tmp/wtStale oldtid" > ~/.codex/reloc/$$
    touch -d "2 hours ago" ~/.codex/reloc/$$
    codex' 2>&1)
  echo "$res" | grep -q "resume -C /tmp/wtStale" && { say "STALE-MARKER-CONSUMED: $res"; fail=1; }
  echo "$res" | grep -q "CODEX-INVOKED:" || { say "LAUNCH-DID-NOT-RUN: $res"; fail=1; }
  res2=$(STUBDIR="$stubdir" LOOPFILE="$LOOP" bash -c '
    export PATH="$STUBDIR:$PATH"; source "$LOOPFILE"
    printf "/tmp/wtFresh newtid %s\n" "$(date +%s)" > ~/.codex/reloc/$$
    codex' 2>&1)
  echo "$res2" | grep -q "resume -C /tmp/wtFresh newtid" || { say "FRESH-MARKER-SKIPPED: $res2"; fail=1; }
  [ "$fail" -eq 0 ] && say "GUARDS-OK" || say "GUARDS-FAILING RELOC_GUARD_MISSING"
  ;;
flags)
  mkstub
  res=$(STUBDIR="$stubdir" LOOPFILE="$LOOP" bash -c '
    export PATH="$STUBDIR:$PATH"; source "$LOOPFILE"
    printf "/tmp/wtF newtid %s\n" "$(date +%s)" > ~/.codex/reloc/$$
    codexs-high resume oldtid' 2>&1)
  echo "$res" | grep -qE "CODEX-INVOKED: --profile codexs -m swe-2-high resume -C /tmp/wtF newtid" \
    || { say "RESUME-FLAGS-BROKEN RELOC_GUARD_MISSING: $res"; fail=1; }
  res=$(STUBDIR="$stubdir" LOOPFILE="$LOOP" bash -c '
    export PATH="$STUBDIR:$PATH"; source "$LOOPFILE"
    printf "/tmp/wtF newtid %s\n" "$(date +%s)" > ~/.codex/reloc/$$
    codex mcp list; [ -f ~/.codex/reloc/$$ ] && echo "MARKER-KEPT"' 2>&1)
  echo "$res" | grep -q "CODEX-INVOKED: mcp list" || { say "MCP-CMD-BROKEN: $res"; fail=1; }
  echo "$res" | grep -q "resume -C" && { say "MARKER-EATEN RELOC_GUARD_MISSING: $res"; fail=1; }
  echo "$res" | grep -q "MARKER-KEPT" || { say "MARKER-LOST RELOC_GUARD_MISSING: $res"; fail=1; }
  res=$(STUBDIR="$stubdir" LOOPFILE="$LOOP" bash -c '
    export PATH="$STUBDIR:$PATH"; source "$LOOPFILE"
    printf "/tmp/wtF newtid %s\n" "$(date +%s)" > ~/.codex/reloc/$$
    codex resume --help; [ -f ~/.codex/reloc/$$ ] && echo "MARKER-KEPT"' 2>&1)
  echo "$res" | grep -q "CODEX-INVOKED: resume --help" || { say "RESUME-HELP-BROKEN: $res"; fail=1; }
  echo "$res" | grep -q "resume -C" && { say "MARKER-EATEN RELOC_GUARD_MISSING: $res"; fail=1; }
  echo "$res" | grep -q "MARKER-KEPT" || { say "MARKER-LOST RELOC_GUARD_MISSING: $res"; fail=1; }
  [ "$fail" -eq 0 ] && say "RESUME-FLAGS-OK" || say "RESUME-FLAGS-BROKEN"
  ;;
ws)
  mkdir -p "$WTD/wt space" "$WTD/wtA"
  out=$(cd "$WTD" && chain "$WTD/wt space" stubtid flag)
  if echo "$out" | grep -q "whitespace"; then say "WS-REFUSED"; else
    say "WS-ACCEPTED RELOC_GUARD_MISSING: $out"; fail=1; fi
  out=$(chain "$WTD/wtA" "bad tid" flag)
  echo "$out" | grep -q "whitespace" || { say "TID-WS-ACCEPTED RELOC_GUARD_MISSING: $out"; fail=1; }
  echo "$out" | grep -q '"sigterm_delivered_to_host": false' || { say "TID-WS-KILLED: $out"; fail=1; }
  echo "$out" | grep -q '"marker_exists": false' || { say "TID-WS-MARKER: $out"; fail=1; }
  out=$(chain wtA stubtid flag)
  echo "$out" | grep -q "must be absolute" || { say "REL-ACCEPTED RELOC_GUARD_MISSING: $out"; fail=1; }
  ;;
esac
cleanup
exit "$fail"
