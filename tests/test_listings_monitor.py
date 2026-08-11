"""Regression tests for listings.monitor (E8 standing deal monitor + NTFY alerts).

Guards: run_thesis alerts ONLY a candidate that clears buildable + pencils +
reachable, dedups via thesis_hits so a second run never re-alerts the same
listing, require_buildable/min_score fail CLOSED on an undetermined verdict,
a commute provider gap is "unknown" (never a hard fail), a missing NTFY topic
records a gap without attempting a real HTTP call, and the FHA firewall:
monitor.py must never import listings.agents or reference the demographic
field. Fully offline -- search() and enrich_listing() are monkeypatched, and
the injectable notifier means no test ever sends a real push.

Dual harness: pytest-collectible and `python tests/test_listings_monitor.py`.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from listings import enrich as enrich_mod   # noqa: E402
from listings import monitor as monitor_mod  # noqa: E402
from listings import search as search_mod    # noqa: E402
from listings import settings                # noqa: E402
from listings import store                   # noqa: E402

_MONITOR_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "listings", "monitor.py")


@contextlib.contextmanager
def _tmp_db():
    prev = os.environ.get("LISTINGS_ROOT")
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["LISTINGS_ROOT"] = tmp
        conn = store.connect()
        store.init_db(conn)
        try:
            yield conn
        finally:
            conn.close()
            if prev is None:
                os.environ.pop("LISTINGS_ROOT", None)
            else:
                os.environ["LISTINGS_ROOT"] = prev


def _seed_listing(conn, source_id: str, address: str, price: float) -> int:
    return store.upsert_listing(conn, {
        "source": "sample", "source_id": source_id, "status": "active",
        "address": address, "lat": 35.99, "lon": -78.90, "price": price,
        "property_type": "land", "planning_jurisdiction": "durham-nc",
        "description_text": "vacant lot",
    })


def _fake_search_for(ids: list[int]):
    def _search(conn, query, filters=None, top_k=20, semantic=True):
        return [store.get_listing(conn, i) for i in ids]
    return _search


def _verdict(buildable_units, score, verdict, provider="google", max_minutes=15.0,
             citations=None, summary="Buildable."):
    """Build a canned enrich_listing()-shaped result AND persist the matching
    enrichment row (mirrors what the real enrich_listing does), since
    monitor.py reads buildability back out of the persisted row rather than
    trusting the in-memory result dict."""
    citations = citations if citations is not None else [{"n": 1, "designation": "Durham UDO Sec. 5.4"}]
    buildability = {"buildable": buildable_units is None or buildable_units > 0,
                    "buildable_units": buildable_units}
    commute = ({"provider": provider, "max_minutes": max_minutes, "combined_minutes": max_minutes,
               "gaps": []} if provider else
              {"provider": None, "max_minutes": None, "combined_minutes": None,
               "gaps": ["no commute provider configured"]})
    scores = {"land-to-build": {"strategy": "land-to-build", "score": score, "verdict": verdict,
                                "gaps": []}}

    def _fake_enrich(conn, listing_id, destinations=None, scenario="typical",
                     strategies=("land-to-build",), location=None, market=None, assumptions=None):
        store.upsert_enrichment(conn, listing_id, {
            "zoning_code": "RS-8",
            "buildability_json": json.dumps(buildability, ensure_ascii=False),
            "buildability_cited": "; ".join(c["designation"] for c in citations),
            "commute_json": json.dumps(commute, ensure_ascii=False),
            "score_json": json.dumps(scores, ensure_ascii=False),
        })
        return {
            "listing_id": listing_id, "location": "durham-nc", "zoning_code": "RS-8",
            "flood_zone": "X", "in_floodway": False, "buildable_summary": summary,
            "citations": citations, "commute": commute, "scores": scores, "gaps": [],
            "disclaimer": "Research, not legal/engineering/investment advice. Verify locally.",
        }
    return _fake_enrich


def _thesis_spec(min_score=None, max_commute_min=None, require_buildable=False):
    return {
        "query": "buildable lot near downtown", "filters": {"property_type": "land"},
        "destinations": [{"name": "Downtown Durham", "lat": 35.994, "lon": -78.898}],
        "scenario": "typical", "strategy": "land-to-build",
        "thresholds": {"min_score": min_score, "max_commute_min": max_commute_min,
                       "require_buildable": require_buildable},
    }


@contextlib.contextmanager
def _patched(module, name, value):
    orig = getattr(module, name)
    setattr(module, name, value)
    try:
        yield
    finally:
        setattr(module, name, orig)


# ---- pass/fail/dedup core ---------------------------------------------------

def test_run_thesis_alerts_only_passing_candidate_and_dedups():
    with _tmp_db() as conn:
        good = _seed_listing(conn, "GOOD-1", "1 Good Ln, Durham, NC", 90_000)
        bad = _seed_listing(conn, "BAD-1", "2 Bad Ln, Durham, NC", 90_000)
        thesis_id = store.save_thesis(conn, "downtown lots",
                                      _thesis_spec(min_score=50, require_buildable=True))

        def fake_enrich(conn, listing_id, **kw):
            fn = (_verdict(2, 75, "strong") if listing_id == good
                  else _verdict(0, 10, "weak"))
            return fn(conn, listing_id, **kw)

        notified: list[tuple] = []

        with _patched(search_mod, "search", _fake_search_for([good, bad])), \
             _patched(enrich_mod, "enrich_listing", fake_enrich):
            hits = monitor_mod.run_thesis(
                conn, thesis_id, notify=True,
                notifier=lambda title, message: notified.append((title, message)))

            assert [h["listing_id"] for h in hits] == [good]
            assert hits[0]["notified"] is True
            assert len(notified) == 1
            assert "Good Ln" in notified[0][1]
            assert "Research, not investment advice" in hits[0]["rationale"]
            assert "Durham UDO Sec. 5.4" in hits[0]["rationale"]  # cited

            # second run: nothing new -- dedup via thesis_hits, no repeat alert.
            hits2 = monitor_mod.run_thesis(
                conn, thesis_id, notify=True,
                notifier=lambda title, message: notified.append((title, message)))
            assert hits2 == []
            assert len(notified) == 1

        recorded = store.list_thesis_hits(conn, thesis_id)
        assert [r["listing_id"] for r in recorded] == [good]


def test_require_buildable_fails_closed_on_undetermined_verdict():
    with _tmp_db() as conn:
        lid = _seed_listing(conn, "UNK-1", "3 Unknown Ln, Durham, NC", 90_000)
        thesis_id = store.save_thesis(conn, "strict", _thesis_spec(require_buildable=True))
        # buildable_units=None, buildable flag also None/False -> undetermined.
        fake_enrich = _verdict(None, None, "insufficient-data", citations=[])

        def _no_buildable_flag(conn, listing_id, **kw):
            store.upsert_enrichment(conn, listing_id, {
                "buildability_json": json.dumps({"buildable_units": None}),
                "score_json": json.dumps({"land-to-build": {"score": None,
                                          "verdict": "insufficient-data"}}),
                "commute_json": None,
            })
            return {"citations": [], "buildable_summary": None, "commute": None,
                    "scores": {"land-to-build": {"score": None, "verdict": "insufficient-data"}},
                    "gaps": []}

        with _patched(search_mod, "search", _fake_search_for([lid])), \
             _patched(enrich_mod, "enrich_listing", _no_buildable_flag):
            hits = monitor_mod.run_thesis(conn, thesis_id, notify=False)
        assert hits == []  # fails closed, not silently treated as buildable


def test_min_score_filters_low_scoring_candidate():
    with _tmp_db() as conn:
        lid = _seed_listing(conn, "LOWSCORE-1", "4 Low Score Ln, Durham, NC", 90_000)
        thesis_id = store.save_thesis(conn, "high bar", _thesis_spec(min_score=80))
        fake_enrich = _verdict(2, 40, "marginal")  # buildable, but below min_score

        with _patched(search_mod, "search", _fake_search_for([lid])), \
             _patched(enrich_mod, "enrich_listing", fake_enrich):
            hits = monitor_mod.run_thesis(conn, thesis_id, notify=False)
        assert hits == []


# ---- commute: unavailable is "unknown", not a hard fail --------------------

def test_commute_unavailable_is_unknown_not_a_fail():
    with _tmp_db() as conn:
        lid = _seed_listing(conn, "NOPROV-1", "5 No Provider Ln, Durham, NC", 90_000)
        thesis_id = store.save_thesis(conn, "commute gated",
                                      _thesis_spec(min_score=50, max_commute_min=20.0))
        # buildable + scores well, but no commute provider configured.
        fake_enrich = _verdict(2, 75, "strong", provider=None)

        with _patched(search_mod, "search", _fake_search_for([lid])), \
             _patched(enrich_mod, "enrich_listing", fake_enrich):
            hits = monitor_mod.run_thesis(conn, thesis_id, notify=False)

        assert len(hits) == 1
        assert hits[0]["reachable"] == "unknown"
        assert any("commute" in g.lower() for g in hits[0]["gaps"])


def test_commute_over_threshold_hard_fails():
    with _tmp_db() as conn:
        lid = _seed_listing(conn, "FAR-1", "6 Far Ln, Durham, NC", 90_000)
        thesis_id = store.save_thesis(conn, "commute gated",
                                      _thesis_spec(min_score=50, max_commute_min=20.0))
        fake_enrich = _verdict(2, 75, "strong", provider="google", max_minutes=45.0)

        with _patched(search_mod, "search", _fake_search_for([lid])), \
             _patched(enrich_mod, "enrich_listing", fake_enrich):
            hits = monitor_mod.run_thesis(conn, thesis_id, notify=False)
        assert hits == []


# ---- notification: no topic configured -> gap, no crash, no HTTP attempt --

def test_notify_with_no_ntfy_topic_records_gap_without_network_attempt():
    with _tmp_db() as conn:
        lid = _seed_listing(conn, "NTFY-1", "7 Ntfy Ln, Durham, NC", 90_000)
        thesis_id = store.save_thesis(conn, "default notifier", _thesis_spec())
        fake_enrich = _verdict(2, 75, "strong")

        def _boom(topic, title, message):
            raise AssertionError("notify_ntfy must not be called when no topic is configured")

        with _patched(search_mod, "search", _fake_search_for([lid])), \
             _patched(enrich_mod, "enrich_listing", fake_enrich), \
             _patched(settings, "ntfy_topic", lambda: ""), \
             _patched(monitor_mod, "notify_ntfy", _boom):
            hits = monitor_mod.run_thesis(conn, thesis_id, notify=True, notifier=None)

        assert len(hits) == 1
        assert hits[0]["notified"] is False
        assert any("NTFY_TOPIC" in g for g in hits[0]["gaps"])


def test_run_all_runs_every_thesis_and_touches_last_run():
    with _tmp_db() as conn:
        good = _seed_listing(conn, "ALL-GOOD", "8 All Good Ln, Durham, NC", 90_000)
        t1 = store.save_thesis(conn, "thesis one", _thesis_spec())
        t2 = store.save_thesis(conn, "thesis two", _thesis_spec(min_score=999))  # unreachable bar
        fake_enrich = _verdict(2, 75, "strong")

        with _patched(search_mod, "search", _fake_search_for([good])), \
             _patched(enrich_mod, "enrich_listing", fake_enrich):
            summary = monitor_mod.run_all(conn, notify=False)

        assert summary["theses_run"] == 2
        assert len(summary["results"][t1]) == 1
        assert summary["results"][t2] == []
        assert summary["new_hits"] == 1
        for tid in (t1, t2):
            row = conn.execute("SELECT last_run FROM theses WHERE id = ?", (tid,)).fetchone()
            assert row["last_run"] is not None


# ---- FHA firewall -----------------------------------------------------------

def test_fha_firewall_no_agents_import_or_demographic_reference():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _fha_firewall import assert_fha_firewall
    assert_fha_firewall(_MONITOR_PY)


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
