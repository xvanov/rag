"""propkb.mls -- pull MLS listing/comparable data into a property KB.

Two paths, matching how a realtor can actually share data:

1. CSV/report EXPORT (works today, zero credentials) -- the agent exports a
   listing/comparables report to CSV; `ingest_csv()` files it as an indexed
   markdown table under sources/web/. This is the recommended path for a
   non-technical agent.

2. RESO Web API (OData) FEED (needs credentials the MLS issues) -- `reso_query()`
   is a thin OData client. Auth is either a static bearer token or OAuth2
   client-credentials, read from settings/.env:
       MLS_API_BASE      e.g. https://api.reso.example.org
       MLS_API_TOKEN     (static bearer)   -- OR --
       MLS_OAUTH_TOKEN_URL, MLS_CLIENT_ID, MLS_CLIENT_SECRET  (client_credentials)
   These come from the MLS / its feed vendor (Trestle, Bridge, Spark), NOT from
   the agent's normal login. Until they're set, reso_query() raises a clear error.

The Triangle MLS is Doorify MLS (formerly TMLS). Nothing here is MLS-specific;
point MLS_API_BASE at whatever RESO Web API endpoint the feed provides.
"""

from __future__ import annotations

import csv as _csv
import json
import os
import urllib.parse
import urllib.request

from . import store

# propkb.settings loads .env at import; read MLS_* straight from the environment.
try:
    from . import settings as _settings  # noqa: F401  (ensures .env is loaded)
except Exception:  # noqa: BLE001
    pass


def _cfg(key: str, default: str = "") -> str:
    return os.environ.get(key, default) or default


# ---------- Path 1: CSV / report export (no credentials) ----------

def ingest_csv(slug: str, csv_path: str, label: str = "mls-export") -> str:
    """Parse an agent-exported CSV into a readable, indexed markdown table under
    sources/web/. Returns the written path."""
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        rows = list(_csv.reader(f))
    if not rows:
        raise ValueError("empty CSV")
    header, body = rows[0], rows[1:]
    lines = [f"# MLS export -- {slug} ({label})", "",
             f"_Ingested from `{csv_path}` ({len(body)} rows). Agent-provided MLS data._", ""]
    # render as a markdown table (cap very wide tables for readability; keep all rows)
    cols = header[:12]
    lines.append("| " + " | ".join(cols) + " |")
    lines.append("| " + " | ".join("---" for _ in cols) + " |")
    for r in body:
        cells = [(r[i] if i < len(r) else "").replace("|", "\\|") for i in range(len(cols))]
        lines.append("| " + " | ".join(cells) + " |")
    if len(header) > 12:
        lines += ["", "_(showing first 12 of %d columns; full data in the source CSV)_" % len(header)]
    md = "\n".join(lines) + "\n"
    name = store.namespace(slug, "mls-" + label + ".md")
    return store.write_text(slug, md, name, kind="web")


# ---------- Path 2: RESO Web API (OData) feed ----------

def _http(url: str, headers: dict, data: bytes | None = None) -> dict:
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=45) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _bearer() -> str:
    """Resolve a bearer token: static MLS_API_TOKEN, else OAuth2 client_credentials."""
    tok = _cfg("MLS_API_TOKEN", "")
    if tok:
        return tok
    token_url = _cfg("MLS_OAUTH_TOKEN_URL", "")
    cid = _cfg("MLS_CLIENT_ID", "")
    secret = _cfg("MLS_CLIENT_SECRET", "")
    if not (token_url and cid and secret):
        raise RuntimeError(
            "MLS credentials not set. Provide MLS_API_TOKEN, or "
            "MLS_OAUTH_TOKEN_URL + MLS_CLIENT_ID + MLS_CLIENT_SECRET in .env. "
            "These are issued by the MLS/feed vendor (Trestle/Bridge/Spark), not the agent login."
        )
    body = urllib.parse.urlencode({"grant_type": "client_credentials",
                                   "client_id": cid, "client_secret": secret}).encode()
    resp = _http(token_url, {"Content-Type": "application/x-www-form-urlencoded"}, body)
    return resp["access_token"]


def reso_query(resource: str = "Property", filter: str = "", select: str = "",
               top: int = 50, orderby: str = "") -> list[dict]:
    """Query a RESO Web API (OData) endpoint. Returns the 'value' records.
    Requires MLS_API_BASE + credentials in .env (see module docstring)."""
    base = _cfg("MLS_API_BASE", "")
    if not base:
        raise RuntimeError("MLS_API_BASE not set in .env (the RESO Web API service root).")
    params = {"$top": str(top)}
    if filter:
        params["$filter"] = filter
    if select:
        params["$select"] = select
    if orderby:
        params["$orderby"] = orderby
    url = base.rstrip("/") + "/" + resource + "?" + urllib.parse.urlencode(params)
    headers = {"Authorization": "Bearer " + _bearer(), "Accept": "application/json"}
    return _http(url, headers).get("value", [])
