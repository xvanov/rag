"""listings.store -- typed SQLite analytics layer for Scout (`listings.db`).

This is the ONE genuinely-new storage component in Scout (PRD §7): numeric/
categorical fields for hard filters, stats, comps, and agent rollups -- things
the semantic (docrag) index is the wrong tool for. A search is hard-filter in
here -> semantic rerank in the listings docrag instance -> enrich/score the
survivors.

NOTHING smart lives here: normalization, scoring, and buildability reasoning
are other modules' jobs (`sources/*.normalize`, `score.py`, `feasibility.py`).
This file is deterministic plumbing: create the schema, upsert, filter, dedup.

JSON-shaped columns are TEXT named ``*_json`` and are the caller's
responsibility to (de)serialize -- this module stores/returns them as raw
JSON strings except where a helper explicitly decodes them (see
``get_listing``/``list_listings``, which decode for convenience).
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional

from . import settings

# Listing columns that are stored as JSON text but decoded to Python objects
# on read by get_listing/list_listings (raw_json is left as a string -- callers
# that want it parse it themselves, it can be arbitrarily source-shaped).
_LISTING_JSON_COLS = ("photos_json",)

_LISTINGS_COLS = (
    "source", "source_id", "mls_number", "status", "address", "lat", "lon",
    "parcel_pin", "planning_jurisdiction", "price", "list_date", "dom",
    "beds", "baths", "sqft", "lot_sqft", "lot_acres", "property_type",
    "year_built", "description_text", "list_agent_name", "list_agent_id",
    "brokerage", "photos_json", "raw_json",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    source                TEXT NOT NULL,
    source_id             TEXT NOT NULL,
    mls_number            TEXT,
    status                TEXT,
    address               TEXT,
    lat                   REAL,
    lon                   REAL,
    parcel_pin            TEXT,
    planning_jurisdiction TEXT,
    price                 REAL,
    list_date             TEXT,
    dom                   INTEGER,
    beds                  REAL,
    baths                 REAL,
    sqft                  REAL,
    lot_sqft              REAL,
    lot_acres             REAL,
    property_type         TEXT,
    year_built            INTEGER,
    description_text      TEXT,
    list_agent_name       TEXT,
    list_agent_id         TEXT,
    brokerage             TEXT,
    photos_json           TEXT,
    first_seen            TEXT,
    last_seen             TEXT,
    raw_json              TEXT,
    UNIQUE (source, source_id)
);

CREATE TABLE IF NOT EXISTS price_history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id INTEGER NOT NULL REFERENCES listings(id),
    date       TEXT,
    price      REAL,
    event      TEXT
);

CREATE TABLE IF NOT EXISTS enrichment (
    listing_id         INTEGER PRIMARY KEY REFERENCES listings(id),
    zoning_code        TEXT,
    allowed_uses_json  TEXT,
    flood_zone         TEXT,
    in_floodway        INTEGER,
    watershed          TEXT,
    commute_json       TEXT,
    poi_json           TEXT,
    buildability_json  TEXT,
    buildability_cited TEXT,
    score_json         TEXT,
    updated_at         TEXT
);

CREATE TABLE IF NOT EXISTS agents (
    agent_id                TEXT PRIMARY KEY,
    name                    TEXT,
    brokerage               TEXT,
    listing_count           INTEGER,
    geo_json                TEXT,
    price_band_json         TEXT,
    below_avm_rate          REAL,
    relist_rate             REAL,
    demographic_signal_json TEXT
);

CREATE TABLE IF NOT EXISTS theses (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    name     TEXT,
    spec_json TEXT,
    created  TEXT,
    last_run TEXT
);

CREATE TABLE IF NOT EXISTS thesis_hits (
    thesis_id     INTEGER NOT NULL REFERENCES theses(id),
    listing_id    INTEGER NOT NULL REFERENCES listings(id),
    first_alerted TEXT,
    rationale     TEXT,
    PRIMARY KEY (thesis_id, listing_id)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------- connection ----------

def db_path() -> str:
    root = settings.listings_root()
    os.makedirs(root, exist_ok=True)
    return os.path.join(root, "listings.db")


def connect() -> sqlite3.Connection:
    """Open the listings.db connection: Row factory + foreign keys ON."""
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: Optional[sqlite3.Connection] = None) -> sqlite3.Connection:
    """Create all six tables if missing. Returns the connection used (opens
    and returns a new one if ``conn`` is None; caller owns closing it)."""
    own = conn is None
    c = conn or connect()
    c.executescript(_SCHEMA)
    c.commit()
    if own:
        return c
    return c


# ---------- listings ----------

def upsert_listing(conn: sqlite3.Connection, listing: dict) -> int:
    """Insert or update a listing by (source, source_id). Maintains
    first_seen/last_seen and appends a price_history row when the price
    changed from the stored value. Returns the listing's ``id``."""
    source = listing.get("source")
    source_id = listing.get("source_id")
    if not source or not source_id:
        raise ValueError("listing dict must have 'source' and 'source_id'")

    row = conn.execute(
        "SELECT id, price FROM listings WHERE source = ? AND source_id = ?",
        (source, source_id),
    ).fetchone()

    now = _now()
    values = {col: listing.get(col) for col in _LISTINGS_COLS}
    for col in _LISTING_JSON_COLS:
        if isinstance(values.get(col), (list, dict)):
            values[col] = json.dumps(values[col], ensure_ascii=False)

    if row is None:
        cols = list(_LISTINGS_COLS) + ["first_seen", "last_seen"]
        placeholders = ", ".join("?" for _ in cols)
        params = [values[c] for c in _LISTINGS_COLS] + [now, now]
        cur = conn.execute(
            "INSERT INTO listings (%s) VALUES (%s)" % (", ".join(cols), placeholders),
            params,
        )
        listing_id = cur.lastrowid
        if values.get("price") is not None:
            conn.execute(
                "INSERT INTO price_history (listing_id, date, price, event) VALUES (?, ?, ?, ?)",
                (listing_id, now, values["price"], "listed"),
            )
    else:
        listing_id = row["id"]
        old_price = row["price"]
        set_clause = ", ".join("%s = ?" % c for c in _LISTINGS_COLS)
        params = [values[c] for c in _LISTINGS_COLS] + [now, listing_id]
        conn.execute(
            "UPDATE listings SET %s, last_seen = ? WHERE id = ?" % set_clause,
            params,
        )
        new_price = values.get("price")
        if new_price is not None and old_price is not None and new_price != old_price:
            conn.execute(
                "INSERT INTO price_history (listing_id, date, price, event) VALUES (?, ?, ?, ?)",
                (listing_id, now, new_price, "price_change"),
            )

    conn.commit()
    return listing_id


def _row_to_listing(row: sqlite3.Row) -> dict:
    d = dict(row)
    for col in _LISTING_JSON_COLS:
        if d.get(col):
            try:
                d[col] = json.loads(d[col])
            except (TypeError, ValueError):
                pass
    return d


def get_listing(conn: sqlite3.Connection, listing_id: int) -> Optional[dict]:
    row = conn.execute("SELECT * FROM listings WHERE id = ?", (listing_id,)).fetchone()
    return _row_to_listing(row) if row else None


def _filter_clause(filters: Optional[dict]) -> tuple[str, list]:
    """Build a WHERE clause from the supported hard filters:
    price_min/price_max, beds_min, baths_min, property_type, status,
    jurisdiction (planning_jurisdiction), bbox=(min_lat, min_lon, max_lat, max_lon)."""
    if not filters:
        return "", []
    clauses: list[str] = []
    params: list = []
    if filters.get("price_min") is not None:
        clauses.append("price >= ?"); params.append(filters["price_min"])
    if filters.get("price_max") is not None:
        clauses.append("price <= ?"); params.append(filters["price_max"])
    if filters.get("beds_min") is not None:
        clauses.append("beds >= ?"); params.append(filters["beds_min"])
    if filters.get("baths_min") is not None:
        clauses.append("baths >= ?"); params.append(filters["baths_min"])
    if filters.get("property_type"):
        clauses.append("property_type = ?"); params.append(filters["property_type"])
    if filters.get("status"):
        clauses.append("status = ?"); params.append(filters["status"])
    if filters.get("jurisdiction"):
        clauses.append("planning_jurisdiction = ?"); params.append(filters["jurisdiction"])
    bbox = filters.get("bbox")
    if bbox:
        min_lat, min_lon, max_lat, max_lon = bbox
        clauses.append("lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?")
        params += [min_lat, max_lat, min_lon, max_lon]
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


def list_listings(conn: sqlite3.Connection, filters: Optional[dict] = None,
                   limit: int = 100, offset: int = 0) -> list[dict]:
    where, params = _filter_clause(filters)
    sql = "SELECT * FROM listings" + where + " ORDER BY id LIMIT ? OFFSET ?"
    rows = conn.execute(sql, params + [limit, offset]).fetchall()
    return [_row_to_listing(r) for r in rows]


def count_listings(conn: sqlite3.Connection, filters: Optional[dict] = None) -> int:
    where, params = _filter_clause(filters)
    sql = "SELECT COUNT(*) AS n FROM listings" + where
    return conn.execute(sql, params).fetchone()["n"]


# ---------- price history ----------

def price_history(conn: sqlite3.Connection, listing_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM price_history WHERE listing_id = ? ORDER BY date", (listing_id,)
    ).fetchall()
    return [dict(r) for r in rows]


# ---------- enrichment ----------

_ENRICHMENT_COLS = (
    "zoning_code", "allowed_uses_json", "flood_zone", "in_floodway", "watershed",
    "commute_json", "poi_json", "buildability_json", "buildability_cited", "score_json",
)


def upsert_enrichment(conn: sqlite3.Connection, listing_id: int, fields: dict) -> None:
    """Insert or update the single enrichment row for a listing (one row per
    listing_id). Unspecified columns keep their previous value."""
    existing = conn.execute(
        "SELECT * FROM enrichment WHERE listing_id = ?", (listing_id,)
    ).fetchone()
    now = _now()
    if existing is None:
        values = {col: fields.get(col) for col in _ENRICHMENT_COLS}
        cols = ["listing_id"] + list(_ENRICHMENT_COLS) + ["updated_at"]
        placeholders = ", ".join("?" for _ in cols)
        params = [listing_id] + [values[c] for c in _ENRICHMENT_COLS] + [now]
        conn.execute(
            "INSERT INTO enrichment (%s) VALUES (%s)" % (", ".join(cols), placeholders),
            params,
        )
    else:
        merged = {col: fields.get(col, existing[col]) for col in _ENRICHMENT_COLS}
        set_clause = ", ".join("%s = ?" % c for c in _ENRICHMENT_COLS)
        params = [merged[c] for c in _ENRICHMENT_COLS] + [now, listing_id]
        conn.execute(
            "UPDATE enrichment SET %s, updated_at = ? WHERE listing_id = ?" % set_clause,
            params,
        )
    conn.commit()


def get_enrichment(conn: sqlite3.Connection, listing_id: int) -> Optional[dict]:
    row = conn.execute(
        "SELECT * FROM enrichment WHERE listing_id = ?", (listing_id,)
    ).fetchone()
    return dict(row) if row else None


# ---------- agents ----------

_AGENT_COLS = (
    "name", "brokerage", "listing_count", "geo_json", "price_band_json",
    "below_avm_rate", "relist_rate", "demographic_signal_json",
)


def upsert_agent(conn: sqlite3.Connection, agent: dict) -> None:
    """Insert or update an agent row by ``agent_id``."""
    agent_id = agent.get("agent_id")
    if not agent_id:
        raise ValueError("agent dict must have 'agent_id'")
    existing = conn.execute(
        "SELECT 1 FROM agents WHERE agent_id = ?", (agent_id,)
    ).fetchone()
    if existing is None:
        values = {col: agent.get(col) for col in _AGENT_COLS}
        cols = ["agent_id"] + list(_AGENT_COLS)
        placeholders = ", ".join("?" for _ in cols)
        params = [agent_id] + [values[c] for c in _AGENT_COLS]
        conn.execute(
            "INSERT INTO agents (%s) VALUES (%s)" % (", ".join(cols), placeholders),
            params,
        )
    else:
        # Merge-preserving (consistent with upsert_enrichment): only overwrite the
        # columns actually supplied, so a partial rollup update never wipes fields.
        provided = [c for c in _AGENT_COLS if c in agent]
        if provided:
            set_clause = ", ".join("%s = ?" % c for c in provided)
            params = [agent[c] for c in provided] + [agent_id]
            conn.execute(
                "UPDATE agents SET %s WHERE agent_id = ?" % set_clause, params
            )
    conn.commit()


def get_agent(conn: sqlite3.Connection, agent_id: str) -> Optional[dict]:
    row = conn.execute("SELECT * FROM agents WHERE agent_id = ?", (agent_id,)).fetchone()
    return dict(row) if row else None


# ---------- theses ----------

def save_thesis(conn: sqlite3.Connection, name: str, spec: dict) -> int:
    """Insert a new saved thesis. Returns its ``id``."""
    now = _now()
    cur = conn.execute(
        "INSERT INTO theses (name, spec_json, created, last_run) VALUES (?, ?, ?, ?)",
        (name, json.dumps(spec, ensure_ascii=False), now, None),
    )
    conn.commit()
    return cur.lastrowid


def list_theses(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM theses ORDER BY id").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        if d.get("spec_json"):
            try:
                d["spec_json"] = json.loads(d["spec_json"])
            except (TypeError, ValueError):
                pass
        out.append(d)
    return out


def touch_thesis_last_run(conn: sqlite3.Connection, thesis_id: int) -> None:
    conn.execute("UPDATE theses SET last_run = ? WHERE id = ?", (_now(), thesis_id))
    conn.commit()


# ---------- thesis hits (dedup) ----------

def thesis_seen(conn: sqlite3.Connection, thesis_id: int, listing_id: int) -> bool:
    """True if this (thesis, listing) pair has already been recorded as a hit."""
    row = conn.execute(
        "SELECT 1 FROM thesis_hits WHERE thesis_id = ? AND listing_id = ?",
        (thesis_id, listing_id),
    ).fetchone()
    return row is not None


def record_thesis_hit(conn: sqlite3.Connection, thesis_id: int, listing_id: int,
                       rationale: str = "") -> bool:
    """Record a thesis hit if not already seen (dedup via INSERT OR IGNORE on
    the composite primary key). Returns True if a new row was inserted."""
    cur = conn.execute(
        "INSERT OR IGNORE INTO thesis_hits (thesis_id, listing_id, first_alerted, rationale) "
        "VALUES (?, ?, ?, ?)",
        (thesis_id, listing_id, _now(), rationale),
    )
    conn.commit()
    return cur.rowcount > 0


def list_thesis_hits(conn: sqlite3.Connection, thesis_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM thesis_hits WHERE thesis_id = ? ORDER BY first_alerted", (thesis_id,)
    ).fetchall()
    return [dict(r) for r in rows]
