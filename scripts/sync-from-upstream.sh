#!/usr/bin/env bash
# Port upstream claude-skills changes into this tree (vendor-drop model).
# Auto-transforms shared files via estate_xform.py to-codex; diverged files are
# reported for manual merge, never overwritten. Reviews then commit by hand.
set -euo pipefail
cd "$(dirname "$0")/.."

MANUAL=(
  CLAUDE.md AGENTS.md settings.json settings.local.json config.toml
  README.md install.sh mcp_config.json hooks.json decisions.md
  hooks/lib/hook_input.py
  skills/code-review/SKILL.md
)
MANUAL_PREFIX=(skills/codex-advisor/ skills/claude-advisor/)
is_manual() {
  local f="$1" m
  for m in "${MANUAL[@]}"; do [[ "$f" == "$m" ]] && return 0; done
  for m in "${MANUAL_PREFIX[@]}"; do [[ "$f" == "$m"* ]] && return 0; done
  return 1
}

git fetch claude
last=$(cat .upstream-sync 2>/dev/null || git merge-base HEAD claude/main)
new=$(git rev-parse claude/main)
[[ "$last" == "$new" ]] && { echo "already at claude/main ($new)"; exit 0; }

echo "syncing $last..$new"
manual_hits=()
while IFS=$'\t' read -r status file extra; do
  case "$status" in
    M|A|T)
      if is_manual "$file"; then manual_hits+=("$file"); continue; fi
      dest=$(python3 -c "import sys; sys.path.insert(0,'scripts'); from estate_xform import xform_path; print(xform_path('$file','codex'))")
      git show "claude/main:$file" | python3 scripts/estate_xform.py to-codex "$file" > /tmp/x.$$
      mkdir -p "$(dirname "$dest")"; mv /tmp/x.$$ "$dest"; echo "  ported  $file -> $dest"
      ;;
    D)
      dest=$(python3 -c "import sys; sys.path.insert(0,'scripts'); from estate_xform import xform_path; print(xform_path('$file','codex'))")
      [[ -e "$dest" ]] && { rm "$dest"; echo "  deleted $dest"; }
      ;;
    R*)
      if is_manual "$extra"; then manual_hits+=("$extra"); continue; fi
      dest=$(python3 -c "import sys; sys.path.insert(0,'scripts'); from estate_xform import xform_path; print(xform_path('$extra','codex'))")
      git show "claude/main:$extra" | python3 scripts/estate_xform.py to-codex "$extra" > "$dest"
      old=$(python3 -c "import sys; sys.path.insert(0,'scripts'); from estate_xform import xform_path; print(xform_path('$file','codex'))")
      [[ -e "$old" && "$old" != "$dest" ]] && rm "$old"
      echo "  renamed $file -> $extra (as $dest)"
      ;;
  esac
done < <(git diff --name-status "$last" claude/main)

echo "$new" > .upstream-sync
git add -A
echo; echo "=== staged ==="; git status --short
if ((${#manual_hits[@]})); then
  echo; echo "!! diverged files changed upstream — manual merge required:"
  printf '   %s\n' "${manual_hits[@]}"
  echo "   inspect: git diff $last claude/main -- <file>"
fi
echo; echo "review the staged diff, then commit. Residual hits were printed to stderr."
