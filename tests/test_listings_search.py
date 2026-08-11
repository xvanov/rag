"""Regression tests for listings.index / listings.search (E2 semantic corpus +
E3 hard-filter -> semantic-rerank pipeline).

Guards: sync_docs writes one corpus doc per listing and it round-trips back to
the listing id; search() with semantic=False is a pure hard-filter pass
through store.list_listings; search() degrades gracefully (no crash, no
exception) when the semantic index hasn't been built yet; and, when Azure
creds are configured, an end-to-end pass -- ingest the 5 sample listings,
build the real index (LIVE Azure embedding calls -- cheap for 5 docs), search
by meaning -- ranks the Guess Rd land listing ("existing septic permit and
well ... flag lot ... ADU potential") top for a paraphrased query.

Runs with plain Python (no pytest needed):
    .venv/Scripts/python.exe tests/test_listings_search.py
...and is also collectable by pytest if installed. The live-embedding test is
guarded to SKIP (not fail) when AZURE_OPENAI_API_KEY/ENDPOINT aren't
configured, so CI without keys still passes.
"""

from __future__ import annotations

import contextlib
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from listings import index as semantic_index  # noqa: E402
from listings import search as search_mod  # noqa: E402
from listings import store  # noqa: E402
from listings.sources.sample import SampleSource  # noqa: E402


@contextlib.contextmanager
def _tmp_db():
    """Isolated LISTINGS_ROOT (listings.db + the semantic corpus/index live
    under it) per test. conn.close() is in the finally (not right after
    yield) so a failed assertion inside the ``with`` block still closes the
    sqlite connection before the TemporaryDirectory tries to delete it --
    otherwise Windows leaves the file locked and the real assertion is masked
    by a PermissionError during teardown."""
    prev = os.environ.get("LISTINGS_ROOT")
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["LISTINGS_ROOT"] = tmp
        conn = store.connect()
        store.init_db(conn)
        try:
            yield conn
        finally:
            conn.close()
            if prev is None:
                os.environ.pop("LISTINGS_ROOT", None)
            else:
                os.environ["LISTINGS_ROOT"] = prev


def _seed_sample(conn) -> int:
    n = 0
    for listing in SampleSource().search("durham", {}):
        store.upsert_listing(conn, listing)
        n += 1
    return n


def _azure_configured() -> bool:
    from docrag import settings as docrag_settings
    return bool(docrag_settings.azure_api_key() and docrag_settings.azure_endpoint())


def test_sync_docs_writes_one_doc_per_listing():
    with _tmp_db() as conn:
        n_seeded = _seed_sample(conn)
        n_written = semantic_index.sync_docs(conn)
        assert n_written == n_seeded

        rows = store.list_listings(conn, limit=100)
        assert len(rows) == n_seeded
        for row in rows:
            path = semantic_index.doc_path(row["id"])
            assert os.path.isfile(path)
            text = open(path, encoding="utf-8").read()
            assert row["address"] in text
            if row.get("description_text"):
                assert row["description_text"] in text
            # source_file round-trip: basename -> listing id
            assert semantic_index.listing_id_from_source_file(
                os.path.basename(path)) == row["id"]


def test_search_no_semantic_applies_hard_filters():
    with _tmp_db() as conn:
        _seed_sample(conn)
        # Sample fixture: 2 "land" listings (Guess Rd $89k, Anthony Rd $62k).
        rows = search_mod.search(conn, "septic and well", filters={"property_type": "land"},
                                 semantic=False)
        assert rows
        assert all(r["property_type"] == "land" for r in rows)
        assert {r["address"] for r in rows} == {
            "0 Guess Rd, Durham, NC 27712", "0 Anthony Rd, Graham, NC 27253",
        }
        # semantic=False never touches the score/rerank machinery.
        assert all(r["_score"] is None for r in rows)

        narrowed = search_mod.search(conn, "", filters={"property_type": "land", "price_max": 70000},
                                     semantic=False)
        assert len(narrowed) == 1
        assert narrowed[0]["address"] == "0 Anthony Rd, Graham, NC 27253"


def test_search_falls_back_gracefully_when_index_absent():
    with _tmp_db() as conn:
        _seed_sample(conn)
        # No sync_docs/reindex call -- the corpus was never built.
        rows = search_mod.search(conn, "existing septic and well on a buildable lot",
                                 filters=None, semantic=True, top_k=5)
        assert rows  # hard-filter fallback still returns candidates
        assert all(r.get("_note") for r in rows)
        assert all(r["_score"] is None for r in rows)


def test_search_dedups_multiple_chunks_of_same_listing(monkeypatch=None):
    """A long description spans multiple docrag chunks (distinct section_ids),
    so rag_query yields several hits with the SAME source_file/listing_id.
    search() must return that listing exactly once (best rank), not once per
    chunk. Deterministic -- stubs rag_query, no Azure needed."""
    with _tmp_db() as conn:
        _seed_sample(conn)
        # Make the up-front index-file check pass without a real build.
        from listings import settings as _s
        _s.apply_docrag_env()
        idx = os.path.join(_s.index_dir(), "%s.db" % semantic_index.CORPUS)
        open(idx, "w").close()

        land = store.list_listings(conn, {"property_type": "land"})
        target = land[0]
        sf = os.path.basename(semantic_index.doc_path(target["id"]))
        other = land[1]
        sf2 = os.path.basename(semantic_index.doc_path(other["id"]))

        # target appears in 3 consecutive chunks, then the other listing once.
        fake = {"status": "ok", "results": [
            {"source_file": sf, "score": 0.9, "text": "chunk A"},
            {"source_file": sf, "score": 0.8, "text": "chunk B"},
            {"source_file": sf, "score": 0.7, "text": "chunk C"},
            {"source_file": sf2, "score": 0.6, "text": "other"},
        ]}
        import docrag.query as dq
        orig = dq.rag_query
        dq.rag_query = lambda *a, **k: fake
        try:
            rows = search_mod.search(conn, "septic well", filters=None, top_k=20)
        finally:
            dq.rag_query = orig

        ids = [r["id"] for r in rows]
        assert ids.count(target["id"]) == 1, "listing duplicated across chunks: %r" % ids
        assert len(ids) == len(set(ids)), "search returned duplicate listing ids"
        assert ids[0] == target["id"]            # best-ranked chunk wins
        assert rows[0]["_rank"] == 1 and rows[1]["_rank"] == 2  # ranks stay contiguous


@pytest.mark.skipif(not _azure_configured(),
                    reason="AZURE_OPENAI_API_KEY/ENDPOINT not configured")
def test_end_to_end_semantic_search_ranks_septic_land_lot_top():
    """LIVE Azure embedding test -- builds the real index for 5 sample docs.

    Skips cleanly (both under pytest via the marker above, and under the
    plain-python runner via the guard below) when Azure creds aren't set.
    """
    if not _azure_configured():
        print("SKIP test_end_to_end_semantic_search_ranks_septic_land_lot_top "
              "-- AZURE_OPENAI_API_KEY/ENDPOINT not configured")
        return
    with _tmp_db() as conn:
        n_seeded = _seed_sample(conn)
        n_docs = semantic_index.rebuild(conn)  # sync_docs + LIVE docrag build --confirm
        assert n_docs == n_seeded

        rows = search_mod.search(
            conn, "existing septic and well on a buildable lot",
            filters={"price_max": 150000, "property_type": "land"}, top_k=5)
        assert rows, "expected at least one semantic hit"
        assert rows[0]["address"] == "0 Guess Rd, Durham, NC 27712"


def _main() -> int:
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print("PASS", t.__name__)
        except AssertionError as e:
            failed += 1
            print("FAIL", t.__name__, "--", e or "assertion failed")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print("ERROR", t.__name__, "--", repr(e))
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_main())
