# PRD — Listings Intelligence ("Scout")

**A natural-language real-estate investment platform: from an investment thesis to a ranked shortlist of listings/parcels that are semantically matched, commute-reachable, demonstrably buildable under local code (cited), and scored on returns — as one agentic loop.**

- **Status:** Draft v0.1 — for review
- **Author:** research session, 2026-07-10
- **Owner:** kalin.ivanov@innergy.com
- **Relationship to existing work:** extends the `docrag` monorepo. Reuses the docrag retrieval engine, `propkb` acquisition/registry, and the `.claude` slash-command TUI workflow. Sibling to `PRD-property-research.md`.

---

## 1. Vision & end goal

Today the repo answers **"what does the code say?"** (docrag) and **"tell me everything about this one property"** (propkb / `property-research`). Both start from an address the user already chose.

Scout adds the missing front of the funnel: **discovery and ranking across the whole market**. The user describes an investment thesis (or a target client/tenant), and the platform finds, filters, scores, and monitors the best *candidate* listings/parcels — then hands the winner to `property-research` for the deep dive.

The long arc: a **localized, agentic real-estate knowledge & decision platform**. Near-term we build the knowledge base and search/analytics. Far-term the AI proposes and defends investment decisions. This PRD covers the near-term platform; autonomous decisioning is explicitly out of scope for v1.

**End-goal user statement:** *"Describe a client, a budget, a strategy, or a set of constraints — get back the best real estate to buy, build on, rent out, or flip, with the reasoning and the citations."*

---

## 2. Positioning — why this wins

Research into ~40 competitors (PropStream, DealMachine, Mashvisor, PropertyRadar, Reonomy, AirDNA, Regrid, ATTOM, Zoneomics, TestFit, TravelTime, and 2024–26 AI-native entrants like Build, Cambio, Fifth Dimension, Rockhood) shows the market is **siloed**:

| Layer | Who owns it | What they miss |
|---|---|---|
| Lead/listing data | PropStream, DealMachine, PropertyRadar | no real zoning/build depth |
| Property records | Regrid, ATTOM, CoreLogic/Cotality | data only, no reasoning |
| Zoning/feasibility | Zoneomics, TestFit, Archistar | national-*shallow* or geometry-only; no building-code reasoning; no citations |
| Commute/location | TravelTime, Walk Score, Local Logic | raw API; nobody ranks *listings* by multi-point access |
| AI research analyst | Build, Cambio, Fifth Dimension | institutional CRE, or consumer — not the local infill/land investor |

**No product reasons across listing text + parcel/zoning + construction feasibility + commute + investment math in one agentic loop.**

Our unfair advantage is the **docrag corpus** — IBC + 9 NC state code books + NCGS 160D + NCDOT + per-jurisdiction UDOs, with **cited provisions**. A "can I actually build X here, and what does the code require?" answer *with citations* is a moat none of the incumbents have and none can build quickly. Scout is the acquisition-search layer that monetizes that moat.

**Positioning line:** *The only platform that turns a plain-English investment thesis into a ranked shortlist of parcels/listings that are (a) semantically matched, (b) reachable from your key destinations, (c) demonstrably buildable under local zoning + NC building code with cited provisions, and (d) scored on returns.*

---

## 3. Scope

### v1 (this PRD)
- **Geography:** NC Triangle — Durham, Wake, Alamance (exactly where docrag zoning + `propkb.acquire` parcel/flood coverage already exists).
- **Strategies scored:** all four, phased — **land-to-build** (leads on our zoning/feasibility moat, ships first), **buy-and-hold rental**, **fix-and-flip**, **short-term rental (STR)**.
- **Listing types:** vacant land/lots, SFH, small multifamily (2–4 units).
- **Surface:** the existing Claude Code TUI via a new `/scout` skill + MCP tools; the stdlib web UI (`server/`) gets read-only search/analytics views later in v1.

### Out of scope (v1)
- Autonomous investment decisions / auto-offers (far-term).
- Markets outside the Triangle.
- Commercial/industrial CRE, large multifamily (5+).
- Public consumer product / multi-tenant SaaS (personal-use scale first).
- Buyer/seller transaction execution, CRM, dialer, marketing.

---

## 4. Users & top use cases

Primary user = the operator (you): investor + small developer doing infill/land in the Triangle.

1. **Thesis search.** "Lots under $150k within 25 min of downtown Durham, buildable for 2+ units by-right, rentable at $1.6k+." → ranked shortlist with rationale.
2. **Semantic prose search.** Find listings whose *description text* implies something structured filters miss — "existing septic + well," "curb cut present," "flag lot," "ADU potential," "motivated/as-is/cash-only," "seller financing." Meaning, not keywords.
3. **Multi-point commute filter.** Candidates within X min of *several* destinations at once (airport + downtown + Jordan Lake), traffic-aware, best/typical/peak-case.
4. **Realtor intelligence.** Group/rank listing agents by volume, geography, price band, below-AVM cadence, relist/price-drop behavior. (Includes an internal-only, firewalled name→ethnicity signal — see §9.)
5. **Market statistics.** $/sf distributions, days-on-market, inventory, price-drop rates, absorption — by ZIP/neighborhood/strategy.
6. **Standing deal monitor + notifications.** A saved thesis that alerts (via existing `NTFY_TOPIC`) only when a new listing is *matched + reachable + buildable + pencils out*, with a one-paragraph cited rationale.
7. **Handoff to deep research.** One command promotes a shortlisted listing into a full `propkb` property + runs `/property-research`.

---

## 5. Feature epics

Ranked; each maps to a competitor gap.

**E1 — Listing ingestion & normalization.** Pull listings for the target geo into a typed store (see §6/§7). Fields: address, price, price history, beds/baths/sqft, lot size, listing description (free text), listing agent + brokerage, status, DOM, photos, MLS/source id.

**E2 — Semantic search over listing prose + photos.** Embed descriptions (and later photo captions via vision) into a docrag instance; retrieve by meaning. This is E2 built on the existing engine — near-zero new retrieval code.

**E3 — Structured/analytical query & statistics.** Typed SQLite table for aggregations the vector store can't do ($/sf, DOM distributions, comps, agent rollups). Powers stats + hard filters (price/beds/geo) that pre-filter before semantic rerank.

**E4 — Buildability verdict (the moat).** For any listing → resolve parcel/zoning/flood via `propkb.acquire`, then ask docrag `docrag_ask(location=<governing jurisdiction>)`: allowed uses, by-right density, setbacks, height, parking, overlays, watershed — **each fact cited** to the governing UDO/NC code provision. Output a structured verdict + citations.

**E5 — Multi-point commute ranking.** Rank candidates by *combined* traffic-aware travel time to N user destinations. Departure-time scenarios (best/typical/peak). POI/context counts (hotels, amenities) via Places.

**E6 — Feasibility-adjusted investment scoring.** Per-strategy pro-forma where upside is conditioned on what's *legally buildable* (by-right units, ADU eligibility, subdivision potential), not just as-is comps. Outputs a score + the assumptions it depends on.

**E7 — Realtor / supply-side intelligence.** Cluster listing agents; surface below-AVM and motivated-seller signals; group by behavior. (Firewalled demographic signal per §9.)

**E8 — Standing deal monitor + notifications.** Saved theses, periodic re-run, threshold alerts via `NTFY_TOPIC`; dedup already-seen listings (mirror `propkb.email` `.processed.json` pattern).

**E9 — Web UI views.** Read-only search + map + stats + saved-thesis dashboard on the existing stdlib server.

**E10 — Entitlement-path playbook** (stretch). When something isn't by-right: variance vs. special-use vs. rezoning, responsible board, typical timeline/fees, ordinance triggers — grounded in local UDO + NCGS 160D. Productizes the existing `BOTTLENECK` logic.

---

## 6. Architecture

**Principle: reuse, don't rebuild.** The docrag engine is already fully parameterized on `DOCRAG_DOCS_ROOT` / `DOCRAG_INDEX_DIR`; `propkb` proves the "same engine, different data instance" pattern. Scout is a third instance plus a thin typed-analytics layer and a data-adapter layer.

```
                         /scout  (.claude/commands/scout.md)  ─┐   Claude orchestrates
                                                               │
   listings_mcp (FastMCP, stdio)  ── listing_search / listing_stats / listing_buildability / listing_score
                                                               │
   ┌───────────────────────────────────────────────────────────────────────────────────┐
   │  listings/  package                                                                  │
   │                                                                                      │
   │   settings.py   clone of propkb/settings.py → DOCRAG_DOCS_ROOT=listings/,            │
   │                 DOCRAG_INDEX_DIR=listings/.index/   (semantic engine, free)          │
   │                                                                                      │
   │   sources/      DATA ADAPTER LAYER (swappable) ── ListingSource protocol             │
   │       rentcast.py   scrape.py   reso.py   attom.py       (see §8)                    │
   │                                                                                      │
   │   store.py      typed SQLite  listings.db  (analytics/filters/stats)  ── §7          │
   │   enrich.py     per-listing: propkb.acquire (parcel/zoning/flood) + commute + POI    │
   │   feasibility.py  E4: docrag_ask buildability verdict + citations                    │
   │   commute.py    E5: Google Routes/Matrix (or TravelTime) + cache                     │
   │   score.py      E6: per-strategy pro-forma, feasibility-adjusted                     │
   │   agents.py     E7: realtor rollups + firewalled demographic signal                  │
   │   monitor.py    E8: saved theses, dedup, NTFY alerts                                  │
   │   index → docrag semantic index over description text (E2)                            │
   └───────────────────────────────────────────────────────────────────────────────────┘
        reuses:  docrag (rag_query/answer/reason, facets),  propkb.acquire + registry + mls,
                 server/ (web UI),  NTFY_TOPIC,  Azure OpenAI (embeddings + synthesis)
```

**Two storage engines, on purpose:**
- **Vector (docrag instance)** — semantic search over description prose (E2). Reuses `chunk/index/query/answer` unchanged.
- **Typed SQLite `listings.db`** — numeric/categorical fields for filters, stats, comps, agent rollups (E3). The recon confirmed the RAG schema has no typed columns and is the wrong tool for aggregations. This is the one genuinely new storage component.

A search = **hard filter in SQLite → semantic rerank in the vector instance → enrich/score the top-K**. Cheap fields gate first; expensive enrichment (commute, buildability) runs only on survivors.

**Reused as-is:** `docrag` retrieval + synthesis + `facets` (jurisdiction gating already maps a parcel's governing town to the right `location`); `propkb.acquire` (ArcGIS REST parcel/zoning/flood, no key/browser) and `propkb.registry`; `propkb.mls` (RESO Web API + agent-CSV ingest — already half of the licensed-data path); the FastMCP + eager-import + `_run_bounded` MCP boilerplate (battle-tested against the stdio import-lock deadlock); `server/`; `NTFY_TOPIC`.

---

## 7. Data model (`listings.db`, typed SQLite)

Minimum viable columns (extend as adapters surface more):

- `listings`: `id, source, source_id, mls_number, status, address, lat, lon, parcel_pin, planning_jurisdiction, price, list_date, dom, beds, baths, sqft, lot_sqft, lot_acres, property_type, year_built, description_text, list_agent_name, list_agent_id, brokerage, photos_json, first_seen, last_seen, raw_json`
- `price_history`: `listing_id, date, price, event`
- `enrichment`: `listing_id, zoning_code, allowed_uses_json, flood_zone, in_floodway, watershed, commute_json, poi_json, buildability_json, buildability_cited, score_json, updated_at`
- `agents`: `agent_id, name, brokerage, listing_count, geo_json, price_band_json, below_avm_rate, relist_rate, demographic_signal_json (firewalled — see §9)`
- `theses`: `id, name, spec_json, created, last_run`
- `thesis_hits`: `thesis_id, listing_id, first_alerted, rationale`

Provenance mirrors propkb: every enriched fact carries its source; buildability facts carry the docrag citation string.

---

## 8. Data acquisition (the biggest risk) — adapter pattern

Everything sits behind a `ListingSource` protocol so the *rest of the platform never knows or cares where a listing came from* — we can swap sources with zero downstream change. `ListingSource.search(geo, filters) -> list[Listing]`.

### 8.1 The critical field: free-text listing remarks (resolved by spike, 2026-07-10)

The **listing description / public remarks** paragraph is the whole point of semantic search (epic E2 — e.g. "existing septic permit and well," "flag lot," "ADU potential"). A two-track spike established:

- **RentCast and ATTOM do NOT carry remarks.** (An earlier draft wrongly claimed RentCast had description text — corrected. RentCast is a public-records/AVM aggregator: it gives structured fields + agent name, *not* the prose.)
- **All three target counties (Durham, Wake, Alamance) are served by ONE MLS — Doorify MLS** (formerly Triangle MLS/TMLS). `PublicRemarks` is a standard RESO field Doorify carries. The data isn't the blocker; the **license gate** is.

**Three verified routes to remarks:**

| Route | Carries remarks (verified) | Land/lots | License gate | Speed | Cost |
|---|---|---|---|---|---|
| **Redfin scrape** — Apify `automation-lab/redfin-scraper` → `listingRemarks` | ✅ | ✅ (`land` type, county search) | none | days | ~$1/run (few-hundred listings) |
| **REAPI MLS Detail** → `publicRemarks` | ✅ (their docs) | verify | none up front (reseller holds license) | same-day key | ~$49–999/mo, MLS add-on quoted |
| **Doorify direct** — RESO Web API via Bridge | ✅ authoritative | ✅ | **broker of record must sign** (be licensed or partner w/ a broker) | 1–6 wks | MLS dues ~$20–70/mo |

### 8.2 Adapter roster

| Adapter | Role | Remarks? | Agent name? | Structured (price/DOM/lot/comps/AVM) | ToS / legal | Cost |
|---|---|---|---|---|---|---|
| `redfin.py` (Apify actor) | **v1 remarks source** | ✅ `listingRemarks` | ✅ | some | breaches Redfin ToS — personal-use scale only | ~$1–3/1k |
| `reapi.py` (RealEstateAPI) | **legit-spine candidate** (verify coverage+ToS) | ✅ `publicRemarks` | ✅ | ✅ MLS Detail + comps | reseller ToS flows down — get written OK to index remarks | ~$49–999/mo |
| `rentcast.py` | structured spine + valuation | ❌ | ✅ | ✅ + AVM + rent est | commercial-friendly, no contract | free 50/mo → $74/$199/$449 |
| `reso.py` (via `propkb.mls`, Bridge/MLS Grid/SimplyRETS → Doorify) | authoritative MLS truth | ✅ | ✅ | ✅ | needs broker/MLS agreement + IDX rules | MLS dues + vendor fee |
| `attom.py` | public-record + comps enrichment | ❌ | no | ✅ records | commercial license | ~$95–500+/mo |

### 8.3 Decision (v1)

**Ship `redfin.py` first as the remarks source and v1 spine.** Softest anti-bot target (Cloudflare + rate-limit only, vs Zillow's PerimeterX press-and-hold), remarks land in a clean named JSON field, native land/lot support, county-level search, trivial cost. Unblocks the semantic-search PoC in days with no license and no waiting. Keep `maxcopell/zillow-detail-scraper` (`description`) as fallback for Redfin-missed FSBO/coming-soon.

**In parallel, spin up a REAPI key** and verify two things before leaning on it: (a) does it actually cover Doorify/Triangle listings, (b) will their ToS permit *indexing remarks for search* — **get that in writing.** If both clear, `reapi.py` becomes the legitimate spine and `redfin.py` drops to fallback.

**Long-term legitimate path** (only if this outgrows personal use): partner with a friendly local broker → Doorify RESO feed via Bridge = guaranteed license-clean remarks.

**Legal reality:** Redfin/Zillow ToS both prohibit scraping regardless of technical feasibility. *hiQ*/*Meta v. Bright Data* keep logged-out scraping out of CFAA-*criminal* territory but **not** out of breach-of-contract. Keep scraping at personal/research volume; **counsel review before any non-personal use.** The adapter isolation means the legal posture is a one-file swap, not a rewrite.

**Open risk to test first:** confirm `listingRemarks` actually *populates for vacant-land listings* (not just residential) on real Alamance/Durham parcels — land remarks are exactly where the septic/flag-lot/ADU language lives.

**Parcel/zoning/flood substrate:** reuse `propkb.acquire` (Durham GIS, Wake iMAPS, FEMA NFHL) — already wired, free, no key. Consider **Regrid** later for standardized zoning if per-county pulls get heavy.

**Commute/POI:** Google Maps Platform — Routes (traffic-aware, `departureTime` scenarios), Route Matrix (~$5/1k elements), Places (POI counts), Geocode-once-and-cache. Evaluate **TravelTime API** for the multi-destination *intersection* primitive (flat-fee, purpose-built for "reachable from A AND B AND C") — likely cheaper and better-fit than DIY on Google for E5. Cache aggressively; commute is the cost driver.

---

## 9. Compliance & guardrails (must-read)

**Fair Housing Act firewall (realtor demographic signal).** You opted to keep a name→ethnicity inference, internal-only, heavily caveated. Non-negotiable design constraints:

- The demographic signal attaches **only to listing agents (the supply side), never to buyers/tenants/sellers**, and is stored in `agents.demographic_signal_json` walled off from any listing/deal record.
- It is **hard-blocked from every housing decision path**: it may **not** be an input to E6 scoring, E8 alerts, ranking, or any buy/sell/rent recommendation. Code-level: `score.py` and `monitor.py` must not import or read the demographic field. Add a test asserting this.
- Every surface that shows it labels it **low-confidence, name-inferred, non-decisional**, with an FHA-risk notice.
- It exists for *your* market-color/analytics curiosity only. Document the liability in-repo. **This is a real legal landmine — revisit with counsel before this leaves personal use, and reconsider dropping it entirely if the product goes external.**

**Scraping ToS.** Per §8 — personal-use scale, adapter-isolated, counsel review before commercial use.

**Existing docrag guardrails inherit:** no help evading permitting/inspections; redirect to the legitimate route (alternative-materials/engineered-design, correct jurisdiction). No hardcoding question-specific values into prompts (see memory `no-hardcoding-in-prompts`).

**Research not advice.** Every buildability/feasibility output ends with a "Verify / gaps" note; it's grounded research, not legal or investment advice.

---

## 10. Roadmap / phasing

**Phase 0 — skeleton (1 week).** `listings/` package + `settings.py` clone; `listings.db` schema; `ListingSource` protocol; **`redfin.py` adapter (Apify actor)** pulling a Durham/Alamance slice — including **vacant-land** listings — into the typed store. First test: confirm `listingRemarks` populates for land parcels. Prove ingest end-to-end.

**Phase 1 — search (E1–E3).** Semantic index over descriptions; hard-filter + semantic-rerank pipeline; `listing_search` + `listing_stats` MCP tools; `/scout` skill for NL query. **Milestone: "find lots mentioning existing septic/well under $150k in Durham" works end-to-end.**

**Phase 2 — the moat (E4).** `feasibility.py`: listing → `propkb.acquire` → `docrag_ask` cited buildability verdict. Wire jurisdiction selection off `PLANNING_JURISDICTION`. **Milestone: per-listing "buildable? cite the provision" verdict.**

**Phase 3 — location + scoring (E5, E6).** Multi-point commute ranking; land-to-build pro-forma first, then buy-hold, flip, STR. **Milestone: full thesis→ranked-shortlist with rationale.**

**Phase 4 — intelligence + monitoring (E7, E8).** Realtor rollups + firewalled signal; saved theses + NTFY alerts; dedup.

**Phase 5 — surfaces + polish (E9, E10).** Web UI views; entitlement-path playbook; handoff-to-`property-research` command.

---

## 11. Success metrics

- **Coverage:** % of active Triangle land/SFH/2–4-unit listings ingested and enriched.
- **Buildability precision:** on a hand-labeled sample, does the cited verdict match a human zoning read? (target ≥90% on by-right yes/no).
- **Search relevance:** for a benchmark set of theses, is the intended listing in the top-10? (build a `evalset/listings.jsonl` like the existing eval harness).
- **Time-to-shortlist:** thesis prompt → ranked shortlist in < 2 min.
- **Signal quality:** monitor alerts that the user acts on / total alerts (precision of the deal monitor).
- **Cost/listing enriched:** commute + API spend per listing (watch the Google/commute line).

---

## 12. Open questions / decisions needed

1. **Does `listingRemarks` populate for vacant-LAND listings on Redfin?** (Not just residential.) First thing to test in Phase 0 — land remarks are where the septic/flag-lot/ADU language lives. *(Blocker-critical.)*
2. **REAPI viability as legit spine** — verify (a) Doorify/Triangle coverage and (b) written ToS confirmation that indexing `publicRemarks` for search is permitted. Determines whether we ever leave the scrape path. *(Resolved MLS question: all three counties = Doorify MLS; RESO vendor path is Bridge Interactive.)*
3. **Commute engine:** Google Routes vs TravelTime for the multi-point intersection — needs a head-to-head on cost + the isochrone-intersection limit.
4. **STR legality data** — STR scoring needs local STR-ordinance rules; is that in docrag already, or a new source?
5. **Photo semantics (E2)** — vision captioning of listing photos: v1 or later?
6. **Refresh cadence** — how often to re-pull listings + re-run theses (cost vs freshness).
7. **Naming** — "Scout" is a placeholder. Confirm or rename.

---

## 13. Verify / gaps

- All competitor pricing is 2026 as-reported; enterprise tiers are quote-based — treat as ballpark.
- Legal posture (scraping ToS, FHA firewall) summarized from research — **not legal advice; get counsel before non-personal use.**
- Google Maps SKUs shifted in 2025; confirm live rates before architecting cost.
- This PRD assumes the docrag engine and `propkb.acquire` behave as documented in the codebase recon; validate `propkb.mls` RESO wiring is current before leaning on the licensed path.
