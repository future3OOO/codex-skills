#!/usr/bin/env bash
# Retained attack probes for codex-relocate + codex-reloc-loop.bashrc.
# Usage: probe-relocate.sh <self|guards|flags|ws> <script> [loop-file]
set -u
HERE="$(cd "$(dirname "$0")" && pwd -P)"
CASE="$1"; SCRIPT="$2"; LOOP="${3:-$HERE/codex-reloc-loop.bashrc}"
fail=0
say() { printf '%s\n' "$*"; }
stubdir=""; mkstub() {
  stubdir=$(mktemp -d)
  printf '#!/bin/bash\necho "CODEX-INVOKED: $*"\n' > "$stubdir/codex"; chmod +x "$stubdir/codex"
}
chain() { # chain <wt> <tid> [flag]  (run from caller cwd)
  bash -c 'python3 "$@"' _ "$HERE/reloc-stubchain.py" "$SCRIPT" "$@" 2>&1
}
cleanup() { [ -n "$stubdir" ] && rm -rf "$stubdir"; rmdir /tmp/wtA /tmp/wtRooted "/tmp/wt space" 2>/dev/null; }

case "$CASE" in
self)
  mkdir -p /tmp/wtA
  out=$(chain /tmp/wtA stubtid flag)
  echo "$out" | grep -q '"sigterm_delivered_to_host": true' || { say "NO-KILL: $out"; fail=1; }
  echo "$out" | grep -q '"marker_exists": true' || { say "NO-MARKER: $out"; fail=1; }
  mc=$(echo "$out" | python3 -c "import sys,json; print(json.loads([l for l in sys.stdin if l.startswith('{')][-1])['marker_content'])" 2>/dev/null)
  echo "$mc" | grep -qE '^/tmp/wtA stubtid [0-9]+$' || { say "MARKER-CONTENT-BAD: '$mc'"; fail=1; }
  out2=$(chain /tmp/wtA stubtid)
  echo "$out2" | grep -q '"sigterm_delivered_to_host": false' || { say "KILLED-WITHOUT-LOOP: $out2"; fail=1; }
  echo "$out2" | grep -q '"marker_exists": false' || { say "MARKER-WITHOUT-LOOP: $out2"; fail=1; }
  echo "$out2" | grep -q "resume -C /tmp/wtA stubtid" || { say "NO-MANUAL-HINT: $out2"; fail=1; }
  [ "$fail" -eq 0 ] && say "SELF-PATH-OK" || say "SELF-PATH-FAILING RELOC_SELF_PATH_MISMATCH"
  ;;
guards)
  mkdir -p /tmp/wtRooted
  out=$(cd /tmp/wtRooted && chain /tmp/wtRooted stubtid flag)
  echo "$out" | grep -q '"sigterm_delivered_to_host": false' && \
  echo "$out" | grep -q '"marker_exists": false' && \
  echo "$out" | grep -q '"exit_code": [1-9]' \
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
  [ "$fail" -eq 0 ] && say "RESUME-FLAGS-OK" || say "RESUME-FLAGS-BROKEN"
  ;;
ws)
  mkdir -p "/tmp/wt space"
  mkdir -p /tmp/wtA
  out=$(cd /tmp && chain "/tmp/wt space" stubtid flag)
  rmdir "/tmp/wt space" 2>/dev/null
  if echo "$out" | grep -q "whitespace"; then say "WS-REFUSED"; else
    say "WS-ACCEPTED RELOC_GUARD_MISSING: $out"; fail=1; fi
  out=$(chain /tmp/wtA "bad tid" flag)
  echo "$out" | grep -q "whitespace" || { say "TID-WS-ACCEPTED RELOC_GUARD_MISSING: $out"; fail=1; }
  echo "$out" | grep -q '"sigterm_delivered_to_host": false' || { say "TID-WS-KILLED: $out"; fail=1; }
  echo "$out" | grep -q '"marker_exists": false' || { say "TID-WS-MARKER: $out"; fail=1; }
  ;;
*) echo "unknown case: $CASE" >&2; exit 2 ;;
esac
cleanup
exit "$fail"
