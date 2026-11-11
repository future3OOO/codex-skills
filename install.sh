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
cat "$SRC/docs/agents/global-rules.md" "$SRC/docs/agents/proof-invariants.md" > "$DEST/AGENTS.md"
mkdir -p "$DEST/docs/agents"
cp "$SRC/docs/agents/advisor-role.md" "$SRC/docs/agents/proof-invariants.md" "$DEST/docs/agents/"
if [[ ! -e "$DEST/docs/agents/advisor-launch.json" ]]; then
  cp "$SRC/docs/agents/advisor-launch.json" "$DEST/docs/agents/"
fi
chmod +x "$DEST"/hooks/*.py

# rsync excludes stop new copies only; remove matching artifacts already live.
find "$DEST/skills" "$DEST/hooks" -type f \
  \( -name 'test_*.py' -o -name 'test-*.sh' -o -name '*.pyc' -o -name '*:Zone.Identifier' \) -delete
find "$DEST/skills" "$DEST/hooks" -type d \
  \( -name tests -o -name __pycache__ \) -prune -exec rm -rf {} +

# Merge our hook entries into the live hooks.json. A managed command replaces
# every installed copy of itself — a command-keyed append-only merge leaves
# stale matchers, events, and timeouts behind forever. Entries the installer
# does not own (gitnexus and any others) survive. Commands expand $HOME. Retired
# hooks go with their entries, files and (the poll hold's) leftover FIFOs.
python3 - "$SRC/hooks.json" "$DEST/hooks.json" <<'PY'
import json, os, shutil, sys
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
ours.update(f"{run} {prefix}/hooks/{script}" for prefix in (home + "/.codex", str(live_path.parent)) for run, script in (
    ("python3", "rcf-intake-gate.py"), ("sh", "workflow-command.sh"), ("sh", "workflow-command.sh answered")))

for event, groups in live.setdefault("hooks", {}).items():
    for group in groups:
        group["hooks"] = [h for h in group.get("hooks", []) if h.get("command") not in ours]
    live["hooks"][event] = [g for g in groups if g.get("hooks")]
for event, groups in managed.items():
    live["hooks"].setdefault(event, []).extend(groups)
live_path.write_text(json.dumps(live, indent=2) + "\n")
for script in ("rcf-intake-gate.py", "workflow-command.py", "workflow-command.sh"):
    (live_path.parent / "hooks" / script).unlink(missing_ok=True)
shutil.rmtree(Path(os.environ.get("CODEX_WORKFLOW_STATE_ROOT") or live_path.parent / "state") / "_running", ignore_errors=True)
PY

# config.toml: append the GitNexus MCP server and the workflow's blocking tool if absent: a
# table is appended when the file still parses with it, so one the user wrote in any spelling
# stays as it is; every other key is left untouched (model/provider/auth live there and are
# not ours to manage). The tool is offered to every lead as a direct tool (not through tool
# search or a code cell, which hands back at 30 s), with a timeout above the longest consult.
python3 - "$DEST" <<'PY'
import sys, tomllib
from pathlib import Path

config = Path(sys.argv[1]) / "config.toml"
config.touch()
for table in ("""
[mcp_servers.gitnexus]
command = "node"
args = [ "/home/prop_/.local/share/gitnexus/current/gitnexus/dist/cli/index.js", "mcp" ]
""", f"""
[mcp_servers.workflow]
command = "python3"
args = [ "{sys.argv[1]}/skills/repo-production-workflow/scripts/workflow_tool.py" ]
tool_timeout_sec = 3600
omit_tools_from = [ "code_mode", "deferred" ]
"""):
    try:
        tomllib.loads(config.read_text() + table)
    except tomllib.TOMLDecodeError:
        continue
    with config.open("a") as live:
        live.write(table)
PY

python3 "$DEST/skills/codex-advisor/scripts/advisor-estate.py" codex --prepare

echo "installed -> $DEST (backup: $BACKUP)"
echo "verify: codex session -> /hooks to trust the new hooks, or run with --dangerously-bypass-hook-trust"
