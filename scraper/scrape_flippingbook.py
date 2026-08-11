# -*- coding: utf-8 -*-
"""
Scrape FlippingBook page-flip publications into the docrag `building-codes`
corpus as clean UTF-8 markdown, one file per top-level TOC entry (Article/Chapter).

FlippingBook ("cld.bz" / "online.flippingbook.com") publications are NOT rasterized
scans -- they carry a real vector text layer. The cheapest clean-text path (no OCR):

  * The viewer loads a signed CloudFront asset per page. The signature uses a CUSTOM
    policy whose Resource is a WILDCARD over the whole publication prefix
    (e.g. ".../00203455/*"), so ONE captured signature fetches every file under it.
  * The per-page SEARCH index `.../search/search<NNNN>.xml` is a plain-text word list:
    each line is `WORD \x02 x \x02 y \x02 ...` (coords in page-units x10). We reconstruct
    reading order by grouping words into lines by y then sorting by x.
  * `workspace.js`/`workspace.json` carries the full nested TOC (title + page number,
    where page number == the search-file index), used to split output by Article.

So the recipe: drive Chromium once with Playwright to capture a signed asset URL
(-> wildcard base + signature) + read the TOC, then pull every page's search XML with
plain `requests` and rebuild the text. Signatures expire (~30 min), so the token is
re-minted automatically if it goes stale mid-run.

Usage:
    .venv/Scripts/python.exe scraper/scrape_flippingbook.py --pub morrisville
    .venv/Scripts/python.exe scraper/scrape_flippingbook.py --pub wake-forest
    # options: --out <dir>  --limit N (first N articles)  --force (re-scrape)
    #          --no-headless

Idempotent & resumable: skips an Article whose .md already exists (unless --force).
"""
import argparse
import base64
import json
import pathlib
import re
import sys
import time
from urllib.parse import urlparse, urlsplit

import requests
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent
CORPUS = ROOT / "corpora" / "building-codes"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
SCRAPED = "2026-07-08"

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


# --------------------------------------------------------------------------- #
# publication config. Paths are RELATIVE to the wildcard-signed base (decoded
# from the CloudFront policy Resource). These are site plumbing, not content.
# --------------------------------------------------------------------------- #
JOBS = {
    "morrisville": {
        "url": ("https://user-cjghrlw.cld.bz/"
                "UDO-Adopted-Version-February-2025-BAC-Updated-4-29-25"),
        "ordinance": ("Unified Development Ordinance of the Town of Morrisville, "
                      "North Carolina (adopted February 2025)"),
        "prefix": "morrisville-udo",
        "out": "morrisville",
        "workspace_rel": "workspace.js",
        "pager_rel": "pager.js",
        "search_rel": "mobile/search/search{n:04d}.xml",
    },
    "wake-forest": {
        "url": "https://online.flippingbook.com/view/703728449/",
        "ordinance": ("Unified Development Ordinance of the Town of Wake Forest, "
                      "North Carolina (consolidated reprint July 19, 2022)"),
        "prefix": "wake-forest-udo",
        "out": "wake-forest",
        "workspace_rel": "html/workspace.json",
        "pager_rel": "common/pager.json",
        "search_rel": "flash/search/search{n:04d}.xml",
    },
}


def slugify(text: str) -> str:
    text = re.sub(r"[^\w\s-]", "", (text or "")).strip().lower()
    return re.sub(r"[-\s]+", "-", text)[:80] or "section"


def header(ordinance: str, part_title: str, url: str, page_range: str) -> str:
    return (f"# {ordinance} — {part_title}\n"
            f"Source: {url}\n"
            f"Ordinance: {ordinance}\n"
            f"Pages: {page_range}\n"
            f"Scraped {SCRAPED} via Playwright + FlippingBook text layer "
            f"(docrag scraper/scrape_flippingbook.py)\n"
            f"\n---\n\n")


# --------------------------------------------------------------------------- #
# 1) mint a signed token by driving the viewer once
# --------------------------------------------------------------------------- #
def mint_token(cfg, headless):
    """Load the viewer, capture one signed asset URL, return (base_url, query, npages).

    base_url = the wildcard resource prefix (ends with '/'); every publication file
    is base_url + <relative path> + '?' + query.
    """
    captured = {"url": None, "npages": None}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        page = browser.new_context(user_agent=UA,
                                   viewport={"width": 1400, "height": 1000}).new_page()

        def on_resp(r):
            if "/textblocks/" in r.url and captured["url"] is None:
                captured["url"] = r.url
        page.on("response", on_resp)

        page.goto(cfg["url"], wait_until="domcontentloaded", timeout=120000)
        for _ in range(40):
            if captured["url"]:
                break
            page.wait_for_timeout(500)
        # total page count off the pager UI
        try:
            txt = page.eval_on_selector(".pager-total", "e=>e.textContent") or ""
            m = re.search(r"(\d+)", txt)
            if m:
                captured["npages"] = int(m.group(1))
        except Exception:
            pass
        browser.close()

    signed = captured["url"]
    if not signed:
        raise SystemExit("[fb] could not capture a signed asset URL")
    sp = urlsplit(signed)
    query = sp.query
    # decode the CloudFront custom policy to get the wildcard Resource base
    pol = re.search(r"Policy=([^&]+)", query)
    base = None
    if pol:
        b64 = pol.group(1).replace("-", "+").replace("_", "=").replace("~", "/")
        try:
            doc = json.loads(base64.b64decode(b64 + "=" * (-len(b64) % 4)))
            res = doc["Statement"][0]["Resource"]        # e.g. http*://host/prefix/*
            res = res.replace("http*://", "https://").rstrip("*")
            base = res
        except Exception:
            pass
    if not base:
        # fallback: strip back to the textblocks folder's parent
        base = signed.split("textblocks/")[0]
    return base, query, captured["npages"]


def build_url(base, query, rel):
    return f"{base}{rel}?{query}"


# --------------------------------------------------------------------------- #
# 2) fetch helpers (requests, with token refresh on staleness)
# --------------------------------------------------------------------------- #
class Fetcher:
    def __init__(self, cfg, headless):
        self.cfg = cfg
        self.headless = headless
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": UA,
            "Referer": urlparse(cfg["url"]).scheme + "://" + urlparse(cfg["url"]).netloc + "/",
        })
        self.base, self.query, self.npages = mint_token(cfg, headless)
        self.minted = time.time()

    def _refresh(self):
        print("[fb] refreshing signed token ...")
        self.base, self.query, np = mint_token(self.cfg, self.headless)
        self.minted = time.time()
        if np:
            self.npages = np

    def get(self, rel):
        """GET base+rel. Returns (status, bytes). Refreshes token if it looks stale."""
        if time.time() - self.minted > 20 * 60:      # pre-empt ~30 min expiry
            self._refresh()
        url = build_url(self.base, self.query, rel)
        r = self.session.get(url, timeout=30)
        if r.status_code == 403:
            # could be expiry OR a legitimately text-less page. Refresh once & retry;
            # if still 403 it's a no-text page.
            self._refresh()
            url = build_url(self.base, self.query, rel)
            r = self.session.get(url, timeout=30)
        return r.status_code, r.content


# --------------------------------------------------------------------------- #
# 3) FlippingBook search-XML -> reading-order text
# --------------------------------------------------------------------------- #
def search_xml_to_text(raw: bytes) -> str:
    """Reconstruct page text from a FlippingBook search index.

    Each newline-separated entry is `WORD \x02 x \x02 y \x02 ...` with coords in
    page-units x10. Words are grouped into lines by y (baseline) then sorted by x.
    Entries without coordinates (page-level counters) are ignored.
    """
    if not raw:
        return ""
    text = raw.decode("utf-8-sig", "replace")
    words = []  # (y, x, word)
    for entry in text.split("\n"):
        if not entry or "\x02" not in entry:
            continue
        parts = entry.split("\x02")
        word = parts[0].strip()
        if not word:
            continue
        try:
            x = int(float(parts[1]))
            y = int(float(parts[2]))
        except (IndexError, ValueError):
            continue
        # trailing \x03-offset noise can cling to the word's last field; word is
        # parts[0] so it's already clean. Drop stray control chars just in case.
        word = re.sub(r"[\x00-\x1f]", "", word)
        if word:
            words.append((y, x, word))
    if not words:
        return ""
    words.sort(key=lambda t: (t[0], t[1]))
    lines, cur, cur_y = [], [], None
    LINE_TOL = 40  # ~4pt: same visual line shares near-identical baseline
    for y, x, w in words:
        if cur_y is None or abs(y - cur_y) <= LINE_TOL:
            cur.append((x, w))
            cur_y = y if cur_y is None else cur_y
        else:
            cur.sort(key=lambda t: t[0])
            lines.append(" ".join(w for _, w in cur))
            cur, cur_y = [(x, w)], y
    if cur:
        cur.sort(key=lambda t: t[0])
        lines.append(" ".join(w for _, w in cur))
    return "\n".join(lines).strip()


# --------------------------------------------------------------------------- #
# 4) main scrape
# --------------------------------------------------------------------------- #
def load_toc(fetcher, cfg):
    st, raw = fetcher.get(cfg["workspace_rel"])
    if st != 200:
        raise SystemExit(f"[fb] workspace fetch failed ({st})")
    doc = json.loads(raw.decode("utf-8-sig", "replace"))
    return doc.get("toc", {}).get("children", []) or []


def page_count(fetcher, cfg):
    if fetcher.npages:
        return fetcher.npages
    st, raw = fetcher.get(cfg["pager_rel"])
    if st == 200:
        try:
            pg = json.loads(raw.decode("utf-8-sig", "replace"))
            nums = [int(k) for k in pg.get("pages", {}) if str(k).isdigit()]
            if nums:
                return max(nums)
        except Exception:
            pass
    return None


def scrape(cfg, out_dir, headless, limit, force):
    prefix = cfg["prefix"]
    fetcher = Fetcher(cfg, headless)
    top = load_toc(fetcher, cfg)
    npages = page_count(fetcher, cfg)
    print(f"[fb] {len(top)} top-level TOC entries, {npages} pages total")

    # build (title, start_page, end_page) ranges from top-level TOC entries
    ranges = []
    for i, node in enumerate(top):
        start = int(node.get("page", 1))
        end = (int(top[i + 1].get("page", start)) - 1) if i + 1 < len(top) else (npages or start)
        if end < start:
            end = start
        ranges.append((node.get("title", f"Section {i+1}").strip(), start, end))
    if limit:
        ranges = ranges[:limit]

    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    page_cache = {}  # page-> text, so overlapping range ends aren't refetched

    def page_text(pn):
        if pn not in page_cache:
            st, raw = fetcher.get(cfg["search_rel"].format(n=pn))
            page_cache[pn] = search_xml_to_text(raw) if st == 200 else ""
        return page_cache[pn]

    for i, (title, start, end) in enumerate(ranges, 1):
        slug = slugify(title)
        path = out_dir / f"{prefix}__{i:02d}-{slug}.md"
        if path.exists() and not force:
            print(f"  [{i:02d}/{len(ranges)}] skip (exists) {path.name}")
            results.append((title, path))
            continue

        page_range = f"{start}-{end}" if end != start else f"{start}"
        parts = [header(cfg["ordinance"], title, cfg["url"], page_range)]
        for pn in range(start, end + 1):
            t = page_text(pn)
            if t:
                parts.append(f"[page {pn}]\n\n{t}\n")
        body = "\n".join(parts).strip() + "\n"
        body = re.sub(r"\n{3,}", "\n\n", body)
        path.write_text(body, encoding="utf-8")
        words = len(body.split())
        print(f"  [{i:02d}/{len(ranges)}] {path.name}  pp{page_range}  {words} words")
        results.append((title, path))
    return results


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pub", required=True, choices=sorted(JOBS))
    ap.add_argument("--out", help="output dir (default: corpora/building-codes/<pub>)")
    ap.add_argument("--limit", type=int, default=0, help="only first N articles")
    ap.add_argument("--force", action="store_true", help="re-scrape existing files")
    ap.add_argument("--no-headless", dest="headless", action="store_false")
    ap.set_defaults(headless=True)
    args = ap.parse_args()

    cfg = JOBS[args.pub]
    out_dir = pathlib.Path(args.out) if args.out else (CORPUS / cfg["out"])

    t0 = time.time()
    results = scrape(cfg, out_dir, args.headless, args.limit, args.force)

    print(f"\n===== {args.pub} summary =====")
    total_words = 0
    for title, path in results:
        words = len(path.read_text(encoding="utf-8").split()) if path.exists() else 0
        total_words += words
        print(f"  {path.name:60} {words:>7} words")
    print(f"  {'TOTAL':60} {total_words:>7} words across {len(results)} files "
          f"({time.time()-t0:.0f}s)")


if __name__ == "__main__":
    main()
