# Property Knowledge Base — system design

A per-property knowledge system built **on top of docrag**. It acquires every
scrap of knowledge about a property (public records, seller/Zillow, county GIS,
tax, plats, maps, and email threads with planning/permitting/flood/waste/etc.),
stores it as an auditable chain, keeps a living *current understanding* and a
*current plan*, and is queryable by Claude via the same `docrag_ask` machinery.

## Core idea: a property IS a docrag corpus
Each property = corpus `prop-<slug>` at `corpora/prop-<slug>/`. Indexed into
`.index/prop-<slug>.db`, so `docrag_ask(corpus="prop-<slug>")` gives grounded,
cited answers over the property's whole record. No new RAG engine.

```
corpora/prop-<slug>/
  facts.yaml          # structured canonical facts; each carries src:[T-NN]
  timeline.md         # dated, append-only provenance chain (the "legal chain")
  UNDERSTANDING.md    # living synthesis: what IS + what can be built where
  PLAN.md             # dev concept + business projection + decision gates
  BOTTLENECK.md       # the ONE current constraint + do-now actions (focus engine)
  BOTTLENECK-LOG.md   # append-only daily/changed bottleneck history
  sources/
    docs/  emails/  gis/  web/  photos/   # raw artifacts, all indexed
    emails/attachments/   # email attachments auto-saved with standard slug names
  MARKET.md           # skeptical, primary-source-first market analysis
  AREA.md             # 10/20/30/50/100-yr area trajectory (scenarios)
  PROPOSAL.md         # dev-idea analysis + residual land-value model (max bid)
```

## Shared, cross-property stores (grow over time)
Two canonical stores at the properties root, consulted BEFORE rediscovering and
appended after each research run — so property N+1 starts ahead of property N:
- **`properties/.contacts.db`** (SQLite) — every agency/vendor/agent/seller we learn,
  keyed by jurisdiction + topic, with source (`<slug>/T-NN`), confidence, last-verified.
  CLI: `propkb contacts add|query|merge|export|seed`. Seeded from the 1621 Clermont roster.
- **`properties/sources_registry.json`** — WHERE to get data per jurisdiction+type
  (verified Durham ArcGIS REST endpoints, tax/ROD/DEQ/FEMA URLs, and the method:
  rest|browser|scrape|manual). CLI: `propkb sources list|query|add|seed`.

## Durham data acquisition (REST-first)
`propkb acquire --slug <slug> --pin <pin>|--address "<addr>"` pulls Durham parcel
(AGOL ArcGIS), zoning + flood (spatial intersect), files raw JSON + a readable
summary under `sources/gis/`. No browser/auth/key for core data; Playwright/tax-card
extras are an optional later pass. (Verified: reproduces 1621 Clermont incl. floodway.)

Three layers, three jobs:
1. **Retrieval** — the indexed corpus (LLM-queryable over everything).
2. **Structured** — `facts.yaml` + `timeline.md` (canonical facts + provenance).
3. **Narrative** — `UNDERSTANDING.md` (truth) + `PLAN.md` (intent), regenerated on intake.

## Code vs. Claude
- **Deterministic plumbing** lives in `docrag/propkb.py` (CLI: `new`, `ls`, `paths`,
  `ingest`, `index`). It scaffolds, files bytes, and (re)indexes — nothing smart.
- **Interpretation** is Claude's job via skills: classify an artifact, extract facts,
  append the chain, regenerate UNDERSTANDING/PLAN, draft outreach.

## Commands (skills)
- **`/property-research <address> + <dev plan>`** — **cold start.** Scaffolds a new property,
  pulls Durham GIS/tax/web + docrag, runs market/area/proposal analysis, builds the full KB,
  names the bottleneck, finds + drafts outreach to contacts, indexes. One-shot autonomous.
  Hands off to `/intake` for increments. See `PRD-property-research.md`.
- **`/intake <file|text|url|"email">`** — ingest new info → classify + file → extract
  facts → append `timeline.md` → update `facts.yaml` → regenerate `UNDERSTANDING.md`
  (+ `PLAN.md` if affected) → re-index → report + discuss next actions.
- **`/property [name|question]`** — show status or answer a question grounded in the
  property's own record (+ building-codes corpus + web), citing provenance (T-NN).
- **`/docrag`** — unchanged: grounded building-code/land-use research.

## CLI quickref
```bash
.venv/Scripts/python.exe -m docrag.propkb ls
.venv/Scripts/python.exe -m docrag.propkb new   --slug <slug> --address "..."
.venv/Scripts/python.exe -m docrag.propkb ingest --slug <slug> --file "<path>" --kind docs|gis|emails|web|photos
.venv/Scripts/python.exe -m docrag.propkb index  --slug <slug>      # reuses docrag.index build
.venv/Scripts/python.exe -m docrag.propkb paths  --slug <slug>
```

---

## Phase 2 — Email integration (designed, not yet built)

Creds in `.env` (gitignored): `GMAIL_USER`, `GMAIL_PASS` (Gmail **app password**).
**Hard rule: the system never autonomously SENDS email.** It reads, files, and
drafts; sending is always a human action.

### 2a. On-demand email intake (`/intake email`)
- Connect via **IMAP** (`imap.gmail.com:993`, app password).
- Fetch recent messages; **flag property-relevant** ones (match address / PIN /
  party domains / subject keywords per property).
- For each relevant message: save raw `.eml` + a parsed `.md` into
  `sources/emails/`, **and auto-extract every attachment (PDFs, images) to
  `sources/emails/attachments/` with standard slug names** (`<email-stem>__<file-slug>.<ext>`)
  — never to Downloads. Then run the normal `/intake` loop (timeline → facts →
  UNDERSTANDING → PLAN → **BOTTLENECK** → reindex). Report what landed.

### 2b. Manual artifact intake (`/intake <file>`)
- Already covered by the skill: any document you obtain elsewhere — drop the path,
  the system categorizes and files it.

### 2c. Background watcher (daemon) — the autonomous-assist loop
A long-running process (or scheduled job) that, on a new relevant email:
1. Intakes it (2a) and **updates the current understanding**.
2. If gaps/ambiguities remain, **drafts follow-up questions** — to the same party
   and/or other parties — saved to Gmail **Drafts** (via IMAP `APPEND` to
   `[Gmail]/Drafts`, or the Gmail API). Never sent.
3. If the new info moves the plan, **drafts a PLAN.md update** (as a diff/proposal).
4. **Pings the user to approve both** the draft questions and the plan update, via:
   - **email to self** (compose to `GMAIL_USER`), and
   - **ntfy** push to phone (`POST https://ntfy.sh/<user-topic>` — topic in `.env`
     as `NTFY_TOPIC`).
5. User reviews drafts in Gmail, edits, sends manually; approves the plan update.

**Open implementation choices (decide at build time):**
- Watcher host: Windows Task Scheduler vs. a `python -m docrag.propmail watch` loop
  vs. Claude Code `/loop` or a scheduled agent.
- Relevance model: rules (address/PIN/domain) first; LLM classification for fuzzy cases.
- Draft storage: IMAP `APPEND` to Drafts vs. Gmail API (`drafts.create`) — API is
  cleaner for threading but needs OAuth instead of the app password.
- Dedup/state: track processed message-ids per property to avoid re-intake.

### Security
- `.env` is gitignored and untracked — keep it that way. App password is revocable;
  rotate if exposed. Never print cred values to logs/chat. Scope ntfy topic to a
  hard-to-guess string.

---

## Status
- **Phase 1 (done):** KB structure, `propkb.py`, `/intake` + `/property` skills,
  and the full migration of `prop-1621-clermont-rd-durham` (facts, timeline,
  UNDERSTANDING, PLAN, all artifacts). Index build is a separate (paid) step.
- **Phase 2 (next):** email intake → watcher → auto-draft questions + plan updates
  → ntfy/email approval ping.
