---
name: improve-codebase-architecture
description: Scan a codebase for deepening opportunities, present them as a visual HTML report, then grill through whichever one the user picks. Use when the user wants to improve architecture, find refactoring opportunities, consolidate tightly-coupled modules, or make a codebase more testable and AI-navigable.
---

# Improve Codebase Architecture

Surface architectural friction and propose **deepening opportunities** —
refactors that turn shallow Modules into deep ones. The aim is testability and
AI-navigability.

This command is _informed_ by the project's domain model and built on a shared
design vocabulary:

- Use `$codebase-design` for the architecture vocabulary: Module, Interface,
  Implementation, Depth, Seam, Adapter, Leverage, and Locality.
- Use `$domain-modeling` for `CONTEXT.md`, ADR formats, and durable domain
  language.

## Process

### 1. Explore

Read the project's domain glossary and any ADRs in the area you're touching first.

Then use the Agent tool with `subagent_type=Explore` to walk the codebase. Don't follow rigid heuristics — explore organically and note where you experience friction:

- Where does understanding one concept require bouncing between many small modules?
- Where are modules **shallow** — interface nearly as complex as the implementation?
- Where have pure functions been extracted just for testability, but the real bugs hide in how they're called (no **locality**)?
- Where do tightly-coupled modules leak across their seams?
- Which parts of the codebase are untested, or hard to test through their current interface?

Apply the **deletion test** to anything you suspect is shallow: would deleting it concentrate complexity, or just move it? A "yes, concentrates" is the signal you want.

### 2. Present candidates as an HTML report

Write a self-contained HTML file to the OS temp directory so nothing lands in
the repo. Resolve the temp dir from `$TMPDIR`, falling back to `/tmp` (or
`%TEMP%` on Windows), and write to
`<tmpdir>/architecture-review-<timestamp>.html`. Open it for the user and tell
them the absolute path.

The report uses Tailwind via CDN for layout and Mermaid via CDN for
graph-shaped diagrams. Each candidate gets a before/after visualization.

For each candidate, render a card with:

- **Files** — which files/modules are involved
- **Problem** — why the current architecture is causing friction
- **Solution** — plain English description of what would change
- **Benefits** — explained in terms of locality and leverage, and also in how tests would improve
- **Before / After diagram** — side-by-side, illustrating the shallowness and
  the deepening
- **Recommendation strength** — `Strong`, `Worth exploring`, or `Speculative`

End with a **Top recommendation** section naming which candidate to tackle
first and why.

Use `CONTEXT.md` vocabulary for the domain and `$codebase-design` vocabulary
for the architecture. If `CONTEXT.md` defines "Order," talk about "the Order
intake Module" — not "the FooBarHandler," and not "the Order service."

**ADR conflicts**: if a candidate contradicts an existing ADR, only surface it when the friction is real enough to warrant revisiting the ADR. Mark it clearly (e.g. _"contradicts ADR-0007 — but worth reopening because…"_). Don't list every theoretical refactor an ADR forbids.

See [HTML-REPORT.md](HTML-REPORT.md) for the scaffold, diagram patterns, and
style guidance.

Do NOT propose Interfaces yet. After the report is written, ask the user:
"Which of these would you like to explore?"

### 3. Grilling loop

Once the user picks a candidate, run `$grilling` to walk the design tree with
them: constraints, dependencies, the shape of the deepened Module, what sits
behind the Seam, what tests survive.

Side effects happen inline as decisions crystallize:

- **Naming a deepened Module after a concept not in `CONTEXT.md`?** Add the
  term to `CONTEXT.md` through `$domain-modeling`. Create the file lazily if it
  doesn't exist.
- **Sharpening a fuzzy term during the conversation?** Update `CONTEXT.md` right there.
- **User rejects the candidate with a load-bearing reason?** Offer an ADR, framed as: _"Want me to record this as an ADR so future architecture reviews don't re-suggest it?"_ Only offer when the reason would actually be needed by a future explorer to avoid re-suggesting the same thing — skip ephemeral reasons ("not worth it right now") and self-evident ones. Use `$domain-modeling` for the ADR format.
- **Want to explore alternative Interfaces for the deepened Module?** Use
  `$codebase-design` and its design-it-twice pattern.
