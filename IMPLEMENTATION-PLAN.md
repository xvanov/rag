# Implementation Plan — `/property-research` + Contacts DB

Executes `PRD-property-research.md`. Sequenced to **de-risk unknowns first**, then build
foundations (contacts/sources), then acquisition tooling, then the orchestrating skill.
Durham-first. Each phase is independently testable.

Legend: **S** ≈ ½ day · **M** ≈ 1–2 days · **L** ≈ 3+ days (spike-dependent).

---

## Phase 0 — Feasibility spikes (do FIRST; they gate the design) — M
The two hard unknowns from the PRD. Prove them before building the pipeline around them.

- **S0.1 Durham ArcGIS REST** — find the Esri service endpoints behind `maps.durhamnc.gov`
  (parcel, zoning, flood, soils layers). Confirm a query by **PIN/address** returns structured
  attributes as JSON. → Deliverable: a working `curl`/python query + the endpoint URLs recorded.
  *Risk: endpoints may be private/proxied → fallback is Playwright-only.*
- **S0.2 Playwright on the Durham map** — headless-drive to a parcel, open the **Property Record /
  Advanced Report / Related Records** tabs, trigger the **PDF export**, capture files. → Deliverable:
  a proof script saving a real export. (Playwright already in `.venv`.)
- **S0.3 Zillow reality check** — what WebFetch/WebSearch actually return vs. blocked; lock the
  **gap-drop protocol** (batched ask at run end). → Deliverable: a short note in the source registry.

**Exit criteria:** know, per source, whether we get it via REST / browser / user-drop. Update PRD §4.1 if reality differs.

---

## Phase 1 — Canonical Contacts DB — M  (independent; highest reuse)
- **`propkb/contacts.py`** — SQLite at `properties/.contacts.db` (path via `settings.properties_root()`,
  sibling to `.index`). Schema per PRD §6. Functions: `add()`, `query(jurisdiction=, topic=, category=)`,
  `merge(keep_id, dupe_ids)`, `export()`, `_dedupe_key()`.
- **CLI verbs** in `propkb/__main__.py` (mirror existing `sub.add_parser` pattern):
  `contacts add|query|merge|export|import`.
- **Seed importer** — parse the 1621 Clermont contact roster (UNDERSTANDING.md §8 / facts) → rows
  (Blalock, Eaton, Stanley, Weist, Nahm, Channell, Howrilla, floodplain, tax/GIS, DEQ, …), each with
  `source=1621-clermont-rd-durham/T-NN`, `confidence`, `last_verified`.
- **Verify:** `contacts query --jurisdiction durham-county-nc --topic well` → Eaton; alias merge
  (Amber ↔ Weist's group) works.

## Phase 2 — Source registry — S  (parallel to Phase 1)
- `sources_registry` table in the same `.contacts.db` (or `properties/.sources.yaml`): keyed by
  `jurisdiction + data_type` → `{endpoint/url, method: rest|browser|scrape|manual, notes}`.
- Seed from Phase 0 findings: Durham ArcGIS REST layers, tax card URL, Register of Deeds, FEMA flood,
  NC DEQ solid-waste/brownfields, USFWS wetlands.
- CLI: `propkb sources add|query`.

## Phase 3 — Durham acquisition helpers — L  (depends on Phase 0 + 2)
- **`propkb/acquire/durham.py`**:
  - `resolve_identity(address) -> {pin, reid, geometry, acreage, zoning, owner}` (ArcGIS REST).
  - `pull_gis(pin) -> dict` (REST attributes) + `pull_gis_browser(pin)` (Playwright: 3 tabs + PDF → `sources/gis/`).
  - `pull_tax(pin)` (tax card, assessed value, history, liens flag → `sources/web/`).
- **`propkb/acquire/zillow.py`** — `pull_zillow(address)` best-effort; returns data + `gaps[]` when blocked.
- Reuse `store.ingest` / `store.write_text` / `ocr.render_pdf` for filing + OCR sidecars.
- **Verify:** run on 1621 Clermont (known answers) + one fresh Durham address; check attributes match.

## Phase 4 — Analysis-doc scaffolding — S  (depends on nothing; can parallel 1–3)
- Extend `store.py`: add `MARKET.md`, `AREA.md`, `PROPOSAL.md` stubs to `create()` + entries in
  `paths()`. Mirror the existing stub style.
- Update `PROPERTY-KB.md` doc to list the new files.

## Phase 5 — The orchestrating skill — L  (depends on 1–4)
- **`.claude/commands/property-research.md`** — instructions for the one-shot autonomous pipeline
  (PRD §4), modeled on `intake.md`'s format:
  1. Scaffold (`propkb new`, auto-slug from address) + `resolve_identity`.
  2. **Consult Contacts DB + source registry FIRST.**
  3. Property pull (Phase 3 helpers; batched user-drop ask at END for blocked sources).
  4. Regulatory research (docrag `building-codes/durham-nc` + web), framed by the dev plan.
  5. Skeptical market (primary-source-first + adversarial-verify subagent) → `MARKET.md`.
  6. Area trajectory (comp plans/FLUM/transit/demographics; base/bull/bear) → `AREA.md`.
  7. Proposal + residual model → `PROPOSAL.md`.
  8. Bottleneck (ToC) → `BOTTLENECK.md`; next steps + contact lookup (DB-first, web-fill, **write back**);
     draft outreach (`propkb.email.draft`, **never send**).
  9. `propkb index`; report.
- Fan-out via subagents/workflow; no budget cap. Guardrails: drafts-only, corpus-governs, no silent gaps.
- Register `/property-research` in the skills list.

## Phase 6 — Integration test + polish — M
- Full run on a fresh Durham address end-to-end; compare KB depth to 1621 Clermont.
- Confirm contacts/sources written back + reused on a 2nd run (rediscovery avoided).
- Fix gaps; document usage in `README.md` / `PROPERTY-KB.md`.

---

## Dependency graph
```
Phase 0 (spikes) ──► Phase 3 (acquire)
Phase 1 (contacts) ─┐
Phase 2 (registry) ─┼─► Phase 5 (skill) ──► Phase 6 (integration)
Phase 4 (doc stubs)─┘
```
Phases 1, 2, 4 can start immediately in parallel with the Phase 0 spikes.

## Suggested build order
1. **Phase 0 spikes** (de-risk) + **Phase 1 contacts DB** (seed from 1621 — immediate value even before the skill).
2. Phase 2 registry + Phase 4 doc stubs.
3. Phase 3 acquisition helpers.
4. Phase 5 skill.
5. Phase 6 integration test.

## Decisions locked (from PRD Q&A) + defaults applied
- Durham-first · hybrid pull · one-shot autonomous · all-parties contacts · SQLite · cold-start→intake · no cap.
- **Slug:** auto-derived from address (e.g. `1621-clermont-rd-durham`).
- **Gaps:** one batched user-drop ask at run END (preserve one-shot).
- **Photos:** pull listing + GIS aerial into `sources/photos/`.

## Cross-cutting
- Tests per helper (mock REST/Playwright responses).
- Windows/PowerShell + `.venv` conventions; keep secrets in `.env`.
- Everything grounded/cited; provenance chain (T-NN) preserved; research-not-advice disclaimer.
