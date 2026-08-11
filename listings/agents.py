"""listings.agents -- E7 realtor/supply-side intelligence + the FHA-firewalled
demographic signal (PRD sec.5 E7, sec.9).

Two independent jobs live here, kept deliberately separate:

1. ``rollup(conn)`` -- ordinary, defensible realtor analytics: per-agent
   listing volume, the jurisdictions/ZIPs they work, their price band, and
   two behavior signals (relist_rate, below_avm_rate) computed straight off
   the listings/price_history tables. Nothing controversial; this is the kind
   of rollup any listing-data product ships.

2. ``infer_demographic`` / ``attach_demographic`` -- a name -> coarse
   ethnicity/heritage GUESS, attached only to listing agents. This is a real
   legal landmine (Fair Housing Act): using an inferred demographic signal to
   affect any housing decision is exactly the kind of disparate-impact fact
   pattern FHA enforcement targets, even when the signal itself is a private
   research curiosity and not customer-facing. The design below is
   non-negotiable (PRD sec.9):

     - It attaches ONLY to listing agents (the supply side), never to
       buyers/tenants/sellers, and lives ONLY in ``agents.demographic_signal_json``
       -- ``attach_demographic`` writes that one column and nothing else.
     - It is hard-blocked from every housing-decision path: ``score.py`` and
       ``monitor.py`` must not import this module or reference a demographic
       field (enforced by ``tests/_fha_firewall.py``, shared across both).
     - Every surface that returns it carries ``decisional: False``, a
       ``confidence: "low"`` label, ``method: "name-inferred"``, and a
       non-empty FHA-risk notice -- so it can never be silently treated as an
       authoritative or actionable fact.
     - The heuristic itself is intentionally tiny, transparent, and
       self-contained (a hardcoded surname hint table, "unknown" default) --
       not a purchased dataset or third-party API. It exists for the
       operator's own market-color curiosity. This module, and its use, must
       be revisited with counsel before Scout leaves personal use, and
       dropping it entirely should be reconsidered if the product ever goes
       external.

Known gap: ``below_avm_rate`` (PRD sec.7) needs an AVM or assessed-value
figure to compare list price against, and neither ``listings`` nor
``enrichment`` carries one yet (no adapter supplies it as of this writing --
RentCast/ATTOM AVM integration is a later Scout increment). ``rollup`` records
it as ``None`` rather than fabricating a proxy; wire a real AVM/assessed-value
field before relying on it.
"""

from __future__ import annotations

import json
import re
import sqlite3
import statistics
from collections import Counter
from typing import Optional

from . import store

# ---------------------------------------------------------------------------
# realtor rollup (E7, ordinary analytics half)
# ---------------------------------------------------------------------------

_ZIP_RE = re.compile(r"(\d{5})(?:-\d{4})?\s*$")


def _zip_from_address(address: Optional[str]) -> Optional[str]:
    """Best-effort 5-digit ZIP off the tail of a "...City, ST 12345" address
    string. None if it isn't there -- never guesses."""
    if not address:
        return None
    m = _ZIP_RE.search(address.strip())
    return m.group(1) if m else None


def _agent_key(row: dict) -> Optional[str]:
    """Stable rollup key for a listing row: the MLS ``list_agent_id`` when
    present, else a deterministic pseudo-id derived from the agent name (so
    agents an adapter didn't give an id are still rolled up), else None (no
    agent identity on this listing -- skip it)."""
    agent_id = row.get("list_agent_id")
    if agent_id:
        return str(agent_id)
    name = row.get("list_agent_name")
    if name and name.strip():
        return "name:" + re.sub(r"\s+", "-", name.strip().lower())
    return None


def _price_band(prices: list) -> dict:
    """min/median/max list price + an equal-width 3-bucket histogram, mirroring
    ``stats.price_distribution``'s bucketing so an agent's band reads
    consistently against the market-wide distribution. {} shape with Nones/[]
    on an empty price list -- never divides by zero."""
    vals = sorted(p for p in prices if p is not None)
    if not vals:
        return {"min": None, "median": None, "max": None, "count": 0, "bands": []}

    lo, hi = vals[0], vals[-1]
    result = {"min": lo, "median": statistics.median(vals), "max": hi, "count": len(vals)}
    if lo == hi:
        result["bands"] = [{"min": lo, "max": hi, "count": len(vals)}]
        return result

    buckets = 3
    width = (hi - lo) / buckets
    edges = [lo + i * width for i in range(buckets + 1)]
    counts = [0] * buckets
    for p in vals:
        idx = min(int((p - lo) / width), buckets - 1)
        counts[idx] += 1
    result["bands"] = [{"min": edges[i], "max": edges[i + 1], "count": counts[i]} for i in range(buckets)]
    return result


def _relist_rate(conn: sqlite3.Connection, listing_ids: list) -> Optional[float]:
    """Fraction of ``listing_ids`` with >=1 ``price_change`` event recorded in
    price_history (our relist/price-drop-cadence proxy). None on an empty group."""
    if not listing_ids:
        return None
    with_change = 0
    for listing_id in listing_ids:
        events = store.price_history(conn, listing_id)
        if any(e.get("event") == "price_change" for e in events):
            with_change += 1
    return with_change / len(listing_ids)


def rollup(conn: sqlite3.Connection) -> int:
    """Recompute one ``agents`` row per distinct listing agent from the
    ``listings`` table: listing_count, geo_json (jurisdiction + ZIP counts),
    price_band_json (min/median/max + a 3-bucket histogram), relist_rate.
    ``below_avm_rate`` is always written as None -- see the module docstring's
    "Known gap" note; there is no AVM/assessed-value field to compute it from
    yet, and this deliberately does not fabricate one.

    Deterministic, read-then-write, safe to re-run: every agent row is fully
    recomputed from the current listings each call (idempotent -- re-running
    against unchanged data upserts the same values). Listings with neither a
    ``list_agent_id`` nor a ``list_agent_name`` are skipped (no agent identity
    to roll up). Never touches ``demographic_signal_json`` -- that column is
    exclusively written by ``attach_demographic``.

    Returns the number of distinct agents upserted.
    """
    total = store.count_listings(conn)
    rows = store.list_listings(conn, limit=total) if total else []

    groups: dict[str, dict] = {}
    for row in rows:
        key = _agent_key(row)
        if key is None:
            continue
        g = groups.setdefault(key, {
            "listing_ids": [], "names": Counter(), "brokerages": Counter(),
            "jurisdictions": Counter(), "zips": Counter(), "prices": [],
        })
        g["listing_ids"].append(row["id"])
        if row.get("list_agent_name"):
            g["names"][row["list_agent_name"]] += 1
        if row.get("brokerage"):
            g["brokerages"][row["brokerage"]] += 1
        if row.get("planning_jurisdiction"):
            g["jurisdictions"][row["planning_jurisdiction"]] += 1
        zip_code = _zip_from_address(row.get("address"))
        if zip_code:
            g["zips"][zip_code] += 1
        if row.get("price") is not None:
            g["prices"].append(row["price"])

    for agent_id, g in groups.items():
        name = g["names"].most_common(1)[0][0] if g["names"] else None
        brokerage = g["brokerages"].most_common(1)[0][0] if g["brokerages"] else None
        geo = {"jurisdictions": dict(g["jurisdictions"]), "zips": dict(g["zips"])}
        store.upsert_agent(conn, {
            "agent_id": agent_id,
            "name": name,
            "brokerage": brokerage,
            "listing_count": len(g["listing_ids"]),
            "geo_json": json.dumps(geo, ensure_ascii=False),
            "price_band_json": json.dumps(_price_band(g["prices"]), ensure_ascii=False),
            "relist_rate": _relist_rate(conn, g["listing_ids"]),
            "below_avm_rate": None,
        })

    return len(groups)


# ---------------------------------------------------------------------------
# firewalled demographic signal (PRD sec.9) -- read the module docstring
# before touching anything below.
# ---------------------------------------------------------------------------

_FHA_NOTICE = (
    "Coarse, low-confidence, name-inferred guess about a listing agent's likely "
    "ethnicity/heritage. NOT verified, NOT authoritative, and MUST NOT be used to "
    "make or influence any housing, lending, listing, or ranking decision -- doing "
    "so risks Fair Housing Act liability. Internal market-color curiosity only "
    "(PRD sec.9); revisit with counsel before this leaves personal use."
)

# Intentionally tiny and transparent -- a hand-written surname hint table, NOT
# a purchased/scraped dataset. Every value is labeled a "coarse-guess", and an
# unrecognized surname always falls back to "unknown". This is crude on
# purpose: it is a low-confidence, non-decisional curiosity, never a fact.
_SURNAME_HINTS: dict[str, str] = {
    "nguyen": "coarse-guess:vietnamese-surname",
    "tran": "coarse-guess:vietnamese-surname",
    "pham": "coarse-guess:vietnamese-surname",
    "patel": "coarse-guess:south-asian-surname",
    "singh": "coarse-guess:south-asian-surname",
    "khan": "coarse-guess:south-asian-or-middle-eastern-surname",
    "kim": "coarse-guess:korean-surname",
    "park": "coarse-guess:korean-surname",
    "chen": "coarse-guess:chinese-surname",
    "wang": "coarse-guess:chinese-surname",
    "li": "coarse-guess:chinese-surname",
    "garcia": "coarse-guess:hispanic-surname",
    "rodriguez": "coarse-guess:hispanic-surname",
    "martinez": "coarse-guess:hispanic-surname",
    "hernandez": "coarse-guess:hispanic-surname",
}


def infer_demographic(name: Optional[str]) -> dict:
    """FIREWALLED name -> coarse demographic-signal guess (PRD sec.9). Looks up
    only the surname (last whitespace-separated token, casefolded, punctuation
    stripped) in the tiny hardcoded ``_SURNAME_HINTS`` table; "unknown" for
    anything unrecognized or an empty/missing name.

    Always returns the fully-labeled, non-decisional shape -- callers must
    never strip these labels off before storing/displaying the signal:
        {"signal": str, "confidence": "low", "method": "name-inferred",
         "decisional": False, "fha_notice": str}
    """
    surname = ""
    if name:
        tokens = re.sub(r"[^\w\s'-]", "", name.strip()).split()
        if tokens:
            surname = tokens[-1].lower()
    signal = _SURNAME_HINTS.get(surname, "unknown")
    return {
        "signal": signal,
        "confidence": "low",
        "method": "name-inferred",
        "decisional": False,
        "fha_notice": _FHA_NOTICE,
    }


def attach_demographic(conn: sqlite3.Connection, agent_id: str) -> dict:
    """Compute ``infer_demographic`` off the stored agent's name and write it
    to ONLY ``agents.demographic_signal_json`` -- via ``store.upsert_agent``'s
    merge-preserving update, passing nothing else, so every other agent column
    is left untouched. Raises ValueError if ``agent_id`` has no row yet (run
    ``rollup`` first). Returns the demographic dict written."""
    agent = store.get_agent(conn, agent_id)
    if agent is None:
        raise ValueError("no agent row for agent_id %r -- run rollup() first" % (agent_id,))
    signal = infer_demographic(agent.get("name"))
    store.upsert_agent(conn, {
        "agent_id": agent_id,
        "demographic_signal_json": json.dumps(signal, ensure_ascii=False),
    })
    return signal
