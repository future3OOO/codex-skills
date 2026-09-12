# Decision: the Codex plugin stays enabled for non-production use

Date: 2026-08-01. Status: accepted.

`config.toml` enables `codex@openai-codex`. Its named non-production
consumers are the operator's interactive Codex pane (ad-hoc second opinions
and rescue sessions driven by the operator directly) and the plugin's
`codex:rescue` / `codex:setup` skills for explicitly delegated rescue work
outside the governed production workflow.

The governed production contract is unchanged: the sole advisor transport for
`preflight-advice` and `final-review` is
`skills/claude-advisor/scripts/ask-claude-advisor.sh`. The plugin forwarder and
the spawn_agent are not fallbacks for those checkpoints, and no plugin surface
records workflow state.

Revisit only if the operator stops using the pane and rescue flows; then the
plugin should be disabled instead of documented.
