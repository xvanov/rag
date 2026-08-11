"""listings CLI (Scout).

    python -m listings init-db
    python -m listings ingest --source sample --geo durham
    python -m listings ingest --source redfin --geo "Durham, NC" --property-type land
    python -m listings ls [--limit 20]
    python -m listings index [--full]
    python -m listings search "existing septic and well" --type land --price-max 150000
    python -m listings stats [--type land --jurisdiction durham-nc]
    python -m listings get --id 3
"""

from __future__ import annotations

import argparse
import json
import sys

from . import store

# Windows consoles default to cp1252; keep stdout tolerant so prints never crash.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass


def _add_filter_args(parser: argparse.ArgumentParser) -> None:
    """Shared hard-filter flags (search + stats), matching the keys
    store._filter_clause understands."""
    parser.add_argument("--price-min", type=float, default=None)
    parser.add_argument("--price-max", type=float, default=None)
    parser.add_argument("--beds-min", type=float, default=None)
    parser.add_argument("--baths-min", type=float, default=None)
    parser.add_argument("--type", dest="property_type", default=None,
                        help="property_type filter, e.g. land / single_family")
    parser.add_argument("--status", default=None)
    parser.add_argument("--jurisdiction", default=None,
                        help="planning_jurisdiction filter, e.g. durham-nc")


def _filters_from_args(args: argparse.Namespace) -> dict:
    filters = {
        "price_min": args.price_min,
        "price_max": args.price_max,
        "beds_min": args.beds_min,
        "baths_min": args.baths_min,
        "property_type": args.property_type,
        "status": args.status,
        "jurisdiction": args.jurisdiction,
    }
    return {k: v for k, v in filters.items() if v is not None}


def _source(name: str):
    """Resolve a ListingSource by name. Import lazily so a missing dep/key for
    one adapter never breaks the others."""
    if name == "sample":
        from .sources.sample import SampleSource
        return SampleSource()
    if name == "redfin":
        from .sources import redfin as _redfin

        class _RedfinSource:
            def search(self, geo: str, filters: dict) -> list[dict]:
                return _redfin.search(geo, filters)

        return _RedfinSource()
    raise ValueError("unknown source: %s" % name)


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(prog="listings", description="Scout listings intelligence CLI.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init-db")

    a = sub.add_parser("ingest")
    a.add_argument("--source", required=True, choices=("sample", "redfin"))
    a.add_argument("--geo", required=True)
    a.add_argument("--property-type", default=None, help="passed through to the adapter as a filter")

    a = sub.add_parser("ls")
    a.add_argument("--limit", type=int, default=20)

    a = sub.add_parser("index", help="Rebuild the semantic corpus+index from the store.")
    a.add_argument("--full", action="store_true",
                   help="Force a full re-embed (default: incremental by SHA256).")

    a = sub.add_parser("search", help="Hard-filter -> semantic-rerank listing search.")
    a.add_argument("query")
    _add_filter_args(a)
    a.add_argument("--top-k", type=int, default=20)
    a.add_argument("--no-semantic", action="store_true",
                   help="Skip the semantic rerank; return hard-filtered rows only.")
    a.add_argument("--json", action="store_true",
                   help="Emit the ranked results as a single JSON array on stdout "
                        "and nothing else (machine-readable; used by the web server, "
                        "which runs search in a child process to avoid mutating its "
                        "own docrag env).")

    a = sub.add_parser("stats")
    _add_filter_args(a)

    a = sub.add_parser("get")
    a.add_argument("--id", type=int, required=True)

    a = sub.add_parser("feasibility", help="E4 buildability verdict for one listing (cited).")
    a.add_argument("--id", type=int, required=True)
    a.add_argument("--location", default=None, help="override governing jurisdiction (docrag location key)")

    a = sub.add_parser("enrich", help="E4/E5/E6: buildability + commute + scoring, persisted.")
    a.add_argument("--id", type=int, required=True)
    a.add_argument("--location", default=None)
    a.add_argument("--strategy", action="append", default=None,
                   help="repeatable; default all (land-to-build/buy-hold/flip/str)")
    a.add_argument("--dest", action="append", default=None,
                   help="commute destination as 'name:lat,lon' (repeatable)")
    a.add_argument("--scenario", default="typical", choices=("best", "typical", "peak"))

    a = sub.add_parser("score", help="E6 score a listing for one strategy (uses stored enrichment).")
    a.add_argument("--id", type=int, required=True)
    a.add_argument("--strategy", default="land-to-build",
                   choices=("land-to-build", "buy-hold", "flip", "str"))

    a = sub.add_parser("agents", help="E7 realtor rollups.")
    asub = a.add_subparsers(dest="agents_cmd", required=True)
    asub.add_parser("rollup", help="(re)compute agent rollups from listings.")
    ashow = asub.add_parser("show", help="show agent rollup(s).")
    ashow.add_argument("--id", default=None, help="agent_id; omit to list all")
    ademo = asub.add_parser("demographic",
                            help="FIREWALLED, internal-only, non-decisional name-inferred signal (PRD §9).")
    ademo.add_argument("--id", required=True, help="agent_id to attach the labeled signal to")

    a = sub.add_parser("thesis", help="E8 saved investment theses.")
    tsub = a.add_subparsers(dest="thesis_cmd", required=True)
    tadd = tsub.add_parser("add")
    tadd.add_argument("--name", required=True)
    tadd.add_argument("--spec-file", required=True, help="path to a thesis spec JSON (see monitor.py docstring)")
    tsub.add_parser("list")
    trun = tsub.add_parser("run")
    trun.add_argument("--id", type=int, required=True)
    trun.add_argument("--no-notify", action="store_true")

    a = sub.add_parser("monitor", help="E8 run all saved theses; alert on new matched+buildable+pencils hits.")
    a.add_argument("--no-notify", action="store_true")

    a = sub.add_parser("entitlement", help="E10 discretionary-path playbook when a goal isn't by-right (cited).")
    a.add_argument("--id", type=int, required=True)
    a.add_argument("--goal", required=True, help='e.g. "3 townhomes" or "an ADU"')
    a.add_argument("--location", default=None)

    a = sub.add_parser("promote", help="Promote a shortlisted listing into a propkb property + prep /property-research.")
    a.add_argument("--id", type=int, required=True)
    a.add_argument("--slug", default=None)

    args = ap.parse_args(argv)

    if args.cmd == "init-db":
        store.init_db()
        print("Initialized listings.db at %s" % store.db_path())
        return 0

    if args.cmd == "ingest":
        conn = store.connect()
        store.init_db(conn)
        filters = {}
        if args.property_type:
            filters["propertyType"] = [args.property_type]
        src = _source(args.source)
        listings = src.search(args.geo, filters)
        n = 0
        for listing in listings:
            store.upsert_listing(conn, listing)
            n += 1
        conn.close()
        print("Ingested %d listing(s) from %s (%s)." % (n, args.source, args.geo))
        return 0

    if args.cmd == "ls":
        conn = store.connect()
        store.init_db(conn)
        rows = store.list_listings(conn, limit=args.limit)
        conn.close()
        if not rows:
            print("(no listings yet)")
        for r in rows:
            print("#%-4d %-40s $%-10s %s / %s" % (
                r["id"], r.get("address") or "?", r.get("price") if r.get("price") is not None else "?",
                r.get("property_type") or "?", r.get("status") or "?"))
        return 0

    if args.cmd == "index":
        from . import index as semantic_index
        conn = store.connect()
        store.init_db(conn)
        n = semantic_index.rebuild(conn, full=args.full)
        conn.close()
        print("Synced %d listing doc(s) into the '%s' corpus and reindexed."
              % (n, semantic_index.CORPUS))
        return 0

    if args.cmd == "search":
        from . import search as search_mod
        conn = store.connect()
        store.init_db(conn)
        filters = _filters_from_args(args)
        rows = search_mod.search(conn, args.query, filters, top_k=args.top_k,
                                 semantic=not args.no_semantic)
        conn.close()
        if args.json:
            # Single JSON array, nothing else on stdout (any diagnostics from the
            # search layer go to stderr). Field set is the stable contract the
            # web server's /api/scout/search parses.
            out = [{
                "id": r.get("id"),
                "address": r.get("address"),
                "price": r.get("price"),
                "property_type": r.get("property_type"),
                "planning_jurisdiction": r.get("planning_jurisdiction"),
                "_rank": r.get("_rank"),
                "_score": r.get("_score"),
                "_snippet": r.get("_snippet"),
                "_note": r.get("_note"),
            } for r in rows]
            print(json.dumps(out, ensure_ascii=False))
            return 0
        if not rows:
            print("(no matches)")
        for r in rows:
            snippet = r.get("_snippet") or (r.get("description_text") or "")[:120]
            score = r.get("_score")
            print("#%-3d id=%-5s $%-10s %-14s %-40s score=%s\n      %s" % (
                r.get("_rank"), r.get("id"), r.get("price") if r.get("price") is not None else "?",
                r.get("property_type") or "?", r.get("address") or "?",
                "%.4f" % score if score is not None else "-", snippet))
        return 0

    if args.cmd == "stats":
        conn = store.connect()
        store.init_db(conn)
        filters = _filters_from_args(args)
        try:
            from .stats import compute_stats
            result = compute_stats(conn, filters)
        except Exception as e:  # noqa: BLE001 -- clean CLI message, not a traceback
            conn.close()
            print("(stats unavailable: %s)" % e, file=sys.stderr)
            return 1
        conn.close()
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0

    if args.cmd == "get":
        conn = store.connect()
        store.init_db(conn)
        listing = store.get_listing(conn, args.id)
        conn.close()
        if listing is None:
            print("No such listing: %d" % args.id, file=sys.stderr)
            return 1
        print(json.dumps(listing, indent=2, ensure_ascii=False))
        return 0

    if args.cmd == "feasibility":
        from . import feasibility as _feas
        conn = store.connect()
        store.init_db(conn)
        listing = store.get_listing(conn, args.id)
        conn.close()
        if listing is None:
            print("No such listing: %d" % args.id, file=sys.stderr)
            return 1
        verdict = _feas.assess(listing, location=args.location)
        print(json.dumps(verdict, indent=2, ensure_ascii=False))
        return 0

    if args.cmd == "enrich":
        from . import enrich as _enrich
        conn = store.connect()
        store.init_db(conn)
        dests = _parse_dests(args.dest)
        try:
            result = _enrich.enrich_listing(
                conn, args.id, destinations=dests, scenario=args.scenario,
                strategies=tuple(args.strategy) if args.strategy else _enrich.STRATEGIES,
                location=args.location)
        except ValueError as e:
            conn.close()
            print(str(e), file=sys.stderr)
            return 1
        conn.close()
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0

    if args.cmd == "score":
        from . import score as _score
        conn = store.connect()
        store.init_db(conn)
        listing = store.get_listing(conn, args.id)
        enrichment = store.get_enrichment(conn, args.id)
        conn.close()
        if listing is None:
            print("No such listing: %d" % args.id, file=sys.stderr)
            return 1
        result = _score.score(listing, enrichment, strategy=args.strategy)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0

    if args.cmd == "agents":
        from . import agents as _agents
        conn = store.connect()
        store.init_db(conn)
        if args.agents_cmd == "rollup":
            n = _agents.rollup(conn)
            conn.close()
            print("Rolled up %d agent(s) from listings." % n)
            return 0
        if args.agents_cmd == "show":
            if args.id:
                row = store.get_agent(conn, args.id)
                conn.close()
                if row is None:
                    print("No such agent: %s" % args.id, file=sys.stderr)
                    return 1
                print(json.dumps(row, indent=2, ensure_ascii=False))
                return 0
            rows = conn.execute("SELECT agent_id, name, brokerage, listing_count "
                                "FROM agents ORDER BY listing_count DESC").fetchall()
            conn.close()
            for r in rows:
                print("%-16s %-26s %-24s n=%s" % (r["agent_id"], r["name"] or "?",
                                                  r["brokerage"] or "?", r["listing_count"]))
            return 0
        if args.agents_cmd == "demographic":
            try:
                result = _agents.attach_demographic(conn, args.id)
            except ValueError as e:
                conn.close()
                print(str(e), file=sys.stderr)
                return 1
            conn.close()
            print(json.dumps(result, indent=2, ensure_ascii=False))
            print("\n[!] FIREWALLED: name-inferred, low-confidence, NON-DECISIONAL. "
                  "Never an input to scoring/ranking/alerts (PRD §9).", file=sys.stderr)
            return 0
        return 1

    if args.cmd == "thesis":
        conn = store.connect()
        store.init_db(conn)
        if args.thesis_cmd == "add":
            with open(args.spec_file, "r", encoding="utf-8") as f:
                spec = json.load(f)
            tid = store.save_thesis(conn, args.name, spec)
            conn.close()
            print("Saved thesis #%d: %s" % (tid, args.name))
            return 0
        if args.thesis_cmd == "list":
            rows = store.list_theses(conn)
            conn.close()
            for r in rows:
                print("#%-3d %-30s last_run=%s" % (r["id"], r.get("name") or "?",
                                                   r.get("last_run") or "never"))
            return 0
        if args.thesis_cmd == "run":
            from . import monitor as _monitor
            try:
                hits = _monitor.run_thesis(conn, args.id, notify=not args.no_notify)
            except ValueError as e:
                conn.close()
                print(str(e), file=sys.stderr)
                return 1
            conn.close()
            print("%d new hit(s)." % len(hits))
            for h in hits:
                print(json.dumps(h, indent=2, ensure_ascii=False))
            return 0
        return 1

    if args.cmd == "monitor":
        from . import monitor as _monitor
        conn = store.connect()
        store.init_db(conn)
        result = _monitor.run_all(conn, notify=not args.no_notify)
        conn.close()
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0

    if args.cmd == "entitlement":
        from . import entitlement as _ent
        conn = store.connect()
        store.init_db(conn)
        listing = store.get_listing(conn, args.id)
        conn.close()
        if listing is None:
            print("No such listing: %d" % args.id, file=sys.stderr)
            return 1
        result = _ent.entitlement_path(listing, args.goal, location=args.location)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0

    if args.cmd == "promote":
        from . import handoff as _handoff
        conn = store.connect()
        store.init_db(conn)
        try:
            result = _handoff.promote(conn, args.id, slug=args.slug)
        except ValueError as e:
            conn.close()
            print(str(e), file=sys.stderr)
            return 1
        conn.close()
        print(json.dumps(result, indent=2, ensure_ascii=False))
        print("\nNext: %s" % result.get("next"), file=sys.stderr)
        return 0

    return 1


def _parse_dests(specs: list | None) -> list[dict]:
    """Parse repeated --dest 'name:lat,lon' flags into commute destination dicts."""
    out: list[dict] = []
    for spec in specs or []:
        try:
            name, coords = spec.split(":", 1)
            lat, lon = (float(x) for x in coords.split(","))
            out.append({"name": name.strip(), "lat": lat, "lon": lon})
        except (ValueError, TypeError):
            raise SystemExit("bad --dest %r; expected 'Name:lat,lon'" % spec)
    return out


if __name__ == "__main__":
    raise SystemExit(main())
