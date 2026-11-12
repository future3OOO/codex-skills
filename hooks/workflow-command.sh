#!/bin/sh
# Start workflow-command.py only where it has work, so an ordinary shell call or model answer costs no interpreter:
# a shell call whose payload names a workflow entry point, or (`answered`) an answer of a turn that has a workflow command.
payload= running=
if [ "$1" = answered ]; then
  running=${CODEX_WORKFLOW_STATE_ROOT:-${CODEX_HOME:-$HOME/.codex}/state}/_running
  set -- "$running"/*
  [ -e "$1" ] || exit 0
fi
while IFS= read -r line || [ -n "$line" ]; do payload=$payload$line; done
if [ -n "$running" ]; then
  turn=${payload#*\"turn_id\":}; turn=${turn# }; turn=${turn#\"}
  set -- "$running/${turn%%\"*}".*
  [ -e "$1" ] || exit 0
else
  case $payload in
  *ask-codex-advisor.sh*|*repo-production-workflow/scripts/workflow.py*|*repo-context-forge/scripts/bootstrap.py*) ;;
  *) exit 0 ;;
  esac
fi
printf %s "$payload" | python3 "${0%.sh}.py"
