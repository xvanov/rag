"""Regression tests for listings.score (E6 feasibility-adjusted scoring).

Guards: land-to-build's residual land value / max-bid arithmetic, the
feasibility gate (a missing buildability verdict must NOT silently assume
max density; a verdict saying "not buildable" must not produce a
full-density score; a price over max bid must verdict "weak"), that a
missing market dict falls back to documented defaults and flags them, that
buy-hold/flip/str all return well-formed dicts without crashing, and the FHA
firewall: score.py must never import listings.agents or reference the
listing-agent demographic-inference field.

Runs with plain Python (no pytest needed):
    .venv/Scripts/python.exe tests/test_listings_score.py
...and is also collectable by pytest if installed.
"""

from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from listings import score as scoremod  # noqa: E402

_SCORE_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "listings", "score.py")


def _approx(a, b, tol=1e-6) -> bool:
    return a is not None and b is not None and abs(a - b) < tol


def _listing(**overrides) -> dict:
    base = {
        "id": 1, "source": "sample", "source_id": "LOT-1", "status": "active",
        "address": "1 Test Ln", "price": 50000, "beds": None, "baths": None,
        "sqft": None, "lot_sqft": 21780, "lot_acres": 0.5, "property_type": "land",
        "planning_jurisdiction": "durham-nc",
    }
    base.update(overrides)
    return base


def _enrichment(buildability: dict | None, cited: str | None = None) -> dict:
    return {
        "buildability_json": json.dumps(buildability) if buildability is not None else None,
        "buildability_cited": cited,
    }


# feasibility.py's settled verdict shape (confirmed with its author) is
# mostly free-text docrag prose per zoning topic + a citations list of
# {"n","designation"} dicts -- NOT a "buildable" bool or "buildable_units"
# int. The one numeric field this scorer needs, ``by_right_units``, was
# requested as an addition and isn't in feasibility.py's output as of this
# writing -- these fixtures include it (as it's expected to land), and
# ``_REAL_WORLD_VERDICT_NO_UNITS`` below exercises today's actual shape
# (no ``by_right_units`` key at all) to prove that case degrades correctly.
_BUILDABLE = {
    "location": "durham-nc", "jurisdiction_source": "parcel",
    "zoning_code": "R-4", "flood_zone": None, "in_floodway": False, "watershed": None,
    "allowed_uses": "Single-family detached and duplex uses are permitted in R-4.",
    "by_right_density": "R-4 permits up to 3 dwelling units by right on a lot this size.",
    "setbacks": "Front 20 ft, side 10 ft, rear 25 ft.", "height": "40 ft max.",
    "parking": "2 spaces per unit.",
    "citations": [{"n": 1, "designation": "Durham UDO 3.2.3 -- R-4 density"}],
    "buildable_summary": "Site is zoned R-4 and can support up to 3 by-right dwelling units.",
    "by_right_units": 3,
    "gaps": ["exact subdivision cost not verified with Durham Engineering"],
    "disclaimer": "Research, not legal advice.",
}
_NOT_BUILDABLE = {
    "location": "durham-nc", "jurisdiction_source": "parcel",
    "zoning_code": "RR", "flood_zone": None, "in_floodway": False, "watershed": None,
    "allowed_uses": "Agricultural and single-family only; no subdivision without rezoning.",
    "by_right_density": "RR does not permit additional dwelling units on this lot by right.",
    "setbacks": "N/A", "height": "N/A", "parking": "N/A",
    "citations": [{"n": 1, "designation": "Durham UDO 3.2.1 -- RR district"}],
    "buildable_summary": "Not zoned for additional residential density; 0 by-right units.",
    "by_right_units": 0,
    "gaps": [],
    "disclaimer": "Research, not legal advice.",
}
# Today's ACTUAL feasibility.py output shape: no by_right_units key at all.
_REAL_WORLD_VERDICT_NO_UNITS = {k: v for k, v in _BUILDABLE.items() if k != "by_right_units"}
_MARKET = {"price": {"median": 450_000}}


def test_land_to_build_residual_land_value_and_margin():
    listing = _listing(price=50_000)
    enrichment = _enrichment(_BUILDABLE)
    result = scoremod.score(listing, enrichment, "land-to-build", market=_MARKET)

    pf = result["pro_forma"]
    assert pf["buildable_units"] == 3
    assert _approx(pf["build_cost"], 165.0 * 1800.0 * 3)          # 891000
    assert _approx(pf["soft_costs"], 0.15 * 891000)                # 133650
    assert _approx(pf["cost_to_unlock"], 15000.0 * 3)              # 45000
    assert _approx(pf["gross_realizable"], 450_000 * 3)            # 1350000
    assert _approx(pf["required_profit"], 0.15 * 1_350_000)        # 202500
    assert _approx(pf["max_bid"], 77_850.0)
    assert _approx(pf["residual_land_value"], pf["max_bid"])
    assert _approx(pf["profit"], 1_350_000 - 50_000 - 891_000 - 133_650 - 45_000)
    assert _approx(pf["margin_pct"], pf["profit"] / pf["gross_realizable"] * 100)

    assert result["feasibility_used"] == {
        "buildable_units": 3, "by_right": None, "source": "buildability verdict",
    }
    assert result["strategy"] == "land-to-build"
    assert result["disclaimer"] == "Research, not investment advice."
    # Purchase price ($50k) is comfortably under max bid ($77.85k) -> not weak.
    assert result["verdict"] in ("strong", "marginal")
    assert isinstance(result["score"], int) and 0 <= result["score"] <= 100


def test_land_to_build_verdicts_weak_when_price_exceeds_max_bid():
    listing = _listing(price=120_000)   # max bid is 77850 in this scenario
    enrichment = _enrichment(_BUILDABLE)
    result = scoremod.score(listing, enrichment, "land-to-build", market=_MARKET)

    assert result["pro_forma"]["max_bid"] < 120_000
    assert result["verdict"] == "weak"


def test_land_to_build_not_buildable_is_not_a_full_density_score():
    listing = _listing(price=50_000)
    enrichment = _enrichment(_NOT_BUILDABLE)
    result = scoremod.score(listing, enrichment, "land-to-build", market=_MARKET)

    assert result["verdict"] in ("insufficient-data", "weak")
    assert result["score"] != 100
    assert result["pro_forma"]["buildable_units"] == 0
    assert any("buildab" in g.lower() for g in result["gaps"])


def test_land_to_build_real_world_verdict_without_unit_count_is_insufficient_data():
    """Exercises TODAY's actual feasibility.py shape (prose fields + citations,
    no ``by_right_units`` yet) -- must not guess a unit count from the prose,
    and feasibility.py's own reported gaps must be folded into the result."""
    listing = _listing(price=50_000)
    enrichment = _enrichment(_REAL_WORLD_VERDICT_NO_UNITS)
    result = scoremod.score(listing, enrichment, "land-to-build", market=_MARKET)

    assert result["verdict"] == "insufficient-data"
    assert result["score"] is None
    assert result["pro_forma"]["buildable_units"] is None
    assert any("unit" in g.lower() for g in result["gaps"])
    assert any(g.startswith("feasibility: ") for g in result["gaps"])


def test_land_to_build_missing_buildability_verdict_is_insufficient_data():
    listing = _listing(price=50_000)
    result = scoremod.score(listing, enrichment=None, strategy="land-to-build", market=_MARKET)

    assert result["verdict"] == "insufficient-data"
    assert result["score"] is None
    assert result["pro_forma"]["buildable_units"] is None
    assert any("buildab" in g.lower() for g in result["gaps"])
    # Must NOT silently assume max density: no unit count anywhere in the result.
    assert "buildable_units" not in result["pro_forma"] or result["pro_forma"]["buildable_units"] is None


def test_land_to_build_missing_market_flags_defaults_in_assumptions():
    listing = _listing(price=50_000)
    enrichment = _enrichment(_BUILDABLE)
    result = scoremod.score(listing, enrichment, "land-to-build", market=None)

    assert result["assumptions"], "assumptions list must be populated when market is absent"
    joined = " ".join(result["assumptions"])
    assert "home_sale_price_per_unit" in joined
    assert "build_cost_per_sqft" in joined
    assert any("no market comps" in g for g in result["gaps"])


def test_buy_hold_returns_well_formed_dict():
    listing = _listing(property_type="single_family", price=300_000, sqft=1500)
    result = scoremod.score(listing, enrichment=None, strategy="buy-hold", market=None)

    assert result["strategy"] == "buy-hold"
    assert set(("score", "verdict", "pro_forma", "feasibility_used",
                "assumptions", "gaps", "disclaimer")) <= set(result.keys())
    assert result["verdict"] in ("strong", "marginal", "weak", "insufficient-data")
    assert result["pro_forma"]["monthly_rent"] is not None
    assert result["assumptions"]  # no market rent -> defaults flagged


def test_flip_returns_well_formed_dict():
    listing = _listing(property_type="single_family", price=200_000, sqft=1400)
    result = scoremod.score(listing, enrichment=None, strategy="flip", market=None)

    assert result["strategy"] == "flip"
    assert result["verdict"] in ("strong", "marginal", "weak", "insufficient-data")
    assert result["pro_forma"]["arv"] is not None
    assert result["pro_forma"]["rehab_cost"] is not None


def test_str_returns_well_formed_dict():
    listing = _listing(property_type="single_family", price=350_000, sqft=1600)
    result = scoremod.score(listing, enrichment=None, strategy="str", market=None)

    assert result["strategy"] == "str"
    assert result["verdict"] in ("strong", "marginal", "weak", "insufficient-data")
    assert any("STR" in g or "short-term" in g.lower() for g in result["gaps"])


def test_empty_and_none_enrichment_never_crash():
    listing = _listing(price=50_000)
    for enrichment in (None, {}, {"buildability_json": None}, {"buildability_json": "not json"}):
        result = scoremod.score(listing, enrichment, "land-to-build", market=_MARKET)
        assert result["verdict"] == "insufficient-data"
        assert result["score"] is None


def test_unknown_strategy_raises():
    try:
        scoremod.score(_listing(), strategy="timeshare")
        assert False, "expected ValueError for an unknown strategy"
    except ValueError:
        pass


def test_fha_firewall_no_agents_import_or_demographic_reference():
    # AST-based, shared with monitor.py's identical requirement -- catches every
    # import spelling incl. the idiomatic `from listings import agents` / `from .
    # import agents` forms a line-regex misses (PRD sec.9).
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _fha_firewall import assert_fha_firewall
    assert_fha_firewall(_SCORE_PY)


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
