"""listings.feasibility -- E4 buildability verdict (the moat).

For any listing this resolves the parcel / zoning / flood via the SAME ArcGIS
REST layer propkb uses, then asks the docrag ``building-codes`` corpus a focused
set of buildability questions grounded to the parcel's actual zoning district --
returning a structured verdict where every substantive fact carries its docrag
citation. This is grounded research, not legal or engineering advice.

Two hard rules shape the implementation:

  1. NO KB POLLUTION. ``propkb.acquire.acquire()`` files JSON + a summary under
     ``properties/<slug>/`` -- we must NOT do that per listing. Instead we call
     the REST helpers directly (``_query_parcel`` / ``_query_spatial``) with a
     cfg from ``_cfg(location)`` and keep the parcel/zoning/flood dicts IN
     MEMORY. ``assess()`` never creates a ``properties/<slug>`` directory.

  2. CROSS-INSTANCE docrag. This process may have the LISTINGS instance env
     applied (DOCRAG_DOCS_ROOT / DOCRAG_INDEX_DIR -> listings_data). The
     ``building-codes`` corpus lives in the DEFAULT docrag instance
     (<repo>/corpora + <repo>/.index). ``_building_codes_env()`` temporarily
     REMOVES those two env vars so ``docrag.settings`` falls back to its defaults
     (where ``building-codes.db`` lives), then restores them.

Best-effort throughout: network / data problems record a gap and never crash
(mirrors ``propkb.acquire``'s ``# noqa: BLE001`` gap-recording style).

Public API:
    assess(listing: dict, location: str | None = None) -> dict
"""

from __future__ import annotations

import contextlib
import os
import re

from propkb import acquire as _acquire

# Grounded-research disclaimer stamped onto every verdict (PRD section 9).
DISCLAIMER = ("Research, not legal/engineering advice. "
              "Verify with the jurisdiction.")

# docrag retrieval depth per buildability question.
_TOP_K = 8

# The building-codes corpus is answered jurisdiction-BALANCED (model / NC-state /
# local layers interleaved), mirroring docrag_ask / the web UI. The `location`
# only actually FILTERS retrieval when passed inside `filters` alongside
# balance=True -- the bare `location=` kwarg just sets answer phrasing.
_BUILDING_CODES_CORPUS = "building-codes"

# Zoning-code field aliases across Durham + Wake layers (same set acquire uses
# in _summary_md._zval; CLASS = Wake countywide zoning field).
_ZONE_FIELDS = ("ZONE_CODE", "UDO_LABEL", "LABEL", "ZONING", "ZONE", "ZONECODE",
                "Zone", "ZONE_CLASS", "ZONE_GEN", "ZONE_TYPE", "DISTRICT",
                "ZONE_DESC", "CLASS")

# Wake iMAPS PLANNING_JURISDICTION short codes -> docrag location key. Wake
# zoning is per-municipality, so the code on the parcel is what picks the
# governing ordinance (WC unincorporated -> county UDO, RA -> Raleigh, ...).
_PJ_CODE_TO_LOCATION = {
    "WC": "wake-county-nc", "RA": "raleigh-nc", "CA": "cary-nc",
    "GA": "garner-nc", "WF": "wake-forest-nc", "AP": "apex-nc",
    "HS": "holly-springs-nc", "MO": "morrisville-nc", "MV": "morrisville-nc",
    "FV": "fuquay-varina-nc", "KN": "knightdale-nc", "WD": "wendell-nc",
    "WE": "wendell-nc", "ZB": "zebulon-nc", "RO": "rolesville-nc",
}
# Full-name fallbacks (Durham parcels carry a name, not a Wake code; also a
# defensive net if a layer returns spelled-out jurisdictions).
_PJ_NAME_TO_LOCATION = {
    "wake county": "wake-county-nc", "raleigh": "raleigh-nc", "cary": "cary-nc",
    "garner": "garner-nc", "wake forest": "wake-forest-nc", "apex": "apex-nc",
    "holly springs": "holly-springs-nc", "morrisville": "morrisville-nc",
    "fuquay-varina": "fuquay-varina-nc", "fuquay varina": "fuquay-varina-nc",
    "knightdale": "knightdale-nc", "wendell": "wendell-nc",
    "zebulon": "zebulon-nc", "rolesville": "rolesville-nc",
    "durham": "durham-nc", "durham county": "durham-nc",
    "city of durham": "durham-nc",
}

DEFAULT_LOCATION = "durham-nc"


def _valid_locations() -> set[str]:
    """The docrag location keys we recognize (from facets, so it stays in sync
    with the corpus). Imported lazily -- facets is cheap but keeps import light."""
    from docrag import facets
    return {loc["key"] for loc in facets.LOCATIONS}


# --------------------------------------------------------------------------- #
# Cross-instance env swap                                                      #
# --------------------------------------------------------------------------- #

@contextlib.contextmanager
def _building_codes_env():
    """Temporarily unbind DOCRAG_DOCS_ROOT / DOCRAG_INDEX_DIR so docrag.settings
    falls back to its defaults (<repo>/corpora + <repo>/.index), where
    ``building-codes.db`` lives -- even when this process has the listings
    instance env applied. Restores whatever was set on exit."""
    saved = {k: os.environ.pop(k, None)
             for k in ("DOCRAG_DOCS_ROOT", "DOCRAG_INDEX_DIR")}
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)


# --------------------------------------------------------------------------- #
# Jurisdiction resolution                                                      #
# --------------------------------------------------------------------------- #

def _map_planning_jurisdiction(value) -> str | None:
    """Map a parcel's PLANNING_JURISDICTION (Wake short code OR a name OR an
    already-normalized docrag key) to a docrag location key, else None."""
    if value in (None, ""):
        return None
    raw = str(value).strip()
    low = raw.lower()
    if low in _valid_locations():          # already a docrag key
        return low
    up = raw.upper()
    if up in _PJ_CODE_TO_LOCATION:         # Wake short code
        return _PJ_CODE_TO_LOCATION[up]
    if low in _PJ_NAME_TO_LOCATION:        # spelled-out jurisdiction
        return _PJ_NAME_TO_LOCATION[low]
    return None


def _resolve_location(location_arg, listing, parcel_attrs) -> tuple[str, str]:
    """Resolve the governing docrag location + how it was determined.

    Priority: explicit ``location`` arg ("arg") > the parcel's own
    PLANNING_JURISDICTION or the listing's stored jurisdiction ("parcel") >
    default durham-nc ("default").
    """
    if location_arg:
        return location_arg.strip().lower(), "arg"
    pj = (parcel_attrs or {}).get("PLANNING_JURISDICTION")
    loc = _map_planning_jurisdiction(pj)
    if loc:
        return loc, "parcel"
    # Fall back to the location the listing was ingested/enriched under.
    lj = _map_planning_jurisdiction((listing or {}).get("planning_jurisdiction"))
    if lj:
        return lj, "parcel"
    return DEFAULT_LOCATION, "default"


def _seed_location(location_arg, listing) -> str:
    """Location used to build the cfg for the FIRST parcel pull (before we can
    read the parcel's own PLANNING_JURISDICTION). Arg wins, then the listing's
    stored jurisdiction, then the default."""
    if location_arg:
        return location_arg.strip().lower()
    lj = _map_planning_jurisdiction((listing or {}).get("planning_jurisdiction"))
    return lj or DEFAULT_LOCATION


# --------------------------------------------------------------------------- #
# In-memory parcel / zoning / flood (no filing)                                #
# --------------------------------------------------------------------------- #

def _zoning_code(zoning: list[dict]) -> str | None:
    """First non-empty zoning district code across the intersecting polygons."""
    codes = []
    for z in zoning or []:
        for k in _ZONE_FIELDS:
            v = z.get(k)
            if v not in (None, ""):
                codes.append(str(v))
                break
    # dedupe preserving order; join if several districts touch the parcel
    seen, out = set(), []
    for c in codes:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return ", ".join(out) if out else None


def _flood_facts(flood: list[dict]) -> tuple[str | None, bool]:
    """(flood zone code, in_floodway). Durham uses ZoneCode; FEMA NFHL FLD_ZONE;
    floodway = AEFW code or a FLOODWAY subtype (same rule as acquire)."""
    zones, floodway = [], False
    for f in flood or []:
        code = f.get("ZoneCode") or f.get("ZONECODE") or f.get("FLD_ZONE")
        if code not in (None, ""):
            zones.append(str(code))
        if str(f.get("ZoneCode") or f.get("ZONECODE") or "").upper() == "AEFW" \
                or "FLOODWAY" in str(f.get("ZONE_SUBTY") or "").upper():
            floodway = True
    seen, out = set(), []
    for z in zones:
        if z not in seen:
            seen.add(z)
            out.append(z)
    return (", ".join(out) if out else None), floodway


def _watershed(zoning: list[dict], flood: list[dict]) -> str | None:
    """Best-effort watershed/overlay detection: any attribute whose key mentions
    WATERSHED (or an overlay LABEL that does) across the intersecting layers."""
    vals = []
    for row in list(zoning or []) + list(flood or []):
        for k, v in row.items():
            if "WATERSHED" in k.upper() and v not in (None, "", 0, "0"):
                vals.append(str(v))
    seen, out = set(), []
    for v in vals:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return ", ".join(out) if out else None


def _resolve_parcel(cfg: dict, pin, address) -> dict:
    """Pull parcel attributes + zoning/flood spatial intersects IN MEMORY.

    Returns {attributes, geometry, sr, zoning, flood, gaps, error?}. Never
    raises on network/data problems -- records gaps instead (BLE001)."""
    out = {"attributes": {}, "geometry": None, "sr": 4326,
           "zoning": [], "flood": [], "gaps": []}
    try:
        parcel = _acquire._query_parcel(cfg, pin, address)
    except Exception as e:  # noqa: BLE001
        out["error"] = "parcel query failed: %s" % e
        return out
    if not isinstance(parcel, dict) or parcel.get("error"):
        out["error"] = (parcel or {}).get("error", "no parcel match") \
            if isinstance(parcel, dict) else "no parcel match"
        return out

    out["attributes"] = parcel.get("attributes", {}) or {}
    out["geometry"] = parcel.get("geometry")
    out["sr"] = parcel.get("sr", 4326)
    return out


def _resolve_spatial(zoning_cfg: dict, geometry, sr) -> tuple[list, list, list]:
    """(zoning rows, flood rows, gaps) for the parcel geometry. Zoning from the
    location's own jurisdiction; flood county-level. Best-effort."""
    zoning, flood, gaps = [], [], []
    if not geometry:
        gaps.append("no parcel geometry -> skipped zoning/flood spatial queries")
        return zoning, flood, gaps
    for jur, dt, sink in ((zoning_cfg["zoning"], "zoning", zoning),
                          (zoning_cfg["county"], "flood", flood)):
        try:
            sink.extend(_acquire._query_spatial(jur, dt, geometry, sr))
        except Exception as e:  # noqa: BLE001
            gaps.append("%s: %s" % (dt, e))
    return zoning, flood, gaps


# --------------------------------------------------------------------------- #
# docrag buildability questions                                                #
# --------------------------------------------------------------------------- #

def _zone_phrase(zoning_code: str | None) -> str:
    return ("zoning district %s" % zoning_code) if zoning_code \
        else "the applicable base zoning district"


def _questions(zoning_code: str | None, property_type: str | None) -> dict:
    """Build the buildability questions from the parcel's ACTUAL zoning code +
    property type. No hardcoded answer values (memory: no-hardcoding-in-prompts)
    -- the queries are assembled from live parcel facts only."""
    zp = _zone_phrase(zoning_code)
    use = (property_type or "a single-family dwelling").strip()
    return {
        "allowed_uses":
            "What uses are permitted by right in %s?" % zp,
        "by_right_density":
            "What is the by-right residential density and minimum lot size in "
            "%s?" % zp,
        "setbacks":
            "What are the minimum front, side, and rear building setbacks in "
            "%s?" % zp,
        "height":
            "What is the maximum building height in %s?" % zp,
        "parking":
            "What is the minimum off-street parking requirement for %s in %s?"
            % (use, zp),
    }


def _location_filters(location: str) -> dict:
    """Retrieval filters for a location, mirroring docrag.mcp_server: the
    jurisdiction-tier selector + each document's latest edition. Best-effort --
    an unopenable index just yields empty versions. MUST be called inside
    _building_codes_env() so the corpus DB resolves to the default instance."""
    eff_versions: dict = {}
    try:
        from docrag.db import open_db
        from docrag import facets
        conn = open_db(_BUILDING_CODES_CORPUS)
        try:
            eff_versions = facets.resolve_versions(conn, {})
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 -- versions are optional sugar
        eff_versions = {}
    from docrag import facets
    return {"location": facets.location(location)["key"],
            "versions": eff_versions}


def _lot_sqft(listing: dict) -> int | None:
    """Lot area in square feet from lot_sqft, else lot_acres * 43,560."""
    v = (listing or {}).get("lot_sqft")
    if v not in (None, ""):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            pass
    a = (listing or {}).get("lot_acres")
    if a not in (None, ""):
        try:
            return int(float(a) * 43560)
        except (TypeError, ValueError):
            pass
    return None


def _units_question(zoning_code: str | None, lot_sqft: int | None) -> str:
    """Targeted question to elicit the by-right dwelling-unit count. When a lot
    size is known we ask the model to APPLY the district's density to it (its
    reasoning, not ours -- no hardcoded math)."""
    zp = _zone_phrase(zoning_code)
    if lot_sqft:
        return ("On a lot of approximately %d square feet in %s, what is the "
                "maximum number of dwelling units permitted by right? State the "
                "number." % (lot_sqft, zp))
    return ("In %s, what is the maximum number of dwelling units permitted by "
            "right per lot? State the number." % zp)


_WORD_NUM = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
             "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
_UNIT_NUM_RE = re.compile(r"(\d{1,3})\s*(?:principal\s+|dwelling\s+)?units?\b")
_UNIT_WORD_RE = re.compile(
    r"\b(zero|one|two|three|four|five|six|seven|eight|nine|ten)\s+"
    r"(?:principal\s+|dwelling\s+)?units?\b")


def _parse_units(text: str | None) -> int | None:
    """Extract an UNAMBIGUOUS by-right dwelling-unit count from the grounded
    answer, else None. Conservative on purpose: if the answer states more than
    one distinct count (e.g. a by-right number vs. a special-use-permit number),
    we DON'T guess -- return None so the scorer treats it as insufficient data
    rather than gating on a wrong number."""
    if not text:
        return None
    low = text.lower()
    cands = {int(m.group(1)) for m in _UNIT_NUM_RE.finditer(low)}
    cands |= {_WORD_NUM[m.group(1)] for m in _UNIT_WORD_RE.finditer(low)}
    return cands.pop() if len(cands) == 1 else None


def _ask(query: str, location: str) -> dict:
    """One grounded docrag call against the building-codes corpus, in the
    DEFAULT docrag instance. Balanced retrieval + a location filter so the
    answer is scoped to the governing jurisdiction (matches docrag_ask). Never
    raises: on error returns a refused-shaped dict so the caller records a gap."""
    try:
        with _building_codes_env():
            from docrag import answer as _dr  # lazy: heavy import chain
            filters = _location_filters(location)
            return _dr.answer(corpus=_BUILDING_CODES_CORPUS, query=query,
                              location=location, top_k=_TOP_K,
                              balance=True, filters=filters)
    except Exception as e:  # noqa: BLE001
        return {"answer": None, "refused": True,
                "refusal_reason": "error: %s" % e, "authorities": []}


def _citations_of(result: dict) -> list[dict]:
    """Normalize an answer() result into [{n, designation}, ...]. Prefers the
    spelled-out ``authorities``; falls back to bare citation ints."""
    auth = result.get("authorities")
    if auth:
        return [{"n": a.get("n"), "designation": a.get("designation")}
                for a in auth]
    out = []
    for n in result.get("citations") or []:
        if isinstance(n, dict):
            out.append({"n": n.get("n"), "designation": n.get("designation")})
        else:
            out.append({"n": n, "designation": None})
    return out


# --------------------------------------------------------------------------- #
# Public API                                                                   #
# --------------------------------------------------------------------------- #

def assess(listing: dict, location: str | None = None) -> dict:
    """Buildability verdict for a listing row.

    Resolves parcel/zoning/flood via propkb's REST layer (IN MEMORY -- no KB
    files written), determines the governing docrag ``location``, then asks the
    ``building-codes`` corpus the buildability questions grounded to the
    parcel's actual zoning district. Returns a structured verdict; best-effort,
    never raises on network/data problems.
    """
    listing = listing or {}
    pin = listing.get("parcel_pin")
    address = listing.get("address")

    verdict: dict = {
        "location": None, "jurisdiction_source": None,
        "zoning_code": None, "flood_zone": None, "in_floodway": False,
        "watershed": None,
        "allowed_uses": None, "by_right_density": None, "setbacks": None,
        "height": None, "parking": None, "by_right_units": None,
        "citations": [],
        "buildable_summary": None,
        "gaps": [],
        "disclaimer": DISCLAIMER,
    }

    # 1) Parcel pull with a seed cfg, then refine the governing location from
    #    the parcel's own PLANNING_JURISDICTION.
    seed = _seed_location(location, listing)
    seed_cfg = _acquire._cfg(seed)

    if not pin and not address:
        verdict["location"], verdict["jurisdiction_source"] = \
            _resolve_location(location, listing, None)
        verdict["gaps"].append("no parcel_pin or address on listing -> "
                               "cannot resolve parcel/zoning/flood")
        verdict["buildable_summary"] = (
            "No parcel identifier (PIN or address) on the listing, so parcel, "
            "zoning, and flood could not be resolved. Buildability is "
            "undetermined.")
        return verdict

    parcel = _resolve_parcel(seed_cfg, pin, address)
    resolved, source = _resolve_location(location, listing,
                                         parcel.get("attributes"))
    verdict["location"], verdict["jurisdiction_source"] = resolved, source

    if parcel.get("error"):
        verdict["gaps"].append("parcel: %s" % parcel["error"])
        verdict["buildable_summary"] = (
            "Parcel could not be resolved (%s); zoning, flood, and the "
            "buildability questions could not be answered. Location resolved "
            "as %s (%s)." % (parcel["error"], resolved, source))
        return verdict

    # 2) Zoning/flood spatial intersects, using the RESOLVED location's cfg
    #    (Wake zoning is per-municipality; flood is county-level).
    zoning_cfg = _acquire._cfg(resolved)
    zoning, flood, sp_gaps = _resolve_spatial(zoning_cfg, parcel["geometry"],
                                              parcel["sr"])
    verdict["gaps"].extend(sp_gaps)

    verdict["zoning_code"] = _zoning_code(zoning)
    verdict["flood_zone"], verdict["in_floodway"] = _flood_facts(flood)
    verdict["watershed"] = _watershed(zoning, flood)
    if not verdict["zoning_code"]:
        verdict["gaps"].append("no zoning district returned for the parcel -> "
                               "buildability questions asked without a district")

    # 3) Buildability questions grounded to the actual zoning code.
    questions = _questions(verdict["zoning_code"], listing.get("property_type"))
    seen_cites: set = set()
    all_cites: list[dict] = []

    def _collect(res: dict) -> None:
        for c in _citations_of(res):
            key = (c.get("n"), c.get("designation"))
            if key not in seen_cites:
                seen_cites.add(key)
                all_cites.append(c)

    for field, query in questions.items():
        res = _ask(query, resolved)
        if res.get("refused") or not res.get("answer"):
            verdict["gaps"].append(
                "%s: docrag gave no grounded answer (%s)"
                % (field, res.get("refusal_reason") or "refused"))
            continue
        verdict[field] = res["answer"]
        _collect(res)

    # Structured by-right dwelling-unit count (for the land-to-build pro-forma).
    # The supporting citation lands in the shared `citations` list. None + a gap
    # when docrag can't ground it or the count is ambiguous.
    ures = _ask(_units_question(verdict["zoning_code"], _lot_sqft(listing)),
                resolved)
    if ures.get("refused") or not ures.get("answer"):
        verdict["gaps"].append("by_right_units: docrag gave no grounded answer "
                               "(%s)" % (ures.get("refusal_reason") or "refused"))
    else:
        _collect(ures)
        verdict["by_right_units"] = _parse_units(ures["answer"])
        if verdict["by_right_units"] is None:
            verdict["gaps"].append("by_right_units: could not extract an "
                                   "unambiguous unit count from the grounded "
                                   "answer")
    verdict["citations"] = all_cites

    # 4) Synthesis paragraph.
    verdict["buildable_summary"] = _summary(verdict)
    return verdict


def _summary(v: dict) -> str:
    """One-paragraph synthesis of the resolved facts (no new claims -- restates
    the cited fields + flags what's missing)."""
    parts = []
    zc = v["zoning_code"]
    parts.append("Parcel governed by %s" % v["location"]
                 + (" in zoning district %s." % zc if zc
                    else "; no zoning district resolved."))
    if v["allowed_uses"]:
        parts.append("Allowed uses and by-right density are addressed by the "
                     "cited provisions.")
    if v["by_right_units"] is not None:
        parts.append("By-right dwelling units: %d." % v["by_right_units"])
    if v["setbacks"] or v["height"] or v["parking"]:
        parts.append("Dimensional standards (setbacks / height / parking) were "
                     "retrieved and cited where available.")
    fz = v["flood_zone"]
    if fz:
        parts.append("Flood zone %s%s."
                     % (fz, " (in floodway)" if v["in_floodway"] else ""))
    if v["watershed"]:
        parts.append("Watershed/overlay: %s." % v["watershed"])
    answered = sum(1 for f in ("allowed_uses", "by_right_density", "setbacks",
                               "height", "parking") if v[f])
    if answered == 0:
        parts.append("No buildability questions could be answered from the "
                     "corpus; treat buildability as undetermined.")
    if v["gaps"]:
        parts.append("Open gaps: %d (see gaps)." % len(v["gaps"]))
    return " ".join(parts)
