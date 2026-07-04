# Issue Tracker

This repository uses GitHub Issues as its only supported issue tracker.

Repository: `future3OOO/codex-skills`

All issue and PRD operations must use the `gh` CLI from inside this checkout.
Do not use Linear, GitLab, local Markdown issue files, or another tracker for
this repository unless the repository contract is explicitly changed.

## Operations

- Create an issue: `gh issue create --title "..." --body "..."`
- Read an issue: `gh issue view <number> --comments --json number,title,body,labels,comments,state,url`
- List issues: `gh issue list --state open --json number,title,labels,url`
- Comment on an issue: `gh issue comment <number> --body "..."`
- Apply a label: `gh issue edit <number> --add-label "..."`
- Remove a label: `gh issue edit <number> --remove-label "..."`
- Close an issue: `gh issue close <number> --comment "..."`

When a skill says "publish to the issue tracker", create a GitHub issue in
`future3OOO/codex-skills`. When a skill says "fetch the relevant ticket", read
the matching GitHub issue with `gh issue view`.

## Pull Requests

Pull requests are not a general triage request surface for this repository.
Triage issue work through GitHub Issues unless the user explicitly names a pull
request.

When a user names a PR, use `gh pr view <number> --comments` and
`gh pr diff <number>` for context, then follow the repository PR workflow and
reviewer gate.

GitHub shares one number space across issues and pull requests. If a bare
`#<number>` is ambiguous, try `gh pr view <number>` first when the request is
about code review or PR state; otherwise use `gh issue view <number>`.

## Agent Discovery

These files are the checked-in local setup contract for tracker-aware skills.
They do not change root `AGENTS.md` authority. Skills that need issue tracker
configuration should read this file directly; later PRD1 consumer-skill slices
must wire explicit references to `docs/agents/` where needed.
