---
description: Cold-start deep research on a property from an address + a development plan — pulls Durham GIS/tax/web + docrag, runs market/area/proposal analysis, builds the full KB, names the bottleneck, finds contacts, drafts outreach. Hands off to /intake for increments.
---

You are doing a **cold-start deep dive** on a property and building its knowledge base from
scratch, systematizing the 1621 Clermont playbook. This is the initial build; ongoing new info
later goes through `/intake`, and queries through `/property`.

**Input `$ARGUMENTS`** = an **address** plus a **development plan** (freeform: what the user wants
to do with it). If either is missing or ambiguous, ask ONCE, briefly, then proceed.

Read `PRD-property-research.md` for the full spec. Scope v1 = **Durham County / Triangle**. Run
**one-shot autonomous, no budget cap** — fan out with subagents; go as deep as the task warrants.

`propkb` CLI (`.venv/Scripts/python.exe -m propkb ...`): `new --slug --address`, `acquire --slug
--pin|--address`, `contacts query|add|merge`, `sources query|add`, `ingest`, `ocr-render`,
`index --slug`. Property files: facts.yaml, timeline.md, UNDERSTANDING.md, PLAN.md, BOTTLENECK.md
(+ LOG), **MARKET.md, AREA.md, PROPOSAL.md**, sources/{docs,emails,gis,web,photos}/.

## Pipeline (each phase writes to the KB as it lands; collect blocked-source gaps for ONE ask at the end)

1. **Scaffold + identity.** Derive a slug from the address (e.g. `1621-clermont-rd-durham`).
   `propkb new --slug <slug> --address "<addr>"`. Then `propkb acquire --slug <slug> --address
   "<addr>"` (or `--pin` if known) → pulls Durham parcel + zoning + flood via ArcGIS REST, files
   `sources/gis/<slug>__durham-gis.md` + raw JSON. **Read that summary** — it gives PIN, REID,
   zoning, acreage, owner, deed book/page, assessed value, and whether a **floodway** intersects.

2. **Consult what we already know FIRST (don't rediscover).**
   - `propkb sources query --jurisdiction durham-county-nc` → the portals/endpoints to use.
   - `propkb contacts query --jurisdiction durham-county-nc` (and `nc-state`) → agencies/vendors
     already on file. Only web-discover contacts that are missing.

3. **Finish property data (hybrid).** Work the **standard source checklist** below (most are
   free/authoritative and were high-value on 1621 Clermont + 212 Pine). Bot-blocked sites (Zillow/
   Redfin/Realtor): **record the gap** and batch-ask the user to drop them in at the end. OCR scanned
   PDFs (`ocr-render` → read PNGs → write `.ocr.md`). Pull listing + aerial images into `sources/photos/`.
   - **Prior sale price / seller basis:** pull `REVENUE_STAMPS` + `DEED_DATE` from the parcel REST
     layer → NC excise = $2/$1,000, so price = stamps × 500. (Huge negotiation input.)
   - **Septic pre-check (rural lots):** the parcel's **soil map unit** (Durham GIS Environmental soils
     layer, point-in-polygon) + the **USDA Official Series Description** → is it suited for a
     conventional septic drainfield? A "very limited" soil (e.g. White Store/Triassic clay) is a
     make-or-break red flag before any paid perc test.
   - **Watershed/overlay:** confirm protected-watershed (Falls/Jordan F/J-B) + other overlays via the
     Durham Planning REST layers (impervious/BUA limits, buffers).
   - **Deed/plat + chain:** Register of Deeds (`rodweb.dconc.gov`) — prior deeds, plat, easements
     (gated → Playwright or ask user).
   - **Adjacent ownership:** query neighboring parcels by REID (federal/Corps? HOA? assemblage lots?).
   - **MLS:** if the user has an agent feed/export → `propkb mls ingest-csv` (agent CSV) or
     `propkb mls query` (RESO Web API; needs MLS creds in .env) → listing history/DOM/sold comps/remarks.
   - **Zillow/Redfin/Realtor** (best-effort; usually 403 → gap-ask), **FEMA flood**, **USFWS wetlands**,
     **historic aerials** (was it ever built?), **school assignment** (DPS locator + GreatSchools).

4. **Regulatory / dev-plan research.** Ground EVERY code/ordinance/statute claim in docrag
   (`docrag_ask`, corpus `building-codes`, `location=durham-nc`) — zoning use-permissions for the
   dev plan, overlays (flood/floodway/watershed/buffer), utilities/access, environmental
   (landfill/brownfield), subdivision + permit path. Web only for what the corpus lacks (current
   process/fees/forms/precedent). Corpus governs on local law; flag divergences, don't average.

5. **Market analysis → `MARKET.md`. Be skeptical.** Primary sources first (closed sales, county
   records, permits, Census/BLS) — NOT agent narratives. Treat listing/agent copy as biased;
   corroborate each claim independently. Spawn an adversarial subagent to try to refute the read.
   Output comp ranges, $/sf, DOM/absorption, rent, with confidence flags. **Comps:** use the MLS if
   available (`propkb mls ingest-csv`/`query`) for real closed sales + private remarks; else county
   sales + note "verify via MLS/CMA". If STR is in the plan, pull STR economics (AirDNA/AirROI/
   Airbtics — vendor-optimistic, discount them).

6. **Area trajectory + livability → `AREA.md`.** 10/20/30/50/100-yr outlook from comprehensive/
   future-land-use plans, zoning trajectory, transit & infrastructure plans, demographic projections,
   historical trajectory. Separate PLANNED/FUNDED from SPECULATIVE; base / bull / bear scenarios, no
   false precision. **Also pull demographics + safety + desirability:** **Census/ACS** (income, age,
   education, owner/renter — data.census.gov / censusreporter.org), **crime** from **FBI Crime Data
   Explorer + the local PD open-data portal** (authoritative; note city-vs-neighborhood divergence),
   **Walk Score**, **GreatSchools**, and major-infrastructure/amenity context. Aggregators
   (bestneighborhood/areavibes/crimegrade/neighborhoodscout) fetch fine (no bot wall) but are
   secondary/modeled — lead with Census + FBI. Give a plain read for both living there + (if STR) guests.

7. **Proposal analysis → `PROPOSAL.md`.** Test the user's dev plan against §4 constraints + §5
   market + §6 trajectory. Build the **residual land-value model** (max bid = gross realizable −
   all costs to unlock/build − profit). List alternatives considered + ruled out, with grounded
   reasons. Be decisive about go/no-go and what would change it.

8. **Synthesize the KB.** Write facts.yaml (each fact `src: [T-NN]`), timeline.md (T-NN provenance
   chain), UNDERSTANDING.md (snapshot, buildable-zone map, constraint stack, open questions,
   contacts, doc index), PLAN.md. Then **BOTTLENECK.md** (Theory of Constraints: the ONE current
   constraint, do-now vs. waiting vs. long-lead, kill criteria) + a BOTTLENECK-LOG entry.

9. **Next steps + contacts + drafts.** List concrete next actions and the right people. For each
   needed agency/vendor: **check the contacts DB first**; web-discover only the missing ones and
   verify real contact info. **Write new finds back:** `propkb contacts add ...` (jurisdiction +
   topic + source=<slug>/T-NN + confidence). Register any newly useful data source: `propkb sources
   add ...`. **Draft** outreach emails with `propkb.email.draft` (Gmail Drafts) — **NEVER send.**

10. **Index + report.** `propkb index --slug <slug>`. Then report: what came in, the buildable
    picture, the bottleneck, the residual/max-bid, and the next actions. End with **ONE batched ask**
    listing the bot-blocked sources you need the user to drop in (Zillow page, any gated portal).

## Guardrails
- **Drafts only — never send** third-party email. Research, not legal/engineering/investment advice.
- **No silent gaps** — always report what couldn't be pulled and why.
- **Cite** everything; mark primary vs. secondary; corpus is authoritative on NC/local law.
- Don't help evade permitting/inspection; redirect to the legitimate path.
- Consult the contacts DB + source registry BEFORE rediscovering; write back what you learn so the
  next property starts ahead.
