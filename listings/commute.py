"""listings.commute -- multi-point traffic-aware commute ranking (PRD E5).

For a candidate listing, rank it against N user-chosen destinations (airport,
downtown, a specific job site, ...) by traffic-aware drive time, under one of
three departure-time scenarios. This is the "reachable from A AND B AND C"
primitive `enrich.py` will call per survivor after the cheap SQLite filter +
semantic rerank narrow the field (PRD §6 architecture, §8 commute/POI).

Two providers, auto-selected by whichever key is configured (Google preferred
when both are set -- see `_resolve_provider`):

  - GOOGLE  -- Routes API `computeRouteMatrix` (traffic-aware, one origin x N
    destinations in a single call, ~$5/1k elements). Needs GOOGLE_MAPS_API_KEY.
  - TRAVELTIME -- the time-filter matrix (purpose-built for the Triangle's
    multi-destination case). Needs TRAVELTIME_APP_ID + TRAVELTIME_APP_KEY.
    NOTE: time-filter is a pairwise (one origin -> many destinations) minutes
    lookup, not TravelTime's true intersection primitive. The actual
    "reachable from ALL destinations at once" primitive is the isochrone-based
    /time-map (+ set intersection) or /time-filter/postcode-districts
    endpoint -- see the comment in `_traveltime_per_destination` for where
    that would plug in once E5 needs to filter candidates, not just score one.

GRACEFUL NO-KEY (mirrors `propkb.mls`'s "creds not set -> clear error", except
`rank()` itself never raises for a missing key -- this is a per-listing
enrichment step, not a required credential, so a missing key degrades to a
well-formed "unavailable" result rather than crashing the caller's pipeline.
No network call is made in that case.

Aggressive JSON-file caching (commute is the cost driver, per PRD §8): a
rank() call and a geocode-address call each get their own small cache file
under `listings_data/.commute_cache/`, keyed on the rounded/normalized inputs.
Cache reads/writes are best-effort -- a missing or corrupt cache file is
treated as empty, never a crash.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from . import settings

SCENARIOS = ("best", "typical", "peak")

_NO_PROVIDER_GAP = (
    "no commute provider configured; set GOOGLE_MAPS_API_KEY or "
    "TRAVELTIME_APP_ID/APP_KEY"
)

_GOOGLE_ROUTE_MATRIX_URL = "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix"
_GOOGLE_GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
_TRAVELTIME_TIME_FILTER_URL = "https://api.traveltimeapp.com/v4/time-filter"


def _cfg(key: str, default: str = "") -> str:
    """Env wins; fall back to docrag's .env-backed settings (mirrors propkb/mls.py)."""
    val = os.environ.get(key, "")
    if val:
        return val
    try:
        from docrag import settings as _s
        return _s.get(key, default) or default
    except Exception:  # noqa: BLE001
        return default


def _google_key() -> str:
    return _cfg("GOOGLE_MAPS_API_KEY", "")


def _traveltime_creds() -> tuple[str, str]:
    return _cfg("TRAVELTIME_APP_ID", ""), _cfg("TRAVELTIME_APP_KEY", "")


# ---------- provider auto-selection ----------

def _resolve_provider(requested: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Pick which provider `rank()` should use. Returns (provider_or_None, gap_or_None).

    requested=None (auto): prefer google if GOOGLE_MAPS_API_KEY is set, else
    traveltime if both TRAVELTIME_APP_ID/APP_KEY are set, else None.
    requested="google"/"traveltime": honor it only if its key is present --
    never silently substitute a different provider than the caller asked for.
    """
    if requested not in (None, "google", "traveltime"):
        raise ValueError("provider must be 'google', 'traveltime', or None; got %r" % requested)

    google_ok = bool(_google_key())
    tt_id, tt_key = _traveltime_creds()
    tt_ok = bool(tt_id and tt_key)

    if requested == "google":
        return ("google", None) if google_ok else (None, "GOOGLE_MAPS_API_KEY not set")
    if requested == "traveltime":
        return ("traveltime", None) if tt_ok else (None, "TRAVELTIME_APP_ID/APP_KEY not set")

    if google_ok:
        return "google", None
    if tt_ok:
        return "traveltime", None
    return None, _NO_PROVIDER_GAP


# ---------- scenario -> departure-time mapping ----------

def _next_occurrence(now_dt: datetime, hour: int, minute: int,
                      weekday: Optional[int] = None, weekdays_only: bool = False) -> datetime:
    candidate = now_dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now_dt:
        candidate += timedelta(days=1)
    if weekday is not None:
        while candidate.weekday() != weekday:
            candidate += timedelta(days=1)
    elif weekdays_only:
        while candidate.weekday() >= 5:  # Mon=0 .. Sat=5, Sun=6
            candidate += timedelta(days=1)
    return candidate


def _scenario_departure(scenario: str, now_dt: Optional[datetime] = None) -> dict:
    """Map a scenario name to a traffic assumption, documented here since it's
    the one judgment call baked into this module:

      - "typical": right now, best-guess traffic model -- the ordinary drive.
      - "best":    next Saturday 10am, best-guess model -- an off-peak,
        light-traffic case (weekend mid-morning).
      - "peak":    next weekday 8:15am, pessimistic model -- the AM commute
        rush a daily driver would actually hit.

    Returns {"departure_time": "now" | <ISO8601>, "traffic_model": <str>}.
    """
    if scenario not in SCENARIOS:
        raise ValueError("scenario must be one of %s, got %r" % (SCENARIOS, scenario))
    now_dt = now_dt or datetime.now(timezone.utc)
    if scenario == "typical":
        return {"departure_time": "now", "traffic_model": "BEST_GUESS"}
    if scenario == "best":
        target = _next_occurrence(now_dt, hour=10, minute=0, weekday=5)  # Saturday
        return {"departure_time": target.isoformat(), "traffic_model": "BEST_GUESS"}
    target = _next_occurrence(now_dt, hour=8, minute=15, weekdays_only=True)  # peak
    return {"departure_time": target.isoformat(), "traffic_model": "PESSIMISTIC"}


# ---------- cache (JSON file under listings_data/.commute_cache/) ----------

def _cache_dir() -> str:
    d = os.path.join(settings.listings_root(), ".commute_cache")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:  # noqa: BLE001 -- best-effort; a read-only fs just disables caching
        pass
    return d


def _cache_file(name: str) -> str:
    return os.path.join(_cache_dir(), name)


def _load_cache(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001 -- missing/corrupt cache == empty cache, never a crash
        return {}


def _save_cache(path: str, data: dict) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:  # noqa: BLE001 -- best-effort; a failed write just costs a cache hit later
        pass


def _rank_cache_key(provider: str, origin: tuple[float, float],
                     destinations: list[dict], scenario: str) -> str:
    norm_origin = [round(origin[0], 5), round(origin[1], 5)]
    norm_dests = []
    for d in destinations:
        lat, lon = d.get("lat"), d.get("lon")
        if lat is not None and lon is not None:
            norm_dests.append({"name": d.get("name"), "lat": round(lat, 5), "lon": round(lon, 5)})
        else:
            norm_dests.append({"name": d.get("name"), "address": d.get("address")})
    payload = {"provider": provider, "origin": norm_origin, "destinations": norm_dests,
               "scenario": scenario}
    blob = json.dumps(payload, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ---------- HTTP (stdlib urllib, mirrors propkb/mls.py's style) ----------

def _http_post_json(url: str, headers: dict, body: dict) -> Any:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _http_get_json(url: str) -> Any:
    with urllib.request.urlopen(url, timeout=15) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


# ---------- geocoding (address -> lat/lon), cached ----------

def _geocode(address: str) -> tuple[Optional[tuple[float, float]], Optional[str]]:
    """Resolve a destination given only as an address. Requires GOOGLE_MAPS_API_KEY
    (TravelTime has no first-class forward-geocode in this module); callers that
    always pass lat/lon avoid this entirely. Returns (coords_or_None, gap_or_None)."""
    key = _google_key()
    if not key:
        return None, "cannot geocode %r: no GOOGLE_MAPS_API_KEY configured" % address

    cache_path = _cache_file("geocode_cache.json")
    cache = _load_cache(cache_path)
    ck = address.strip().lower()
    if ck in cache:
        v = cache[ck]
        return (v[0], v[1]), None

    url = _GOOGLE_GEOCODE_URL + "?" + urllib.parse.urlencode({"address": address, "key": key})
    try:
        data = _http_get_json(url)
    except Exception as e:  # noqa: BLE001 -- geocoding failure is a gap, not a crash
        return None, "geocoding %r failed: %s" % (address, e)

    results = data.get("results") or []
    if not results:
        return None, "geocoding %r returned no results" % address
    loc = results[0]["geometry"]["location"]
    coords = (loc["lat"], loc["lng"])
    cache[ck] = list(coords)
    _save_cache(cache_path, cache)
    return coords, None


# ---------- Google Routes API (computeRouteMatrix) ----------

def _google_route_matrix(origin: tuple[float, float], coords: list[tuple[float, float]],
                          departure: dict) -> list[dict]:
    key = _google_key()
    if not key:
        raise RuntimeError("GOOGLE_MAPS_API_KEY not set (needed for the Routes computeRouteMatrix call).")

    body: dict = {
        "origins": [{"waypoint": {"location": {"latLng": {
            "latitude": origin[0], "longitude": origin[1]}}}}],
        "destinations": [{"waypoint": {"location": {"latLng": {
            "latitude": lat, "longitude": lon}}}} for lat, lon in coords],
        "travelMode": "DRIVE",
        "routingPreference": "TRAFFIC_AWARE",
    }
    if departure["departure_time"] != "now":
        body["departureTime"] = departure["departure_time"]
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": key,
        "X-Goog-FieldMask": "originIndex,destinationIndex,duration,distanceMeters,condition,status",
    }
    return _http_post_json(_GOOGLE_ROUTE_MATRIX_URL, headers, body)


def _parse_google_elements(elements: list[dict], n_destinations: int) -> list[dict]:
    """Map computeRouteMatrix's flat element list (one per origin x destination
    pair) back into per-destination order. `status: {}` means OK per the Routes
    API convention; any non-empty status is an error/unreachable pair."""
    per: list[Optional[dict]] = [None] * n_destinations
    for el in elements:
        idx = el.get("destinationIndex", 0)
        status = el.get("status") or {}
        if status:
            per[idx] = {"minutes": None, "distance_km": None,
                        "status": status.get("message") or "error"}
            continue
        dur = el.get("duration", "")
        minutes = None
        if isinstance(dur, str) and dur.endswith("s"):
            try:
                minutes = float(dur[:-1]) / 60.0
            except ValueError:  # noqa: BLE001 -- unparseable duration is a gap, not a crash
                minutes = None
        dist_m = el.get("distanceMeters")
        distance_km = (dist_m / 1000.0) if isinstance(dist_m, (int, float)) else None
        per[idx] = {"minutes": minutes, "distance_km": distance_km,
                     "status": el.get("condition", "ok")}
    return [p or {"minutes": None, "distance_km": None, "status": "no_element"} for p in per]


# ---------- TravelTime time-filter matrix ----------

def _traveltime_per_destination(origin: tuple[float, float], coords: list[tuple[float, float]],
                                 departure: dict) -> list[dict]:
    app_id, app_key = _traveltime_creds()
    if not (app_id and app_key):
        raise RuntimeError(
            "TravelTime credentials not set. Set TRAVELTIME_APP_ID + TRAVELTIME_APP_KEY "
            "in .env (issued at https://docs.traveltime.com/api/overview/getting-keys)."
        )
    dep_time = departure["departure_time"]
    if dep_time == "now":
        dep_time = datetime.now(timezone.utc).isoformat()

    dest_ids = ["dest-%d" % i for i in range(len(coords))]
    locations = [{"id": "origin", "coords": {"lat": origin[0], "lng": origin[1]}}]
    locations += [{"id": did, "coords": {"lat": lat, "lng": lon}}
                  for did, (lat, lon) in zip(dest_ids, coords)]
    # This is the pairwise time-filter primitive (one origin -> many
    # destinations' minutes) -- it fills the per-destination contract this
    # module returns. TravelTime's actual multi-destination INTERSECTION
    # primitive ("reachable from A AND B AND C at once") is the isochrone-based
    # /time-map (+ set intersection) or /time-filter/postcode-districts
    # endpoint; that's what a future "filter candidates reachable from ALL
    # destinations" step (as opposed to scoring one known candidate) should
    # call instead, and it would plug in right here.
    body = {
        "locations": locations,
        "departure_searches": [{
            "id": "commute",
            "departure_location_id": "origin",
            "arrival_location_ids": dest_ids,
            "transportation": {"type": "driving"},
            "departure_time": dep_time,
            "travel_time": 7200,
            "properties": ["travel_time", "distance"],
        }],
    }
    headers = {"Content-Type": "application/json", "X-Application-Id": app_id, "X-Api-Key": app_key}
    data = _http_post_json(_TRAVELTIME_TIME_FILTER_URL, headers, body)

    by_id: dict[str, tuple[Optional[float], Optional[float]]] = {}
    for search in (data.get("results") or []):
        for loc in (search.get("locations") or []):
            props = loc.get("properties") or [{}]
            props0 = props[0] if props else {}
            by_id[loc.get("id")] = (props0.get("travel_time"), props0.get("distance"))

    out = []
    for did in dest_ids:
        secs, dist_m = by_id.get(did, (None, None))
        minutes = (secs / 60.0) if isinstance(secs, (int, float)) else None
        distance_km = (dist_m / 1000.0) if isinstance(dist_m, (int, float)) else None
        out.append({"minutes": minutes, "distance_km": distance_km,
                     "status": "ok" if minutes is not None else "unreachable"})
    return out


# ---------- public interface ----------

def rank(origin: tuple[float, float], destinations: list[dict], scenario: str = "typical",
         provider: Optional[str] = None) -> dict:
    """Rank one candidate origin against N destinations for a departure-time
    scenario. See the module docstring for provider selection + caching.

    destinations: [{"name": .., "lat": .., "lon": ..}, ...] or
                  [{"name": .., "address": ..}, ...] (geocoded-and-cached).

    Never raises for a missing provider key or a network failure -- both
    degrade to gaps in the returned dict so a caller's pipeline (enrich.py)
    can keep going on the rest of a listing's enrichment.
    """
    if scenario not in SCENARIOS:
        raise ValueError("scenario must be one of %s, got %r" % (SCENARIOS, scenario))

    provider_resolved, gap = _resolve_provider(provider)

    result: dict = {
        "origin": [origin[0], origin[1]],
        "scenario": scenario,
        "provider": provider_resolved,
        "per_destination": [],
        "combined_minutes": None,
        "max_minutes": None,
        "reachable_all_within": None,
        "gaps": [],
        "cached": False,
    }

    if provider_resolved is None:
        result["per_destination"] = [
            {"name": d.get("name") or d.get("address"), "minutes": None,
             "distance_km": None, "status": "no_provider"}
            for d in destinations
        ]
        result["gaps"] = [gap or _NO_PROVIDER_GAP]
        return result

    cache_path = _cache_file("rank_cache.json")
    key = _rank_cache_key(provider_resolved, origin, destinations, scenario)
    cache = _load_cache(cache_path)
    if key in cache:
        cached = dict(cache[key])
        cached["cached"] = True
        return cached

    names = [d.get("name") or d.get("address") or "?" for d in destinations]
    coords: list[Optional[tuple[float, float]]] = []
    gaps: list[str] = []
    for d in destinations:
        lat, lon = d.get("lat"), d.get("lon")
        if lat is not None and lon is not None:
            coords.append((lat, lon))
        elif d.get("address"):
            latlon, geo_gap = _geocode(d["address"])
            coords.append(latlon)
            if geo_gap:
                gaps.append(geo_gap)
        else:
            coords.append(None)
            gaps.append("destination %r missing lat/lon and address" % names[len(coords) - 1])

    per_destination: list[Optional[dict]] = [None] * len(destinations)
    resolved_idx = [i for i, c in enumerate(coords) if c is not None]
    for i, c in enumerate(coords):
        if c is None:
            per_destination[i] = {"name": names[i], "minutes": None,
                                   "distance_km": None, "status": "unresolved"}

    if resolved_idx:
        departure = _scenario_departure(scenario)
        active_coords = [coords[i] for i in resolved_idx]
        try:
            if provider_resolved == "google":
                raw = _google_route_matrix(origin, active_coords, departure)
                parsed = _parse_google_elements(raw, len(active_coords))
            else:
                parsed = _traveltime_per_destination(origin, active_coords, departure)
        except Exception as e:  # noqa: BLE001 -- provider/network failure is a gap, not a crash
            gaps.append("%s commute lookup failed: %s" % (provider_resolved, e))
            parsed = [{"minutes": None, "distance_km": None, "status": "error"} for _ in active_coords]
        for j, i in enumerate(resolved_idx):
            entry = dict(parsed[j])
            entry["name"] = names[i]
            per_destination[i] = entry

    minutes_list = [p["minutes"] for p in per_destination]
    available = [m for m in minutes_list if m is not None]
    if minutes_list and len(available) == len(minutes_list):
        combined_minutes: Optional[float] = sum(minutes_list)
        max_minutes: Optional[float] = max(minutes_list)
    else:
        combined_minutes = None
        max_minutes = max(available) if available else None
        if minutes_list and available:
            gaps.append("some destinations unreachable; combined_minutes withheld")

    result["per_destination"] = per_destination
    result["combined_minutes"] = combined_minutes
    result["max_minutes"] = max_minutes
    result["gaps"] = gaps

    cache[key] = {k: v for k, v in result.items() if k != "cached"}
    _save_cache(cache_path, cache)
    return result
