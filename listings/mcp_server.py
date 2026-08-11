"""listings MCP server -- Scout listings-intelligence tools (stdio).

A THIRD docrag instance (after ``docrag``=building-codes and ``propkb``=
properties), bound to the Scout data/index roots and exposed as its own MCP
server so a Claude agent can drive the search -> buildability -> score funnel
over the listing corpus. Tools: listing_search / listing_stats /
listing_buildability / listing_score / listing_enrich.

The buildability + score tools reach across instances: feasibility.py answers
the ``building-codes`` corpus in the DEFAULT docrag instance (it temporarily
unbinds the listings env, see feasibility._building_codes_env), so a listing's
zoning/flood verdict is grounded and cited to the real ordinance layers.

Run:  python -m listings.mcp_server
Register in .mcp.json alongside the docrag + propkb servers.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as _FTimeout

# stdio hygiene (same rationale as docrag/propkb mcp_server: stdout is the
# JSON-RPC channel; force all logging to stderr and silence INFO).
os.environ.setdefault("PYTHONUNBUFFERED", "1")
logging.basicConfig(level=logging.WARNING, stream=sys.stderr, force=True)
logging.disable(logging.INFO)
os.environ.setdefault("DOCRAG_RERANK", "0")

# CRITICAL: bind the engine to the LISTINGS instance BEFORE importing docrag
# modules, so every docrag call in this process uses listings_data/ +
# listings_data/.index (feasibility temporarily unbinds it for building-codes).
from . import settings as _ls           # noqa: E402
_ls.apply_docrag_env()

from mcp.server.fastmcp import FastMCP   # noqa: E402

# Eager heavy imports on the MAIN thread (numpy / sqlite-vec / docrag chain) --
# the same import-lock discipline propkb uses to avoid the stdio deadlock. Every
# module a tool touches is imported here, not lazily inside a worker thread.
from . import store                      # noqa: E402
from . import search as _search          # noqa: E402
from . import stats as _stats            # noqa: E402
from . import feasibility as _feasibility  # noqa: E402
from . import score as _score            # noqa: E402
from . import enrich as _enrich          # noqa: E402
from docrag.query import rag_query as _rag_query    # noqa: E402,F401 (primes chain)
from docrag.answer import answer as _answer         # noqa: E402,F401 (primes chain)

mcp = FastMCP("listings")

# Single-worker executor: tools in THIS process must never run concurrently.
# listing_buildability pops the docrag env (feasibility._building_codes_env) to
# reach the building-codes corpus, while listing_search reads that same env for
# the listings corpus -- if they ran on two threads of the default executor, one
# would read the env mid-swap and query the wrong corpus. max_workers=1
# serializes every tool call, eliminating that intra-process race with no deeper
# refactor. Agent tool-calls are effectively sequential anyway, and the
# per-call timeout in _run_bounded still applies (it wraps the future).
_EXECUTOR = ThreadPoolExecutor(max_workers=1)


def _tool_timeout() -> float:
    from docrag import settings as _s
    return float(_s.get("DOCRAG_TOOL_TIMEOUT", 150) or 150)


async def _run_bounded(fn):
    loop = asyncio.get_event_loop()
    try:
        return await asyncio.wait_for(loop.run_in_executor(_EXECUTOR, fn),
                                      timeout=_tool_timeout())
    except (asyncio.TimeoutError, TimeoutError) as e:
        raise _FTimeout from e


# --------------------------------------------------------------------------- #
# formatting helpers (strings only -- mirror propkb's human-readable returns)  #
# --------------------------------------------------------------------------- #

def _money(v) -> str:
    try:
        return "${:,.0f}".format(float(v))
    except (TypeError, ValueError):
        return "?"


def _pct(v) -> str:
    try:
        return "{:.1f}%".format(float(v))
    except (TypeError, ValueError):
        return "?"


def _num(v) -> str:
    if v is None:
        return "?"
    try:
        f = float(v)
        return "{:,.0f}".format(f) if f == int(f) else "{:,.2f}".format(f)
    except (TypeError, ValueError):
        return str(v)


def _filters(price_max: float, price_min: float, property_type: str,
             jurisdiction: str, beds_min: float) -> dict:
    """Build the store filter dict, omitting zero/empty values."""
    f: dict = {}
    if price_min and price_min > 0:
        f["price_min"] = price_min
    if price_max and price_max > 0:
        f["price_max"] = price_max
    if beds_min and beds_min > 0:
        f["beds_min"] = beds_min
    if (property_type or "").strip():
        f["property_type"] = property_type.strip()
    if (jurisdiction or "").strip():
        f["jurisdiction"] = jurisdiction.strip()
    return f


# --------------------------------------------------------------------------- #
# tools                                                                        #
# --------------------------------------------------------------------------- #

@mcp.tool()
async def listing_search(query: str, price_max: float = 0, price_min: float = 0,
                         property_type: str = "", jurisdiction: str = "",
                         beds_min: float = 0, top_k: int = 15,
                         semantic: bool = True) -> str:
    """Search listings: hard-filter (SQLite) then semantic rerank over the
    listing-description corpus. Returns a ranked shortlist.

    Args:
        query: natural-language intent, matched against listing remarks
            (e.g. "existing septic and well", "flag lot with ADU potential").
        price_max / price_min: price bounds in dollars (0 = no bound).
        property_type: exact type filter, e.g. ``land`` / ``single_family`` /
            ``multi_family_2_4`` (empty = any).
        jurisdiction: planning_jurisdiction filter, e.g. ``durham-nc`` (empty = any).
        beds_min: minimum bedrooms (0 = no bound).
        top_k: how many to return (1-50).
        semantic: rerank semantically; False = hard-filtered rows only.
    """
    query = (query or "").strip()
    top_k = max(1, min(int(top_k or 15), 50))
    filters = _filters(price_max, price_min, property_type, jurisdiction, beds_min)

    def _work():
        conn = store.connect()
        store.init_db(conn)
        try:
            return _search.search(conn, query, filters, top_k=top_k, semantic=semantic)
        finally:
            conn.close()

    try:
        rows = await _run_bounded(_work)
    except _FTimeout:
        return "ERROR: listing_search timed out (>%ds)." % int(_tool_timeout())
    except Exception as e:  # noqa: BLE001
        return "ERROR: listing_search failed: %s" % e

    if not rows:
        crit = ", ".join("%s=%s" % (k, v) for k, v in filters.items()) or "(none)"
        return ("No listings matched. Query=%r  filters: %s\n"
                "(Ingest with: python -m listings ingest --source sample --geo durham)"
                % (query, crit))

    note = rows[0].get("_note")
    head = "Found %d listing(s)%s:" % (
        len(rows), " (semantic off)" if not semantic or not query else "")
    lines = [head]
    if note:
        lines.append("  [!] %s" % note)
    for r in rows:
        score = r.get("_score")
        lines.append(
            "#%s  id=%s  %s  %s  %s  score=%s"
            % (r.get("_rank"), r.get("id"), r.get("address") or "?",
               _money(r.get("price")), r.get("property_type") or "?",
               ("%.4f" % score) if score is not None else "-"))
        snippet = r.get("_snippet") or " ".join(
            (r.get("description_text") or "").split())[:220]
        if snippet:
            lines.append("     %s" % snippet)
    lines.append("\nNext: listing_buildability(listing_id=<id>) for the cited "
                 "zoning/flood verdict, then listing_score(listing_id=<id>).")
    return "\n".join(lines)


@mcp.tool()
async def listing_stats(property_type: str = "", jurisdiction: str = "",
                        price_max: float = 0) -> str:
    """Market statistics over the filtered listing slice: price / $-per-sqft /
    days-on-market distributions, per-type and per-jurisdiction rollups,
    price-drop rate, and inventory. Read-only aggregation over listings.db.
    """
    filters = _filters(price_max, 0, property_type, jurisdiction, 0)

    def _work():
        conn = store.connect()
        store.init_db(conn)
        try:
            return _stats.compute_stats(conn, filters)
        finally:
            conn.close()

    try:
        s = await _run_bounded(_work)
    except _FTimeout:
        return "ERROR: listing_stats timed out."
    except Exception as e:  # noqa: BLE001
        return "ERROR: listing_stats failed: %s" % e

    if not s.get("count"):
        crit = ", ".join("%s=%s" % (k, v) for k, v in filters.items()) or "(none)"
        return "No listings in scope. filters: %s" % crit

    p = s["price"]
    psf = s["price_per_sqft"]
    dom = s["dom"]
    drop = s["price_drop_rate"]
    inv = s["inventory"]

    lines = ["Market stats over %d listing(s):" % s["count"]]
    lines.append("  Price:     min %s | median %s | mean %s | max %s"
                 % (_money(p["min"]), _money(p["median"]), _money(p["mean"]), _money(p["max"])))
    lines.append("  $/sqft:    min %s | median %s | max %s"
                 % (_num(psf["min"]), _num(psf["median"]), _num(psf["max"])))
    lines.append("  Days-on-market: min %s | median %s | mean %s | max %s"
                 % (_num(dom["min"]), _num(dom["median"]), _num(dom["mean"]), _num(dom["max"])))
    if drop.get("rate") is not None:
        lines.append("  Price-drop rate: %s (median drop %s)"
                     % (_pct(drop["rate"] * 100), _pct(drop["median_drop_pct"])
                        if drop.get("median_drop_pct") is not None else "?"))
    lines.append("  Inventory: %d active | %s"
                 % (inv["total_active"],
                    ", ".join("%s=%d" % (k, v) for k, v in inv["status_breakdown"].items())))

    if s["by_property_type"]:
        lines.append("  By property type:")
        for pt, d in s["by_property_type"].items():
            lines.append("    %-18s n=%-3d median %s" % (pt, d["count"], _money(d["price_median"])))
    if s["by_jurisdiction"]:
        lines.append("  By jurisdiction:")
        for j, d in s["by_jurisdiction"].items():
            lines.append("    %-18s n=%-3d median %s" % (j, d["count"], _money(d["median_price"])))
    return "\n".join(lines)


@mcp.tool()
async def listing_buildability(listing_id: int = 0, address: str = "",
                               location: str = "") -> str:
    """Buildability verdict (THE moat): resolves parcel / zoning / flood via
    the ArcGIS REST layer, then grounds by-right use/density/setbacks/height/
    parking to the parcel's ACTUAL zoning district against the ``building-codes``
    corpus -- every substantive fact carries its docrag citation.

    Pass ``listing_id`` for a stored listing, OR ``address`` to assess an
    arbitrary parcel ad-hoc. ``location`` overrides the governing jurisdiction
    (docrag location key, e.g. ``durham-nc``); omit to derive it from the parcel.

    Research, not legal/engineering advice.
    """
    listing_id = int(listing_id or 0)
    address = (address or "").strip()
    location = (location or "").strip().lower() or None

    if not listing_id and not address:
        return "ERROR: pass either listing_id or address."

    def _work():
        if listing_id:
            conn = store.connect()
            store.init_db(conn)
            try:
                row = store.get_listing(conn, listing_id)
            finally:
                conn.close()
            if row is None:
                return {"_error": "no listing with id %d" % listing_id}
            return _feasibility.assess(row, location=location)
        # ad-hoc: synthesize a minimal listing dict from the address.
        return _feasibility.assess({"address": address}, location=location)

    try:
        v = await _run_bounded(_work)
    except _FTimeout:
        return "ERROR: listing_buildability timed out (>%ds); parcel/docrag layer slow." % int(_tool_timeout())
    except Exception as e:  # noqa: BLE001
        return "ERROR: listing_buildability failed: %s" % e

    if v.get("_error"):
        return "ERROR: %s" % v["_error"]

    lines = ["Buildability verdict:"]
    subj = ("listing #%d" % listing_id) if listing_id else address
    lines.append("  Subject: %s" % subj)
    lines.append("  Governing location: %s (%s)"
                 % (v.get("location") or "?", v.get("jurisdiction_source") or "?"))
    lines.append("  Zoning district: %s" % (v.get("zoning_code") or "not resolved"))
    fz = v.get("flood_zone")
    lines.append("  Flood zone: %s%s"
                 % (fz or "not resolved", " (IN FLOODWAY)" if v.get("in_floodway") else ""))
    if v.get("watershed"):
        lines.append("  Watershed/overlay: %s" % v["watershed"])
    if v.get("by_right_units") is not None:
        lines.append("  By-right dwelling units: %d" % v["by_right_units"])

    for label, key in (("Allowed uses", "allowed_uses"),
                       ("By-right density", "by_right_density"),
                       ("Setbacks", "setbacks"), ("Max height", "height"),
                       ("Parking", "parking")):
        val = v.get(key)
        if val:
            lines.append("  %s: %s" % (label, " ".join(str(val).split())))

    if v.get("buildable_summary"):
        lines.append("\nSummary: %s" % v["buildable_summary"])

    cites = v.get("citations") or []
    if cites:
        lines.append("\nCitations:")
        for c in cites:
            lines.append("  [%s] %s" % (c.get("n"), c.get("designation") or ""))
    else:
        lines.append("\nCitations: (none returned -- treat facts as unverified)")

    if v.get("gaps"):
        lines.append("\nVerify / gaps:")
        for g in v["gaps"]:
            lines.append("  - %s" % g)
    lines.append("\n%s" % (v.get("disclaimer") or _feasibility.DISCLAIMER))
    return "\n".join(lines)


@mcp.tool()
async def listing_score(listing_id: int, strategy: str = "land-to-build") -> str:
    """Feasibility-adjusted investment score for a stored listing under one
    strategy: ``land-to-build`` (default), ``buy-hold``, ``flip``, or ``str``.

    Uses the listing's STORED enrichment (run listing_enrich first for a
    buildability-grounded verdict). land-to-build upside is gated on the
    by-right unit count from the buildability verdict, never on as-is comps
    alone -- no verdict yields ``insufficient-data``, not a guess.

    Research, not investment advice.
    """
    listing_id = int(listing_id or 0)
    strategy = (strategy or "land-to-build").strip()
    if not listing_id:
        return "ERROR: listing_id is required."

    def _work():
        conn = store.connect()
        store.init_db(conn)
        try:
            listing = store.get_listing(conn, listing_id)
            enrichment = store.get_enrichment(conn, listing_id)
        finally:
            conn.close()
        if listing is None:
            return {"_error": "no listing with id %d" % listing_id}
        try:
            res = _score.score(listing, enrichment, strategy=strategy)
        except ValueError as e:
            return {"_error": str(e)}
        return {"listing": listing, "enrichment": enrichment, "score": res}

    try:
        out = await _run_bounded(_work)
    except _FTimeout:
        return "ERROR: listing_score timed out."
    except Exception as e:  # noqa: BLE001
        return "ERROR: listing_score failed: %s" % e

    if out.get("_error"):
        return "ERROR: %s" % out["_error"]

    listing = out["listing"]
    res = out["score"]
    lines = ["Score -- %s (strategy: %s):"
             % (listing.get("address") or ("#%d" % listing_id), res.get("strategy"))]
    sc = res.get("score")
    lines.append("  Score: %s/100   Verdict: %s"
                 % (sc if sc is not None else "n/a", res.get("verdict")))
    if out.get("enrichment") is None:
        lines.append("  [!] No stored enrichment -- run listing_enrich(%d) first for a "
                     "buildability-grounded verdict; land-to-build will read as "
                     "insufficient-data without one." % listing_id)

    pf = res.get("pro_forma") or {}
    if pf:
        lines.append("  Pro forma:")
        for k, val in pf.items():
            if val is None:
                shown = "n/a"
            elif "pct" in k or "margin" in k:
                shown = _pct(val)
            elif "units" in k:
                shown = _num(val)
            else:
                shown = _money(val)
            lines.append("    %-26s %s" % (k, shown))

    fu = res.get("feasibility_used") or {}
    if fu:
        lines.append("  Feasibility used: units=%s source=%s"
                     % (fu.get("buildable_units"), fu.get("source")))
    if res.get("assumptions"):
        lines.append("  Assumptions:")
        for a in res["assumptions"]:
            lines.append("    - %s" % a)
    if res.get("gaps"):
        lines.append("  Verify / gaps:")
        for g in res["gaps"]:
            lines.append("    - %s" % g)
    lines.append("\n%s" % (res.get("disclaimer") or "Research, not investment advice."))
    return "\n".join(lines)


@mcp.tool()
async def listing_enrich(listing_id: int, strategy: str = "land-to-build") -> str:
    """Enrich a stored listing and persist it: buildability verdict (E4) +
    per-strategy scoring (E6), written to the enrichment row so later
    search/score passes read it without re-paying the API cost.

    Commute (E5) needs a routing key and destinations, so it is skipped here
    (no destinations passed) -- use the CLI ``python -m listings enrich --dest``
    for travel-time ranking. ``strategy`` selects which score to summarize;
    all four are computed and stored regardless.

    Research, not legal/engineering/investment advice.
    """
    listing_id = int(listing_id or 0)
    strategy = (strategy or "land-to-build").strip()
    if not listing_id:
        return "ERROR: listing_id is required."

    def _work():
        conn = store.connect()
        store.init_db(conn)
        try:
            return _enrich.enrich_listing(conn, listing_id)
        finally:
            conn.close()

    try:
        r = await _run_bounded(_work)
    except _FTimeout:
        return "ERROR: listing_enrich timed out (>%ds); parcel/docrag layer slow." % int(_tool_timeout())
    except ValueError as e:
        return "ERROR: %s" % e
    except Exception as e:  # noqa: BLE001
        return "ERROR: listing_enrich failed: %s" % e

    lines = ["Enriched listing #%d:" % listing_id]
    lines.append("  Governing location: %s" % (r.get("location") or "?"))
    lines.append("  Zoning: %s" % (r.get("zoning_code") or "not resolved"))
    lines.append("  Flood zone: %s%s"
                 % (r.get("flood_zone") or "not resolved",
                    " (IN FLOODWAY)" if r.get("in_floodway") else ""))
    if r.get("buildable_summary"):
        lines.append("  Buildability: %s" % r["buildable_summary"])

    scores = r.get("scores") or {}
    if scores:
        lines.append("  Scores by strategy:")
        for strat, sd in scores.items():
            sc = sd.get("score")
            marker = " <-" if strat == strategy else ""
            lines.append("    %-16s score=%-5s verdict=%s%s"
                         % (strat, sc if sc is not None else "n/a",
                            sd.get("verdict"), marker))

    cites = r.get("citations") or []
    if cites:
        lines.append("  Citations:")
        for c in cites:
            lines.append("    [%s] %s" % (c.get("n"), c.get("designation") or ""))
    if r.get("commute") is None:
        lines.append("  Commute: skipped (no destinations / routing key -- use the CLI --dest).")
    if r.get("gaps"):
        lines.append("  Verify / gaps:")
        for g in r["gaps"]:
            lines.append("    - %s" % g)
    lines.append("\nStored. Read it back with listing_score(%d, strategy=...)." % listing_id)
    lines.append(r.get("disclaimer") or
                 "Research, not legal/engineering/investment advice. Verify locally.")
    return "\n".join(lines)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
