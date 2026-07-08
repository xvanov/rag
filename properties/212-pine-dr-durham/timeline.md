# Timeline -- 212 Pine Dr #3 & 4, Durham, NC 27713 (the provenance chain)

Dated, append-only record of every fact, document, and conversation, with its
source. facts.yaml and UNDERSTANDING.md cite these IDs (T-NN). New entries go at
the bottom via /intake.

---

**T-01 - 2026-07-07 - Property created**
Knowledge base scaffolded for 212 Pine Dr, Durham, NC 27713 via /property-research.

**T-02 · 2026-07-07 · Durham GIS REST pull + IDENTITY CORRECTION (primary — ArcGIS)**
`propkb acquire` pulled the Durham parcel (AGOL ArcGIS REST). Key result + a material correction:
- **PIN 0707680500 / REID 143264**, **212 PINE DR**, owner **HERRING, Elizabeth B & David R**
  (mail 4206 Riceland Dr), deed **DB 5947/977**.
- **Zoning RR (Rural Residential)**; **acreage 0.92**; land class **"VAC RES / LOT-SML TRA" = VACANT**;
  assessed **$29,440 (land only)**. Flood **Zone X — not in floodplain/floodway.**
- **CORRECTION:** `PROPERTY_DESCR = "OAK HILL / BLK:C / LT#03 & LT#04 PL000043-000020"`. The input's
  **"#3 & 4" = platted LOTS 3 & 4 of the Oak Hill subdivision (Block C, Plat Book 43/20), NOT condo
  units.** The property is a **single vacant 0.92-ac RR lot**, no existing structure.
- **Implication:** the short-term-rental plan = **ground-up construction on vacant rural land**, not
  operating existing units → analysis reframed toward buildability (well/septic vs city, access, RR
  dwelling standards, two-lot question) + STR permitting in RR.
- Artifact: `sources/gis/212-pine-dr-durham__durham-gis.md` (+ raw JSON).
- docrag (building-codes/durham-nc) queried for RR STR-use + dwelling standards + lot-of-record →
  **corpus silent** (STR/RR use-table not in corpus); relying on web research (pending).

**T-03 · 2026-07-07 · STR regulation + market research (web + docrag; SECONDARY)**
Build-to-STR in RR Durham is **NOT clean/by-right:**
- Durham has **no dedicated STR ordinance / registration** (NCGS 160D-1207 limits forced registration).
  STRs fall under the UDO **"Overnight Accommodations"** use category (Sec. 5.2). In **RR**: **B&B =
  "L/m"** (limited use OR minor Special Use Permit; implies a resident host — poor fit for absentee
  whole-house); **Hotel/Motel/Extended-Stay = "L"** but triggers a **minor SUP + 50-ft setback when
  within 200 ft of / access adjacent to residential** — which fires on a 0.92-ac lot inside Oak Hill.
- A **non-owner-occupied whole-house STR fits NEITHER box cleanly** → Durham Planning makes the
  use-classification call; genuine ambiguity. Likely outcome: **minor SUP required**, or constrained
  to **owner-occupied B&B** form. **Must get a written zoning/use determination before buying.**
- **Building code:** true transient/non-owner operation = **R-1 (commercial)** occupancy (NC Bldg
  Code 310.2) — materially pricier build; owner-occupied ≤8 guest rooms can use IRC (310.4.2).
- **Taxes (settled):** Durham 6% room-occupancy tax (Airbnb remits) + NC/local **7.5% sales tax**
  (host registers via NCDOR NC-BR).
- **Market (vendor data, skeptic-discounted):** ADR ~$140–185 (mid $160), occupancy 42–64%, **gross
  ~$17–33K/yr (plan $20–25K; $15–20K yr-1 new listing)**; Durham = bottom ~10–15% nationally for ADR.
  Gross unlikely to cover a new-build carry after taxes/PM/costs. → See MARKET.md. Rejected blogs that
  misattributed **Raleigh's** STR rules (R-5/R-10, 400-ft frontage) to Durham.

**T-04 · 2026-07-07 · Buildability research (web + Durham GIS + docrag; SECONDARY, HIGH conf. on utilities)**
- **UNINCORPORATED Durham County** (parcel CITY field null; tax district Cnty-Drhm/Fd-Parkwood).
  Centroid 35.88903, -78.97972 — SW Durham off **Farrington/Stagecoach Rd, near NC-751 / Jordan Lake
  + Jordan Game Land.** Walk Score 3/100 (car-dependent), rural/exurban.
- **WATER = WELL, WASTEWATER = SEPTIC (HIGH confidence).** Old Oak Hill lots are unincorporated on
  well+septic; adjacent **Southpoint Manor (Oxfordshire Ln, ~300–400 ft)** was annexed and has city
  water/sewer. City service = a costly fallback (voluntary annexation + main extension), not the plan.
  **→ #1 GATE: septic soil/perc suitability (Durham County Environmental Health; UDO §12.7, 15A NCAC
  18E). Well permitted by county health (NCGS 87-97).**
- **ACCESS #2 GATE:** "Pine Dr" serves only 2 parcels + not in OpenStreetMap → likely minor/stub/
  unimproved/possibly private. UDO §13.5.1 + §14.3.2 require an **accepted/maintained street** to build.
  Verify NCDOT SR status (apps.ncdot.gov/srlookup).
- **JORDAN LAKE WATERSHED (pivotal):** Little Creek + tributaries within 200–400 m → likely Durham
  **Rural Tier + protected water-supply watershed** → stream buffers (~50 ft) + impervious cap, and
  **RR min lot rises to 2 ac (Rural Tier non-watershed) / 3 ac (Rural Tier watershed)** vs 30,000 sf
  "All Other Locations" (UDO §6.2.1A). At **0.92 ac (~40,000 sf)** the lot **conforms only if "All
  Other Locations"; if Rural-Tier-watershed it is a SUBSTANDARD nonconforming lot of record** — still
  buildable for ONE dwelling under **§14.3.2** IF pre-ordinance plat (yes, PB 43), ≥30 ft wide, not in
  flood (Zone X ✓), on an accepted street (#2), water/wastewater available (#1).
  **→ TWO homes almost certainly OUT; one dwelling at most.**
- Slope ~10% (moderate, buildable). Check plat PB 43/20 + deed for easements/frontage.

**T-05 · 2026-07-07 · Area trajectory research (web; SECONDARY, proxy data)**
SW Durham near Jordan Lake / NC-751; long-run favorable but transit unfunded:
- **Structural tailwind:** RTP job growth (Durham Co. pop +27% since 2010); **committed roads** —
  Complete 540 loop Phase 2 (target 2028), I-40 widening (~2026), East End Connector/I-885 (2022),
  RDU Vision 2040 runway/terminal. **Light rail CANCELLED (2019); commuter rail unfunded (FTA declined
  2023); BRT planning-only** — do NOT underwrite transit as committed.
- **Jordan Lake** = permanent recreation amenity (STR plus) BUT drives the watershed overlay that
  limits density (double-edged).
- **Values:** 27713 ~$365K ZHVI / ~$424–434K Redfin median (Jul 2026); MSA HPI +58.5%/5yr, cooling to
  ~+3.3% YoY; 2023–26 soft plateau (inventory +41%, ~7% rates). County permitted 2,905 units in 2024.
- **Read:** trajectory helps *exit value* modestly, not *buildability/STR permitting* (the gates).
  50/100-yr = speculative optionality. See AREA.md.

**T-06 · 2026-07-07 · PLAN CLARIFIED by owner — owner-occupied homestay, not non-owner STR (MATERIAL)**
Kalin clarified the development plan: **build and occupy the home as his own PERMANENT RESIDENCE, and
rent out a ROOM (later maybe more rooms) on Airbnb.** This is owner-occupied — a fundamentally
different (and far friendlier) case than the non-owner whole-house STR analyzed in T-03/PROPOSAL:
- **Regulatory reframe — now the permittable path.** Owner-occupied room rental = **bed-&-breakfast /
  homestay form**, which in RR is the **"L/m"** use (permitted with limitations, minor SUP possibly
  required) — and the **owner-occupancy that was the blocker is now SATISFIED.** No longer the
  hotel/extended-stay + 50-ft-setback trap. VERIFY exact Durham requirements (homestay vs B&B vs
  accessory home occupation, any guest-room cap, whether a minor SUP is actually triggered).
- **Building code reframe — IRC, not R-1.** Owner-occupied dwelling renting ≤8 guest rooms builds to
  the **residential IRC** (NC Bldg Code 310.4.2), NOT R-1 commercial → the big commercial-build
  premium disappears.
- **Economics reframe.** It's now a **primary residence + incremental room income**, not an investment
  STR. The residual/max-bid model (which assumed a pure STR investment) no longer governs — value =
  "a home I want to live in that I can build here, with a rented room offsetting cost." STR income
  (~$8–14K net whole-house) is smaller for a room, but it's gravy on a house occupied anyway.
- **Unchanged:** buildability gates — septic **perc (gate #1)**, Pine Dr **access (gate #2)**, Jordan
  Lake **watershed → one dwelling** (fine, it's one residence), flood Zone X ✓.
- **Verdict shift:** from "lean WALK" → **plausibly VIABLE, now gated by BUILDABILITY (perc/access) +
  whether you want to live in this rural Jordan-Lake location** — not by STR permitting.
- Staged Planning draft (T-03 framing) regenerated to ask the owner-occupied homestay question.

**T-07 · 2026-07-07 · Zillow price recovered (user-provided link; via portal search — Zillow 403'd)**
Zillow (zpid 449399807) is bot-blocked; recovered listing facts via portal search:
- **Asking ~$140,000** for the 0.92-ac lot (40,075 sf) — "build or invest on wooded acres, SW Durham
  Co. near Stagecoach/Farrington, **borders US Army Corps of Engineers / Jordan Lake land for
  privacy**; ~1.9 mi to I-40, 4.6 mi to UNC, 10 mi to Duke, 4.6 mi to Southpoint."
- **DISCREPANCY to verify:** a **$275,000 / 4.98-ac** "212 Pine Dr" listing (−$25K price drop) also
  appears — possibly Lots 3&4 PLUS adjacent lots (a larger assemblage) or a separate nearby listing.
  Confirm exactly what's being sold (the 0.92-ac Lots 3&4 at $140K, or a 4.98-ac package at $275K).
- **Price read:** $140K ask vs my buildable-lot estimate ~$40–90K (contingent on perc+access). At
  ask it's **above a contingent-buildable rural lot's value** — but for an OWNER-OCCUPIED homesite you
  want (Corps/lake privacy is a genuine residence amenity), the calculus is partly personal. Still:
  **condition any offer on perc + access; don't pay $140K for a lot that might not perc or have a
  buildable street.** Source: portal search (Homes.com/Redfin snippets); verify on the actual listing.

**T-08 · 2026-07-08 · Full Zillow listing text (owner-provided; Zillow 403'd auto-fetch) — resolves 2 gates**
Filed verbatim: `sources/web/212-pine-dr-durham__zillow-listing.md`. Materially richer than the portal
snippet in T-07. Key:
- **SEPTIC GATE (#1) likely addressed:** "**Soil testing results are in**" — seller HAS results.
  -> **request them** (a passing soil/perc result would clear the make-or-break gate). New survey coming.
- **ACCESS GATE (#2) RESOLVED (as a cost):** the lot sits at "the **corner of two publicly dedicated,
  UNDEVELOPED rights-of-way**," and **"building requires construction of an ~20'-wide road to NCDOT
  standards, along with well and septic."** Access is legally available but **the road must be BUILT**
  -> a real, quantifiable cost line (cf. 1621 Clermont's road problem). Confirms well + septic.
- **IMPERVIOUS/watershed refined:** "**no impervious restrictions unless lot lines are moved or lots
  are recombined**" -> the single 0.92-ac lot is fine as-is; **recombining TRIGGERS** watershed
  impervious limits. (Refines T-04 watershed inference.)
- **Assemblage clarified:** neighbor **7412 Star Dr** separately for sale -> combine for ±1.84 ac
  (would trigger impervious). Explains the $275K/4.98-ac listing confusion (T-07) = separate lots.
- **New flag:** "**Proposed Jordan Lake Water Supply program facility nearby**" — research (amenity vs
  nuisance vs future impact?).
- **Flexible:** No HOA/dues, no build timeline, no builder requirement.
- **NET:** buildability gates are now (1) get the soil results, (2) **cost the ~20' NCDOT-standard
  road** — the road-build is the new dominant cost driver alongside well+septic.

**T-09 · 2026-07-08 · Road requirement grounded + adjacent-parcel ownership + Corps land (docrag + REST + web)**
- **ROAD = real land-use requirement, owner's expense (docrag/UDO):** §12.2.2 — no building unless
  the parcel abuts a publicly accepted/maintained street, except a **private street for ≤6 SF lots,
  which must still be built to City/NCDOT public-street standards**; a plain driveway/easement does
  NOT qualify for a standalone SF lot. §12.2.1 — on an unmaintained ROW a **building permit can issue
  but NO occupancy permit (CO)** until the street is built to acceptable standard or surety posted.
  → **You cannot occupy until the road is built.** ~400 ft to Stagecoach × ~20' NCDOT standard ≈ the
  dominant cost (~$40–100K+; verify + ask Durham/NCDOT re a reduced rural cross-section). NOT electrical.
- **Adjacent parcels (Durham REST):** the subject's neighbors in Oak Hill Blk C are almost all
  **UNITED STATES OF AMERICA** ("WILD/PARKS/FDRL PROJ," 1970s deed bks 413–416): Lots 1&2 (204 Pine,
  REID 143265), Lot 7 (7418 Star, 143263), Lots 8-10 (7413 Oak Leaf, 143266), Blk D 5&6 (3207
  Stagecoach, 143267). **The Herrings own BOTH the subject (Lots 3&4, 212 Pine) AND 7412 Star Dr
  (Lots 5&6, REID 143262) — same deed 5947/977.** So "buy the lot next door" = same seller;
  combined = Lots 3-6 (1.84 ac). Surrounded by Corps land → **permanent privacy (Corps won't develop).**
- **Corps lots NOT buyable (web, high conf.):** federal Jordan Lake project land, held for project
  purposes, not sold on request. Disposal only via excess→GSA (competitive, rare for buffer land),
  land exchange (must benefit project), or special Act of Congress. Corps policy: "private exclusive
  use of public land is always denied." **Best case = a limited outgrant (lease/easement/permit), no
  fee title.** Contact: **USACE Wilmington District Real Estate Division** (69 Darlington Ave,
  Wilmington NC 28403; ~910-251-4626) + B. Everett Jordan Lake Project Office.
- **Inholding history:** Oak Hill platted (PB 43/20, ~1960s–early '70s) BEFORE the reservoir; Corps
  took lots inside the taking line/buffer contour in the 1970s; higher-ground lots (Herrings' 3-6)
  weren't needed → stayed private. Herrings acquired 2008. Verify plat date + 1970s USA deeds at
  Durham ROD.

**T-10 · 2026-07-08 · Duplex + lot-split ruled out (docrag/UDO)**
- **Duplex NOT permitted in RR.** UDO §6.2.2: RR allows only single-family detached + manufactured
  homes; duplexes only in conservation subdivisions in the Suburban Tier (n/a here). → single-family only.
- **Cannot split the combined parcel into 2 buildable lots.** The "#3&4"/"#5&6" lot numbers are the
  original platted lots ALREADY COMBINED into one parcel each (a merge, not a split opportunity).
  Splitting back = a subdivision (1→2, no recombination exemption; NCGS 160D-802); each ~0.46 ac
  (~20,038 sf) is UNDER the RR minimum in every category (30k sf / 2 ac / 3 ac watershed); UDO §14.3.1
  PROHIBITS creating new undersized lots (absent variance). §14.3.2 only rescues pre-existing lots of
  record, doesn't authorize a fresh split. Plus each sub-lot needs its own well+septic on 0.46 ac.
  → **ONE home per parcel (212 Pine = 1 homesite; 7412 Star = 1 homesite).**
- **Corollary:** can't add dwelling units → the only way to scale rental is MORE ROOMS in the one
  owner-occupied house (the homestay plan). Fits intent.
- **Road standard authority (Q5):** reduced rural cross-section is set by **Durham Development Review
  / Public Works Transportation (Justin Weist — in contacts DB)** + **NCDOT District 5 (Durham)** for
  the Stagecoach Rd connection. Civil-eng/road-contractor RFP + contacts being sourced.

**T-11 · 2026-07-08 · Road-build contacts sourced + RFP drafted (web + contacts DB writeback)**
- **Reduced rural cross-section is a jurisdiction call — confirm it FIRST** (drives the whole cost):
  **Durham Development Review / Transportation, 919-560-4326** (Durham adopted NCDOT construction
  standards 2018; Justin Weist is Eng Mgr here) + **NCDOT Div 5 Durham, 919-220-4600** (Jorge Benitez
  Jaimes, PE) for the Stagecoach (state-road) connection/encroachment permit.
- **Civil/site engineers (design + permit set):** Summit (Brandon Johnson, brandon.johnson@summitde.com,
  919-322-0115 — already in DB), Qunity (Durham, 919-490-4990, no public email), WithersRavenel
  (919-469-3340). **Contractors (build):** Carolina Excavation Specialties (919-302-7306, all-in incl
  septic), Kapusnik Grading (Martin@KapusnikGrading.com), J. Coble Construction
  (office@jcobleconstruction.com), Hunter Excavating (Hunterexcavatingllc@gmail.com), Max Tractor.
  All written to the canonical contacts DB.
- **RFP staged** (drafts, NOT sent) to the 4 firms with verified emails (Summit, Kapusnik, J. Coble,
  Hunter) requesting a ROM cost for ~400 ft NCDOT-standard road + well + septic. Phone-only firms
  (Qunity, WithersRavenel, Carolina Excavation, Max Tractor) → call.
- **Sequencing:** confirm cross-section with Development Review (919-560-4326) → then firms quote to
  that section. Also confirm city-limits vs ETJ (decides City vs NCDOT authority).

**T-12 · 2026-07-08 · Inholding history synthesis — "paper subdivision" partially taken for the lake**
Explains BOTH the private inholdings AND the undeveloped-ROW road problem (same root cause):
- Oak Hill was **platted as a speculative suburban subdivision (~1960s, PB 43/20)** — streets + ~1-ac
  lots on paper, largely UNBUILT.
- Corps built Jordan Lake (authorized 1963, built 1970s-82) and acquired the lots inside the project/
  buffer boundary -> **most Oak Hill lots became USA-owned** (deed bks 413/414/416 = ~1970s). Lots above
  the taking contour (Herrings' 3-6) weren't needed -> **stayed private inholdings**; Herrings bought 2008.
- **KEY LINK:** the platted streets (Pine Dr, Star Dr) were **never constructed** because the
  subdivision was absorbed by the lake before it developed -- which is exactly WHY Pine Dr is an
  "undeveloped ROW" and the road must be built. Same root cause = the privacy buffer AND the access cost.
- **Public records to confirm (pullable):** Durham ROD (rodweb.dconc.gov) - plat PB 43/20 date, the
  1970s "to USA" deeds (DB 413/691, 414/525, 416/803), Herring chain (DB 5947/977); USACE Wilmington
  Real Estate tract/taking-line maps; EDNC federal condemnation dockets if any lots were taken. ROD
  portal is gated (browser/manual) -> deed images not auto-pulled yet.

**T-13 · 2026-07-08 · Draft staged - road cross-section inquiry to Justin Weist (Development Review)**
Drafted (NOT sent) to Justin.Weist@durhamnc.gov: required road/street cross-section for a single SFH
on the unopened ROW ~400 ft off Stagecoach; reduced rural section possible?; City vs NCDOT Div 5
jurisdiction; request a pre-application meeting. Key cost-unblock - quotes can't be accurate until the
required section is known.
