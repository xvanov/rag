"""propkb.acquire -- pull structured property data (REST-first).

Durham + Wake County: parcel/zoning/flood/address come as open ArcGIS REST JSON
-- no browser, no auth, no key. This module queries those endpoints (from
propkb.registry), files the raw JSON under sources/gis/, and writes a
human-readable .md summary that gets indexed.

Multi-jurisdiction: each supported LOCATION maps (JURISDICTIONS below) to the
registry jurisdictions for its parcels/flood/address layers (shared at county
level) and its zoning layer (per-municipality in Wake), plus the parcel PIN/
address field names (Durham uses PIN/LOCATION_ADDR; Wake uses PIN_NUM/
SITE_ADDRESS). Field lookups in the summary are alias-tolerant (_g/_zval), so one
code path serves both counties.

Public API:
    acquire(slug, location="durham-nc", pin=None, address=None) -> dict summary
    durham(slug, pin=None, address=None) -> dict summary   # back-compat wrapper
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


# location key -> where to pull each layer + parcel field names.
# `county` jurisdiction serves parcels/flood/address (shared); `zoning` is the
# jurisdiction whose registry row holds the right zoning layer (per-municipality
# in Wake). Durham keeps everything under one jurisdiction.
JURISDICTIONS = {
    "durham-nc": {"label": "Durham", "county": "durham-county-nc",
                  "zoning": "durham-county-nc", "pin_field": "PIN",
                  "addr_field": "LOCATION_ADDR"},
}
# Wake: county-level parcels/flood/address under wake-county-nc; zoning per-town.
_WAKE_PIN, _WAKE_ADDR = "PIN_NUM", "SITE_ADDRESS"
for _key, _label in (
    ("wake-county-nc", "Wake County (unincorporated)"),
    ("raleigh-nc", "Raleigh"), ("cary-nc", "Cary"), ("garner-nc", "Garner"),
    ("wake-forest-nc", "Wake Forest"), ("apex-nc", "Apex"),
    ("holly-springs-nc", "Holly Springs"), ("morrisville-nc", "Morrisville"),
    ("fuquay-varina-nc", "Fuquay-Varina"), ("knightdale-nc", "Knightdale"),
    ("wendell-nc", "Wendell"), ("zebulon-nc", "Zebulon"),
    ("rolesville-nc", "Rolesville"),
):
    JURISDICTIONS[_key] = {"label": _label, "county": "wake-county-nc",
                           "zoning": _key, "pin_field": _WAKE_PIN,
                           "addr_field": _WAKE_ADDR}

DEFAULT_LOCATION = "durham-nc"


def _cfg(location: str | None) -> dict:
    return JURISDICTIONS.get((location or "").strip().lower()) \
        or JURISDICTIONS[DEFAULT_LOCATION]


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


def _endpoint(jurisdiction: str, data_type: str) -> str:
    rows = registry.query(jurisdiction=jurisdiction, data_type=data_type)
    if not rows:
        registry.seed()
        rows = registry.query(jurisdiction=jurisdiction, data_type=data_type)
    return rows[0]["url"] if rows else ""


def _query_parcel(cfg: dict, pin: str | None, address: str | None) -> dict:
    url = _endpoint(cfg["county"], "parcels") + "/query"
    if not url or url == "/query":
        return {"error": "no parcels endpoint for %s" % cfg["county"]}
    if pin:
        where = "%s='%s'" % (cfg["pin_field"], pin.replace("'", ""))
    elif address:
        num = address.replace("'", "").split(",")[0].strip()
        where = "UPPER(%s) LIKE UPPER('%s%%')" % (cfg["addr_field"], num)
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


def _query_spatial(jurisdiction: str, data_type: str, geometry: dict, sr: int) -> list[dict]:
    url = _endpoint(jurisdiction, data_type)
    if not url:
        raise RuntimeError("no %s endpoint for %s" % (data_type, jurisdiction))
    resp = _http_json(url + "/query", {
        "geometry": json.dumps({"rings": geometry.get("rings", []),
                                "spatialReference": {"wkid": sr}}),
        "geometryType": "esriGeometryPolygon", "inSR": str(sr),
        "spatialRel": "esriSpatialRelIntersects", "outFields": "*",
        "returnGeometry": "false", "f": "json"}, method="POST")
    return [f.get("attributes", {}) for f in (resp.get("features") or [])]


def acquire(slug: str, location: str = DEFAULT_LOCATION,
            pin: str | None = None, address: str | None = None) -> dict:
    """Pull parcel + zoning + flood for a property in `location`; file raw JSON +
    a summary. `location` is a docrag location key (durham-nc, wake-county-nc,
    raleigh-nc, cary-nc, ...)."""
    registry.seed()
    cfg = _cfg(location)
    out: dict = {"slug": slug, "location": location, "label": cfg["label"],
                 "pulled": date.today().isoformat(), "gaps": []}

    parcel = _query_parcel(cfg, pin, address)
    if parcel.get("error"):
        out["error"] = parcel["error"]
        return out
    attrs = parcel["attributes"]
    geom, sr = parcel.get("geometry"), parcel.get("sr", 4326)
    out["parcel"] = attrs

    # spatial layers (best-effort; record gaps, never crash). Zoning comes from
    # the location's own jurisdiction; flood is county-level.
    zoning, flood = [], []
    if geom:
        for jur, dt, sink in ((cfg["zoning"], "zoning", "zoning"),
                              (cfg["county"], "flood", "flood")):
            try:
                res = _query_spatial(jur, dt, geom, sr)
                out[sink] = res
                (zoning if dt == "zoning" else flood).extend(res)
            except Exception as e:  # noqa: BLE001
                out["gaps"].append("%s: %s" % (dt, e))
    else:
        out["gaps"].append("no parcel geometry -> skipped zoning/flood spatial queries")

    # file raw json + a readable summary (both indexable)
    raw_name = store.namespace(slug, "gis-raw.json")
    store.write_text(slug, json.dumps(out, indent=2, ensure_ascii=False), raw_name, kind="gis")
    out["files"] = [store.paths(slug)["gis"] + "/" + raw_name]

    md = _summary_md(slug, cfg, attrs, zoning, flood, out["gaps"])
    md_name = store.namespace(slug, "gis.md")
    store.write_text(slug, md, md_name, kind="gis")
    out["files"].append(store.paths(slug)["gis"] + "/" + md_name)
    return out


def durham(slug: str, pin: str | None = None, address: str | None = None) -> dict:
    """Back-compat wrapper: acquire() with the Durham location."""
    return acquire(slug, "durham-nc", pin=pin, address=address)


def _g(attrs: dict, *keys, default="?"):
    for k in keys:
        if k in attrs and attrs[k] not in (None, ""):
            return attrs[k]
    return default


def _summary_md(slug, cfg, a, zoning, flood, gaps) -> str:
    def _zval(z):
        for k in ("ZONE_CODE", "UDO_LABEL", "LABEL", "ZONING", "ZONE", "ZONECODE",
                  "Zone", "ZONE_CLASS", "ZONE_GEN", "ZONE_TYPE", "DISTRICT",
                  "ZONE_DESC", "CLASS"):   # CLASS = Wake countywide zoning field
            if z.get(k) not in (None, ""):
                return str(z[k])
        return "?"
    zone_vals = ", ".join(sorted({_zval(z) for z in zoning})) or "(none returned)"
    # flood zone code: Durham "ZoneCode"; FEMA NFHL "FLD_ZONE"
    flood_vals = ", ".join(sorted({str(f.get("ZoneCode") or f.get("ZONECODE")
                                       or f.get("FLD_ZONE") or "?") for f in flood})) \
        or "(none returned)"
    floodway = any(str(f.get("ZoneCode") or f.get("ZONECODE") or "").upper() == "AEFW"
                   or "FLOODWAY" in str(f.get("ZONE_SUBTY") or "").upper() for f in flood)
    return f"""# {cfg['label']} GIS pull -- {slug}

_Auto-pulled via ArcGIS REST ({date.today().isoformat()}). Verify before relying._

## Parcel
- **PIN:** {_g(a, 'PIN', 'PIN_NUM')} · **REID:** {_g(a, 'REID')}
- **Address:** {_g(a, 'LOCATION_ADDR', 'SITE_ADDRESS')} {_g(a, 'ZIP', 'ZIPNUM', default='')}
- **Owner:** {_g(a, 'PROPERTY_OWNER', 'OWNER')}
- **Zoning (parcel field):** {_g(a, 'ZONING', 'CLASS')} · **Planning jurisdiction:** {_g(a, 'PLANNING_JURISDICTION', default='-')}
- **Acreage:** {_g(a, 'ACREAGE', 'DEED_ACRES', 'CALCULATED_ACRES', 'DEEDED_ACRES', 'CALC_AREA')}
- **Land class:** {_g(a, 'LAND_CLASS', 'LAND_CLASS_DECODE', 'TYPE_USE_DECODE')}
- **Deed:** Book {_g(a, 'DEED_BOOK')} / Page {_g(a, 'DEED_PAGE')} ({_g(a, 'DEED_DATE', default='')})
- **Assessed:** land ${_g(a, 'TOTAL_LAND_VALUE_ASSESSED', 'LAND_VAL', default='?')} · total ${_g(a, 'TOTAL_PROP_VALUE', 'TOTAL_PROPERTY_VALUE', 'TOTAL_VALUE_ASSD', default='?')}

## Zoning overlay (spatial intersect)
- {zone_vals}

## Flood (AEFW / FLOODWAY = floodway)
- Zones: {flood_vals}
- **Floodway present:** {'YES' if floodway else 'no/none returned'}

## Gaps / to verify
{chr(10).join('- ' + g for g in gaps) if gaps else '- (none)'}
- Tax-card extras (photo/sketch/permit history/>3yr sales) NOT pulled -- portal/CAMA, later pass.
"""
