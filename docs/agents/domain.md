# Domain Docs

This repository uses the single-context domain documentation model.

## Read Order

Before using project vocabulary in issue titles, PRDs, implementation plans,
test names, review findings, or architecture notes, read the available docs in
this order:

1. `CONTEXT.md` at the repository root, if it exists.
2. `docs/adr/`, if it exists, for decisions relevant to the area being changed.
3. `.out-of-scope/*.md`, if triaging or reconsidering a rejected enhancement.

If these files or directories do not exist, proceed silently. Do not create
empty scaffolding up front. Domain-modeling skills create `CONTEXT.md` and ADRs
lazily when a term or decision is actually resolved.

## Model

Single-context repositories have one root glossary and one repository-level ADR
directory:

```text
/
|-- CONTEXT.md
|-- docs/
|   |-- adr/
|   `-- agents/
```

`CONTEXT.md` is a glossary, not a spec, scratch pad, or implementation plan.
Use its terms exactly when they exist. If the term you need is missing, either
avoid inventing new language or note the gap for a domain-modeling pass.

ADRs record durable decisions. If proposed work contradicts an ADR, surface the
conflict explicitly instead of silently overriding it.
