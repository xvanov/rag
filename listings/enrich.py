"""listings.enrich -- E4/E5/E6 orchestration: turn a bare listing row into an
enriched, scored candidate and persist it.

This is the glue over the three moat modules, run only on the survivors of the
cheap hard-filter/semantic gate (PRD sec.6 -- expensive enrichment last):

    feasibility.assess   -> buildability verdict + citations   (E4, the moat)
    commute.rank         -> multi-point travel time            (E5)
    score.score          -> per-strategy, feasibility-adjusted  (E6)

The results are written to the typed ``enrichment`` row (store.upsert_enrichment)
so a later search/monitor pass reads them without re-paying the API cost. Every
step is best-effort: a failure in one (no parcel match, no commute key, a docrag
refusal) records a gap and still writes what did resolve -- enrichment never
crashes the pipeline.

Schema bridge (buildability -> scoring): feasibility answers density as PROSE
(``by_right_density`` grounded + cited to the UDO), while score.py needs a
numeric ``buildable_units``. We do NOT invent a density. We store the full
verdict, and additionally attempt to READ an integer unit count out of the
cited UDO answer text (``_units_from_text``); when one is found it is passed
through tagged ``buildable_units_source: "parsed-from-udo-text"`` so the number
is traceable and clearly low-confidence, and when none is found the field stays
absent and score.py correctly returns "insufficient-data" (PRD sec.9: never
silently assume max density). The operator can always override via assumptions.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from . import commute as _commute
from . import feasibility as _feasibility
from . import score as _score
from . import store

STRATEGIES = ("land-to-build", "buy-hold", "flip", "str")

# Ordered patterns to pull a by-right unit count out of a cited UDO answer
# paragraph, e.g. "up to 2 dwelling units by right", "a maximum of 3 units",
# "single-family (1 unit)". Best-effort only -- the number is always tagged as
# parsed-from-text so downstream treats it as low-confidence, never authoritative.
_UNIT_PATTERNS = (
    re.compile(r"\b(\d+)\s+(?:dwelling\s+units?|units?|dwellings?|lots?)\b", re.I),
    re.compile(r"\b(?:up to|maximum of|max(?:imum)?|allows?)\s+(\d+)\b", re.I),
)
_SINGLE_FAMILY = re.compile(r"\bsingle[-\s]family\b", re.I)


def _units_from_text(*texts: str | None) -> int | None:
    """Best-effort by-right unit count from the feasibility prose. Returns None
    when nothing is confidently extractable (caller then leaves it absent)."""
    blob = " ".join(t for t in texts if t)
    if not blob:
        return None
    for pat in _UNIT_PATTERNS:
        m = pat.search(blob)
        if m:
            try:
                n = int(m.group(1))
            except (TypeError, ValueError):
                continue
            if 0 <= n <= 500:      # sanity bound; reject absurd matches (years, etc.)
                return n
    if _SINGLE_FAMILY.search(blob):
        return 1
    return None


def _buildability_payload(verdict: dict) -> dict:
    """Normalize a feasibility verdict into the ``buildability_json`` shape
    score.py reads.

    Prefer feasibility's own grounded, docrag-cited ``by_right_units`` (it asks
    the UDO a targeted density question off the parcel's lot size + zoning). Only
    when that key is ABSENT (older verdict shape) do we fall back to reading a
    number out of the density prose -- and we never override a grounded ``None``
    with a regex guess, since a grounded "couldn't determine" is real
    information (PRD sec.9: don't silently assume density)."""
    payload = dict(verdict)
    if "by_right_units" in verdict:
        units = verdict.get("by_right_units")
        source = "feasibility-grounded"
    else:
        units = _units_from_text(verdict.get("by_right_density"),
                                 verdict.get("allowed_uses"),
                                 verdict.get("buildable_summary"))
        source = "parsed-from-udo-text"
    if units is not None:
        payload["buildable_units"] = units
        payload["buildable_units_source"] = source
    # A definite "not buildable" only when there IS a resolved zoning yet the
    # summary is explicitly negative; otherwise leave buildable unset (unknown),
    # so a missing verdict never reads as a confident yes/no.
    if verdict.get("zoning_code"):
        payload.setdefault("buildable", True if units is None or units > 0 else None)
    return payload


def enrich_listing(conn, listing_id: int, destinations: list[dict] | None = None,
                   scenario: str = "typical", strategies=STRATEGIES,
                   location: str | None = None, market: dict | None = None,
                   assumptions: dict | None = None) -> dict:
    """Run feasibility + commute + scoring for one listing and persist the
    enrichment row. Returns the assembled enrichment dict (also stored)."""
    listing = store.get_listing(conn, listing_id)
    if listing is None:
        raise ValueError("no listing with id %r" % listing_id)

    gaps: list[str] = []

    # --- E4: buildability (the moat) ------------------------------------
    try:
        verdict = _feasibility.assess(listing, location=location)
    except Exception as e:  # noqa: BLE001 -- best-effort; never crash the pipeline
        verdict = {"gaps": ["feasibility failed: %s" % e], "citations": [],
                   "zoning_code": None}
    gaps += ["feasibility: " + g for g in (verdict.get("gaps") or [])]
    buildability = _buildability_payload(verdict)
    citations = verdict.get("citations") or []
    # PRD sec.7: buildability_cited holds the docrag citation STRING (the named
    # provisions the verdict rests on), not a 0/1 flag.
    cited_str = "; ".join(
        str(c.get("designation") or "").strip()
        for c in citations if c.get("designation")
    )

    # --- E5: commute ----------------------------------------------------
    commute_res = None
    if destinations:
        lat, lon = listing.get("lat"), listing.get("lon")
        if lat is None or lon is None:
            gaps.append("commute: listing has no lat/lon")
        else:
            try:
                commute_res = _commute.rank((lat, lon), destinations, scenario=scenario)
            except Exception as e:  # noqa: BLE001
                gaps.append("commute failed: %s" % e)
            else:
                gaps += ["commute: " + g for g in (commute_res.get("gaps") or [])]

    # --- E6: scoring (feasibility-adjusted), per requested strategy ------
    enrichment_view = {
        "buildability_json": json.dumps(buildability, ensure_ascii=False),
        "buildability_cited": cited_str,
    }
    scores: dict[str, dict] = {}
    for strat in strategies:
        try:
            scores[strat] = _score.score(listing, enrichment_view, strategy=strat,
                                          market=market, assumptions=assumptions)
        except Exception as e:  # noqa: BLE001
            scores[strat] = {"strategy": strat, "score": None,
                             "verdict": "error", "gaps": ["scoring failed: %s" % e]}

    # --- persist --------------------------------------------------------
    row = {
        "zoning_code": verdict.get("zoning_code"),
        "allowed_uses_json": json.dumps(verdict.get("allowed_uses"), ensure_ascii=False),
        "flood_zone": verdict.get("flood_zone"),
        "in_floodway": 1 if verdict.get("in_floodway") else 0,
        "watershed": verdict.get("watershed"),
        "commute_json": json.dumps(commute_res, ensure_ascii=False) if commute_res else None,
        "poi_json": None,   # POI counts: E5 stretch, not wired yet
        "buildability_json": enrichment_view["buildability_json"],
        "buildability_cited": enrichment_view["buildability_cited"],
        "score_json": json.dumps(scores, ensure_ascii=False),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    store.upsert_enrichment(conn, listing_id, row)

    return {
        "listing_id": listing_id,
        "location": verdict.get("location"),
        "zoning_code": verdict.get("zoning_code"),
        "flood_zone": verdict.get("flood_zone"),
        "in_floodway": bool(verdict.get("in_floodway")),
        "buildable_summary": verdict.get("buildable_summary"),
        "citations": citations,
        "commute": commute_res,
        "scores": scores,
        "gaps": gaps,
        "disclaimer": "Research, not legal/engineering/investment advice. Verify locally.",
    }
