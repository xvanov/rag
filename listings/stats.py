"""listings.stats -- market statistics/aggregations over listings.db (PRD §4 use
case 5, epic E3).

The semantic (docrag) index is the wrong tool for $/sf distributions, DOM
stats, price-drop rates, and jurisdiction rollups -- those are exactly what
the typed SQLite store (`listings/store.py`) exists for (PRD §6). This module
is read-only, stdlib-only aggregation over that store: no writes, no network,
deterministic given the same rows.

Primary entry point: ``compute_stats(conn, filters)``, scoped by the same
filter keys ``store._filter_clause`` accepts (price_min/max, beds_min,
baths_min, property_type, status, jurisdiction, bbox), so a caller can get
stats for the whole market or for any filtered slice. Every aggregate degrades
to ``None`` on an empty set instead of raising or dividing by zero.
"""

from __future__ import annotations

import statistics
from typing import Optional

from . import store


def _median(values: list[float]) -> Optional[float]:
    return statistics.median(values) if values else None


def _numeric_stats(values: list[float]) -> dict:
    """min/median/mean/max over the non-None values, or all-None if there are none."""
    vals = [v for v in values if v is not None]
    if not vals:
        return {"min": None, "median": None, "mean": None, "max": None}
    return {
        "min": min(vals),
        "median": statistics.median(vals),
        "mean": statistics.mean(vals),
        "max": max(vals),
    }


def _all_matching(conn, filters: Optional[dict]) -> list[dict]:
    """Every listing row matching ``filters`` (no pagination -- stats need the
    whole slice). Counts first so an empty slice never issues a fetch."""
    total = store.count_listings(conn, filters)
    if not total:
        return []
    return store.list_listings(conn, filters, limit=total)


def _price_drop_stats(conn, listing_ids: list[int]) -> dict:
    """Fraction of ``listing_ids`` with >=1 price DECREASE recorded in
    price_history, plus the median drop percentage across those decrease
    events (computed against each event's immediately-prior recorded price).
    None/None on an empty set of listings -- never divides by zero."""
    total = len(listing_ids)
    if not total:
        return {"rate": None, "median_drop_pct": None}

    placeholders = ", ".join("?" for _ in listing_ids)
    rows = conn.execute(
        "SELECT listing_id, price FROM price_history "
        f"WHERE listing_id IN ({placeholders}) ORDER BY listing_id, date, id",
        listing_ids,
    ).fetchall()

    by_listing: dict[int, list[float]] = {}
    for r in rows:
        by_listing.setdefault(r["listing_id"], []).append(r["price"])

    drop_pcts: list[float] = []
    listings_with_drop: set[int] = set()
    for listing_id, prices in by_listing.items():
        prev = None
        for price in prices:
            if prev is not None and price is not None and prev and price < prev:
                drop_pcts.append((prev - price) / prev * 100)
                listings_with_drop.add(listing_id)
            prev = price

    return {
        "rate": len(listings_with_drop) / total,
        "median_drop_pct": _median(drop_pcts),
    }


def compute_stats(conn, filters: Optional[dict] = None) -> dict:
    """Market statistics over the listings matching ``filters`` (or the whole
    store if ``filters`` is None/empty). Read-only; safe to call on an empty
    store (every aggregate below the top-level ``count`` degrades to None/{}).

    Returns:
        {
          "count": int,
          "by_property_type": {type: {count, price_min, price_median, price_max}},
          "price": {min, median, mean, max},
          "price_per_sqft": {min, median, max},   # rows with sqft>0 and a price only
          "dom": {min, median, mean, max},        # rows with a dom value only
          "by_jurisdiction": {jurisdiction: {count, median_price}},
          "price_drop_rate": {rate, median_drop_pct},
          "inventory": {total_active, status_breakdown: {status: count}},
        }
    """
    rows = _all_matching(conn, filters)
    total = len(rows)

    prices = [r["price"] for r in rows if r.get("price") is not None]
    price_stats = _numeric_stats(prices)

    psf_values = [
        r["price"] / r["sqft"]
        for r in rows
        if r.get("price") is not None and r.get("sqft") and r["sqft"] > 0
    ]
    psf_full = _numeric_stats(psf_values)
    price_per_sqft = {k: psf_full[k] for k in ("min", "median", "max")}

    doms = [r["dom"] for r in rows if r.get("dom") is not None]
    dom_stats = _numeric_stats(doms)

    by_property_type: dict[str, dict] = {}
    for pt in sorted({r.get("property_type") for r in rows if r.get("property_type")}):
        pt_rows = [r for r in rows if r.get("property_type") == pt]
        pt_prices = _numeric_stats([r["price"] for r in pt_rows if r.get("price") is not None])
        by_property_type[pt] = {
            "count": len(pt_rows),
            "price_min": pt_prices["min"],
            "price_median": pt_prices["median"],
            "price_max": pt_prices["max"],
        }

    by_jurisdiction: dict[str, dict] = {}
    for j in sorted({r.get("planning_jurisdiction") for r in rows if r.get("planning_jurisdiction")}):
        j_rows = [r for r in rows if r.get("planning_jurisdiction") == j]
        by_jurisdiction[j] = {
            "count": len(j_rows),
            "median_price": _median([r["price"] for r in j_rows if r.get("price") is not None]),
        }

    status_breakdown: dict[str, int] = {}
    for r in rows:
        s = r.get("status") or "unknown"
        status_breakdown[s] = status_breakdown.get(s, 0) + 1

    drop = _price_drop_stats(conn, [r["id"] for r in rows])

    return {
        "count": total,
        "by_property_type": by_property_type,
        "price": price_stats,
        "price_per_sqft": price_per_sqft,
        "dom": dom_stats,
        "by_jurisdiction": by_jurisdiction,
        "price_drop_rate": drop,
        "inventory": {
            "total_active": sum(1 for r in rows if r.get("status") == "active"),
            "status_breakdown": status_breakdown,
        },
    }


def price_distribution(conn, filters: Optional[dict] = None, buckets: int = 5) -> list[dict]:
    """Bucket the filtered listings' prices into ``buckets`` equal-width bins,
    ascending. [] if no priced listings match or ``buckets`` <= 0; a single
    bucket spanning the (degenerate) range if every matching price is equal."""
    rows = _all_matching(conn, filters)
    prices = sorted(r["price"] for r in rows if r.get("price") is not None)
    if not prices or buckets <= 0:
        return []

    lo, hi = prices[0], prices[-1]
    if lo == hi:
        return [{"min": lo, "max": hi, "count": len(prices)}]

    width = (hi - lo) / buckets
    edges = [lo + i * width for i in range(buckets + 1)]
    counts = [0] * buckets
    for p in prices:
        idx = min(int((p - lo) / width), buckets - 1)
        counts[idx] += 1
    return [{"min": edges[i], "max": edges[i + 1], "count": counts[i]} for i in range(buckets)]
