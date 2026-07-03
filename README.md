# Codex Skills

Private working mirror of the local Codex skills installed at
`~/.codex/skills`.

This repository intentionally excludes `.system` skills supplied by the Codex
runtime. It is intended to make changes reviewable through normal GitHub pull
requests before syncing them back into the local Codex skill directory.

## Layout

- `skills/` - custom user skills, one directory per skill.

## Update Workflow

1. Create a branch for one skill or one cohesive skill-system change.
2. Open a pull request describing the source upstream change and the local
   behavior we want to preserve.
3. After merge, sync the changed skill directories back to `~/.codex/skills`.

## Upstream Context

Several skills are derived from or influenced by Matt Pocock's public skills
repo:

https://github.com/future3OOO/skills
