"""listings.index -- semantic corpus (E2) over listing description text.

Mirrors ``propkb.store.reindex()``: the listings docrag instance is a THIRD
docrag corpus (after ``building-codes`` and per-property slugs), built the
same way -- a folder of plain-text docs under the listings data root, indexed
by the shared docrag engine bound via ``settings.apply_docrag_env()``.

One doc per listing, named ``listing-<id>.txt`` so a search hit's
``source_file`` maps straight back to the listing's ``id`` (see
``listing_id_from_source_file`` -- ``search.py`` uses it to rejoin the
semantic hit to the typed SQLite row). The doc carries a small header
(address + price + property_type) ahead of the free-text description so a
leaf chunk retains that context even after the sliding-window split.

NOTHING smart lives here -- normalization/scoring live elsewhere. This file
just (re)writes docs and calls the docrag build, same division of labor as
propkb.store.
"""

from __future__ import annotations

import os
import re
import sys

from . import settings, store

# Fixed corpus name for the listings semantic instance (docs live under
# ``{listings_root()}/listings/``, distinct from ``listings.db`` beside it).
CORPUS = "listings"

_DOC_RE = re.compile(r"^listing-(\d+)\.txt$")

# store.list_listings has no "give me everything" mode; this caps how many
# rows a single sync_docs pass will pick up. Generous for a personal-scale
# corpus (PRD v1 = Triangle land/SFH/2-4-unit); raise if that ever binds.
_SYNC_LIMIT = 100_000


def corpus_dir() -> str:
    return os.path.join(settings.listings_root(), CORPUS)


def doc_path(listing_id: int) -> str:
    return os.path.join(corpus_dir(), "listing-%d.txt" % listing_id)


def listing_id_from_source_file(source_file: str) -> int | None:
    """Reverse of ``doc_path``: recover the listing id a search hit came from."""
    m = _DOC_RE.match(os.path.basename(source_file or ""))
    return int(m.group(1)) if m else None


def _doc_text(listing: dict) -> str:
    header = "%s\n$%s | %s | %s" % (
        listing.get("address") or "(no address)",
        listing.get("price") if listing.get("price") is not None else "?",
        listing.get("property_type") or "?",
        listing.get("planning_jurisdiction") or "?",
    )
    body = (listing.get("description_text") or "").strip()
    return header + "\n\n" + body + "\n"


def sync_docs(conn) -> int:
    """(Re)write one text doc per listing in ``conn`` into the corpus dir.

    Full overwrite of each listing's doc (cheap -- these are short text files);
    docrag's own SHA256 check in ``reindex`` skips unchanged ones on the next
    build. Does not prune docs for listings deleted from the store -- listings
    are never hard-deleted in this store, so that's not a live concern yet.
    Returns the number of docs written.
    """
    out_dir = corpus_dir()
    os.makedirs(out_dir, exist_ok=True)
    n = 0
    for listing in store.list_listings(conn, limit=_SYNC_LIMIT):
        listing_id = listing.get("id")
        if listing_id is None:
            continue
        with open(doc_path(listing_id), "w", encoding="utf-8") as f:
            f.write(_doc_text(listing))
        n += 1
    return n


def reindex(full: bool = False) -> int:
    """Apply the listings docrag env, then build the ``listings`` corpus index.

    Returns docrag.index.main's exit code (0 = success), same convention as
    propkb.store.reindex.
    """
    settings.apply_docrag_env()
    from docrag import index as _index  # imported AFTER env is applied

    argv = ["build", "--corpus", CORPUS, "--confirm"]
    if full:
        argv.append("--full")
    return _index.main(argv)


def rebuild(conn, full: bool = False) -> int:
    """sync_docs then reindex. Incremental by default (docrag skips unchanged
    files by SHA256; pass full=True to force a full re-embed).

    Returns the doc count from sync_docs (what the CLI reports); a nonzero
    reindex exit code is logged to stderr but doesn't raise -- mirrors the
    graceful-degradation rule elsewhere in this package.
    """
    n = sync_docs(conn)
    rc = reindex(full=full)
    if rc != 0:
        sys.stderr.write("[listings.index] reindex exited %d\n" % rc)
    return n
