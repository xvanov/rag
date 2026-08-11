"""listings.sources.redfin -- v1 remarks adapter via Apify's Redfin scraper actor.

Per PRD §8 (spike resolved 2026-07-10): RentCast/ATTOM don't carry the listing
description text; Redfin's `listingRemarks` field does, land/lot listings
included, and Doorify MLS (the Triangle's one MLS) requires a broker-of-record
agreement to reach directly. The Apify actor `automation-lab/redfin-scraper`
is the fastest, cheapest, license-free route to remarks -- ship it as v1,
retire it in favor of `reapi.py` (or a Doorify/Bridge RESO feed) once that
legit path clears (PRD §8.3).

ToS posture: Redfin's ToS prohibits scraping regardless of technical
feasibility. hiQ / Meta v. Bright Data keep logged-out scraping out of
CFAA-criminal territory but NOT out of breach-of-contract. Keep this adapter
at personal/research volume (a few hundred listings/run, ~$1-3/1k via Apify);
get counsel review before any non-personal-use volume. The adapter-isolation
in `sources/` means swapping this out later is a one-file change, not a
platform rewrite.

Two steps, kept separate so `normalize()` is unit-testable without network:
  1. `search()` calls the Apify actor's run-sync-get-dataset-items endpoint.
  2. `normalize(raw)` maps one raw Apify record to the `listings` table schema
     (see `listings/sources/__init__.py`). Pure function, no I/O -- tested
     against `tests/fixtures/redfin_sample.json`.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

_ACTOR = "automation-lab~redfin-scraper"
_RUN_SYNC_URL = "https://api.apify.com/v2/acts/%s/run-sync-get-dataset-items" % _ACTOR


def _cfg(key: str, default: str = "") -> str:
    val = os.environ.get(key, "")
    if val:
        return val
    try:
        from docrag import settings as _s
        return _s.get(key, default) or default
    except Exception:  # noqa: BLE001
        return default


def _token() -> str:
    tok = _cfg("APIFY_TOKEN", "")
    if not tok:
        raise RuntimeError(
            "APIFY_TOKEN not set. Create an Apify account, grab an API token from "
            "https://console.apify.com/account/integrations, and set APIFY_TOKEN "
            "in .env (or the environment). Needed to run the "
            "automation-lab/redfin-scraper actor."
        )
    return tok


def _http_post_json(url: str, payload: dict) -> list:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def search(geo: str, filters: dict) -> list[dict]:
    """Run the Apify Redfin actor for a geography (a Redfin-searchable region
    name, e.g. "Durham, NC" or "Alamance County, NC") and normalize the
    results. ``filters`` is passed through into the actor input (price range,
    property type, etc -- see the actor's input schema on Apify); land/lot
    listings should set something like {"propertyType": ["land"]}."""
    token = _token()
    actor_input = {"regionName": geo, **(filters or {})}
    url = _RUN_SYNC_URL + "?" + urllib.parse.urlencode({"token": token})
    raw_items = _http_post_json(url, actor_input)
    return [normalize(item) for item in raw_items]


def _get(d: dict, *path, default=None):
    cur = d
    for key in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
    return cur if cur is not None else default


def _scalar(raw: dict, key: str):
    """Coerce a Redfin numeric field that may arrive either as a bare scalar or
    as a ``{"value": <n>}`` wrapper (and may be an explicit null in either form)
    into a scalar-or-None. Never returns the wrapper dict -- returning the parent
    dict here was a real bug: land listings routinely carry ``{"value": null}``
    for sqFt/lotSize, and a dict then crashes the sqlite bind + the acres math."""
    v = raw.get(key)
    if isinstance(v, dict):
        v = v.get("value")
    return v


def normalize(raw: dict) -> dict:
    """Map one raw automation-lab/redfin-scraper record to the `listings`
    table schema (see `listings/sources/__init__.py`). Pure function -- no
    network. Any field the actor doesn't supply is left None; never fabricate
    a value (parcel_pin/planning_jurisdiction are NOT Redfin fields -- those
    get resolved later via `propkb.acquire`)."""
    street = _get(raw, "streetLine", "line", default="") or raw.get("address", "")
    city = raw.get("city", "")
    state = raw.get("state", "")
    zip_code = raw.get("zip", "")
    address = ", ".join(p for p in (street, city, state) if p)
    if zip_code:
        address = (address + " " + zip_code).strip()

    lot_sqft = _scalar(raw, "lotSize")

    source_id = str(raw.get("listingId") or raw.get("mlsId") or raw.get("url") or "")
    if not source_id:
        raise ValueError(
            "redfin record has no listingId/mlsId/url to key on; refusing to "
            "ingest (would collide on UNIQUE(source, '')). raw=%.200s" % json.dumps(raw)
        )

    return {
        "source": "redfin",
        "source_id": source_id,
        "mls_number": raw.get("mlsId"),
        "status": raw.get("status"),
        "address": address,
        "lat": _get(raw, "latLong", "latitude"),
        "lon": _get(raw, "latLong", "longitude"),
        "parcel_pin": None,             # resolved later via propkb.acquire
        "planning_jurisdiction": None,  # resolved later via propkb.acquire
        "price": _scalar(raw, "price"),
        "list_date": raw.get("listedDate"),
        "dom": raw.get("dom"),
        "beds": raw.get("beds"),
        "baths": raw.get("baths"),
        "sqft": _scalar(raw, "sqFt"),
        "lot_sqft": lot_sqft,
        "lot_acres": (lot_sqft / 43560.0) if lot_sqft else None,
        "property_type": raw.get("propertyType"),
        "year_built": raw.get("yearBuilt"),
        "description_text": raw.get("listingRemarks", "") or "",
        "list_agent_name": raw.get("agentName"),
        "list_agent_id": raw.get("agentId"),
        "brokerage": raw.get("brokerName"),
        "photos_json": raw.get("photos") or [],
        "raw_json": json.dumps(raw, ensure_ascii=False),
    }
