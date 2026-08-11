"""Regression tests for listings.agents (E7 realtor rollup + the FHA-firewalled
demographic signal, PRD sec.5/sec.9).

Guards: rollup() computes correct listing_count/geo/price_band/relist_rate per
agent and is idempotent on re-run; infer_demographic returns the fully-labeled
low-confidence/non-decisional dict (and "unknown" for an unrecognized name);
attach_demographic writes ONLY demographic_signal_json, never another agent
column; and a firewall-intent test asserting the demographic dict can never be
silently treated as decisional (decisional is always False, fha_notice is
always non-empty) -- mirroring the shared assertion in tests/_fha_firewall.py
that score.py/monitor.py may not even import this module.

Runs with plain Python (no pytest needed):
    .venv/Scripts/python.exe tests/test_listings_agents.py
...and is also collectable by pytest if installed.
"""

from __future__ import annotations

import contextlib
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from listings import agents, store  # noqa: E402


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


def _approx(a, b, tol=1e-6) -> bool:
    return a is not None and b is not None and abs(a - b) < tol


_BASE = {
    "source": "sample",
    "source_id": "AGT-0000",
    "mls_number": None,
    "status": "active",
    "address": "1 Test St, Durham, NC 27713",
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
    """Three agents, varied jurisdictions/price bands, one with a seeded price
    DROP in price_history:

    - AGT-1001 "Dana Whitfield" / Bull City Land Co.: 2 listings, both
      durham-nc (27712, 27713), prices 89000 & 62000, no price change on
      either -> relist_rate 0.0.
    - AGT-1002 "Marcus Reyes" / Triangle Home Realty: 2 listings, durham-nc
      (27713) + cary-nc (27511), prices 245000 (dropped from 260000, a
      price_change event) & 389000 -> relist_rate 0.5.
    - AGT-1003 "Priya Anand" / Oak City Realty Group: 1 listing, raleigh-nc
      (27607), price 415000, no price change -> relist_rate 0.0.
    """
    store.upsert_listing(conn, _listing(
        source_id="AGT-0001", address="0 Guess Rd, Durham, NC 27712",
        planning_jurisdiction="durham-nc", price=89000, property_type="land",
        list_agent_name="Dana Whitfield", list_agent_id="AGT-1001",
        brokerage="Bull City Land Co.",
    ))
    store.upsert_listing(conn, _listing(
        source_id="AGT-0002", address="0 Anthony Rd, Graham, NC 27713",
        planning_jurisdiction="durham-nc", price=62000, property_type="land",
        list_agent_name="Dana Whitfield", list_agent_id="AGT-1001",
        brokerage="Bull City Land Co.",
    ))

    store.upsert_listing(conn, _listing(
        source_id="AGT-0003", address="212 Pine Dr, Durham, NC 27713",
        planning_jurisdiction="durham-nc", price=260000, property_type="single_family",
        list_agent_name="Marcus Reyes", list_agent_id="AGT-1002",
        brokerage="Triangle Home Realty",
    ))
    # Price drop on the same listing -> a price_change event.
    store.upsert_listing(conn, _listing(
        source_id="AGT-0003", address="212 Pine Dr, Durham, NC 27713",
        planning_jurisdiction="durham-nc", price=245000, property_type="single_family",
        list_agent_name="Marcus Reyes", list_agent_id="AGT-1002",
        brokerage="Triangle Home Realty",
    ))
    store.upsert_listing(conn, _listing(
        source_id="AGT-0004", address="310 Kildaire Farm Rd, Cary, NC 27511",
        planning_jurisdiction="cary-nc", price=389000, property_type="single_family",
        list_agent_name="Marcus Reyes", list_agent_id="AGT-1002",
        brokerage="Triangle Home Realty",
    ))

    store.upsert_listing(conn, _listing(
        source_id="AGT-0005", address="5518 Chapel Hill Rd, Raleigh, NC 27607",
        planning_jurisdiction="raleigh-nc", price=415000, property_type="multi_family_2_4",
        list_agent_name="Priya Anand", list_agent_id="AGT-1003",
        brokerage="Oak City Realty Group",
    ))


def test_rollup_counts_and_upserts_three_agents():
    with _tmp_db() as conn:
        _seed(conn)
        n = agents.rollup(conn)
        assert n == 3


def test_rollup_listing_count_and_geo_per_agent():
    with _tmp_db() as conn:
        _seed(conn)
        agents.rollup(conn)

        dana = store.get_agent(conn, "AGT-1001")
        assert dana["name"] == "Dana Whitfield"
        assert dana["brokerage"] == "Bull City Land Co."
        assert dana["listing_count"] == 2
        import json
        geo = json.loads(dana["geo_json"])
        assert geo["jurisdictions"] == {"durham-nc": 2}
        assert geo["zips"] == {"27712": 1, "27713": 1}

        marcus = store.get_agent(conn, "AGT-1002")
        assert marcus["listing_count"] == 2
        geo = json.loads(marcus["geo_json"])
        assert geo["jurisdictions"] == {"durham-nc": 1, "cary-nc": 1}
        assert geo["zips"] == {"27713": 1, "27511": 1}


def test_rollup_price_band_per_agent():
    with _tmp_db() as conn:
        _seed(conn)
        agents.rollup(conn)

        priya = store.get_agent(conn, "AGT-1003")
        import json
        band = json.loads(priya["price_band_json"])
        assert band["min"] == 415000
        assert band["max"] == 415000
        assert band["median"] == 415000
        assert band["count"] == 1

        marcus = store.get_agent(conn, "AGT-1002")
        band = json.loads(marcus["price_band_json"])
        # Marcus's current (latest) prices are 245000 and 389000.
        assert band["min"] == 245000
        assert band["max"] == 389000
        assert _approx(band["median"], (245000 + 389000) / 2)
        assert band["count"] == 2
        assert sum(b["count"] for b in band["bands"]) == 2


def test_rollup_relist_rate_per_agent():
    with _tmp_db() as conn:
        _seed(conn)
        agents.rollup(conn)

        dana = store.get_agent(conn, "AGT-1001")
        assert dana["relist_rate"] == 0.0

        marcus = store.get_agent(conn, "AGT-1002")
        # 1 of 2 listings (AGT-0003) has a recorded price_change.
        assert _approx(marcus["relist_rate"], 0.5)


def test_rollup_below_avm_rate_is_none_gap():
    with _tmp_db() as conn:
        _seed(conn)
        agents.rollup(conn)
        for agent_id in ("AGT-1001", "AGT-1002", "AGT-1003"):
            assert store.get_agent(conn, agent_id)["below_avm_rate"] is None


def test_rollup_is_idempotent():
    with _tmp_db() as conn:
        _seed(conn)
        agents.rollup(conn)
        first = dict(store.get_agent(conn, "AGT-1002"))
        n = agents.rollup(conn)
        second = dict(store.get_agent(conn, "AGT-1002"))
        assert n == 3
        assert first == second


def test_rollup_skips_listings_with_no_agent_identity():
    with _tmp_db() as conn:
        _seed(conn)
        store.upsert_listing(conn, _listing(source_id="AGT-0006", list_agent_name=None,
                                             list_agent_id=None))
        n = agents.rollup(conn)
        assert n == 3  # the agentless listing contributes no 4th group


def test_rollup_falls_back_to_name_derived_key_when_no_agent_id():
    with _tmp_db() as conn:
        store.upsert_listing(conn, _listing(source_id="AGT-0007", list_agent_id=None,
                                             list_agent_name="No Id Agent"))
        n = agents.rollup(conn)
        assert n == 1
        row = store.get_agent(conn, "name:no-id-agent")
        assert row is not None
        assert row["name"] == "No Id Agent"
        assert row["listing_count"] == 1


# ---------------------------------------------------------------------------
# firewalled demographic signal
# ---------------------------------------------------------------------------

def test_infer_demographic_recognized_surname():
    d = agents.infer_demographic("Anh Nguyen")
    assert d["signal"] == "coarse-guess:vietnamese-surname"
    assert d["confidence"] == "low"
    assert d["method"] == "name-inferred"
    assert d["decisional"] is False
    assert d["fha_notice"]


def test_infer_demographic_unrecognized_surname_is_unknown():
    d = agents.infer_demographic("Jordan Smith-Whoeverson")
    assert d["signal"] == "unknown"
    assert d["decisional"] is False
    assert d["fha_notice"]


def test_infer_demographic_empty_name_is_unknown():
    for name in (None, "", "   "):
        d = agents.infer_demographic(name)
        assert d["signal"] == "unknown"
        assert d["decisional"] is False


def test_attach_demographic_writes_only_demographic_column():
    with _tmp_db() as conn:
        _seed(conn)
        agents.rollup(conn)
        before = dict(store.get_agent(conn, "AGT-1001"))

        signal = agents.attach_demographic(conn, "AGT-1001")
        assert signal["decisional"] is False

        after = dict(store.get_agent(conn, "AGT-1001"))
        import json
        assert json.loads(after["demographic_signal_json"]) == signal
        for col in before:
            if col == "demographic_signal_json":
                continue
            assert after[col] == before[col], "attach_demographic touched column %r" % col


def test_attach_demographic_unknown_agent_raises():
    with _tmp_db() as conn:
        try:
            agents.attach_demographic(conn, "no-such-agent")
            assert False, "expected ValueError"
        except ValueError:
            pass


def test_firewall_intent_signal_always_non_decisional_with_notice():
    """FIREWALL-INTENT: no matter the input, the demographic dict can never
    be silently treated as decisional -- decisional is always False and
    fha_notice is always a non-empty string."""
    for name in ("Nguyen", "Garcia", "Kim", "totally-unrecognized-surname", None, ""):
        d = agents.infer_demographic(name)
        assert d["decisional"] is False
        assert isinstance(d["fha_notice"], str) and d["fha_notice"].strip()


def test_score_and_monitor_do_not_reference_agents_module():
    """Shared firewall assertion (tests/_fha_firewall.py): the decision
    modules must not import listings.agents or name a demographic field."""
    from _fha_firewall import assert_fha_firewall

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    score_path = os.path.join(repo_root, "listings", "score.py")
    assert_fha_firewall(score_path)

    monitor_path = os.path.join(repo_root, "listings", "monitor.py")
    if os.path.exists(monitor_path):
        assert_fha_firewall(monitor_path)


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
