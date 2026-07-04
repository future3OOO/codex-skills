---
name: to-prd
description: Turn the current conversation context into a PRD and publish it to the project issue tracker. Use when user wants to create a PRD from the current context.
---

This skill takes the current conversation context and codebase understanding and produces a PRD. Do NOT interview the user — just synthesize what you already know.

Issue tracker and triage label vocabulary come from the target checkout. Read
`docs/agents/issue-tracker.md`, `docs/agents/triage-labels.md`, and
`docs/agents/domain.md` when present. If they are missing, inspect the current
repo's Git remote and labels directly; do not assume this skills repo is the
project tracker.

## Process

1. Explore the repo to understand the current state of the codebase, if you haven't already. Use the project's domain glossary vocabulary throughout the PRD, and respect any ADRs in the area you're touching.

2. Sketch the public seams where the feature should be tested. Existing seams
   are preferred. Use the highest seam that proves user-visible behavior. If a
   new seam is needed, propose one at the highest point possible; the ideal is
   one strong seam, not many small ones.

Check with the user that these seams match their expectations when the PRD is
not already fully specified.

3. Write the PRD using the template below, then publish it to the project issue tracker. Apply `ready-for-agent` when the PRD is fully specified and that label exists in the target repo; otherwise apply `needs-triage` so it enters the normal triage flow.

When the target repo's `AGENTS.md` or production workflow rules are stricter
than this template, the PRD must preserve the stricter rules.

<prd-template>

## Problem Statement

The problem that the user is facing, from the user's perspective.

## Solution

The solution to the problem, from the user's perspective.

## User Stories

A LONG, numbered list of user stories. Each user story should be in the format of:

1. As an <actor>, I want a <feature>, so that <benefit>

<user-story-example>
1. As a mobile bank customer, I want to see balance on my accounts, so that I can make better informed decisions about my spending
</user-story-example>

This list of user stories should be extremely extensive and cover all aspects of the feature.

## Implementation Decisions

A list of implementation decisions that were made. This can include:

- The modules that will be built/modified
- The interfaces of those modules that will be modified
- Technical clarifications from the developer
- Architectural decisions
- Schema changes
- API contracts
- Specific interactions

Do NOT include specific file paths or code snippets. They may end up being outdated very quickly.

Exception: if a prototype produced a snippet that encodes a decision more
precisely than prose can, such as a state machine, reducer, schema, or type
shape, inline only the decision-rich part and say it came from a prototype.

## Testing Decisions

A list of testing decisions that were made. Include:

- A description of what makes a good test (only test external behavior, not implementation details)
- Which public seams will be tested
- Prior art for the tests (i.e. similar types of tests in the codebase)

## Out of Scope

A description of the things that are out of scope for this PRD.

## Further Notes

Any further notes about the feature.

</prd-template>
