"""Regression tests for listings.commute (PRD E5 -- multi-point commute ranking).

Guards the graceful-no-key rule (no provider configured -> a well-formed
"unavailable" dict, NO network call), the Google Route Matrix parse path, the
JSON-file cache (a repeat rank() call must not re-hit the network), and that
the best/typical/peak scenario mapping never crashes.

Runs with plain Python (no pytest needed):
    .venv/Scripts/python.exe tests/test_listings_commute.py
...and is also collectable by pytest if installed.
"""

from __future__ import annotations

import contextlib
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from listings import commute  # noqa: E402

_ORIGIN = (36.0014, -78.9382)  # a Durham-ish point
_DESTINATIONS = [
    {"name": "RDU Airport", "lat": 35.8776, "lon": -78.7875},
    {"name": "Downtown Durham", "lat": 35.9940, "lon": -78.8986},
]

# A canned computeRouteMatrix response: element 0 -> RDU (10 min/8km), element
# 1 -> Downtown Durham (30 min/32km). Field mask matches what
# `_google_route_matrix` requests (originIndex/destinationIndex/duration/
# distanceMeters/condition/status).
_GOOGLE_ROUTE_MATRIX_RESPONSE = [
    {"originIndex": 0, "destinationIndex": 0, "duration": "600s", "distanceMeters": 8000,
     "condition": "ROUTE_EXISTS", "status": {}},
    {"originIndex": 0, "destinationIndex": 1, "duration": "1800s", "distanceMeters": 32000,
     "condition": "ROUTE_EXISTS", "status": {}},
]


@contextlib.contextmanager
def _isolated_env(**env_overrides):
    """Isolate LISTINGS_ROOT (so the cache file doesn't touch the real repo)
    and set/unset the given provider-key env vars for the duration of a test."""
    keys = ("LISTINGS_ROOT", "GOOGLE_MAPS_API_KEY", "TRAVELTIME_APP_ID", "TRAVELTIME_APP_KEY")
    prev = {k: os.environ.get(k) for k in keys}
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["LISTINGS_ROOT"] = tmp
        for k in ("GOOGLE_MAPS_API_KEY", "TRAVELTIME_APP_ID", "TRAVELTIME_APP_KEY"):
            os.environ.pop(k, None)
        os.environ.update(env_overrides)
        try:
            yield tmp
        finally:
            for k, v in prev.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


def test_no_provider_returns_unavailable_dict_and_makes_no_network_call():
    calls = []

    def _boom(*a, **kw):
        calls.append((a, kw))
        raise AssertionError("network call must not happen with no provider configured")

    import urllib.request
    prev_urlopen = urllib.request.urlopen
    urllib.request.urlopen = _boom
    try:
        with _isolated_env():
            result = commute.rank(_ORIGIN, _DESTINATIONS)
    finally:
        urllib.request.urlopen = prev_urlopen

    assert calls == []
    assert result["provider"] is None
    assert result["cached"] is False
    assert len(result["per_destination"]) == 2
    for p in result["per_destination"]:
        assert p["minutes"] is None
        assert p["distance_km"] is None
    assert result["combined_minutes"] is None
    assert result["max_minutes"] is None
    assert result["gaps"]
    assert "GOOGLE_MAPS_API_KEY" in result["gaps"][0]
    assert "TRAVELTIME_APP_ID" in result["gaps"][0]


def test_google_route_matrix_parses_minutes_distance_and_aggregates():
    with _isolated_env(GOOGLE_MAPS_API_KEY="test-key"):
        prev = commute._http_post_json
        commute._http_post_json = lambda url, headers, body: _GOOGLE_ROUTE_MATRIX_RESPONSE
        try:
            result = commute.rank(_ORIGIN, _DESTINATIONS, scenario="typical")
        finally:
            commute._http_post_json = prev

    assert result["provider"] == "google"
    assert result["cached"] is False
    rdu, downtown = result["per_destination"]
    assert rdu["name"] == "RDU Airport"
    assert rdu["minutes"] == 10.0
    assert rdu["distance_km"] == 8.0
    assert downtown["name"] == "Downtown Durham"
    assert downtown["minutes"] == 30.0
    assert downtown["distance_km"] == 32.0
    assert result["combined_minutes"] == 40.0
    assert result["max_minutes"] == 30.0
    assert result["reachable_all_within"] is None
    assert not result["gaps"]


def test_repeat_rank_call_hits_cache_and_skips_the_network():
    calls = {"n": 0}

    def _counting_post(url, headers, body):
        calls["n"] += 1
        return _GOOGLE_ROUTE_MATRIX_RESPONSE

    with _isolated_env(GOOGLE_MAPS_API_KEY="test-key"):
        prev = commute._http_post_json
        commute._http_post_json = _counting_post
        try:
            first = commute.rank(_ORIGIN, _DESTINATIONS, scenario="typical")
            second = commute.rank(_ORIGIN, _DESTINATIONS, scenario="typical")
        finally:
            commute._http_post_json = prev

    assert calls["n"] == 1
    assert first["cached"] is False
    assert second["cached"] is True
    assert second["combined_minutes"] == first["combined_minutes"]
    assert second["per_destination"] == first["per_destination"]


def test_scenario_mapping_does_not_crash_for_best_typical_peak():
    for scenario in commute.SCENARIOS:
        departure = commute._scenario_departure(scenario)
        assert "departure_time" in departure
        assert "traffic_model" in departure

    for scenario in commute.SCENARIOS:
        with _isolated_env():
            result = commute.rank(_ORIGIN, _DESTINATIONS, scenario=scenario)
            assert result["scenario"] == scenario


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
