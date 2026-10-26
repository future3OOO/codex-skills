# Blueprint and visual output

Write `external-investigation.md` and `external-investigation-outline.html` in the
same task-specific directory. The Markdown blueprint is authoritative; the HTML
summarizes it. Keep source notes in the report rather than creating another ledger.

## Markdown blueprint

Keep the decision summary short; place supporting detail beneath it. Include only
the following material that affects this investigation:

1. **Objective and scope.** Intended behavior, success criteria, hard constraints,
   non-goals and the local evidence or brief the investigation builds on.
2. **Decisions and alternatives.** Recommended direction, meaningful alternatives,
   including the existing baseline where applicable, trade-offs and why the choice
   fits. Rank by constraint fit and relevant evidence; explain what would reverse it.
3. **Proposed design.** Modules and their responsibilities, Interfaces, Seam placement,
   important flows and state ownership. Describe inputs, outputs, invariants, ordering
   and failures where they affect callers. Name configuration or operational needs
   only when consequential. Distinguish existing, changed and proposed structure.
4. **Reuse map.** For each useful donor, give the repository and file/function link,
   pinned when available; the part to adopt, adapt or learn from; dependencies,
   license obligations, required changes and how it fits the proposed Interface.
   A recommendation to build custom should explain what the precedents still teach.
5. **Change and preservation.** Existing callers and behavior that must remain correct;
   integration points and migration or compatibility requirements when applicable.
   For greenfield work, state the initial scope without inventing existing paths.
6. **First slice and validation.** The smallest end-to-end capability worth implementing,
   its observable acceptance outcome and affected preservation checks. For unresolved
   assumptions, specify a validation operation, expected observation and consequence
   of failure. Proposed checks are not executed proof or a duplicate Behavior Map.
7. **Readiness and gaps.** Unresolved questions, decision impact, next action and the
   evidence needed. Note channels searched and important coverage limits.

Every material recommendation must connect the requirement, the supporting and
challenging evidence, the proposed change and its validation. Make that connection
explicit in prose or a compact decision table; extra identifiers are unnecessary.

## Evidence and readiness

Cite non-obvious factual claims beside the claim. Separate inspected source,
tests read, behavior actually executed, documentation or maintainer assertions,
and inference about applicability. A source authoritative for a protocol requirement
does not thereby establish performance or operational suitability. Record relevant
version, environment and workload limits; historical failures may not apply today.

Use a source's own claim strength. Do not infer deployment or benchmark results
from a README, code presence, popularity, missing metadata or an unsuccessful search.
Explain independent corroboration where available and state when it is absent.

Set one overall readiness disposition and identify which decisions determine it:

- **Ready for planning:** a coherent recommendation fits the known constraints;
  no unresolved question prevents choosing the design. Implementation still needs proof.
- **Conditional:** name the proposed direction, the assumption needing validation
  and the checkpoint before a dependent or hard-to-reverse commitment.
- **Unresolved:** a material constraint or choice cannot yet be settled; identify
  the smallest question or investigation that would unblock it. Do not force a winner.

## Standalone HTML outline

Show the objective and readiness first, then the recommended structure or change,
the main alternatives and their decisive trade-offs. Show Module responsibilities
and important relationships; a stack list alone is insufficient. For an existing
project, distinguish what stays, changes or is proposed. Include the first slice,
linked precedents and top unresolved assumptions beside the decisions they affect.

Use a self-contained HTML document with inline CSS and no external fonts, scripts,
stylesheets or embedded resources that require network access. Ordinary source links
are allowed. Prefer a simple diagram, flow or comparison to a decorative dashboard.
Use semantic headings, readable contrast and a layout that works on narrow screens.
Use explicit evidence labels where helpful; never invent numerical confidence scores.

Check that the HTML and Markdown make the same recommendations, show the same
readiness and blockers, and link to the correct sources. Open the HTML locally in an
available browser to check readability and relationships; if unavailable, report
that visual rendering remains unverified. Do not install a renderer solely for this.
