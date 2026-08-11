"""Regression tests for listings.stats (market statistics/aggregations, E3).

Guards: compute_stats over a known seeded mix returns correct count,
by_property_type/by_jurisdiction rollups, price_per_sqft excludes
null/zero-sqft rows (land), dom stats only cover rows with a dom value,
price_drop_rate reflects seeded price DECREASES (and ignores increases),
filters scope the whole result, and an empty store degrades every
aggregate to None/{} instead of crashing.

Runs with plain Python (no pytest needed):
    .venv/Scripts/python.exe tests/test_listings_stats.py
...and is also collectable by pytest if installed.
"""

from __future__ import annotations

import contextlib
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from listings import stats, store  # noqa: E402


@contextlib.contextmanager
def _tmp_db():
    """Isolated LISTINGS_ROOT (and therefore listings.db) per test."""
    prev = os.environ.get("LISTINGS_ROOT")
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["LISTINGS_ROOT"] = tmp
        conn = store.connect()
        try:
            store.init_db(conn)
            yield conn
        finally:
            # Close in finally so a failed body assertion doesn't leave the
            # sqlite handle open (Windows: masking PermissionError on cleanup).
            conn.close()
            if prev is None:
                os.environ.pop("LISTINGS_ROOT", None)
            else:
                os.environ["LISTINGS_ROOT"] = prev


def _approx(a, b, tol=1e-6) -> bool:
    return a is not None and b is not None and abs(a - b) < tol


_BASE = {
    "source": "sample",
    "source_id": "STAT-0000",
    "mls_number": None,
    "status": "active",
    "address": "1 Test St",
    "lat": 36.0,
    "lon": -78.9,
    "parcel_pin": None,
    "planning_jurisdiction": "durham-nc",
    "price": 100000,
    "list_date": "2026-06-01",
    "dom": 10,
    "beds": None,
    "baths": None,
    "sqft": None,
    "lot_sqft": None,
    "lot_acres": None,
    "property_type": "land",
    "year_built": None,
    "description_text": "",
    "list_agent_name": None,
    "list_agent_id": None,
    "brokerage": None,
    "photos_json": [],
    "raw_json": "{}",
}


def _listing(**overrides) -> dict:
    d = dict(_BASE)
    d.update(overrides)
    return d


def _seed(conn) -> None:
    """Known mix: two land parcels (null sqft), two single_family, one
    multi_family_2_4; varied jurisdictions/status; one seeded price DROP
    (L1) and one seeded price INCREASE (L3, must NOT count as a drop)."""
    # L1: land, durham-nc, active -- price drops 100000 -> 90000 (10% drop).
    conn_listing = _listing(source_id="STAT-0001", price=100000, dom=12,
                             property_type="land", planning_jurisdiction="durham-nc",
                             status="active")
    store.upsert_listing(conn, conn_listing)
    store.upsert_listing(conn, _listing(source_id="STAT-0001", price=90000, dom=12,
                                         property_type="land", planning_jurisdiction="durham-nc",
                                         status="active"))

    # L2: single_family, durham-nc, active, sqft 1420, no price change.
    store.upsert_listing(conn, _listing(source_id="STAT-0002", price=245000, dom=24,
                                         sqft=1420, property_type="single_family",
                                         planning_jurisdiction="durham-nc", status="active"))

    # L3: single_family, raleigh-nc, pending -- price INCREASES 400000 -> 415000.
    store.upsert_listing(conn, _listing(source_id="STAT-0003", price=400000, dom=9,
                                         sqft=2380, property_type="single_family",
                                         planning_jurisdiction="raleigh-nc", status="pending"))
    store.upsert_listing(conn, _listing(source_id="STAT-0003", price=415000, dom=9,
                                         sqft=2380, property_type="single_family",
                                         planning_jurisdiction="raleigh-nc", status="pending"))

    # L4: multi_family_2_4, cary-nc, active, sqft 1800, no price change.
    store.upsert_listing(conn, _listing(source_id="STAT-0004", price=389000, dom=18,
                                         sqft=1800, property_type="multi_family_2_4",
                                         planning_jurisdiction="cary-nc", status="active"))

    # L5: land, alamance-county-nc, active, null sqft, no price change.
    store.upsert_listing(conn, _listing(source_id="STAT-0005", price=62000, dom=2,
                                         property_type="land",
                                         planning_jurisdiction="alamance-county-nc",
                                         status="active"))


def test_compute_stats_count_and_by_property_type():
    with _tmp_db() as conn:
        _seed(conn)
        s = stats.compute_stats(conn)
        assert s["count"] == 5

        bpt = s["by_property_type"]
        assert set(bpt.keys()) == {"land", "single_family", "multi_family_2_4"}
        assert bpt["land"]["count"] == 2
        assert bpt["land"]["price_min"] == 62000
        assert bpt["land"]["price_max"] == 90000
        assert _approx(bpt["land"]["price_median"], (62000 + 90000) / 2)
        assert bpt["single_family"]["count"] == 2
        assert _approx(bpt["single_family"]["price_median"], (245000 + 415000) / 2)
        assert bpt["multi_family_2_4"]["count"] == 1
        assert bpt["multi_family_2_4"]["price_median"] == 389000


def test_compute_stats_price_and_dom():
    with _tmp_db() as conn:
        _seed(conn)
        s = stats.compute_stats(conn)

        prices = [90000, 245000, 415000, 389000, 62000]
        assert s["price"]["min"] == min(prices)
        assert s["price"]["max"] == max(prices)
        assert _approx(s["price"]["median"], sorted(prices)[2])
        assert _approx(s["price"]["mean"], sum(prices) / len(prices))

        doms = [12, 24, 9, 18, 2]
        assert s["dom"]["min"] == min(doms)
        assert s["dom"]["max"] == max(doms)
        assert _approx(s["dom"]["mean"], sum(doms) / len(doms))
        assert _approx(s["dom"]["median"], sorted(doms)[2])


def test_price_per_sqft_excludes_null_sqft_land_rows():
    with _tmp_db() as conn:
        _seed(conn)
        s = stats.compute_stats(conn)
        # Only L2 (245000/1420), L3 (415000/2380), L4 (389000/1800) have sqft.
        expected = sorted([245000 / 1420, 415000 / 2380, 389000 / 1800])
        assert _approx(s["price_per_sqft"]["min"], expected[0])
        assert _approx(s["price_per_sqft"]["max"], expected[-1])
        assert _approx(s["price_per_sqft"]["median"], expected[1])


def test_by_jurisdiction():
    with _tmp_db() as conn:
        _seed(conn)
        s = stats.compute_stats(conn)
        bj = s["by_jurisdiction"]
        assert bj["durham-nc"]["count"] == 2
        assert _approx(bj["durham-nc"]["median_price"], (90000 + 245000) / 2)
        assert bj["raleigh-nc"]["count"] == 1
        assert bj["raleigh-nc"]["median_price"] == 415000
        assert bj["cary-nc"]["count"] == 1
        assert bj["alamance-county-nc"]["count"] == 1


def test_price_drop_rate_counts_decreases_only():
    with _tmp_db() as conn:
        _seed(conn)
        s = stats.compute_stats(conn)
        drop = s["price_drop_rate"]
        # Only L1 (STAT-0001) has a recorded decrease; L3 increased, others unchanged.
        assert _approx(drop["rate"], 1 / 5)
        assert _approx(drop["median_drop_pct"], 10.0)


def test_inventory_status_breakdown():
    with _tmp_db() as conn:
        _seed(conn)
        s = stats.compute_stats(conn)
        inv = s["inventory"]
        assert inv["total_active"] == 4  # all but L3 (pending)
        assert inv["status_breakdown"] == {"active": 4, "pending": 1}


def test_filters_scope_stats():
    with _tmp_db() as conn:
        _seed(conn)
        land_only = stats.compute_stats(conn, {"property_type": "land"})
        assert land_only["count"] == 2
        assert set(land_only["by_property_type"].keys()) == {"land"}

        durham_only = stats.compute_stats(conn, {"jurisdiction": "durham-nc"})
        assert durham_only["count"] == 2


def test_empty_store_returns_well_formed_none_aggregates():
    with _tmp_db() as conn:
        s = stats.compute_stats(conn)
        assert s["count"] == 0
        assert s["by_property_type"] == {}
        assert s["by_jurisdiction"] == {}
        assert s["price"] == {"min": None, "median": None, "mean": None, "max": None}
        assert s["price_per_sqft"] == {"min": None, "median": None, "max": None}
        assert s["dom"] == {"min": None, "median": None, "mean": None, "max": None}
        assert s["price_drop_rate"] == {"rate": None, "median_drop_pct": None}
        assert s["inventory"] == {"total_active": 0, "status_breakdown": {}}


def test_price_distribution_buckets():
    with _tmp_db() as conn:
        _seed(conn)
        buckets = stats.price_distribution(conn, buckets=3)
        assert len(buckets) == 3
        assert sum(b["count"] for b in buckets) == 5
        assert buckets[0]["min"] == 62000  # lowest seeded price
        assert buckets[-1]["max"] == 415000  # highest seeded price

        assert stats.price_distribution(conn, {"property_type": "nope"}) == []


def _main() -> int:
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print("PASS", t.__name__)
        except AssertionError as e:
            failed += 1
            print("FAIL", t.__name__, "--", e or "assertion failed")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print("ERROR", t.__name__, "--", repr(e))
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_main())
