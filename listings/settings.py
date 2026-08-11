"""listings settings -- point the shared docrag engine at the Scout data instance.

The general docrag engine resolves its corpus root + index dir from
``DOCRAG_DOCS_ROOT`` / ``DOCRAG_INDEX_DIR`` (env wins; read fresh on each call).
Scout keeps its data in a SEPARATE instance, a sibling to the code (mirrors how
propkb puts data in ``properties/`` and code in ``propkb/`` -- package code and
corpus files never collide):

    listings_data/            listing corpora + typed store  (LISTINGS_ROOT)
    listings_data/.index/     listing semantic index          (LISTINGS_INDEX_DIR)

``apply_docrag_env()`` exports those two paths into the docrag env vars so any
docrag call (index build, retrieval, synthesis) operates on the listings
instance instead of building-codes or properties. Call it ONCE at process
start (CLI / MCP server) so the whole process is bound to the listings instance.
"""

from __future__ import annotations

import os

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_THIS_DIR)


def repo_root() -> str:
    return _REPO_ROOT


def listings_root() -> str:
    """Root holding the listings corpora + ``listings.db``. Override via LISTINGS_ROOT."""
    return os.environ.get("LISTINGS_ROOT") or os.path.join(_REPO_ROOT, "listings_data")


def index_dir() -> str:
    """Listings semantic index instance. Override via LISTINGS_INDEX_DIR."""
    val = os.environ.get("LISTINGS_INDEX_DIR") or os.path.join(listings_root(), ".index")
    os.makedirs(val, exist_ok=True)
    return val


def apply_docrag_env() -> None:
    """Bind the docrag engine to the listings data + index instance.

    Sets DOCRAG_DOCS_ROOT/DOCRAG_INDEX_DIR (override=True) so subsequent docrag
    calls in THIS process use the listings instance, never building-codes or
    properties."""
    os.environ["DOCRAG_DOCS_ROOT"] = listings_root()
    os.environ["DOCRAG_INDEX_DIR"] = index_dir()


# ---- shared config passthroughs (values come from the repo .env via docrag.settings) ----

def ntfy_topic() -> str:
    from docrag import settings as _s
    return _s.get("NTFY_TOPIC", "") or ""


def azure_endpoint() -> str:
    from docrag import settings as _s
    return _s.azure_endpoint()


def azure_api_key() -> str:
    from docrag import settings as _s
    return _s.azure_api_key()
