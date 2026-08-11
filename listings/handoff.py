"""listings.handoff -- promote a shortlisted Scout listing into a propkb
property (PRD sec.4 use case 7).

Scout finds/ranks candidates across the whole market; propkb / the
``/property-research`` skill does the deep, cold-start dive on the ONE
address the user actually chose. This module is the seam between them: it
prepares the propkb knowledge base (scaffold + a parcel/zoning/flood seed
pull + a note carrying the Scout listing context) and hands back the command
to run next. It does NOT run ``/property-research`` itself -- that's a
Claude-orchestrated skill, not a plain function call.

REUSE, DON'T REBUILD: scaffolding is ``propkb.store.create`` (idempotent --
never clobbers an existing property dir); the parcel/zoning/flood seed pull
is ``propkb.acquire.acquire`` (the same ArcGIS REST layer feasibility.py
uses, this time filed under the property KB the normal way); jurisdiction
mapping is ``feasibility._map_planning_jurisdiction`` (the same Wake
short-code / name -> docrag location table entitlement.py and feasibility.py
already rely on). The only new logic here is the address -> slug derivation
and the sources/web/ note.

Best-effort: a failed acquire() pull (network, no parcel match) records a gap
and ``promote()`` still returns the prepared slug/dir -- it only raises when
the ``listing_id`` itself doesn't exist (a caller bug, not a data problem).

Public API:
    promote(conn, listing_id: int, slug: str | None = None) -> dict
"""

from __future__ import annotations

import json
import os
import re
from datetime import date

from propkb import acquire as _pk_acquire
from propkb import store as _pk_store

from . import feasibility, store


def _slugify_address(address: str | None) -> str:
    """Kebab-case slug from a listing address, mirroring existing property
    slugs (e.g. "212 Pine Dr, Durham, NC 27713" -> "212-pine-dr-durham"):
    street + city kept, state/ZIP dropped."""
    parts = [p.strip() for p in (address or "").split(",") if p.strip()]
    keep = parts[:2] if len(parts) >= 2 else parts
    text = "-".join(keep) if keep else "listing"
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text or "listing"


def _fmt(v, default="?"):
    return default if v in (None, "") else v


def _buildability_note(conn, listing_id: int) -> str:
    """Best-effort buildability summary from Scout's own enrichment row, if
    ``enrich_listing()`` has already been run for this listing -- gives
    /property-research a head start instead of starting from zero."""
    enr = store.get_enrichment(conn, listing_id)
    if not enr:
        return ("_(not yet enriched -- run `listings enrich --id %d` for a "
                "buildability head start before /property-research)_"
                % listing_id)
    lines = []
    if enr.get("zoning_code"):
        lines.append("- **Zoning:** %s" % enr["zoning_code"])
    if enr.get("flood_zone"):
        lines.append("- **Flood zone:** %s%s" % (
            enr["flood_zone"], " (in floodway)" if enr.get("in_floodway") else ""))
    if enr.get("buildability_cited"):
        lines.append("- **Cited provisions:** %s" % enr["buildability_cited"])
    if enr.get("buildability_json"):
        try:
            b = json.loads(enr["buildability_json"])
        except (TypeError, ValueError):
            b = {}
        if b.get("buildable_summary"):
            lines.append("- **Summary:** %s" % b["buildable_summary"])
    return "\n".join(lines) if lines else "_(enrichment row present but empty)_"


def _web_note(listing: dict, conn, listing_id: int) -> str:
    price = listing.get("price")
    return """# Scout listing -- {address}

_Promoted from Scout ({source}/{source_id}) on {today}. Verify before relying._

## Listing
- **Status:** {status} - **Price:** {price} - **DOM:** {dom}
- **Beds/Baths/Sqft:** {beds}/{baths}/{sqft} - **Lot:** {lot_acres} ac ({lot_sqft} sf)
- **Property type:** {property_type} - **Year built:** {year_built}
- **MLS #:** {mls} - **Agent:** {agent} ({brokerage})

## Public remarks
{remarks}

## Buildability (Scout enrichment)
{buildability}

> Research, not legal/engineering/investment advice. Verify locally.
""".format(
        address=_fmt(listing.get("address")),
        source=_fmt(listing.get("source")), source_id=_fmt(listing.get("source_id")),
        today=date.today().isoformat(),
        status=_fmt(listing.get("status")),
        price=("$%s" % price) if price is not None else "?",
        dom=_fmt(listing.get("dom")),
        beds=_fmt(listing.get("beds")), baths=_fmt(listing.get("baths")),
        sqft=_fmt(listing.get("sqft")),
        lot_acres=_fmt(listing.get("lot_acres")), lot_sqft=_fmt(listing.get("lot_sqft")),
        property_type=_fmt(listing.get("property_type")),
        year_built=_fmt(listing.get("year_built")),
        mls=_fmt(listing.get("mls_number")),
        agent=_fmt(listing.get("list_agent_name")), brokerage=_fmt(listing.get("brokerage")),
        remarks=_fmt(listing.get("description_text"), "(no remarks on file)"),
        buildability=_buildability_note(conn, listing_id),
    )


def promote(conn, listing_id: int, slug: str | None = None) -> dict:
    """Promote a shortlisted Scout listing into a full propkb property.

    Scaffolds ``properties/<slug>/`` (``propkb.store.create`` -- idempotent),
    seeds it with a parcel/zoning/flood pull (``propkb.acquire.acquire`` --
    best-effort, gap recorded on failure), and writes a
    ``sources/web/<slug>__scout-listing.md`` note with the Scout listing
    context so ``/property-research`` starts with the shortlist rationale in
    hand instead of from zero. Never runs ``/property-research`` itself.
    """
    listing = store.get_listing(conn, listing_id)
    if listing is None:
        raise ValueError("no listing with id %r" % listing_id)

    slug = (slug or _slugify_address(listing.get("address"))).strip().lower()
    gaps: list[str] = []

    already_existed = os.path.isdir(_pk_store.prop_dir(slug))
    p = _pk_store.create(slug, listing.get("address") or slug)
    created = not already_existed

    location = feasibility._map_planning_jurisdiction(
        listing.get("planning_jurisdiction")) or feasibility.DEFAULT_LOCATION
    try:
        acquired = _pk_acquire.acquire(
            slug, location=location, pin=listing.get("parcel_pin"),
            address=listing.get("address"))
        if acquired.get("error"):
            gaps.append("acquire: %s" % acquired["error"])
    except Exception as e:  # noqa: BLE001 -- best-effort, never crash the handoff
        acquired = {"error": str(e)}
        gaps.append("acquire: %s" % e)

    note_text = _web_note(listing, conn, listing_id)
    note_name = _pk_store.namespace(slug, "scout-listing.md")
    note_path = _pk_store.write_text(slug, note_text, note_name, kind="web")

    return {
        "slug": slug, "dir": p["dir"], "created": created,
        "acquired": acquired, "web_note": note_path, "gaps": gaps,
        "next": "/property-research " + slug,
    }
