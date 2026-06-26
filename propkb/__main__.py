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
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
