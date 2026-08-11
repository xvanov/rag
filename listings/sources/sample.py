"""listings.sources.sample -- canned, realistic Triangle listings.

The offline test spine: no network, no key, deterministic. Exercises the
whole pipeline (ingest -> search -> enrich -> score) without any external API,
and is the fixture the semantic-search PoC (E2) is validated against -- the
descriptions deliberately carry the exact free-text phrases the PRD cares
about ("existing septic permit and well", "flag lot", "ADU potential",
"as-is", "seller financing").

Mix: vacant land, single-family, and a 2-4 unit, across Durham/Wake/Alamance
so downstream jurisdiction-selection logic has something to exercise.
"""

from __future__ import annotations

_LISTINGS = [
    {
        "source": "sample",
        "source_id": "SAMPLE-0001",
        "mls_number": "TMLS0000001",
        "status": "active",
        "address": "0 Guess Rd, Durham, NC 27712",
        "lat": 36.0743,
        "lon": -78.9203,
        "parcel_pin": "0812345678",
        "planning_jurisdiction": "durham-nc",
        "price": 89000,
        "list_date": "2026-06-01",
        "dom": 12,
        "beds": None,
        "baths": None,
        "sqft": None,
        "lot_sqft": 43560,
        "lot_acres": 1.0,
        "property_type": "land",
        "year_built": None,
        "description_text": (
            "Buildable 1-acre lot just outside city limits. Existing septic permit "
            "and well already on file with the county -- huge cost savings for the "
            "next owner. Irregular flag lot with a shared gravel drive; ADU potential "
            "if the septic can be sized up. Seller financing considered for the right offer."
        ),
        "list_agent_name": "Dana Whitfield",
        "list_agent_id": "AGT-1001",
        "brokerage": "Bull City Land Co.",
        "photos_json": [],
        "raw_json": "{}",
    },
    {
        "source": "sample",
        "source_id": "SAMPLE-0002",
        "mls_number": "TMLS0000002",
        "status": "active",
        "address": "212 Pine Dr, Durham, NC 27713",
        "lat": 35.9312,
        "lon": -78.8654,
        "parcel_pin": "0707680500",
        "planning_jurisdiction": "durham-nc",
        "price": 245000,
        "list_date": "2026-05-20",
        "dom": 24,
        "beds": 3,
        "baths": 2,
        "sqft": 1420,
        "lot_sqft": 21780,
        "lot_acres": 0.5,
        "property_type": "single_family",
        "year_built": 1978,
        "description_text": (
            "Charming brick ranch on a half-acre, selling as-is -- great bones, needs "
            "cosmetic updates. Detached workshop out back. Seller financing available "
            "for a qualified buyer; motivated, bring offers."
        ),
        "list_agent_name": "Marcus Reyes",
        "list_agent_id": "AGT-1002",
        "brokerage": "Triangle Home Realty",
        "photos_json": [],
        "raw_json": "{}",
    },
    {
        "source": "sample",
        "source_id": "SAMPLE-0003",
        "mls_number": "TMLS0000003",
        "status": "active",
        "address": "5518 Chapel Hill Rd, Raleigh, NC 27607",
        "lat": 35.7847,
        "lon": -78.7183,
        "parcel_pin": "1234567890",
        "planning_jurisdiction": "raleigh-nc",
        "price": 415000,
        "list_date": "2026-06-15",
        "dom": 9,
        "beds": 4,
        "baths": 3,
        "sqft": 2380,
        "lot_sqft": 9583,
        "lot_acres": 0.22,
        "property_type": "multi_family_2_4",
        "year_built": 1962,
        "description_text": (
            "Solid duplex with two long-term tenants and a strong rental history. "
            "Detached garage has ADU potential (verify with the city). Curb cut "
            "already in place. Priced to move."
        ),
        "list_agent_name": "Priya Anand",
        "list_agent_id": "AGT-1003",
        "brokerage": "Oak City Realty Group",
        "photos_json": [],
        "raw_json": "{}",
    },
    {
        "source": "sample",
        "source_id": "SAMPLE-0004",
        "mls_number": "TMLS0000004",
        "status": "active",
        "address": "0 Anthony Rd, Graham, NC 27253",
        "lat": 36.0684,
        "lon": -79.4362,
        "parcel_pin": "9988776655",
        "planning_jurisdiction": "alamance-county-nc",
        "price": 62000,
        "list_date": "2026-06-28",
        "dom": 2,
        "beds": None,
        "baths": None,
        "sqft": None,
        "lot_sqft": 435600,
        "lot_acres": 10.0,
        "property_type": "land",
        "year_built": None,
        "description_text": (
            "10-acre flag lot down a recorded easement, mostly wooded with a cleared "
            "building envelope. Existing septic permit and well from a prior mobile "
            "home are documented with the county health department. Cash or seller "
            "financing; owner is flexible."
        ),
        "list_agent_name": "Dana Whitfield",
        "list_agent_id": "AGT-1001",
        "brokerage": "Bull City Land Co.",
        "photos_json": [],
        "raw_json": "{}",
    },
    {
        "source": "sample",
        "source_id": "SAMPLE-0005",
        "mls_number": "TMLS0000005",
        "status": "active",
        "address": "310 Kildaire Farm Rd, Cary, NC 27511",
        "lat": 35.7723,
        "lon": -78.7811,
        "parcel_pin": "5544332211",
        "planning_jurisdiction": "cary-nc",
        "price": 389000,
        "list_date": "2026-06-05",
        "dom": 18,
        "beds": 3,
        "baths": 2,
        "sqft": 1650,
        "lot_sqft": 8712,
        "lot_acres": 0.2,
        "property_type": "single_family",
        "year_built": 1985,
        "description_text": (
            "Move-in ready 3BR in a quiet cul-de-sac, updated kitchen, fenced yard. "
            "Sold as-is; sellers relocating and motivated to close quickly."
        ),
        "list_agent_name": "Marcus Reyes",
        "list_agent_id": "AGT-1002",
        "brokerage": "Triangle Home Realty",
        "photos_json": [],
        "raw_json": "{}",
    },
]


class SampleSource:
    """Canned offline ``ListingSource``. ``geo``/``filters`` are accepted for
    protocol conformance but ignored -- it always returns the full canned set."""

    def search(self, geo: str, filters: dict) -> list[dict]:
        return [dict(listing) for listing in _LISTINGS]
