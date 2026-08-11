"""Tests for listings.entitlement -- E10 entitlement-path playbook.

UNIT tests run fully offline: propkb.acquire's REST helpers and docrag.answer
are monkeypatched with canned fixtures (mirrors test_listings_feasibility.py),
so no network / Azure is touched. They assert:
  * path_options / recommended / citations populate from a canned
    "requires a rezoning" answer, with the responsible board + citation
    extracted from the grounded text
  * the applicability question gates whether the discretionary-mechanism
    questions get asked at all -- a clear by-right answer short-circuits with
    empty path_options and no variance/special-use/rezoning queries fired
  * a docrag refusal records a gap and never crashes; refusing everything
    still returns a well-shaped, non-crashing result
  * every query is built from the goal + the parcel's LIVE zoning code (no
    hardcoded values -- memory: no-hardcoding-in-prompts)
  * the research-not-advice disclaimer (feasibility.DISCLAIMER) is always
    present

The single LIVE test hits the real ArcGIS REST + building-codes index for one
Durham parcel; it SKIPS cleanly without Azure keys / network so keyless CI
passes.

Runs with plain Python (no pytest needed):
    .venv/Scripts/python.exe tests/test_listings_entitlement.py
...and is also collectable by pytest.
"""

from __future__ import annotations

import contextlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from propkb import acquire as _acquire          # noqa: E402
from listings import entitlement, feasibility   # noqa: E402

try:
    import pytest                                # noqa: E402
except ImportError:                              # plain-python harness
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

_BUILDABILITY_ANSWER = {
    "answer": "Under Durham UDO Section 5.3 [1], RS-8 permits detached houses "
              "by right at one dwelling unit per lot.",
    "citations": [1],
    "authorities": [{"n": 1, "designation": "Durham UDO Sec. 5.3 Use table"}],
    "refused": False, "refusal_reason": None,
}

_APPLICABILITY_NOT_BY_RIGHT = {
    "answer": "Three townhomes exceed the by-right density for RS-8, so this "
              "is not permitted by right. The appropriate discretionary path "
              "would be a rezoning.",
    "citations": [2],
    "authorities": [{"n": 2, "designation": "Durham UDO Sec. 3.2 Density"}],
    "refused": False, "refusal_reason": None,
}

_APPLICABILITY_BY_RIGHT = {
    "answer": "An accessory dwelling unit is permitted by right in RS-8 "
              "subject to the accessory-structure standards.",
    "citations": [3],
    "authorities": [{"n": 3, "designation": "Durham UDO Sec. 5.4 Accessory uses"}],
    "refused": False, "refusal_reason": None,
}

_REZONING_ANSWER = {
    "answer": "A rezoning to a district permitting multi-unit development "
              "would be required, approved by the City Council following a "
              "Planning Commission recommendation, typically a 3 to 6 month "
              "process.",
    "citations": [4],
    "authorities": [{"n": 4, "designation": "NCGS 160D-601 Zoning amendments"}],
    "refused": False, "refusal_reason": None,
}

_VARIANCE_ANSWER = {
    "answer": "A variance is not the appropriate mechanism for a density "
              "increase; variances are heard by the Board of Adjustment for "
              "dimensional relief only.",
    "citations": [5],
    "authorities": [{"n": 5, "designation": "NCGS 160D-705 Variances"}],
    "refused": False, "refusal_reason": None,
}

_SUP_ANSWER = {
    "answer": "A special use permit approved by the Planning Commission "
              "could allow additional density in some overlays, but does not "
              "apply to base RS-8.",
    "citations": [6],
    "authorities": [{"n": 6, "designation": "Durham UDO Sec. 12.5 Special use permits"}],
    "refused": False, "refusal_reason": None,
}


class _Recorder:
    """Swaps propkb.acquire + docrag.answer with canned fixtures, mirroring
    test_listings_feasibility.py's ``_Recorder``. Routes the fake answer by
    the UNIQUE marker phrase baked into each of entitlement._questions()'s
    query strings; anything else (feasibility.assess's own buildability
    questions) falls back to the canned buildability answer."""

    def __init__(self, parcel=_DURHAM_PARCEL, spatial=None, applicability=None,
                variance=None, special_use=None, rezoning=None, buildability=None):
        self.parcel = parcel
        self.spatial = spatial if spatial is not None else [{"ZONE_CODE": "RS-8"}]
        self.applicability = applicability if applicability is not None \
            else _APPLICABILITY_NOT_BY_RIGHT
        self.variance = variance if variance is not None else _VARIANCE_ANSWER
        self.special_use = special_use if special_use is not None else _SUP_ANSWER
        self.rezoning = rezoning if rezoning is not None else _REZONING_ANSWER
        self.buildability = buildability if buildability is not None \
            else _BUILDABILITY_ANSWER
        self.answer_calls = []
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
        low = query.lower()
        if "appropriate discretionary path" in low:
            return dict(self.applicability)
        if "which board hears variance requests" in low:
            return dict(self.variance)
        if "special use permit or conditional use permit required" in low:
            return dict(self.special_use)
        if "require a rezoning" in low:
            return dict(self.rezoning)
        return dict(self.buildability)

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


# --------------------------------------------------------------------------- #
# UNIT: not-by-right -> full playbook                                         #
# --------------------------------------------------------------------------- #

def test_path_options_recommended_citations_populated():
    with _Recorder().apply():
        v = entitlement.entitlement_path({"parcel_pin": "0812345678"}, "3 townhomes")
    assert v["goal"] == "3 townhomes"
    assert v["location"] == "durham-nc"
    assert v["recommended"] == "rezoning"
    mechs = {p["mechanism"] for p in v["path_options"]}
    assert mechs == {"variance", "special_use_permit", "rezoning"}
    rezoning_opt = next(p for p in v["path_options"] if p["mechanism"] == "rezoning")
    assert rezoning_opt["board"] == "City Council"
    assert rezoning_opt["citation"] == "NCGS 160D-601 Zoning amendments"
    assert rezoning_opt["when_required"] == _REZONING_ANSWER["answer"]
    assert v["citations"]
    assert v["disclaimer"] == feasibility.DISCLAIMER


def test_variance_board_extracted():
    with _Recorder().apply():
        v = entitlement.entitlement_path({"parcel_pin": "0812345678"}, "3 townhomes")
    variance_opt = next(p for p in v["path_options"] if p["mechanism"] == "variance")
    assert variance_opt["board"] == "Board of Adjustment"


def test_timeline_note_extracted_from_grounded_text():
    with _Recorder().apply():
        v = entitlement.entitlement_path({"parcel_pin": "0812345678"}, "3 townhomes")
    assert "3 to 6 month" in v["timeline_note"]


def test_queries_built_from_goal_and_live_zoning_no_hardcoding():
    rec = _Recorder()
    with rec.apply():
        entitlement.entitlement_path({"parcel_pin": "0812345678"}, "3 townhomes")
    assert any("3 townhomes" in c["query"] for c in rec.answer_calls)
    assert any("RS-8" in c["query"] for c in rec.answer_calls)
    assert all(c["corpus"] == "building-codes" for c in rec.answer_calls)
    assert all(c["location"] == "durham-nc" for c in rec.answer_calls)


# --------------------------------------------------------------------------- #
# UNIT: by-right short-circuits (no discretionary path needed)                #
# --------------------------------------------------------------------------- #

def test_by_right_goal_skips_mechanism_questions():
    rec = _Recorder(applicability=_APPLICABILITY_BY_RIGHT)
    with rec.apply():
        v = entitlement.entitlement_path({"parcel_pin": "0812345678"}, "an ADU")
    assert v["recommended"] == "by_right"
    assert v["path_options"] == []
    assert v["timeline_note"]
    assert v["disclaimer"] == feasibility.DISCLAIMER
    # only feasibility.assess's buildability questions + the applicability
    # question ran -- no variance/special-use/rezoning queries were asked.
    assert not any("which board hears variance requests" in c["query"].lower()
                  for c in rec.answer_calls)
    assert not any("require a rezoning" in c["query"].lower()
                  for c in rec.answer_calls)


# --------------------------------------------------------------------------- #
# UNIT: refusal -> gap, never crash                                           #
# --------------------------------------------------------------------------- #

def test_applicability_refusal_falls_back_to_mechanism_questions():
    refusal = {"answer": None, "refused": True, "refusal_reason": "low_confidence",
              "authorities": []}
    rec = _Recorder(applicability=refusal)
    with rec.apply():
        v = entitlement.entitlement_path({"parcel_pin": "0812345678"}, "3 townhomes")
    assert any("applicability" in g and "no grounded answer" in g for g in v["gaps"])
    # still grounded on the mechanism questions despite the applicability refusal
    assert v["path_options"]
    assert v["disclaimer"] == feasibility.DISCLAIMER


def test_all_refused_gives_gaps_no_crash():
    refusal = {"answer": None, "refused": True, "refusal_reason": "low_confidence",
              "authorities": []}
    rec = _Recorder(applicability=refusal, variance=refusal, special_use=refusal,
                    rezoning=refusal, buildability=refusal)
    with rec.apply():
        v = entitlement.entitlement_path({"parcel_pin": "0812345678"}, "3 townhomes")
    assert v["path_options"] == []
    assert v["recommended"] is None
    assert v["citations"] == []
    assert v["gaps"]
    assert v["disclaimer"] == feasibility.DISCLAIMER


def test_no_parcel_match_still_returns_well_shaped_result():
    with _Recorder(parcel={"error": "no parcel match"}).apply():
        v = entitlement.entitlement_path({"parcel_pin": "does-not-exist"}, "3 townhomes")
    assert any("no parcel match" in g for g in v["gaps"])
    assert v["disclaimer"] == feasibility.DISCLAIMER
    assert "path_options" in v and "recommended" in v and "citations" in v


# --------------------------------------------------------------------------- #
# LIVE: one real Durham parcel (guarded skip)                                 #
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
def test_live_durham_entitlement():
    if not _live_enabled():
        if pytest is not None:
            pytest.skip(_LIVE_REASON)
        return
    v = entitlement.entitlement_path(
        {"address": "1621 Clermont Rd, Durham NC 27713"}, "a triplex",
        location="durham-nc")
    print("LIVE entitlement:", {k: v[k] for k in ("location", "recommended")})
    print("LIVE path_options:", v["path_options"][:1])
    assert v["location"] == "durham-nc"
    assert v["disclaimer"] == feasibility.DISCLAIMER


# --------------------------------------------------------------------------- #
# plain-python harness                                                        #
# --------------------------------------------------------------------------- #

def _main() -> int:
    live_on = _live_enabled()
    tests = [(k, v) for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for name, t in tests:
        if name == "test_live_durham_entitlement" and not live_on:
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
