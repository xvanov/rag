"""propkb settings -- point the shared docrag engine at the property data instance.

The general docrag engine resolves its corpus root + index dir from
``DOCRAG_DOCS_ROOT`` / ``DOCRAG_INDEX_DIR`` (env wins; read fresh on each call).
propkb keeps its data in a SEPARATE instance:

    properties/<slug>/        property corpora   (PROPKB_ROOT)
    properties/.index/        property indexes   (PROPKB_INDEX_DIR)

``apply_docrag_env()`` exports those two paths into the docrag env vars so any
docrag call (index build, retrieval, synthesis) operates on the property instance
instead of the building-codes one. Call it ONCE at process start (CLI / MCP server)
so the whole process is bound to the property instance.
"""

from __future__ import annotations

import os

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_THIS_DIR)


def repo_root() -> str:
    return _REPO_ROOT


def properties_root() -> str:
    """Root holding ``<slug>/`` property corpora. Override via PROPKB_ROOT."""
    return os.environ.get("PROPKB_ROOT") or os.path.join(_REPO_ROOT, "properties")


def index_dir() -> str:
    """Property index instance. Override via PROPKB_INDEX_DIR."""
    val = os.environ.get("PROPKB_INDEX_DIR") or os.path.join(properties_root(), ".index")
    os.makedirs(val, exist_ok=True)
    return val


def apply_docrag_env() -> None:
    """Bind the docrag engine to the property data + index instance.

    Sets DOCRAG_DOCS_ROOT/DOCRAG_INDEX_DIR (override=True) so subsequent docrag
    calls in THIS process use the property instance, never building-codes."""
    os.environ["DOCRAG_DOCS_ROOT"] = properties_root()
    os.environ["DOCRAG_INDEX_DIR"] = index_dir()


# ---- email / notify (Phase 2); values come from the repo .env via docrag.settings ----

def gmail_user() -> str:
    from docrag import settings as _s
    return _s.get("GMAIL_USER", "") or ""


def gmail_pass() -> str:
    """Gmail app password. Strip spaces (Google displays it as 4x4 groups but
    IMAP/SMTP want it without spaces)."""
    from docrag import settings as _s
    return (_s.get("GMAIL_PASS", "") or "").replace(" ", "")


def ntfy_topic() -> str:
    from docrag import settings as _s
    return _s.get("NTFY_TOPIC", "") or ""
