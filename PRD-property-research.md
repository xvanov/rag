# PRD — `/property-research` command + canonical Contacts DB

**Status:** draft for review · **Author:** Kalin + Claude · **Date:** 2026-07-07
**Origin:** systematize the 1621 Clermont Rd playbook (property KB built over T-01…T-36)
into a repeatable, one-shot cold-start research command.

---

## 1. Goal

One command — `/property-research` — takes **an address + a development plan** and, in a
single autonomous run, builds a complete initial knowledge base for that property: scours the
web + docrag for everything decision-relevant, produces the four analyses (property, regulatory/
dev-plan, market, area-trajectory) plus a proposal analysis, names the current **bottleneck**,
and lists **next steps** (including real contact info + draft outreach). It **learns as it goes**:
every contact and useful source it discovers is written to shared, canonical stores so the next
property doesn't start from zero.

**Non-goal:** ongoing incremental updates (that stays with `/intake`) and querying an existing
property (`/property`). See §7.

---

## 2. Inputs

- **Address** (required) — street address; the command resolves PIN/REID and geometry.
- **Development plan** (required, freeform) — the user's intent ("2 SFH or a duplex on the
  buildable upland," "self-storage," "hold as rental," etc.). Drives the regulatory + proposal
  analyses.
- **Optional:** budget/depth override (default = no cap, max thoroughness), and any files the
  user already has (drop-ins for bot-blocked sources — see §4.2).

---

## 3. Scope (v1)

- **Jurisdiction: Durham County / Triangle first.** Hard-wire Durham sources (below). Architect
  the jurisdiction-specific bits behind a small "source registry" so other counties can be added
  later, but do NOT build a general any-US-county resolver in v1.
- **docrag corpus:** `building-codes`, `location=durham-nc` (already covers Durham UDO + NC state
  + model codes; Alamance jurisdictions available if the address falls there).

---

## 4. Pipeline (one-shot autonomous, no cap)

The command fans out with subagents/workflow; each phase writes to the property KB as it lands.
**Consults the canonical Contacts DB + source registry BEFORE re-discovering anything.**

### 4.0 Scaffold + identity
- `propkb new --slug <derived> --address "<addr>"`.
- Resolve **PIN / REID / parcel geometry / acreage / zoning / owner** from the address.

### 4.1 Property data acquisition — **HYBRID** (auto where possible; ask user for gaps)
Auto-pull, in priority order, and clearly list what it could NOT get (never silently omit):
- **Durham County GIS — `maps.durhamnc.gov`** (Esri/ArcGIS). Two mechanisms:
  - **ArcGIS REST** — query the parcel/feature services by PIN/address for structured attributes
    (the fast, robust path; discover the service endpoints once and register them).
  - **Playwright browser automation** (Playwright is already installed) — for the three tabs
    (**Property Record, Advanced Report, Related Records**) and the **PDF export**, when data
    isn't in the REST layer. Save exports into `sources/gis/`.
- **Durham County tax record** — scrape parcel/tax card, assessed value, tax history, liens flag.
- **Zillow** — best-effort (WebFetch/search); it bot-blocks, so when blocked the command
  **explicitly asks the user to drop in the Zillow page/export** rather than returning thin data.
  Capture: listing price/history, DOM, status, beds/baths/sqft, prior sales, tax history, any
  Zestimate/rent estimate, photos, listing remarks.
- **Other public sources on the fly** — Register of Deeds (deeds/plats), FEMA flood, NC DEQ
  (solid waste / brownfields / well records), USFWS wetlands, Census, school assignment, etc.,
  as relevant to the address + plan.
- OCR any scanned PDFs (vision) into `.ocr.md` sidecars, per the `intake` pattern.

### 4.2 Regulatory / development-plan research
- Ground every code/ordinance/statute claim in **docrag** (`building-codes`, `durham-nc`).
- Web for current process/fees/forms/precedent the corpus lacks.
- Framed by the **dev plan**: zoning use-permissions, overlays (flood/watershed/buffer), utilities/
  access, environmental (landfill/brownfield), subdivision path, permit path.

### 4.3 Market analysis — **skeptical by design**
- **Primary sources first:** actual closed sales (county records), permits issued, Census/BLS,
  rent data — NOT agent narratives.
- **Discount promotional framing** ("great time to buy") — treat agent/listing copy as biased;
  corroborate every claim with an independent primary source.
- **Adversarial verification** (workflow pattern): a second agent tries to refute the market read.
- Output: realistic comp ranges, $/sf, absorption/DOM, rent, with confidence flags + "verify via
  MLS/agent CMA" where data is gated.

### 4.4 Area trajectory — 10 / 20 / 30 / 50 / 100 years
- Sources: municipal **comprehensive / future-land-use plans**, zoning trajectory, transit &
  infrastructure plans (roads, transit, utilities, Research Triangle growth), demographic
  projections, historical trajectory of the immediate area.
- Inherently uncertain → present as **scenarios** (base / bull / bear) with the drivers, not false
  precision. Explicitly separate "planned/funded" from "speculative."

### 4.5 Proposal analysis (the user's dev idea)
- Test the dev plan against §4.2 constraints + §4.3 market + §4.4 trajectory.
- Residual land-value model (max bid) with cost/revenue ranges, like 1621 Clermont T-36.
- Alternatives considered + ruled out, with grounded reasons.

### 4.6 Bottleneck + next steps + contacts
- **BOTTLENECK.md** (Theory of Constraints): the ONE current constraint, do-now vs. waiting vs.
  long-lead, kill criteria.
- **Next steps:** concrete actions + the **right people** — look up real contact info (name,
  role, email, phone) for each needed agency/vendor. **Check the Contacts DB first**; only
  web-discover what's missing; **write new finds back** to the DB.
- **Draft** outreach emails (drafts only — `propkb.email.draft`; **NEVER send**).

---

## 5. Outputs (per property)

Standard propkb set **+ three analysis docs**:
- `facts.yaml`, `timeline.md`, `UNDERSTANDING.md`, `PLAN.md`, `BOTTLENECK.md` (+ `BOTTLENECK-LOG.md`)
- **`MARKET.md`** — skeptical market analysis (§4.3)
- **`AREA.md`** — 10–100-yr area trajectory (§4.4)
- **`PROPOSAL.md`** — dev-idea analysis + residual model (§4.5)
- `sources/{docs,gis,web,photos,emails}/` populated; corpus re-indexed (`propkb index`).

---

## 6. Canonical Contacts DB (shared, grows across properties)

**Storage: SQLite** — `properties/.contacts.db` (sibling to `.index`), one canonical table.
**Auto-consulted before re-discovering; auto-appended after each run.**

Suggested schema (refine at build):
| field | notes |
|---|---|
| `id` | pk |
| `name` | person or office |
| `org` | agency / firm |
| `role` | title / function |
| `category` | agency \| vendor (attorney/engineer/REC/surveyor) \| agent \| seller \| other |
| `jurisdiction` | e.g. durham-county-nc, nc-state, federal |
| `topic` | tags: zoning, floodplain, solid-waste, well, brownfield, title, geotech, water-main… |
| `email`, `phone`, `address` | contact channels |
| `source` | how learned (property slug + T-NN, or URL) |
| `confidence` | confirmed \| inferred |
| `last_verified` | date |
| `notes` | free text |

- **Dedup/merge:** key on (name|org + jurisdiction + topic); expose a merge for aliases/roll-ups
  (e.g., "Amber @ 919-560-4326" ↔ Justin Weist's Development Review group).
- **CLI:** `propkb contacts add|query|merge|export` (query by jurisdiction/topic).
- **Seed it** from the 1621 Clermont roster (Blalock, Eaton, Stanley, Weist, Nahm/Morningstar,
  Channell, Howrilla, DEQ, floodplain, tax/GIS, etc.) as the first import.

**Source registry (parallel idea):** when the command finds a *new useful data source* (a portal,
a REST endpoint, a dataset), record it so future runs reuse it instead of rediscovering — a small
`sources_registry` table or YAML keyed by jurisdiction + data-type.

---

## 7. Relationship to existing skills

- **`/property-research` = cold start.** Scaffolds the property + does the initial deep dive.
- **`/intake` = increments.** Ongoing new info (emails, docs) after the cold start.
- **`/property` = queries.** Ask the existing record.
- property-research **hands off** to intake; it does not re-run/refresh an existing property
  (avoids clobbering hand-edits).

---

## 8. Principles & guardrails

- **Corpus governs on NC/local law;** flag divergences, don't average (per CLAUDE.md).
- **Primary-source-first + skepticism** on market/agent claims; adversarial verification.
- **Cite everything;** mark primary vs. secondary; provenance chain (T-NN) preserved.
- **Drafts only — never send** third-party email. Research, not legal/engineering advice.
- **Don't help evade** permitting/inspection; redirect to the legitimate path.
- **No silent gaps** — always report what couldn't be pulled and why.

---

## 9. Build notes (feasibility)

- **Playwright** already in `.venv` → drives the Durham map exports/tabs + any JS-gated page.
- **ArcGIS REST** — Durham publishes Esri services; discover parcel/flood/zoning layer endpoints
  once, register them. Query by PIN/address for structured attributes (no browser needed).
- Reuse the **propkb** engine (scaffold/ingest/ocr-render/index/email.draft) + **docrag** MCP +
  subagent/workflow fan-out. Contacts DB is a new small module (`propkb/contacts.py`) + CLI verbs.

---

## 10. Open questions / v2

- Generalize beyond Durham (jurisdiction resolver for any US county).
- Optional paid data APIs (ATTOM/Regrid/Zillow-RapidAPI) if best-effort scraping proves too thin.
- Auto-refresh mode (scheduled re-pull of listing status / new permits).

## 11. Success criteria

- Given only an address + a plan, one run produces a KB comparable to what 1621 Clermont took
  many manual sessions to build — grounded, cited, with a defensible bottleneck + max-bid.
- The Contacts DB measurably reduces contact-rediscovery on the 2nd+ Durham property.
