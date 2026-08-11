"""listings.search -- E3 pipeline: hard filter (SQLite) -> semantic rerank
(docrag ``listings`` corpus) -> ranked listing rows.

Architecture (PRD sec.6): cheap fields gate first, semantic reranks only
within that survivor set, expensive enrichment runs later on the top-K this
module returns. Two storage engines, one query:

  1. HARD FILTER  -- store.list_listings(filters) resolves the candidate id
     set (price/beds/baths/property_type/status/jurisdiction/bbox).
  2. SEMANTIC RERANK -- rag_query() over the listings docrag corpus for
     ``query``; each hit's source_file ("listing-<id>.txt") maps back to a
     listing id (index.listing_id_from_source_file). Hits are walked in
     descending relevance and kept only if their id survived step 1 --
     the semantic rank order is preserved, the candidate set just prunes it.
  3. If semantic=False or the query is empty, skip straight to the
     hard-filtered rows (no LLM/embedding call).

Degrades gracefully when the semantic index isn't built yet (or Azure creds
are absent, or the embedding call fails for any other reason): open_db()
auto-creates an empty corpus DB rather than raising, so there's no single
FileNotFoundError to catch -- instead the whole rag_query() call is wrapped
broadly and any failure falls back to hard-filter-only results with a note,
consistent with the best-effort/no-crash convention used across this repo
(see docrag/index.py's own broad excepts).
"""

from __future__ import annotations

import os
import sys

from . import index as semantic_index
from . import settings, store

# How many extra semantic hits to fetch beyond top_k, to survive the
# intersection with the hard-filter candidate set without starving results.
_FETCH_MULTIPLIER = 5
_MIN_FETCH_K = 50

# Cap on the hard-filter candidate pull -- generous for a personal-scale
# Triangle corpus (see listings.index._SYNC_LIMIT).
_HARD_FILTER_LIMIT = 100_000

_SNIPPET_CHARS = 220


def _snippet(text: str | None) -> str:
    text = " ".join((text or "").split())
    if len(text) <= _SNIPPET_CHARS:
        return text
    return text[: _SNIPPET_CHARS - 3].rstrip() + "..."


def _fallback_rows(rows: list[dict], top_k: int, note: str | None = None) -> list[dict]:
    out = rows[:top_k]
    for i, r in enumerate(out, start=1):
        r["_rank"] = i
        r["_score"] = None
        if note:
            r["_note"] = note
    return out


def search(conn, query: str, filters: dict | None = None, top_k: int = 20,
           semantic: bool = True) -> list[dict]:
    """Hard-filter -> semantic-rerank listing search. Returns listing dicts
    (as store.get_listing shapes them) annotated with ``_rank`` (1-based) and
    ``_score`` (the reranked/RRF score, or None when semantic wasn't used)."""
    query = (query or "").strip()
    candidates = store.list_listings(conn, filters, limit=_HARD_FILTER_LIMIT)

    if not semantic or not query:
        return _fallback_rows(candidates, top_k)

    candidate_ids = {c["id"] for c in candidates}
    if not candidate_ids:
        return []

    settings.apply_docrag_env()

    # rag_query's open_db() auto-creates an empty corpus DB rather than
    # raising when it's missing, so an un-built index wouldn't otherwise
    # surface as an error -- it would just quietly return zero hits, which is
    # indistinguishable from "query genuinely matched nothing". Check for the
    # index file up front so "never indexed" gets the honest fallback+note
    # instead of masquerading as a real no-match.
    db_path = os.path.join(settings.index_dir(), "%s.db" % semantic_index.CORPUS)
    if not os.path.isfile(db_path):
        return _fallback_rows(candidates, top_k,
                              note="semantic index not built yet; hard-filter results only")

    fetch_k = max(top_k * _FETCH_MULTIPLIER, _MIN_FETCH_K)
    try:
        from docrag.query import rag_query
        retrieval = rag_query(semantic_index.CORPUS, query, top_k=fetch_k)
    except Exception as e:  # noqa: BLE001 -- no Azure creds / embedding failure / transient
        sys.stderr.write(
            "[listings.search] semantic layer unavailable (%s); "
            "falling back to hard-filter-only results\n" % e
        )
        return _fallback_rows(candidates, top_k,
                              note="semantic index unavailable; hard-filter results only")

    hits = retrieval.get("results") or []
    out: list[dict] = []
    seen: set[int] = set()   # dedup: a long description spans multiple chunks
    for res in hits:         # (sliding-window leaves, distinct section_ids) and
        # thus yields multiple rag_query hits for the SAME listing_id -- keep
        # only the first (best-ranked) hit per listing so dups don't eat top_k.
        listing_id = semantic_index.listing_id_from_source_file(res.get("source_file"))
        if listing_id is None or listing_id not in candidate_ids or listing_id in seen:
            continue
        listing = store.get_listing(conn, listing_id)
        if listing is None:
            continue
        seen.add(listing_id)
        listing["_rank"] = len(out) + 1
        listing["_score"] = res.get("score")
        listing["_snippet"] = _snippet(res.get("text"))
        out.append(listing)
        if len(out) >= top_k:
            break

    return out
