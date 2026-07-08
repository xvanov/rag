"""propkb CLI.

    python -m propkb ls
    python -m propkb new    --slug 1621-clermont-rd-durham --address "1621 Clermont Rd, Durham NC 27713"
    python -m propkb paths  --slug 1621-clermont-rd-durham
    python -m propkb ingest --slug 1621-clermont-rd-durham --file C:/path/x.pdf --kind docs
    python -m propkb index  --slug 1621-clermont-rd-durham
    python -m propkb ocr-render --file C:/path/scan.pdf --out C:/tmp/ocr   # -> PNGs for in-session OCR
    python -m propkb email-sync --slug 1621-clermont-rd-durham [--days 7]  # Phase 2
"""

from __future__ import annotations

import argparse
import sys

from . import store

# Windows consoles default to cp1252; email subjects carry unicode (directional
# isolates, smart quotes). Make stdout tolerant so prints never crash.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass


def _print_paths(slug: str) -> int:
    import os
    p = store.paths(slug)
    if not os.path.isdir(p["dir"]):
        print("No such property: %s" % p["slug"], file=sys.stderr)
        return 1
    for k in ("slug", "dir", "facts", "timeline", "understanding", "plan", "sources"):
        print("%-14s %s" % (k + ":", p[k]))
    return 0


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(prog="propkb",
                                 description="Per-property knowledge base on docrag.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("new"); a.add_argument("--slug", required=True); a.add_argument("--address", default="")
    sub.add_parser("ls")
    a = sub.add_parser("paths"); a.add_argument("--slug", required=True)
    a = sub.add_parser("ingest"); a.add_argument("--slug", required=True)
    a.add_argument("--file", required=True); a.add_argument("--kind", default="docs", choices=store.KINDS)
    a.add_argument("--rename", default=None)
    a = sub.add_parser("index"); a.add_argument("--slug", required=True); a.add_argument("--full", action="store_true")
    a = sub.add_parser("reslug", help="prefix all source files with the property slug + fix references")
    a.add_argument("--slug", required=True)
    a = sub.add_parser("ocr-render"); a.add_argument("--file", required=True)
    a.add_argument("--out", required=True); a.add_argument("--scale", type=int, default=3)
    a.add_argument("--max-pages", type=int, default=0)
    a = sub.add_parser("email-sync"); a.add_argument("--slug", required=True)
    a.add_argument("--days", type=int, default=7); a.add_argument("--max", type=int, default=50)

    # contacts: canonical cross-property contacts DB
    a = sub.add_parser("contacts")
    csub = a.add_subparsers(dest="contacts_cmd", required=True)
    ca = csub.add_parser("add")
    for f in ("name", "org", "role", "category", "jurisdiction", "topic",
              "email", "phone", "address", "source", "confidence", "notes"):
        ca.add_argument("--" + f, default="")
    cq = csub.add_parser("query")
    cq.add_argument("--jurisdiction"); cq.add_argument("--topic")
    cq.add_argument("--category"); cq.add_argument("--text")
    cm = csub.add_parser("merge")
    cm.add_argument("--keep", type=int, required=True)
    cm.add_argument("--dupes", required=True, help="comma-separated ids")
    ce = csub.add_parser("export"); ce.add_argument("--format", default="json", choices=("json", "yaml"))
    csub.add_parser("seed")

    # sources: canonical source registry (where to get data per jurisdiction)
    a = sub.add_parser("sources")
    ssub = a.add_subparsers(dest="sources_cmd", required=True)
    ssub.add_parser("list")
    ssub.add_parser("seed")
    sq = ssub.add_parser("query"); sq.add_argument("--jurisdiction"); sq.add_argument("--type")
    sa = ssub.add_parser("add")
    for f in ("jurisdiction", "data_type", "name", "method", "url", "notes"):
        sa.add_argument("--" + f, default="")

    # acquire: pull Durham property data (REST-first) into a property KB
    a = sub.add_parser("acquire"); a.add_argument("--slug", required=True)
    a.add_argument("--pin", default=""); a.add_argument("--address", default="")

    # mls: ingest an agent CSV export, or query a RESO Web API feed
    a = sub.add_parser("mls")
    msub = a.add_subparsers(dest="mls_cmd", required=True)
    mi = msub.add_parser("ingest-csv"); mi.add_argument("--slug", required=True)
    mi.add_argument("--file", required=True); mi.add_argument("--label", default="mls-export")
    mq = msub.add_parser("query")
    mq.add_argument("--resource", default="Property"); mq.add_argument("--filter", default="")
    mq.add_argument("--select", default=""); mq.add_argument("--top", type=int, default=50)
    mq.add_argument("--orderby", default="")

    args = ap.parse_args(argv)

    if args.cmd == "new":
        p = store.create(args.slug, args.address); print("Created %s at %s" % (p["slug"], p["dir"])); return 0
    if args.cmd == "ls":
        props = store.list_properties(); print("\n".join(props) if props else "(no properties yet)"); return 0
    if args.cmd == "paths":
        return _print_paths(args.slug)
    if args.cmd == "ingest":
        print("Filed -> %s" % store.ingest(args.slug, args.file, args.kind, args.rename)); return 0
    if args.cmd == "index":
        return store.reindex(args.slug, full=args.full)
    if args.cmd == "reslug":
        m = store.reslug(args.slug)
        print("Renamed %d file(s):" % len(m))
        for old, new in sorted(m.items()):
            print("  %s\n   -> %s" % (old, new))
        return 0
    if args.cmd == "ocr-render":
        from . import ocr
        pngs = ocr.render_pdf(args.file, args.out, dpi_scale=args.scale, max_pages=args.max_pages)
        print("\n".join(pngs)); return 0
    if args.cmd == "email-sync":
        from . import email as _email
        hits = _email.sync(args.slug, days=args.days, max_msgs=args.max)
        print("Filed %d relevant message(s):" % len(hits))
        for h in hits:
            print("  - %s | %s" % (h.get("date", "?"), h.get("subject", "")))
        return 0
    if args.cmd == "contacts":
        from . import contacts as _contacts
        if args.contacts_cmd == "add":
            cid = _contacts.add(**{f: getattr(args, f) for f in (
                "name", "org", "role", "category", "jurisdiction", "topic",
                "email", "phone", "address", "source", "confidence", "notes")})
            print("Contact #%d saved." % cid); return 0
        if args.contacts_cmd == "query":
            rows = _contacts.query(jurisdiction=args.jurisdiction, topic=args.topic,
                                   category=args.category, text=args.text)
            if not rows:
                print("(no matching contacts)"); return 0
            for r in rows:
                who = r["name"] or "(office)"
                print("#%-3d %-28s | %-32s | %s | %s | [%s] %s"
                      % (r["id"], who, r["org"], r["email"] or "-",
                         r["phone"] or "-", r["topic"], r["jurisdiction"]))
            print("\n%d contact(s)." % len(rows)); return 0
        if args.contacts_cmd == "merge":
            dupes = [int(x) for x in args.dupes.split(",") if x.strip()]
            m = _contacts.merge(args.keep, dupes)
            print("Merged %s into #%d (%s)." % (dupes, args.keep, m["name"] or m["org"])); return 0
        if args.contacts_cmd == "export":
            print(_contacts.export(args.format)); return 0
        if args.contacts_cmd == "seed":
            n = _contacts.seed_1621(); print("Seeded/updated %d contact(s) from 1621 Clermont." % n); return 0
        return 1
    if args.cmd == "sources":
        from . import registry as _reg
        if args.sources_cmd == "seed":
            print("Registry now has %d source(s)." % _reg.seed()); return 0
        if args.sources_cmd == "list":
            for r in _reg.list_all():
                print("[%s] %-22s %-8s %s" % (r["jurisdiction"], r["data_type"], r["method"], r["url"]))
            return 0
        if args.sources_cmd == "query":
            rows = _reg.query(jurisdiction=args.jurisdiction, data_type=args.type)
            print(_json_dumps(rows)); return 0
        if args.sources_cmd == "add":
            _reg.add(**{f: getattr(args, f) for f in ("jurisdiction", "data_type", "name", "method", "url", "notes")})
            print("Source added/updated."); return 0
        return 1
    if args.cmd == "acquire":
        from . import acquire
        result = acquire.durham(args.slug, pin=args.pin or None, address=args.address or None)
        print(_json_dumps(result))
        return 0
    if args.cmd == "mls":
        from . import mls as _mls
        if args.mls_cmd == "ingest-csv":
            path = _mls.ingest_csv(args.slug, args.file, label=args.label)
            print("Filed -> %s" % path); return 0
        if args.mls_cmd == "query":
            rows = _mls.reso_query(resource=args.resource, filter=args.filter,
                                   select=args.select, top=args.top, orderby=args.orderby)
            print(_json_dumps(rows)); return 0
        return 1
    return 1


def _json_dumps(obj) -> str:
    import json
    return json.dumps(obj, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    raise SystemExit(main())
