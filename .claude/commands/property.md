---
description: Show or query a property's current understanding (propkb knowledge base + docrag)
---

You maintain a per-property knowledge base in the `propkb` package (built on `docrag`;
see `PROPERTY-KB.md`). Each property is a corpus under `properties/<slug>/` holding
`facts.yaml`, `timeline.md`, `UNDERSTANDING.md`, `PLAN.md`, and `sources/`. The property
RAG is a separate instance, exposed via the `propkb` MCP server: `property_list`,
`property_status`, `property_ask`, `property_sources`.

The user's request: `$ARGUMENTS` — a property name/address, a question, or empty.

1. **Resolve the property.** Use the `property_list` MCP tool (or
   `python -m propkb ls`). If `$ARGUMENTS` names/implies one, use it. If empty and only
   one exists, use it. If ambiguous, ask.

2. **Question about the property** → answer grounded in its own record first via the
   `property_ask` MCP tool (corpus = slug), and read `facts.yaml` / `UNDERSTANDING.md` /
   `timeline.md` for specifics + provenance (cite T-NN). For any code/ordinance point,
   also ground via `docrag_ask(corpus="building-codes", location="durham-nc")`.
   Web-search for what the record lacks. Reconcile; the corpus governs on NC/Durham law.

3. **Status request** → use `property_status` (returns UNDERSTANDING + PLAN) and
   summarize: snapshot, buildable-zone map, top blockers, decision gates, next actions.

4. **Always surface the CURRENT BOTTLENECK** (`BOTTLENECK.md`) — the one thing blocking
   forward progress and the do-now actions to clear it — plus open questions / next
   actions, so the live edge is unmistakable.

5. Offer to `/intake` new info or **draft** outreach (drafts only — never send).

Research, not legal advice. Cite provenance (T-NN) so every claim is traceable.
