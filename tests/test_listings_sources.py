"""Regression tests for listings.sources (SampleSource + the redfin adapter).

Guards: SampleSource carries the exact remark phrases the PRD's semantic
search cares about; redfin.normalize() maps the saved fixture JSON to the
listings schema without any network; redfin.search() raises a clear,
actionable error when APIFY_TOKEN is unset (never a crash / silent no-op).

Runs with plain Python (no pytest needed):
    .venv/Scripts/python.exe tests/test_listings_sources.py
...and is also collectable by pytest if installed.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from listings.sources.sample import SampleSource  # noqa: E402
from listings.sources import redfin  # noqa: E402

_FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "redfin_sample.json")

_KEY_PHRASES = (
    "existing septic permit and well",
    "flag lot",
    "adu potential",
    "as-is",
    "seller financing",
)


@contextlib.contextmanager
def _no_apify_token():
    prev = os.environ.pop("APIFY_TOKEN", None)
    try:
        yield
    finally:
        if prev is not None:
            os.environ["APIFY_TOKEN"] = prev


def test_sample_source_returns_key_remark_phrases():
    listings = SampleSource().search("durham", {})
    assert len(listings) >= 3  # a mix of land / SFH / 2-4 unit

    all_text = " ".join(l.get("description_text", "") for l in listings).lower()
    for phrase in _KEY_PHRASES:
        assert phrase in all_text, "missing key remark phrase: %r" % phrase

    property_types = {l["property_type"] for l in listings}
    assert "land" in property_types
    assert "single_family" in property_types
    assert "multi_family_2_4" in property_types


def test_redfin_normalize_maps_fixture_to_schema():
    with open(_FIXTURE, "r", encoding="utf-8") as f:
        raw = json.load(f)[0]

    listing = redfin.normalize(raw)

    assert listing["source"] == "redfin"
    assert listing["source_id"] == "999999999"
    assert listing["mls_number"] == "TMLS2612345"
    assert listing["status"] == "Active"
    assert listing["address"] == "0 Guess Rd, Durham, NC 27712"
    assert listing["lat"] == 36.0743
    assert listing["lon"] == -78.9203
    assert listing["price"] == 89000
    assert listing["lot_sqft"] == 43560
    assert listing["lot_acres"] == 1.0
    assert listing["property_type"] == "Land"
    assert "existing septic permit and well" in listing["description_text"].lower()
    assert "flag lot" in listing["description_text"].lower()
    assert "adu potential" in listing["description_text"].lower()
    assert listing["list_agent_name"] == "Dana Whitfield"
    assert listing["brokerage"] == "Bull City Land Co."
    assert json.loads(listing["raw_json"]) == raw


def test_redfin_normalize_land_with_null_nested_values():
    """A land listing with sqFt/lotSize/price all `{"value": null}` must
    normalize to scalar None (never the wrapper dict) and be sqlite-bindable."""
    with open(_FIXTURE, "r", encoding="utf-8") as f:
        raw = json.load(f)[1]

    listing = redfin.normalize(raw)

    assert listing["source_id"] == "888888888"
    assert listing["sqft"] is None
    assert listing["lot_sqft"] is None
    assert listing["lot_acres"] is None   # would crash if lot_sqft were a dict
    assert listing["price"] is None
    for v in listing.values():
        assert not isinstance(v, dict), "normalize leaked a wrapper dict: %r" % v

    # ...and it actually binds + upserts into the typed store without error.
    import tempfile
    from listings import store
    d = tempfile.mkdtemp()
    prev = os.environ.get("LISTINGS_ROOT")
    os.environ["LISTINGS_ROOT"] = d
    try:
        conn = store.connect()
        store.init_db(conn)
        lid = store.upsert_listing(conn, listing)
        assert store.get_listing(conn, lid)["source_id"] == "888888888"
        conn.close()
    finally:
        if prev is not None:
            os.environ["LISTINGS_ROOT"] = prev
        else:
            os.environ.pop("LISTINGS_ROOT", None)


def test_redfin_normalize_raises_without_id():
    try:
        redfin.normalize({"listingRemarks": "no id here"})
        raise AssertionError("expected ValueError for id-less record")
    except ValueError as e:
        assert "no listingId" in str(e)


def test_redfin_search_raises_clear_error_without_token():
    with _no_apify_token():
        try:
            redfin.search("Durham, NC", {})
            raise AssertionError("expected RuntimeError for missing APIFY_TOKEN")
        except RuntimeError as e:
            assert "APIFY_TOKEN" in str(e)


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
