"""Tests for listings.feasibility -- E4 buildability verdict.

UNIT tests run fully offline: propkb.acquire's REST helpers and docrag.answer
are monkeypatched with canned fixtures, so no network / Azure is touched. They
assert:
  * jurisdiction mapping (Wake WC/RA short codes + Durham -> docrag location key)
  * citations flow through from answer() into the verdict
  * gaps populated when the parcel query errors
  * NEVER creates a properties/<slug> dir (KB is not polluted)
  * _building_codes_env() removes then restores the two docrag env vars

The single LIVE test hits the real ArcGIS REST + building-codes index for ONE
Durham parcel; it SKIPS cleanly without Azure keys / network so keyless CI passes.

Runs with plain Python (no pytest needed):
    .venv/Scripts/python.exe tests/test_listings_feasibility.py
...and is also collectable by pytest.
"""

from __future__ import annotations

import contextlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from propkb import acquire as _acquire           # noqa: E402
from listings import feasibility                 # noqa: E402

try:
    import pytest                                 # noqa: E402
except ImportError:                               # plain-python harness
    pytest = None


# --------------------------------------------------------------------------- #
# Canned fixtures                                                              #
# --------------------------------------------------------------------------- #

_GEOM = {"rings": [[[-78.9, 36.0], [-78.9, 36.1], [-78.8, 36.1], [-78.9, 36.0]]]}

_DURHAM_PARCEL = {
    "attributes": {"PIN": "0812345678", "LOCATION_ADDR": "123 Main St",
                   "PLANNING_JURISDICTION": "Durham"},
    "geometry": _GEOM, "sr": 4326,
}
_WAKE_WC_PARCEL = {
    "attributes": {"PIN_NUM": "0700000001", "SITE_ADDRESS": "1 County Rd",
                   "PLANNING_JURISDICTION": "WC"},
    "geometry": _GEOM, "sr": 4326,
}
_RALEIGH_PARCEL = {
    "attributes": {"PIN_NUM": "1700000002", "SITE_ADDRESS": "2 Fayetteville St",
                   "PLANNING_JURISDICTION": "RA"},
    "geometry": _GEOM, "sr": 4326,
}

_CANNED_ANSWER = {
    "answer": "Under Durham UDO § 5.3 [1], the RS-8 district permits detached "
              "houses by right.",
    "citations": [1],
    "authorities": [{"n": 1, "designation": "Durham UDO § 5.3 Use table"}],
    "refused": False, "refusal_reason": None,
}


class _Recorder:
    """Swaps propkb.acquire + docrag.answer with canned fixtures. Works with or
    without pytest's monkeypatch (falls back to manual save/restore)."""

    def __init__(self, parcel, spatial=None, answer=None, units_answer=None):
        self.parcel = parcel
        self.spatial = spatial if spatial is not None \
            else [{"ZONE_CODE": "RS-8"}]
        self.answer = answer if answer is not None else _CANNED_ANSWER
        # optional distinct answer for the by-right-units question
        self.units_answer = units_answer
        self.answer_calls = []
        self.env_during_answer = []
        self._saved = {}

    def _fake_parcel(self, cfg, pin, address):
        return self.parcel

    def _fake_spatial(self, jurisdiction, data_type, geometry, sr):
        if data_type == "flood":
            return [{"FLD_ZONE": "X"}]
        return list(self.spatial)

    def _fake_answer(self, corpus, query, location=None, top_k=None, **kw):
        self.answer_calls.append({"corpus": corpus, "query": query,
                                  "location": location})
        # capture whether the building-codes env swap is active during the call
        self.env_during_answer.append({
            "DOCRAG_DOCS_ROOT": os.environ.get("DOCRAG_DOCS_ROOT"),
            "DOCRAG_INDEX_DIR": os.environ.get("DOCRAG_INDEX_DIR"),
        })
        if "dwelling units" in query.lower() and self.units_answer is not None:
            return dict(self.units_answer)
        return dict(self.answer)

    @contextlib.contextmanager
    def apply(self):
        import docrag.answer as dra
        targets = [(_acquire, "_query_parcel", self._fake_parcel),
                   (_acquire, "_query_spatial", self._fake_spatial),
                   (dra, "answer", self._fake_answer)]
        for mod, name, fn in targets:
            self._saved[(mod, name)] = getattr(mod, name)
            setattr(mod, name, fn)
        try:
            yield self
        finally:
            for (mod, name), orig in self._saved.items():
                setattr(mod, name, orig)


@contextlib.contextmanager
def _tmp_propkb_root():
    """Isolate the property KB root in an empty temp dir so we can assert
    assess() never files anything under it."""
    import tempfile
    prev = os.environ.get("PROPKB_ROOT")
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["PROPKB_ROOT"] = tmp
        try:
            yield tmp
        finally:
            if prev is None:
                os.environ.pop("PROPKB_ROOT", None)
            else:
                os.environ["PROPKB_ROOT"] = prev


# --------------------------------------------------------------------------- #
# UNIT: jurisdiction mapping                                                   #
# --------------------------------------------------------------------------- #

def test_durham_parcel_maps_to_durham_nc():
    with _Recorder(_DURHAM_PARCEL).apply():
        v = feasibility.assess({"parcel_pin": "0812345678",
                                "address": "123 Main St"})
    assert v["location"] == "durham-nc"
    assert v["jurisdiction_source"] == "parcel"


def test_wake_wc_code_maps_to_wake_county_nc():
    with _Recorder(_WAKE_WC_PARCEL).apply():
        v = feasibility.assess({"parcel_pin": "0700000001",
                                "planning_jurisdiction": "wake-county-nc"})
    assert v["location"] == "wake-county-nc"
    assert v["jurisdiction_source"] == "parcel"


def test_wake_ra_code_maps_to_raleigh_nc():
    with _Recorder(_RALEIGH_PARCEL).apply():
        v = feasibility.assess({"parcel_pin": "1700000002",
                                "planning_jurisdiction": "wake-county-nc"})
    # RA on the parcel overrides the wake-county seed -> Raleigh governs.
    assert v["location"] == "raleigh-nc"
    assert v["jurisdiction_source"] == "parcel"


def test_explicit_location_arg_wins():
    with _Recorder(_WAKE_WC_PARCEL).apply():
        v = feasibility.assess({"parcel_pin": "0700000001"},
                               location="cary-nc")
    assert v["location"] == "cary-nc"
    assert v["jurisdiction_source"] == "arg"


def test_defaults_to_durham_when_unresolvable():
    parcel = {"attributes": {"PIN": "x"}, "geometry": _GEOM, "sr": 4326}
    with _Recorder(parcel).apply():
        v = feasibility.assess({"parcel_pin": "x"})
    assert v["location"] == "durham-nc"
    assert v["jurisdiction_source"] == "default"


def test_pj_mapping_helper():
    assert feasibility._map_planning_jurisdiction("WC") == "wake-county-nc"
    assert feasibility._map_planning_jurisdiction("RA") == "raleigh-nc"
    assert feasibility._map_planning_jurisdiction("CA") == "cary-nc"
    assert feasibility._map_planning_jurisdiction("Durham") == "durham-nc"
    assert feasibility._map_planning_jurisdiction("raleigh-nc") == "raleigh-nc"
    assert feasibility._map_planning_jurisdiction("") is None
    assert feasibility._map_planning_jurisdiction(None) is None
    assert feasibility._map_planning_jurisdiction("ZZ") is None


# --------------------------------------------------------------------------- #
# UNIT: citations + fields flow through                                        #
# --------------------------------------------------------------------------- #

def test_citations_and_fields_flow_through():
    with _Recorder(_DURHAM_PARCEL).apply():
        v = feasibility.assess({"parcel_pin": "0812345678"})
    assert v["zoning_code"] == "RS-8"
    assert v["flood_zone"] == "X"
    assert v["in_floodway"] is False
    # every buildability field got the canned answer
    for f in ("allowed_uses", "by_right_density", "setbacks", "height",
              "parking"):
        assert v[f] and "RS-8" in v[f]
    # citations deduped to the single canned authority
    assert v["citations"] == [{"n": 1, "designation": "Durham UDO § 5.3 Use table"}]
    assert v["buildable_summary"]
    assert v["disclaimer"] == feasibility.DISCLAIMER


def test_answer_queries_are_grounded_to_zone_and_ask_building_codes():
    rec = _Recorder(_DURHAM_PARCEL)
    with rec.apply():
        feasibility.assess({"parcel_pin": "0812345678",
                            "property_type": "townhouse"})
    # 5 buildability questions + 1 by-right-units question
    assert len(rec.answer_calls) == 6
    assert all(c["corpus"] == "building-codes" for c in rec.answer_calls)
    assert all(c["location"] == "durham-nc" for c in rec.answer_calls)
    # queries are assembled from the live zoning code (no hardcoded answers)
    assert any("RS-8" in c["query"] for c in rec.answer_calls)
    assert any("townhouse" in c["query"] for c in rec.answer_calls)
    assert any("dwelling units" in c["query"] for c in rec.answer_calls)


def test_verdict_has_exact_key_set():
    with _Recorder(_DURHAM_PARCEL).apply():
        v = feasibility.assess({"parcel_pin": "0812345678"})
    assert set(v) == {
        "location", "jurisdiction_source", "zoning_code", "flood_zone",
        "in_floodway", "watershed", "allowed_uses", "by_right_density",
        "setbacks", "height", "parking", "by_right_units", "citations",
        "buildable_summary", "gaps", "disclaimer"}


# --------------------------------------------------------------------------- #
# UNIT: by_right_units extraction                                              #
# --------------------------------------------------------------------------- #

def _units_ans(text):
    return {"answer": text, "citations": [1],
            "authorities": [{"n": 1, "designation": "Durham UDO § 5.x density"}],
            "refused": False, "refusal_reason": None}


def test_by_right_units_numeric_extracted():
    rec = _Recorder(_DURHAM_PARCEL, units_answer=_units_ans(
        "On an 8,000 sf lot in RS-8, up to 2 dwelling units are permitted by "
        "right [1]."))
    with rec.apply():
        v = feasibility.assess({"parcel_pin": "0812345678", "lot_sqft": 8000})
    assert v["by_right_units"] == 2
    # the units question was asked with the lot size baked in
    assert any("8000 square feet" in c["query"] for c in rec.answer_calls)


def test_by_right_units_word_number():
    rec = _Recorder(_DURHAM_PARCEL, units_answer=_units_ans(
        "In RS-8, one dwelling unit is permitted by right per lot [1]."))
    with rec.apply():
        v = feasibility.assess({"parcel_pin": "0812345678"})
    assert v["by_right_units"] == 1


def test_by_right_units_ambiguous_is_none_with_gap():
    rec = _Recorder(_DURHAM_PARCEL, units_answer=_units_ans(
        "1 dwelling unit is permitted by right, or up to 4 units with a "
        "special use permit [1]."))
    with rec.apply():
        v = feasibility.assess({"parcel_pin": "0812345678"})
    assert v["by_right_units"] is None
    assert any("by_right_units" in g for g in v["gaps"])


def test_by_right_units_none_when_no_count_in_answer():
    # default canned answer mentions no unit count
    with _Recorder(_DURHAM_PARCEL).apply():
        v = feasibility.assess({"parcel_pin": "0812345678"})
    assert v["by_right_units"] is None
    assert any("by_right_units" in g for g in v["gaps"])


def test_by_right_units_from_lot_acres():
    rec = _Recorder(_DURHAM_PARCEL, units_answer=_units_ans("3 units [1]."))
    with rec.apply():
        v = feasibility.assess({"parcel_pin": "0812345678", "lot_acres": 1.0})
    assert v["by_right_units"] == 3
    assert any("43560 square feet" in c["query"] for c in rec.answer_calls)


def test_parse_units_helper():
    assert feasibility._parse_units("up to 2 dwelling units [1]") == 2
    assert feasibility._parse_units("one unit permitted") == 1
    assert feasibility._parse_units("no explicit count here") is None
    assert feasibility._parse_units("1 unit or 4 units") is None  # ambiguous
    assert feasibility._parse_units("") is None
    assert feasibility._parse_units(None) is None


# --------------------------------------------------------------------------- #
# UNIT: graceful gaps                                                          #
# --------------------------------------------------------------------------- #

def test_parcel_error_populates_gaps_no_crash():
    with _Recorder({"error": "no parcel match"}).apply():
        v = feasibility.assess({"parcel_pin": "does-not-exist"})
    assert any("no parcel match" in g for g in v["gaps"])
    assert v["buildable_summary"]           # explains what's missing
    assert v["zoning_code"] is None


def test_no_pin_or_address_gives_gap():
    v = feasibility.assess({})
    assert any("no parcel_pin or address" in g for g in v["gaps"])
    assert v["jurisdiction_source"] == "default"


def test_docrag_refusal_records_gap_not_crash():
    refusal = {"answer": None, "refused": True,
               "refusal_reason": "low_confidence", "authorities": []}
    with _Recorder(_DURHAM_PARCEL, answer=refusal).apply():
        v = feasibility.assess({"parcel_pin": "0812345678"})
    assert v["allowed_uses"] is None
    assert any("docrag gave no grounded answer" in g for g in v["gaps"])
    assert v["citations"] == []


def test_query_parcel_exception_is_caught():
    def _boom(cfg, pin, address):
        raise RuntimeError("network down")
    saved = _acquire._query_parcel
    _acquire._query_parcel = _boom
    try:
        v = feasibility.assess({"parcel_pin": "x"})
    finally:
        _acquire._query_parcel = saved
    assert any("parcel query failed" in g for g in v["gaps"])


# --------------------------------------------------------------------------- #
# UNIT: no KB pollution                                                        #
# --------------------------------------------------------------------------- #

def test_assess_never_creates_properties_dir():
    with _tmp_propkb_root() as root:
        before = set(os.listdir(root))
        with _Recorder(_DURHAM_PARCEL).apply():
            feasibility.assess({"parcel_pin": "0812345678",
                                "address": "123 Main St"})
        after = set(os.listdir(root))
    assert before == after == set()          # nothing filed under the KB root


# --------------------------------------------------------------------------- #
# UNIT: env swap                                                               #
# --------------------------------------------------------------------------- #

def test_building_codes_env_removes_and_restores():
    os.environ["DOCRAG_DOCS_ROOT"] = "/listings_data"
    os.environ["DOCRAG_INDEX_DIR"] = "/listings_data/.index"
    try:
        with feasibility._building_codes_env():
            assert "DOCRAG_DOCS_ROOT" not in os.environ
            assert "DOCRAG_INDEX_DIR" not in os.environ
        assert os.environ["DOCRAG_DOCS_ROOT"] == "/listings_data"
        assert os.environ["DOCRAG_INDEX_DIR"] == "/listings_data/.index"
    finally:
        os.environ.pop("DOCRAG_DOCS_ROOT", None)
        os.environ.pop("DOCRAG_INDEX_DIR", None)


def test_building_codes_env_when_unset_stays_unset():
    os.environ.pop("DOCRAG_DOCS_ROOT", None)
    os.environ.pop("DOCRAG_INDEX_DIR", None)
    with feasibility._building_codes_env():
        assert "DOCRAG_DOCS_ROOT" not in os.environ
    assert "DOCRAG_DOCS_ROOT" not in os.environ


def test_answer_called_inside_building_codes_env():
    """The two listings env vars must be ABSENT during the answer() call so
    docrag falls back to the building-codes instance."""
    rec = _Recorder(_DURHAM_PARCEL)
    os.environ["DOCRAG_DOCS_ROOT"] = "/listings_data"
    os.environ["DOCRAG_INDEX_DIR"] = "/listings_data/.index"
    try:
        with rec.apply():
            feasibility.assess({"parcel_pin": "0812345678"})
    finally:
        os.environ.pop("DOCRAG_DOCS_ROOT", None)
        os.environ.pop("DOCRAG_INDEX_DIR", None)
    assert rec.env_during_answer
    for snap in rec.env_during_answer:
        assert snap["DOCRAG_DOCS_ROOT"] is None
        assert snap["DOCRAG_INDEX_DIR"] is None


# --------------------------------------------------------------------------- #
# LIVE: one real Durham parcel (guarded skip)                                  #
# --------------------------------------------------------------------------- #

def _live_enabled() -> bool:
    from docrag import settings as _s
    return bool(_s.azure_endpoint() and _s.azure_api_key())


_LIVE_REASON = "live test needs AZURE_OPENAI_* keys + network (building-codes index)"

if pytest is not None:
    _live_mark = pytest.mark.skipif(not _live_enabled(), reason=_LIVE_REASON)
else:  # plain-python: no-op decorator
    def _live_mark(fn):
        return fn


@_live_mark
def test_live_durham_assess():
    if not _live_enabled():
        if pytest is not None:
            pytest.skip(_LIVE_REASON)
        return
    v = feasibility.assess({"address": "1621 Clermont Rd, Durham NC 27713"},
                           location="durham-nc")
    print("LIVE verdict:", {k: v[k] for k in
                            ("location", "zoning_code", "flood_zone",
                             "in_floodway", "by_right_units")})
    print("LIVE citations:", v["citations"][:3])
    assert v["location"] == "durham-nc"
    assert v["zoning_code"], "expected a zoning district for the Durham parcel"
    assert len(v["citations"]) >= 1, "expected >=1 docrag citation"


# --------------------------------------------------------------------------- #
# plain-python harness                                                         #
# --------------------------------------------------------------------------- #

def _main() -> int:
    live_on = _live_enabled()
    tests = [(k, v) for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for name, t in tests:
        if name == "test_live_durham_assess" and not live_on:
            print("SKIP", name, "--", _LIVE_REASON)
            continue
        try:
            t()
            print("PASS", name)
        except AssertionError as e:
            failed += 1
            print("FAIL", name, "--", e or "assertion failed")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print("ERROR", name, "--", repr(e))
    ran = len(tests) - (0 if live_on else 1)
    print(f"\n{ran - failed}/{ran} passed"
          + ("" if live_on else " (live test skipped)"))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_main())
