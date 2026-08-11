"""Regression tests for listings.store (the typed listings.db analytics layer).

Guards: schema creation is idempotent; upsert_listing dedups by
(source, source_id) and is idempotent on re-ingest; a price change appends a
price_history row (no change does not); hard filters narrow list_listings
correctly; thesis-hit dedup never double-records the same (thesis, listing)
pair.

Runs with plain Python (no pytest needed):
    .venv/Scripts/python.exe tests/test_listings_store.py
...and is also collectable by pytest if installed.
"""

from __future__ import annotations

import contextlib
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from listings import store  # noqa: E402


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
            # sqlite handle open -- on Windows that turns TemporaryDirectory
            # cleanup into a masking PermissionError [WinError 32].
            conn.close()
            if prev is None:
                os.environ.pop("LISTINGS_ROOT", None)
            else:
                os.environ["LISTINGS_ROOT"] = prev


_LISTING = {
    "source": "sample",
    "source_id": "SAMPLE-0001",
    "mls_number": "TMLS0000001",
    "status": "active",
    "address": "0 Guess Rd, Durham, NC 27712",
    "lat": 36.0743,
    "lon": -78.9203,
    "parcel_pin": "0812345678",
    "planning_jurisdiction": "durham-nc",
    "price": 89000,
    "list_date": "2026-06-01",
    "dom": 12,
    "beds": None,
    "baths": None,
    "sqft": None,
    "lot_sqft": 43560,
    "lot_acres": 1.0,
    "property_type": "land",
    "year_built": None,
    "description_text": "Existing septic permit and well. Flag lot. ADU potential.",
    "list_agent_name": "Dana Whitfield",
    "list_agent_id": "AGT-1001",
    "brokerage": "Bull City Land Co.",
    "photos_json": [],
    "raw_json": "{}",
}


def test_init_db_creates_all_tables():
    with _tmp_db() as conn:
        tables = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        for expected in ("listings", "price_history", "enrichment", "agents",
                         "theses", "thesis_hits"):
            assert expected in tables


def test_init_db_is_idempotent():
    with _tmp_db() as conn:
        store.init_db(conn)  # second call must not raise / must not duplicate schema
        store.init_db(conn)


def test_upsert_listing_inserts_then_updates_by_source_and_source_id():
    with _tmp_db() as conn:
        id1 = store.upsert_listing(conn, dict(_LISTING))
        assert store.count_listings(conn) == 1

        updated = dict(_LISTING)
        updated["status"] = "pending"
        id2 = store.upsert_listing(conn, updated)

        assert id1 == id2                       # same natural key -> same row
        assert store.count_listings(conn) == 1  # no duplicate row
        row = store.get_listing(conn, id1)
        assert row["status"] == "pending"


def test_upsert_listing_price_change_appends_price_history():
    with _tmp_db() as conn:
        listing_id = store.upsert_listing(conn, dict(_LISTING))
        assert len(store.price_history(conn, listing_id)) == 1  # initial "listed" event

        same_price = dict(_LISTING)
        store.upsert_listing(conn, same_price)
        assert len(store.price_history(conn, listing_id)) == 1  # no change -> no new row

        dropped = dict(_LISTING)
        dropped["price"] = 79000
        store.upsert_listing(conn, dropped)
        hist = store.price_history(conn, listing_id)
        assert len(hist) == 2
        assert hist[-1]["price"] == 79000
        assert hist[-1]["event"] == "price_change"


def test_hard_filters_narrow_results():
    with _tmp_db() as conn:
        store.upsert_listing(conn, dict(_LISTING))  # land, $89k, durham-nc

        sfh = dict(_LISTING)
        sfh.update(source_id="SAMPLE-0002", property_type="single_family",
                   price=245000, beds=3, baths=2, planning_jurisdiction="raleigh-nc")
        store.upsert_listing(conn, sfh)

        assert store.count_listings(conn) == 2
        assert store.count_listings(conn, {"property_type": "land"}) == 1
        assert store.count_listings(conn, {"price_max": 100000}) == 1
        assert store.count_listings(conn, {"price_min": 100000}) == 1
        assert store.count_listings(conn, {"beds_min": 3}) == 1
        assert store.count_listings(conn, {"jurisdiction": "raleigh-nc"}) == 1
        assert store.count_listings(conn, {"jurisdiction": "cary-nc"}) == 0

        land_rows = store.list_listings(conn, {"property_type": "land"})
        assert len(land_rows) == 1
        assert land_rows[0]["source_id"] == "SAMPLE-0001"


def test_thesis_hit_dedup():
    with _tmp_db() as conn:
        listing_id = store.upsert_listing(conn, dict(_LISTING))
        thesis_id = store.save_thesis(conn, "cheap land near durham", {"price_max": 150000})

        assert store.thesis_seen(conn, thesis_id, listing_id) is False
        first = store.record_thesis_hit(conn, thesis_id, listing_id, rationale="matches thesis")
        assert first is True
        assert store.thesis_seen(conn, thesis_id, listing_id) is True

        second = store.record_thesis_hit(conn, thesis_id, listing_id, rationale="re-run, same hit")
        assert second is False  # dedup: no duplicate row
        assert len(store.list_thesis_hits(conn, thesis_id)) == 1


def test_bbox_filter():
    with _tmp_db() as conn:
        store.upsert_listing(conn, dict(_LISTING))  # lat 36.0743, lon -78.9203 (Durham)

        far = dict(_LISTING)
        far.update(source_id="SAMPLE-FAR", lat=35.7796, lon=-78.6382)  # Raleigh
        store.upsert_listing(conn, far)

        # bbox = (min_lat, min_lon, max_lat, max_lon); tight around Durham point only
        durham_box = {"bbox": (36.0, -79.0, 36.1, -78.8)}
        rows = store.list_listings(conn, durham_box)
        assert len(rows) == 1
        assert rows[0]["source_id"] == "SAMPLE-0001"

        # widen to include both
        wide = {"bbox": (35.0, -79.5, 37.0, -78.0)}
        assert store.count_listings(conn, wide) == 2


def test_upsert_agent_inserts_then_updates():
    with _tmp_db() as conn:
        store.upsert_agent(conn, {"agent_id": "AGT-1001", "name": "Dana Whitfield",
                                  "brokerage": "Bull City Land Co.", "listing_count": 3})
        a = store.get_agent(conn, "AGT-1001")
        assert a["name"] == "Dana Whitfield"
        assert a["listing_count"] == 3

        store.upsert_agent(conn, {"agent_id": "AGT-1001", "listing_count": 5})
        a2 = store.get_agent(conn, "AGT-1001")
        assert a2["listing_count"] == 5
        assert a2["name"] == "Dana Whitfield"  # preserved
        assert store.get_agent(conn, "NOPE") is None


def test_enrichment_upsert_and_get():
    with _tmp_db() as conn:
        listing_id = store.upsert_listing(conn, dict(_LISTING))
        assert store.get_enrichment(conn, listing_id) is None

        store.upsert_enrichment(conn, listing_id, {"zoning_code": "RS-8", "flood_zone": "X"})
        enr = store.get_enrichment(conn, listing_id)
        assert enr["zoning_code"] == "RS-8"
        assert enr["flood_zone"] == "X"

        store.upsert_enrichment(conn, listing_id, {"flood_zone": "AE"})
        enr2 = store.get_enrichment(conn, listing_id)
        assert enr2["zoning_code"] == "RS-8"  # untouched field preserved
        assert enr2["flood_zone"] == "AE"


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
