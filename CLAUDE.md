# Working in this repo as a research agent

This repo (`docrag`) is a grounded document-RAG over building regulations. The
intended way to use it is **agentic**: you (the Claude session) take a question,
decide what to do, query the docrag corpus to ground claims, search the web for
what the corpus can't supply, reason, and answer with citations to both layers.
You orchestrate; docrag supplies grounded local-law retrieval.

## The corpus

`building-codes` holds shared layers + several **local** jurisdictions at once:
- **Model** — 2021 International Building Code (IBC)
- **State** — 2024 NC State Building Codes (9 books) + NC statutes (NCGS 160D, etc.) + NCDOT access policy *(shared by every location)*
- **Local** — one layer per jurisdiction, selected at query time via `location`:
  - **Durham** — Durham Unified Development Ordinance (UDO)
  - **Alamance County** (unincorporated) — Alamance County UDO + watershed, addressing, land-development plan, inspections fees
  - **Burlington** — Burlington UDO + procedures manual, historic overlay standards, stormwater, fees, permit guidance
  - **Graham** — Graham Development Ordinance + historic resources handbook, standard details, fees
  - **Alamance small towns** — Haw River, Swepsonville, Green Level, Village of Alamance zoning ordinances
  - **Wake County** (unincorporated) — Wake County UDO + Stormwater Design Manual + Swift Creek / Falls / Neuse watershed materials
  - **Raleigh** — Raleigh UDO · **Cary** — Cary Land Development Ordinance (LDO) · **Garner** — Garner UDO · **Wake Forest** — Wake Forest UDO
  - **Other Wake towns** — Apex, Holly Springs, Morrisville, Fuquay-Varina (LDO), Knightdale, Wendell, Zebulon, Rolesville (LDO) *(zoning/parcel pull wired; full UDO/LDO text ingested per demand)*

The shared model/state/federal/NCDOT layers stack under **every** location; the
`location` selector picks which single local ordinance applies, so a Durham
question never sees Alamance or Wake ordinances and vice versa. **Note (Wake):**
zoning is **per-municipality** — pick the `location` matching the *governing*
jurisdiction (a parcel's `PLANNING_JURISDICTION` from the Wake parcel pull:
`WC`=wake-county-nc, `RA`=raleigh-nc, `CA`=cary-nc, etc.), not just the county.

**`location` values:** `durham-nc` (default), `alamance-county-nc`,
`burlington-nc`, `graham-nc`, `alamance-towns-nc`, `wake-county-nc`, `raleigh-nc`,
`cary-nc`, `garner-nc`, `wake-forest-nc`, `apex-nc`, `holly-springs-nc`,
`morrisville-nc`, `fuquay-varina-nc`, `knightdale-nc`, `wendell-nc`, `zebulon-nc`,
`rolesville-nc`, `north-carolina` (statewide, no local layer), `model` (I-Codes
only). Always pass the one matching the question's jurisdiction.

**propkb parcel pull (Wake + Durham):** `propkb acquire --slug <s> --location
<loc> --address "<addr>"` (or `--pin`). Wake parcels/flood/address come from Wake
iMAPS + FEMA NFHL; zoning from the per-town layer. Registry: `propkb sources
query --jurisdiction wake-county-nc`.

(`udo` is a Durham-only corpus; default to `building-codes`.)

## How to query docrag

**Preferred — MCP tools** (registered via `.mcp.json`; the agent decides when to call them):
- `docrag_ask(question, corpus="building-codes", location="durham-nc")` → grounded, **cited** answer. Pass the `location` matching the question's jurisdiction (see values above).
- `docrag_sources(question, corpus, top_k, location)` → raw provision passages (no LLM) when you want to read the text and reason yourself.
- `docrag_corpora()` → list indexed corpora.

**Fallback — CLI** (always works, even without MCP):
```bash
.venv/Scripts/python.exe -m docrag.ask "minimum stair riser height for an exit stair?"
.venv/Scripts/python.exe -m docrag.ask --location burlington-nc "max height of an accessory structure?"
.venv/Scripts/python.exe -m docrag.ask --sources "fire separation distance"   # chunks only
```

## How to answer building-code / land-use questions

1. **Ground in the corpus first.** Call `docrag_ask` (or the CLI) before asserting what a code/statute/ordinance says. Cite the named provision it returns (e.g. "NCGS 160D-1110", "NC Residential Code R101.2.1").
2. **Web-search for what the corpus lacks** — real-world precedent, current process/fees/contacts, products, recent amendments, edition confirmation. Cite sources (prefer official/primary; flag secondary).
3. **Corpus governs on NC/local law.** When the web and the corpus conflict on a point of NC building law or a local ordinance (Durham, Alamance County, Burlington, Graham, …), the corpus is authoritative — flag the divergence, don't average.
4. **Reason to a decision.** Apply thresholds and scope conditions to the facts; be decisive; state the assumption a conclusion depends on; separate what's settled from what must be verified locally.
5. **End with a short "Verify / gaps" note.** This is research, not legal advice — say so.

## Guardrails

- Don't help evade safety inspections or permitting (e.g. concealing non-compliant
  construction from inspectors). Redirect to the legitimate path — the
  alternative-materials/engineered-design route, the correct jurisdiction, etc.
- Don't hardcode question-specific values into prompts or `reason.py`; docrag must
  answer organically from retrieval. (See memory `no-hardcoding-in-prompts`.)

## Running the app

```bash
.venv/Scripts/python.exe server/server.py --start   # web UI at http://localhost:8099/
.venv/Scripts/python.exe server/server.py --stop
.venv/Scripts/python.exe -m docrag.mcp_server        # MCP server (stdio) for agent tools
```
