# HTML Report Format

Render the architecture review as a single self-contained HTML file in the OS
temp directory. Tailwind and Mermaid come from CDNs. Mermaid handles call graphs,
dependencies, and sequences; hand-built HTML/CSS or inline SVG handles diagrams
where the point is mass, depth, or a collapsed call graph.

## Scaffold

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>Architecture review - {{repo name}}</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script type="module">
      import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs";
      mermaid.initialize({ startOnLoad: true, theme: "neutral", securityLevel: "loose" });
    </script>
  </head>
  <body class="bg-stone-50 text-slate-900 font-sans">
    <main class="max-w-5xl mx-auto px-6 py-12 space-y-12">
      <header>...</header>
      <section id="candidates" class="space-y-10">...</section>
      <section id="top-recommendation">...</section>
    </main>
  </body>
</html>
```

## Candidate Card

Each candidate is one `<article>`:

- **Title** — short, names the deepening.
- **Badge row** — recommendation strength plus dependency category.
- **Files** — monospaced list.
- **Before / After diagram** — the centerpiece.
- **Problem** — one sentence.
- **Solution** — one sentence.
- **Wins** — short bullets using `$codebase-design` vocabulary.
- **ADR callout** — only when the candidate conflicts with an existing ADR.

## Diagram Patterns

- Mermaid flowchart for dependency or call graphs.
- Mermaid sequence diagram for before/after round trips.
- Hand-built boxes and arrows when Mermaid layout fights the point.
- Cross-section diagram for shallow layered pass-throughs.
- Mass diagram for Interface surface area versus Implementation depth.
- Call-graph collapse when many helpers should become hidden Implementation.

## Style

Keep prose sparse. Let diagrams carry the weight. Use exactly the vocabulary
from `$codebase-design`: Module, Interface, Implementation, Depth, deep,
shallow, Seam, Adapter, Leverage, and Locality.
