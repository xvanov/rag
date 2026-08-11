"""listings.sources -- the swappable data-adapter layer (PRD §8).

Everything sits behind the ``ListingSource`` protocol so the rest of the
platform never knows or cares where a listing came from -- sources can be
swapped with zero downstream change (redfin scrape today, REAPI or a
Doorify/Bridge RESO feed later, with no change to `store.py`, `score.py`, etc).

``ListingSource.search(geo, filters)`` returns a list of NORMALIZED listing
dicts whose keys match the ``listings`` table columns in `listings/store.py`
(source, source_id, mls_number, status, address, lat, lon, parcel_pin,
planning_jurisdiction, price, list_date, dom, beds, baths, sqft, lot_sqft,
lot_acres, property_type, year_built, description_text, list_agent_name,
list_agent_id, brokerage, photos_json, raw_json). ``description_text`` carries
the free-text remarks/description -- the field the semantic search (E2) is
built on.

Convention for adapters: keep the raw-source-shaped fetch and the
schema-shaped ``normalize(raw: dict) -> dict`` translation as SEPARATE
functions/steps. ``normalize()`` must be a pure function (no network, no I/O)
so it can be unit-tested against a saved fixture. ``search()`` does the
network call, then maps each raw record through ``normalize()``. Any field
the adapter can't supply is simply omitted (store.py tolerates missing keys);
never fabricate a value.
"""

from __future__ import annotations

from typing import Protocol


class ListingSource(Protocol):
    """A data adapter that can search a geography for listings.

    ``geo`` is an adapter-defined geography token (e.g. a county/city name);
    ``filters`` is an adapter-defined dict of source-native search filters
    (price range, property type, etc -- NOT the store.py hard-filter dict).
    Returns normalized listing dicts (see module docstring)."""

    def search(self, geo: str, filters: dict) -> list[dict]:
        ...
