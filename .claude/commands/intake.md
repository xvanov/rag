---
description: Intake new property info (file/paste/url/email) into the per-property knowledge base, OCR scans, update UNDERSTANDING + PLAN, then talk next actions
---

You maintain a per-property knowledge base in the `propkb` package (built on the
general `docrag` engine; see `PROPERTY-KB.md`). Each property is a corpus under
`properties/<slug>/` with: `facts.yaml` (structured), `timeline.md` (dated provenance
chain, T-NN), `UNDERSTANDING.md` (living synthesis), `PLAN.md` (dev concept +
projection), and `sources/{docs,emails,gis,web,photos}/`. The property RAG is a
SEPARATE instance from building-codes (`properties/.index/`).

CLI (use `.venv/Scripts/python.exe -m propkb ...`): `ls`, `new --slug --address`,
`paths --slug`, `ingest --slug --file --kind`, `index --slug`,
`ocr-render --file --out`, `email-sync --slug`.

The user is handing you new info: `$ARGUMENTS` (a file path, pasted text, a URL, or
`email --slug <slug>` to pull from Gmail). Loop:

1. **Identify the property.** `python -m propkb ls`; match by address/PIN/context. If
   none fits, scaffold one (`python -m propkb new ...`). Never guess silently.

2. **Acquire the artifact.**
   - File/text/url → file it: `python -m propkb ingest --slug <slug> --file "<path>" --kind <docs|gis|emails|web|photos>`, or for pasted text/email body write a `.md` into the right `sources/` subfolder.
   - `email` → `python -m propkb email-sync --slug <slug> [--days N]` pulls relevant unseen mail and files `.eml` + parsed `.md` into `sources/emails/`. Then continue the loop over what it filed.

3. **OCR scanned PDFs/images (vision, not algorithmic).** If an artifact is a scanned
   PDF/image, render it and read it yourself: `python -m propkb ocr-render --file "<pdf>" --out <tmp>`
   → Read the PNGs → write your transcription as `sources/docs/<name>.ocr.md` (via
   `ingest`/a `.md` write). That sidecar indexes and becomes searchable. Don't file blind.

4. **Extract facts.** Pull every decision-relevant fact; note who said it, when, and
   whether authoritative (agency/primary) vs. soft (opinion).

5. **Append the chain.** Add a `T-NN` entry to `timeline.md` (date, source, substance,
   artifact path). Update `facts.yaml` for changed/added facts, each `src: [T-NN]`.
   Append, never rewrite history; flag contradictions explicitly.

6. **Regenerate UNDERSTANDING.md** (snapshot, buildable-zone map, constraint stack,
   open questions, contacts, doc index; bump date + confidence). **Update PLAN.md** if a
   decision gate, cost line, or concept moved.

6b. **Re-run the bottleneck analysis (Theory of Constraints).** Rewrite `BOTTLENECK.md`
   to name the ONE current constraint blocking forward progress and the precise actions
   to clear it (do-now vs. WAITING-on-others vs. proactive long-lead items to start while
   waiting), plus kill criteria. If the bottleneck CHANGED from the prior state, append a
   dated entry to `BOTTLENECK-LOG.md` (append-only; one per day / per change). The whole
   project is a pipeline — we focus on the current bottleneck until it clears, the project
   completes, or it's killed (won't buy — not profitable).

7. **Re-index:** `python -m propkb index --slug <slug>` (incremental; needs Azure embed
   creds — if unavailable, say so and skip).

8. **Ground** new legal/code claims in `docrag_ask(corpus="building-codes",
   location="durham-nc")`; query the property's own record with the `property_ask` MCP
   tool. Web-search for current process/fees/precedent.

9. **Report + converse.** What came in, what changed, new contradictions/blockers,
   recommended next actions. If new info implies questions for an agency/party, offer to
   **draft** them (drafts only — `propkb.email.draft` appends to Gmail Drafts; NEVER send).

Research, not legal advice. Don't help evade inspections/permitting — redirect to the
legitimate path.
