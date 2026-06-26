"""propkb.watch -- background email watcher (Phase 2).

Polls Gmail for each property on an interval. When new relevant mail is filed, it
NOTIFIES the user (self email + ntfy) that new info arrived and is pending
interpretation. It does NOT itself reason/draft -- that's an LLM step.

Two hosting options for the FULL autonomous loop (intake -> update understanding
-> draft follow-up questions to Gmail Drafts -> draft PLAN update -> ping to
approve):

  A. Recommended: a Claude Code scheduled agent / `/loop` running
        /intake email --slug <slug>
     on an interval. Claude does the reasoning + drafting; this module's sync()
     + notify() are the primitives it calls.

  B. Standalone: run this module under Windows Task Scheduler / cron. It files
     new mail + pings you to run /intake. To make it fully autonomous, set
     PROPKB_CLAUDE_CMD to a headless Claude invocation (e.g. a `claude -p ...`
     command) and this loop will shell out to it per property with new mail.

Run:  python -m propkb.watch --interval 600    # seconds; one pass if --once
"""

from __future__ import annotations

import argparse
import os
import subprocess
import time

from . import email as _email
from . import store


def _claude_cmd(slug: str) -> list[str] | None:
    tmpl = os.environ.get("PROPKB_CLAUDE_CMD")
    if not tmpl:
        return None
    return tmpl.replace("{slug}", slug).split()


def pass_once(days: int = 3) -> int:
    """One polling pass over all properties. Returns count of properties with new mail."""
    touched = 0
    for slug in store.list_properties():
        try:
            hits = _email.sync(slug, days=days)
        except Exception as e:  # noqa: BLE001
            print("[watch] %s: sync error: %s" % (slug, e))
            continue
        if not hits:
            continue
        touched += 1
        lines = "\n".join("- %s: %s" % (h["date"], h["subject"]) for h in hits)
        _email.notify(
            "New mail for %s (%d)" % (slug, len(hits)),
            "Filed %d message(s):\n%s\n\nRun: /intake email --slug %s" % (len(hits), lines, slug))
        cmd = _claude_cmd(slug)
        if cmd:
            print("[watch] %s: %d new; invoking Claude: %s" % (slug, len(hits), " ".join(cmd)))
            try:
                subprocess.run(cmd, timeout=1800)
            except Exception as e:  # noqa: BLE001
                print("[watch] %s: claude invoke failed: %s" % (slug, e))
        else:
            print("[watch] %s: %d new message(s) filed; pinged user to run /intake." % (slug, len(hits)))
    return touched


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(prog="propkb.watch")
    ap.add_argument("--interval", type=int, default=600, help="seconds between passes")
    ap.add_argument("--days", type=int, default=3, help="look-back window per pass")
    ap.add_argument("--once", action="store_true", help="single pass then exit")
    args = ap.parse_args(argv)
    while True:
        n = pass_once(days=args.days)
        print("[watch] pass complete; %d propert%s with new mail." % (n, "y" if n == 1 else "ies"))
        if args.once:
            return 0
        time.sleep(max(60, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
