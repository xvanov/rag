"""propkb.contacts -- canonical, cross-property contacts database.

A single SQLite table at ``properties/.contacts.db`` (sibling to ``.index``) that
GROWS across every property. The /property-research + /intake skills consult it
BEFORE re-discovering an agency/vendor, and append new finds back to it, so we
never rediscover the same Durham floodplain reviewer twice.

Design notes:
- Plain stdlib ``sqlite3`` (no extension needed; this is a small relational store).
- Keyed for dedupe on (name, org, jurisdiction) case-folded; ``add()`` upserts and
  fills blanks rather than duplicating.
- ``topic`` is a comma-separated tag list (jurisdiction + topic is how callers query).
- Provenance: every row records ``source`` (property slug + T-NN, or a URL) and a
  ``confidence`` (confirmed|inferred) + ``last_verified`` date.

CLI (wired in __main__.py):
    python -m propkb contacts add    --name "Patrick Eaton" --org "Durham County Env Health" ...
    python -m propkb contacts query  --jurisdiction durham-county-nc --topic well
    python -m propkb contacts merge  --keep 3 --dupes 7,9
    python -m propkb contacts export [--format yaml|json]
    python -m propkb contacts seed                         # load the 1621 Clermont roster
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import date
from typing import Any, Optional

from . import settings

# Controlled vocab (soft -- not enforced, just documented for consistent queries).
CATEGORIES = ("agency", "vendor", "agent", "seller", "other")


def db_path() -> str:
    """Canonical contacts DB path: ``<properties_root>/.contacts.db``."""
    root = settings.properties_root()
    os.makedirs(root, exist_ok=True)
    return os.path.join(root, ".contacts.db")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS contacts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL DEFAULT '',
    org           TEXT NOT NULL DEFAULT '',
    role          TEXT NOT NULL DEFAULT '',
    category      TEXT NOT NULL DEFAULT 'other',
    jurisdiction  TEXT NOT NULL DEFAULT '',
    topic         TEXT NOT NULL DEFAULT '',   -- comma-separated tags
    email         TEXT NOT NULL DEFAULT '',
    phone         TEXT NOT NULL DEFAULT '',
    address       TEXT NOT NULL DEFAULT '',
    source        TEXT NOT NULL DEFAULT '',
    confidence    TEXT NOT NULL DEFAULT 'inferred',
    last_verified TEXT NOT NULL DEFAULT '',
    notes         TEXT NOT NULL DEFAULT '',
    dedupe_key    TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_contacts_dedupe ON contacts(dedupe_key);
CREATE INDEX IF NOT EXISTS ix_contacts_juris ON contacts(jurisdiction);
"""

_FIELDS = ("name", "org", "role", "category", "jurisdiction", "topic",
           "email", "phone", "address", "source", "confidence",
           "last_verified", "notes")


def _conn() -> sqlite3.Connection:
    con = sqlite3.connect(db_path())
    con.row_factory = sqlite3.Row
    con.executescript(_SCHEMA)
    return con


def _norm(s: str) -> str:
    return " ".join((s or "").split()).strip().lower()


def _dedupe_key(name: str, org: str, jurisdiction: str) -> str:
    """Identity for upsert. A contact is 'the same' if name+org+jurisdiction match
    (case/space-insensitive). Name-less agency rows key on org+jurisdiction."""
    return "|".join((_norm(name), _norm(org), _norm(jurisdiction)))


def add(**fields: Any) -> int:
    """Insert or UPSERT a contact. On dedupe-key match, fill blank columns and
    merge topic tags rather than duplicating. Returns the row id."""
    row = {k: (fields.get(k) or "") for k in _FIELDS}
    if not row["last_verified"]:
        row["last_verified"] = date.today().isoformat()
    if row["category"] not in CATEGORIES:
        row["category"] = "other"
    key = _dedupe_key(row["name"], row["org"], row["jurisdiction"])
    with _conn() as con:
        existing = con.execute(
            "SELECT * FROM contacts WHERE dedupe_key=?", (key,)
        ).fetchone()
        if existing is None:
            cols = ", ".join(_FIELDS + ("dedupe_key",))
            ph = ", ".join(["?"] * (len(_FIELDS) + 1))
            cur = con.execute(
                f"INSERT INTO contacts ({cols}) VALUES ({ph})",
                tuple(row[f] for f in _FIELDS) + (key,),
            )
            return int(cur.lastrowid)
        # upsert: fill blanks, union topic tags, prefer 'confirmed'
        merged = dict(existing)
        for f in _FIELDS:
            if f == "topic":
                merged["topic"] = _merge_tags(existing["topic"], row["topic"])
            elif f == "confidence":
                merged["confidence"] = ("confirmed"
                                        if "confirmed" in (existing["confidence"], row["confidence"])
                                        else row["confidence"] or existing["confidence"])
            elif not existing[f] and row[f]:
                merged[f] = row[f]
        con.execute(
            "UPDATE contacts SET " + ", ".join(f"{f}=?" for f in _FIELDS)
            + " WHERE id=?",
            tuple(merged[f] for f in _FIELDS) + (existing["id"],),
        )
        return int(existing["id"])


def _merge_tags(a: str, b: str) -> str:
    tags = []
    for src in (a, b):
        for t in (src or "").split(","):
            t = t.strip()
            if t and t not in tags:
                tags.append(t)
    return ", ".join(tags)


def query(jurisdiction: Optional[str] = None, topic: Optional[str] = None,
          category: Optional[str] = None, text: Optional[str] = None) -> list[dict]:
    """Filter contacts. jurisdiction is exact (case-insensitive); topic matches any
    tag; text is a substring over name/org/role/notes."""
    sql = "SELECT * FROM contacts WHERE 1=1"
    args: list = []
    if jurisdiction:
        sql += " AND lower(jurisdiction)=?"; args.append(_norm(jurisdiction))
    if category:
        sql += " AND category=?"; args.append(category)
    if topic:
        sql += " AND (',' || replace(lower(topic),' ','') || ',') LIKE ?"
        args.append(f"%,{_norm(topic).replace(' ', '')},%")
    if text:
        sql += " AND (lower(name) LIKE ? OR lower(org) LIKE ? OR lower(role) LIKE ? OR lower(notes) LIKE ?)"
        t = f"%{_norm(text)}%"; args += [t, t, t, t]
    sql += " ORDER BY jurisdiction, org, name"
    with _conn() as con:
        return [dict(r) for r in con.execute(sql, args).fetchall()]


def get(contact_id: int) -> Optional[dict]:
    with _conn() as con:
        r = con.execute("SELECT * FROM contacts WHERE id=?", (contact_id,)).fetchone()
        return dict(r) if r else None


def merge(keep_id: int, dupe_ids: list[int]) -> dict:
    """Fold dupe rows into keep_id (fill blanks + union topics), then delete dupes.
    Use for aliases/roll-ups (e.g. an unnamed office row into a named person)."""
    with _conn() as con:
        keep = con.execute("SELECT * FROM contacts WHERE id=?", (keep_id,)).fetchone()
        if keep is None:
            raise ValueError(f"keep id {keep_id} not found")
        merged = dict(keep)
        for did in dupe_ids:
            d = con.execute("SELECT * FROM contacts WHERE id=?", (did,)).fetchone()
            if d is None:
                continue
            for f in _FIELDS:
                if f == "topic":
                    merged["topic"] = _merge_tags(merged["topic"], d["topic"])
                elif not merged[f] and d[f]:
                    merged[f] = d[f]
        merged["dedupe_key"] = _dedupe_key(merged["name"], merged["org"], merged["jurisdiction"])
        con.execute(
            "UPDATE contacts SET " + ", ".join(f"{f}=?" for f in _FIELDS)
            + ", dedupe_key=? WHERE id=?",
            tuple(merged[f] for f in _FIELDS) + (merged["dedupe_key"], keep_id),
        )
        if dupe_ids:
            con.execute(
                "DELETE FROM contacts WHERE id IN (%s)" % ",".join("?" * len(dupe_ids)),
                tuple(dupe_ids),
            )
    return merged


def export(fmt: str = "json") -> str:
    rows = query()
    if fmt == "yaml":
        try:
            import yaml  # type: ignore
            return yaml.safe_dump(rows, sort_keys=False, allow_unicode=True)
        except ImportError:
            pass
    return json.dumps(rows, indent=2, ensure_ascii=False)


# ---------- seed: the 1621 Clermont roster (import #1) ----------

def seed_1621() -> int:
    """Load the contacts we learned building the 1621 Clermont KB. Idempotent
    (upserts by dedupe key). Returns the count processed."""
    roster = [
        # Durham City-County agencies
        dict(name="W.C. (Wyatt) Blalock", org="Durham City-County Building & Safety",
             role="Building Division Chief (AHJ)", category="agency",
             jurisdiction="durham-county-nc", topic="building-permit, geotech, foundation",
             email="Wyatt.Blalock@durhamnc.gov", phone="919-369-9133",
             confidence="confirmed", source="1621-clermont-rd-durham/T-25"),
        dict(name="", org="Durham City-County Building & Safety",
             role="Permit Technicians (intake desk)", category="agency",
             jurisdiction="durham-county-nc", topic="building-permit, impact-fees",
             email="PermitTechnicians@durhamnc.gov", phone="919-560-4144",
             confidence="confirmed", source="1621-clermont-rd-durham/T-24"),
        dict(name="Emma Howrilla", org="Durham Planning & Development",
             role="Planning Specialist", category="agency",
             jurisdiction="durham-county-nc", topic="zoning, subdivision, buffer, access",
             email="Planning@durhamnc.gov", phone="919-560-4137",
             confidence="confirmed", source="1621-clermont-rd-durham/T-15"),
        dict(name="Justin Weist, PE", org="Durham Public Works / Development Review",
             role="Engineering Manager (Infrastructure Review)", category="agency",
             jurisdiction="durham-county-nc", topic="access, road, water-main, infrastructure",
             email="Justin.Weist@durhamnc.gov", phone="919-560-4326 x30278",
             confidence="confirmed", source="1621-clermont-rd-durham/T-27"),
        dict(name="", org="Durham Stormwater & GIS Services",
             role="Floodplain team", category="agency",
             jurisdiction="durham-county-nc", topic="floodplain, floodway, flood",
             email="DSCFloodplain@durhamnc.gov",
             confidence="confirmed", source="1621-clermont-rd-durham/T-17"),
        dict(name="", org="Durham Stormwater", role="Stormwater/BMP", category="agency",
             jurisdiction="durham-county-nc", topic="stormwater, wetland, watershed",
             email="StormwaterBMPs@durhamnc.gov",
             confidence="inferred", source="1621-clermont-rd-durham/T-16"),
        # Durham County (county-level)
        dict(name="Patrick C. Eaton, REHS", org="Durham County Dept. of Public Health",
             role="Onsite Water Protection Supervisor", category="agency",
             jurisdiction="durham-county-nc", topic="well, septic, water",
             email="peaton@dconc.gov", phone="919-560-7812",
             confidence="confirmed", source="1621-clermont-rd-durham/T-33",
             notes="500-ft well setback from a landfill limit"),
        dict(name="", org="Durham County Environmental Health",
             role="Health Inspector intake", category="agency",
             jurisdiction="durham-county-nc", topic="well, septic",
             email="HealthInspector@dconc.gov", phone="919-560-7800",
             confidence="confirmed", source="1621-clermont-rd-durham/T-23"),
        dict(name="", org="Durham County Tax / GIS", role="Tax Mapping / GIS", category="agency",
             jurisdiction="durham-county-nc", topic="tax, gis, parcel, liens",
             email="Tax-MappingGIS@dconc.gov",
             confidence="confirmed", source="1621-clermont-rd-durham/T-18"),
        # NC state
        dict(name="Sherri C. Stanley", org="NCDEQ Division of Waste Management, Solid Waste Section",
             role="Permitting Branch Head", category="agency",
             jurisdiction="nc-state", topic="solid-waste, landfill, open-dump, brownfield",
             email="sherri.stanley@deq.nc.gov", phone="919-707-8235",
             confidence="confirmed", source="1621-clermont-rd-durham/T-34"),
        dict(name="Ryan Channell", org="NCDEQ Superfund Section (Pre-Regulatory Landfill Unit)",
             role="Unit Supervisor", category="agency",
             jurisdiction="nc-state", topic="pre-regulatory-landfill, brownfield, solid-waste",
             email="Ryan.Channell@deq.nc.gov", phone="919-707-8333",
             confidence="confirmed", source="1621-clermont-rd-durham/T-27"),
        dict(name="Sharon Eckard, PG", org="NCDEQ Brownfields Redevelopment Section",
             role="Eastern Branch Head", category="agency",
             jurisdiction="nc-state", topic="brownfield",
             email="sharon.eckard@deq.nc.gov", phone="919-707-8379",
             confidence="confirmed", source="1621-clermont-rd-durham/T-15"),
        # Vendors
        dict(name="William J. (Bill) Brian, Jr.", org="Morningstar Law Group",
             role="Attorney (land use / boundary / brownfields)", category="vendor",
             jurisdiction="durham-county-nc", topic="attorney, title, boundary, brownfield",
             email="bbrian@morningstarlawgroup.com", phone="919-590-0372",
             confidence="confirmed", source="1621-clermont-rd-durham/T-15"),
        dict(name="David Nahm", org="Morningstar Law Group",
             role="Counsel (title / boundary)", category="vendor",
             jurisdiction="durham-county-nc", topic="attorney, title, boundary",
             email="dnahm@morningstarlawgroup.com", phone="919-590-0357",
             confidence="confirmed", source="1621-clermont-rd-durham/T-33"),
        dict(name="Brandon Johnson, PE", org="Summit Design & Engineering",
             role="Civil Engineering Dept Manager (roadway/transportation)", category="vendor",
             jurisdiction="nc-state", topic="engineer, civil, road, feasibility",
             email="brandon.johnson@summitde.com", phone="919-322-0115",
             confidence="confirmed", source="1621-clermont-rd-durham/T-29",
             notes="Loop in Summit Land Development team for site/geotech; info@summitde.com, 919-732-3883"),
    ]
    for c in roster:
        add(**c)
    return len(roster)
