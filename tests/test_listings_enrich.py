"""Regression tests for listings.enrich (E4/E5/E6 orchestration glue).

Guards: the buildability prose -> numeric unit-count bridge (_units_from_text)
only fires on confident matches and never invents a density; enrich_listing
wires feasibility -> scoring so a cited by-right density flows into a real
land-to-build score; a missing/negative verdict yields insufficient-data (never
a silent max-density assumption); commute runs only when destinations are given;
and the enrichment row is persisted. All offline -- feasibility.assess and
commute.rank are monkeypatched, no network / no Azure / no API keys.

Dual harness: pytest-collectible and `python tests/test_listings_enrich.py`.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from listings import enrich as enrich_mod  # noqa: E402
from listings import feasibility as feasibility_mod  # noqa: E402
from listings import commute as commute_mod  # noqa: E402
from listings import store  # noqa: E402
from listings.sources.sample import SampleSource  # noqa: E402


@contextlib.contextmanager
def _tmp_db():
    prev = os.environ.get("LISTINGS_ROOT")
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["LISTINGS_ROOT"] = tmp
        conn = store.connect()
        try:
            store.init_db(conn)
            yield conn
        finally:
            conn.close()
            if prev is None:
                os.environ.pop("LISTINGS_ROOT", None)
            else:
                os.environ["LISTINGS_ROOT"] = prev


def _seed_one(conn) -> int:
    listing = SampleSource().search("durham", {})[0]  # the Guess Rd land lot
    return store.upsert_listing(conn, listing)


# ---- _units_from_text (the prose -> number bridge) --------------------------

def test_units_from_text_extracts_confident_counts():
    assert enrich_mod._units_from_text("up to 2 dwelling units by right") == 2
    assert enrich_mod._units_from_text("a maximum of 3 units permitted") == 3
    assert enrich_mod._units_from_text("single-family residential district") == 1
    # nothing confidently extractable -> None (caller then leaves it absent)
    assert enrich_mod._units_from_text("residential uses are permitted") is None
    assert enrich_mod._units_from_text(None, "") is None
    # absurd numbers rejected by the sanity bound
    assert enrich_mod._units_from_text("established in 1998 zoning") is None


_VERDICT_2U = {
    "location": "durham-nc", "jurisdiction_source": "parcel",
    "zoning_code": "RS-8", "flood_zone": "X", "in_floodway": False, "watershed": None,
    "allowed_uses": "Single-family and duplex.",
    "by_right_density": "Up to 2 dwelling units by right on this lot.",
    "setbacks": "10 ft", "height": "35 ft", "parking": "2 spaces",
    "citations": [{"n": 1, "designation": "Durham UDO Sec. 5.4"}],
    "buildable_summary": "Buildable for up to 2 units by right.",
    "gaps": [], "disclaimer": "Research, not legal advice.",
}


def test_enrich_flows_cited_density_into_land_score(monkeypatch=None):
    with _tmp_db() as conn:
        lid = _seed_one(conn)

        orig_assess = feasibility_mod.assess
        feasibility_mod.assess = lambda listing, location=None: dict(_VERDICT_2U)
        try:
            result = enrich_mod.enrich_listing(conn, lid, strategies=("land-to-build",))
        finally:
            feasibility_mod.assess = orig_assess

        assert result["zoning_code"] == "RS-8"
        assert result["citations"]  # cited
        land = result["scores"]["land-to-build"]
        # a real by-right count reached scoring -> not insufficient-data
        assert land["verdict"] != "insufficient-data"
        assert land["feasibility_used"]["buildable_units"] == 2

        # persisted: enrichment row written with the buildability + score JSON
        enr = store.get_enrichment(conn, lid)
        assert enr is not None
        assert "Durham UDO Sec. 5.4" in (enr["buildability_cited"] or "")  # PRD §7 citation string
        b = json.loads(enr["buildability_json"])
        assert b["buildable_units"] == 2
        assert b["buildable_units_source"] == "parsed-from-udo-text"
        assert json.loads(enr["score_json"])["land-to-build"]["strategy"] == "land-to-build"


def test_enrich_prefers_grounded_by_right_units_over_text_parse(monkeypatch=None):
    """When feasibility supplies its own grounded by_right_units, use it and tag
    the source 'feasibility-grounded' -- do not regex the prose."""
    with _tmp_db() as conn:
        lid = _seed_one(conn)
        verdict = dict(_VERDICT_2U)
        verdict["by_right_units"] = 4                       # grounded value...
        verdict["by_right_density"] = "up to 2 units by right"  # ...prose says 2; grounded wins
        orig = feasibility_mod.assess
        feasibility_mod.assess = lambda listing, location=None: dict(verdict)
        try:
            result = enrich_mod.enrich_listing(conn, lid, strategies=("land-to-build",))
        finally:
            feasibility_mod.assess = orig
        b = json.loads(store.get_enrichment(conn, lid)["buildability_json"])
        assert b["buildable_units"] == 4
        assert b["buildable_units_source"] == "feasibility-grounded"


def test_enrich_does_not_override_grounded_none(monkeypatch=None):
    """A grounded by_right_units=None (docrag couldn't determine) must NOT be
    replaced by a regex guess from the prose -- that would fake density."""
    with _tmp_db() as conn:
        lid = _seed_one(conn)
        verdict = dict(_VERDICT_2U)
        verdict["by_right_units"] = None
        verdict["by_right_density"] = "up to 2 dwelling units by right"  # tempting to parse
        orig = feasibility_mod.assess
        feasibility_mod.assess = lambda listing, location=None: dict(verdict)
        try:
            result = enrich_mod.enrich_listing(conn, lid, strategies=("land-to-build",))
        finally:
            feasibility_mod.assess = orig
        b = json.loads(store.get_enrichment(conn, lid)["buildability_json"])
        assert "buildable_units" not in b   # not faked from prose
        assert result["scores"]["land-to-build"]["verdict"] == "insufficient-data"


def test_enrich_no_parcel_is_insufficient_not_assumed(monkeypatch=None):
    """A verdict with no zoning/density must NOT produce a full-density score."""
    with _tmp_db() as conn:
        lid = _seed_one(conn)
        empty = {"location": "durham-nc", "zoning_code": None, "flood_zone": None,
                 "in_floodway": False, "watershed": None, "allowed_uses": None,
                 "by_right_density": None, "citations": [],
                 "buildable_summary": "undetermined", "gaps": ["no parcel match"]}
        orig = feasibility_mod.assess
        feasibility_mod.assess = lambda listing, location=None: dict(empty)
        try:
            result = enrich_mod.enrich_listing(conn, lid, strategies=("land-to-build",))
        finally:
            feasibility_mod.assess = orig

        land = result["scores"]["land-to-build"]
        assert land["verdict"] == "insufficient-data"
        assert any("feasibility" in g for g in result["gaps"])


def test_enrich_survives_feasibility_exception(monkeypatch=None):
    """If feasibility.assess itself raises, enrich records a gap and still
    returns/persists rather than crashing the pipeline."""
    with _tmp_db() as conn:
        lid = _seed_one(conn)
        orig = feasibility_mod.assess

        def boom(listing, location=None):
            raise RuntimeError("arcgis down")

        feasibility_mod.assess = boom
        try:
            result = enrich_mod.enrich_listing(conn, lid, strategies=("land-to-build",))
        finally:
            feasibility_mod.assess = orig

        assert any("feasibility" in g for g in result["gaps"])
        assert result["scores"]["land-to-build"]["verdict"] == "insufficient-data"
        assert store.get_enrichment(conn, lid) is not None  # still persisted


def test_enrich_runs_commute_only_with_destinations(monkeypatch=None):
    with _tmp_db() as conn:
        lid = _seed_one(conn)
        orig_assess = feasibility_mod.assess
        feasibility_mod.assess = lambda listing, location=None: dict(_VERDICT_2U)

        called = {"n": 0}

        def fake_rank(origin, destinations, scenario="typical", provider=None):
            called["n"] += 1
            return {"origin": list(origin), "scenario": scenario, "provider": None,
                    "per_destination": [{"name": d["name"], "minutes": None,
                                         "status": "no_provider"} for d in destinations],
                    "combined_minutes": None, "max_minutes": None,
                    "reachable_all_within": None, "gaps": ["no commute provider configured"],
                    "cached": False}

        orig_rank = commute_mod.rank
        commute_mod.rank = fake_rank
        try:
            no_dest = enrich_mod.enrich_listing(conn, lid, strategies=("land-to-build",))
            assert no_dest["commute"] is None
            assert called["n"] == 0

            with_dest = enrich_mod.enrich_listing(
                conn, lid, destinations=[{"name": "RDU", "lat": 35.88, "lon": -78.79}],
                strategies=("land-to-build",))
            assert called["n"] == 1
            assert with_dest["commute"]["provider"] is None
            assert store.get_enrichment(conn, lid)["commute_json"]  # persisted
        finally:
            feasibility_mod.assess = orig_assess
            commute_mod.rank = orig_rank


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
