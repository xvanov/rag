"""propkb.registry -- canonical source registry (grows across properties).

A small JSON store of WHERE to get data for a jurisdiction, so /property-research
reuses a discovered portal/REST endpoint instead of rediscovering it. Seeded from
the Phase-0 Durham spike (verified 2026-07-07). Human-editable + git-diffable.

CLI (wired in __main__.py):
    python -m propkb sources list
    python -m propkb sources query --jurisdiction durham-county-nc --type parcels
    python -m propkb sources add --jurisdiction ... --type ... --method rest --url ...
"""

from __future__ import annotations

import json
import os
from typing import Optional

from . import settings

METHODS = ("rest", "browser", "scrape", "manual")


def registry_path() -> str:
    root = settings.properties_root()
    os.makedirs(root, exist_ok=True)
    return os.path.join(root, "sources_registry.json")


# Verified live 2026-07-07 (Phase-0 spike) unless noted. See IMPLEMENTATION-PLAN.md.
_SEED = [
    {"jurisdiction": "durham-county-nc", "data_type": "parcels", "method": "rest",
     "name": "Durham Parcels (AGOL hosted, primary)",
     "url": "https://services2.arcgis.com/G5vR3cOjh6g2Ed8E/arcgis/rest/services/Parcels_NEW/FeatureServer/0",
     "notes": "query?where=PIN='<pin>'&outFields=*&f=json ; or where=LOCATION_ADDR LIKE '<addr>%'. "
              "Has PIN,REID,ZONING,ACREAGE,PROPERTY_OWNER,DEED_BOOK/PAGE,TOTAL_*_VALUE_ASSESSED."},
    {"jurisdiction": "durham-county-nc", "data_type": "parcels_onprem", "method": "rest",
     "name": "Durham Parcels (on-prem mirror)",
     "url": "https://webgis.durhamnc.gov/server/rest/services/PublicServices/Property/MapServer/4",
     "notes": "same tax dataset as hosted; fallback."},
    {"jurisdiction": "durham-county-nc", "data_type": "zoning", "method": "rest",
     "name": "Durham Zoning polygons",
     "url": "https://webgis.durhamnc.gov/server/rest/services/PublicServices/Planning/MapServer/12",
     "notes": "polygon layer; spatial-intersect with parcel geometry. Planning svc also has watershed(3), airport-overlay(6), historic(1,2,10,11), UGB(24)."},
    {"jurisdiction": "durham-county-nc", "data_type": "flood", "method": "rest",
     "name": "Durham Flood Zones (development)",
     "url": "https://webgis.durhamnc.gov/server/rest/services/PublicServices/Flood_Zones_Development/MapServer/0",
     "notes": "ZoneCode: AEFW=floodway, AE/AO/A=SFHA, X*=outside. Spatial-intersect with parcel."},
    {"jurisdiction": "durham-county-nc", "data_type": "address_points", "method": "rest",
     "name": "Durham Address Points",
     "url": "https://webgis.durhamnc.gov/server/rest/services/PublicServices/Property/MapServer/0",
     "notes": "layer 2 = Tax Districts."},
    {"jurisdiction": "durham-county-nc", "data_type": "tax_card", "method": "browser",
     "name": "Durham Tax / Property Record (Spatialest)",
     "url": "https://property.spatialest.com/nc/durham-tax/",
     "notes": "JS app, no public API -> Playwright for photo/sketch/permit-history/>3yr-sales. "
              "Core assessment data already in REST parcels. Try CAMA first: https://taxcama.dconc.gov/camapwa/"},
    {"jurisdiction": "durham-county-nc", "data_type": "deeds", "method": "scrape",
     "name": "Durham Register of Deeds",
     "url": "https://rodweb.dconc.gov/web/",
     "notes": "deeds/plats by grantor/grantee/book-page; pair with parcel DEED_BOOK/PAGE. Disclaimer gate."},
    {"jurisdiction": "nc-state", "data_type": "solid_waste", "method": "manual",
     "name": "NCDEQ Solid Waste Section",
     "url": "https://www.deq.nc.gov/about/divisions/waste-management/solid-waste-section",
     "notes": "open-dump / landfill records; contact Permitting Branch Head."},
    {"jurisdiction": "nc-state", "data_type": "recognized_env_consultants", "method": "manual",
     "name": "NCDEQ Recognized Environmental Consultants (REC) list",
     "url": "https://www.deq.nc.gov/waste-management/dwm/sf/ihs/rec-program/approved-recs-july-2025-pdf/download",
     "notes": "approved consultants for waste-boundary + soil/GW/gas assessment."},
    {"jurisdiction": "federal", "data_type": "wetlands", "method": "rest",
     "name": "USFWS National Wetlands Inventory",
     "url": "https://www.fws.gov/wetlands/data/Mapper.html",
     "notes": "NWI wetlands; also an ArcGIS service available."},
    {"jurisdiction": "federal", "data_type": "flood_fema", "method": "rest",
     "name": "FEMA National Flood Hazard Layer",
     "url": "https://hazards.fema.gov/femaportal/wps/portal/NFHLWMS",
     "notes": "authoritative FEMA flood; Durham flood layer usually sufficient locally."},
]


def _load() -> list[dict]:
    p = registry_path()
    if not os.path.isfile(p):
        return []
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def _save(rows: list[dict]) -> None:
    with open(registry_path(), "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, ensure_ascii=False)


def _key(r: dict) -> tuple:
    return (r.get("jurisdiction", "").lower(), r.get("data_type", "").lower())


def seed() -> int:
    """Write seed entries (idempotent by jurisdiction+data_type). Returns total rows."""
    rows = _load()
    have = {_key(r) for r in rows}
    for s in _SEED:
        if _key(s) not in have:
            rows.append(s)
    _save(rows)
    return len(rows)


def add(**fields) -> None:
    rows = _load()
    row = {k: fields.get(k, "") for k in ("jurisdiction", "data_type", "name", "method", "url", "notes")}
    rows = [r for r in rows if _key(r) != _key(row)]  # replace same key
    rows.append(row)
    _save(rows)


def query(jurisdiction: Optional[str] = None, data_type: Optional[str] = None) -> list[dict]:
    rows = _load()
    if jurisdiction:
        rows = [r for r in rows if r.get("jurisdiction", "").lower() == jurisdiction.lower()]
    if data_type:
        rows = [r for r in rows if r.get("data_type", "").lower() == data_type.lower()]
    return rows


def list_all() -> list[dict]:
    return _load()
