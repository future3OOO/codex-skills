#!/usr/bin/env bash
# Install the codex-skills estate to ~/.codex/.
# Never writes to ~/.claude or ~/.config/devin or any other harness directory.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd -P)"
DEST="${CODEX_HOME:-$HOME/.codex}"
BACKUP="$HOME/.codex-backups/$(date +%Y%m%d-%H%M%S)"
EXCLUDES=(--exclude='.system/' --exclude='tests/' --exclude='test_*.py' --exclude='test-*.sh' \
  --exclude='__pycache__' --exclude='*.pyc' --exclude='*:Zone.Identifier')

mkdir -p "$DEST" "$BACKUP"
for path in AGENTS.md hooks.json hooks skills; do
  [[ -e "$DEST/$path" ]] && cp -a "$DEST/$path" "$BACKUP/"
done

rsync -a "${EXCLUDES[@]}" "$SRC/hooks" "$SRC/skills" "$DEST/"
cp "$SRC/docs/agents/global-rules.md" "$DEST/AGENTS.md"
chmod +x "$DEST"/hooks/*.py

# rsync excludes stop new copies only; remove matching artifacts already live.
find "$DEST/skills" "$DEST/hooks" -type f \
  \( -name 'test_*.py' -o -name 'test-*.sh' -o -name '*.pyc' -o -name '*:Zone.Identifier' \) -delete
find "$DEST/skills" "$DEST/hooks" -type d \
  \( -name tests -o -name __pycache__ \) -prune -exec rm -rf {} +

# Merge our hook entries into the live hooks.json. A managed command replaces
# every installed copy of itself — a command-keyed append-only merge leaves
# stale matchers, events, and timeouts behind forever. Entries the installer
# does not own (gitnexus and any others) survive. Commands expand $HOME.
python3 - "$SRC/hooks.json" "$DEST/hooks.json" <<'PY'
import json, os, sys
from pathlib import Path

live_path = Path(sys.argv[2])
live = json.loads(live_path.read_text()) if live_path.exists() else {"hooks": {}}
home = os.environ["HOME"]

def expand(value):
    if isinstance(value, str):
        return value.replace("$HOME", home).replace("~/.codex", home + "/.codex")
    if isinstance(value, list):
        return [expand(v) for v in value]
    if isinstance(value, dict):
        return {k: expand(v) for k, v in value.items()}
    return value

managed = expand(json.loads(Path(sys.argv[1]).read_text())["hooks"])
ours = {h.get("command") for gs in managed.values() for g in gs for h in g.get("hooks", [])}

for event, groups in live.setdefault("hooks", {}).items():
    for group in groups:
        group["hooks"] = [h for h in group.get("hooks", []) if h.get("command") not in ours]
    live["hooks"][event] = [g for g in groups if g.get("hooks")]
for event, groups in managed.items():
    live["hooks"].setdefault(event, []).extend(groups)
live_path.write_text(json.dumps(live, indent=2) + "\n")
PY

# config.toml: append the GitNexus MCP server if absent; every other key is
# left untouched (model/provider/auth live there and are not ours to manage).
if ! grep -q '^\[mcp_servers\.gitnexus\]' "$DEST/config.toml" 2>/dev/null; then
  cat >> "$DEST/config.toml" <<'TOML'

[mcp_servers.gitnexus]
command = "node"
args = [ "/home/prop_/.local/share/gitnexus/current/gitnexus/dist/cli/index.js", "mcp" ]
TOML
fi

echo "installed -> $DEST (backup: $BACKUP)"
echo "verify: codex session -> /hooks to trust the new hooks, or run with --dangerously-bypass-hook-trust"
