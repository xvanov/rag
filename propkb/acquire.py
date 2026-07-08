"""propkb.acquire -- pull structured property data (REST-first).

Durham County: parcel/zoning/flood come as open ArcGIS REST JSON (Phase-0 spike,
verified 2026-07-07) -- no browser, no auth, no key. This module queries those
endpoints (from propkb.registry), files the raw JSON under sources/gis/, and writes
a human-readable .md summary that gets indexed. Playwright/tax-card extras are a
later, optional pass.

Public API:
    durham(slug, pin=None, address=None) -> dict summary
"""

from __future__ import annotations

import json
import ssl
import urllib.parse
import urllib.request
from datetime import date

from . import registry, store

_TIMEOUT = 30
_UA = {"User-Agent": "propkb/0.1 (property research)"}


def _http_json(url: str, params: dict, method: str = "GET") -> dict:
    """Fetch ArcGIS query JSON. Falls back to an unverified TLS context on cert
    errors (public read-only gov endpoints); never sends credentials."""
    data = urllib.parse.urlencode(params).encode()
    if method == "GET":
        req = urllib.request.Request(url + "?" + data.decode(), headers=_UA)
    else:
        req = urllib.request.Request(url, data=data, headers=_UA)
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except (ssl.SSLError, urllib.error.URLError) as e:
        if isinstance(e, urllib.error.URLError) and not isinstance(getattr(e, "reason", None), ssl.SSLError):
            raise
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen(req, timeout=_TIMEOUT, context=ctx) as r:
            return json.loads(r.read().decode("utf-8", "replace"))


def _endpoint(data_type: str) -> str:
    rows = registry.query(jurisdiction="durham-county-nc", data_type=data_type)
    if not rows:
        registry.seed()
        rows = registry.query(jurisdiction="durham-county-nc", data_type=data_type)
    return rows[0]["url"] if rows else ""


def _query_parcel(pin: str | None, address: str | None) -> dict:
    url = _endpoint("parcels") + "/query"
    if pin:
        where = "PIN='%s'" % pin.replace("'", "")
    elif address:
        where = "UPPER(LOCATION_ADDR) LIKE UPPER('%s%%')" % address.replace("'", "").split(",")[0].strip()
    else:
        raise ValueError("need pin or address")
    resp = _http_json(url, {"where": where, "outFields": "*",
                            "returnGeometry": "true", "outSR": "4326", "f": "json"})
    feats = resp.get("features") or []
    if not feats:
        return {"error": "no parcel match", "where": where}
    return {"attributes": feats[0].get("attributes", {}),
            "geometry": feats[0].get("geometry"),
            "sr": (resp.get("spatialReference") or {}).get("wkid", 4326)}


def _query_spatial(data_type: str, geometry: dict, sr: int) -> list[dict]:
    url = _endpoint(data_type) + "/query"
    resp = _http_json(url, {
        "geometry": json.dumps({"rings": geometry.get("rings", []),
                                "spatialReference": {"wkid": sr}}),
        "geometryType": "esriGeometryPolygon", "inSR": str(sr),
        "spatialRel": "esriSpatialRelIntersects", "outFields": "*",
        "returnGeometry": "false", "f": "json"}, method="POST")
    return [f.get("attributes", {}) for f in (resp.get("features") or [])]


def durham(slug: str, pin: str | None = None, address: str | None = None) -> dict:
    """Pull Durham parcel + zoning + flood for a property; file raw JSON + a summary."""
    registry.seed()
    out: dict = {"slug": slug, "pulled": date.today().isoformat(), "gaps": []}

    parcel = _query_parcel(pin, address)
    if parcel.get("error"):
        out["error"] = parcel["error"]
        return out
    attrs = parcel["attributes"]
    geom, sr = parcel.get("geometry"), parcel.get("sr", 4326)
    out["parcel"] = attrs

    # spatial layers (best-effort; record gaps, never crash)
    zoning, flood = [], []
    if geom:
        for dt, sink in (("zoning", "zoning"), ("flood", "flood")):
            try:
                res = _query_spatial(dt, geom, sr)
                out[sink] = res
                (zoning if dt == "zoning" else flood).extend(res)
            except Exception as e:  # noqa: BLE001
                out["gaps"].append("%s: %s" % (dt, e))
    else:
        out["gaps"].append("no parcel geometry -> skipped zoning/flood spatial queries")

    # file raw json + a readable summary (both indexable)
    raw_name = store.namespace(slug, "durham-gis-raw.json")
    store.write_text(slug, json.dumps(out, indent=2, ensure_ascii=False), raw_name, kind="gis")
    out["files"] = [store.paths(slug)["gis"] + "/" + raw_name]

    md = _summary_md(slug, attrs, zoning, flood, out["gaps"])
    md_name = store.namespace(slug, "durham-gis.md")
    store.write_text(slug, md, md_name, kind="gis")
    out["files"].append(store.paths(slug)["gis"] + "/" + md_name)
    return out


def _g(attrs: dict, *keys, default="?"):
    for k in keys:
        if k in attrs and attrs[k] not in (None, ""):
            return attrs[k]
    return default


def _summary_md(slug, a, zoning, flood, gaps) -> str:
    zone_vals = ", ".join(sorted({str(z.get("ZONING") or z.get("ZONE") or z.get("ZONECODE")
                                      or z.get("Zone") or "?") for z in zoning})) or "(none returned)"
    flood_vals = ", ".join(sorted({str(f.get("ZoneCode") or f.get("ZONECODE") or "?")
                                   for f in flood})) or "(none returned)"
    floodway = any(str(f.get("ZoneCode") or f.get("ZONECODE") or "").upper() == "AEFW" for f in flood)
    return f"""# Durham GIS pull -- {slug}

_Auto-pulled via ArcGIS REST ({date.today().isoformat()}). Verify before relying._

## Parcel (Durham Tax / AGOL)
- **PIN:** {_g(a, 'PIN')} · **REID:** {_g(a, 'REID')}
- **Address:** {_g(a, 'LOCATION_ADDR')} {_g(a, 'ZIP', default='')}
- **Owner:** {_g(a, 'PROPERTY_OWNER', 'OWNER')}
- **Zoning (parcel field):** {_g(a, 'ZONING')}
- **Acreage:** {_g(a, 'ACREAGE', 'CALCULATED_ACRES', 'DEEDED_ACRES')}
- **Land class:** {_g(a, 'LAND_CLASS')}
- **Deed:** Book {_g(a, 'DEED_BOOK')} / Page {_g(a, 'DEED_PAGE')} ({_g(a, 'DEED_DATE', default='')})
- **Assessed:** land ${_g(a, 'TOTAL_LAND_VALUE_ASSESSED', default='?')} · total ${_g(a, 'TOTAL_PROP_VALUE', 'TOTAL_PROPERTY_VALUE', default='?')}

## Zoning overlay (spatial intersect)
- {zone_vals}

## Flood (Durham Flood_Zones_Development; AEFW = floodway)
- Zones: {flood_vals}
- **Floodway present:** {'YES' if floodway else 'no/none returned'}

## Gaps / to verify
{chr(10).join('- ' + g for g in gaps) if gaps else '- (none)'}
- Tax-card extras (photo/sketch/permit history/>3yr sales) NOT pulled -- Spatialest/CAMA, later pass.
"""
