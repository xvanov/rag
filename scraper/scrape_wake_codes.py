# -*- coding: utf-8 -*-
"""
Scrape Wake County, NC land-use ordinances into the docrag `building-codes`
corpus as clean UTF-8 markdown, one file per Article/Chapter.

Most source sites block plain HTTP (403), so this drives a real Chromium
browser via Playwright. Each jurisdiction uses the cheapest complete method
reverse-engineered from that site:

  wake-county  Municode SPA. The site's own JSON API rejects replayed calls
  wendell      (401), so we NAVIGATE the SPA by ?nodeId=... and passively
               capture the /api/CodesContent responses the app itself fetches.
               Navigating to an ordinance/UDO root node returns the flat list
               of every descendant doc (to enumerate top-level Articles/
               Chapters); navigating to one of those child nodes returns all of
               its sections WITH full HTML content in one response. Both targets
               share _scrape_municode(); they differ only in base URL + the id
               of the root node (wake: UNDEOR, wendell: UNDEORUD).

  raleigh      Self-hosted Drupal (udo.raleighnc.gov). Each of the 12 chapters
               has a "Printer-friendly version" Drupal book export
               (/book/export/html/N) that renders the ENTIRE chapter (all
               articles + sections) inline in one page. One fetch per chapter.

  garner       American Legal (codelibrary.amlegal.com). Full node tree comes
  zebulon      from the /api/toc-chain/<uuid>/<node>/ API (works via in-page
               fetch). Each leaf-section page fully renders that section's
               content as flat <div id="rid-<docId>"> blocks; we visit each
               leaf, slice out its own blocks, and assemble one file per Article.
               Both targets share _scrape_amlegal(); they differ only in the
               client path segment + root node (garner: garner/latest/garner_nc
               @ 0-0-0-5458, zebulon: zebulon/latest/zebulon_nc_udo @ 0-0-0-1).

  fuquay-varina  SPECIAL CASE. Fuquay's LDO is NOT printed in Municode: Part 9
               (COOR_PT9LADEOR) is a one-paragraph stub that merely links to an
               898-page FlippingBook (online.flippingbook.com/view/1031340238).
               The FlippingBook page-image `textblocks/*.xml` carry only glyph
               coordinates, but the `flash/search/search*.xml` search index
               carries the REAL page text (word tokens). We read the book's
               workspace.json TOC (Article -> start page), fetch the search
               index for each page (one CloudFront-signed URL, wildcard policy,
               fetched in-page), and emit one file per Article from its page
               range. Word-level extraction: real text, but NO heading/table
               structure and approximate reading order in multi-column/table
               pages. Flagged in each file's header.

Usage:
    .venv/Scripts/python.exe scraper/scrape_wake_codes.py --jurisdiction wake-county
    .venv/Scripts/python.exe scraper/scrape_wake_codes.py --jurisdiction wendell
    .venv/Scripts/python.exe scraper/scrape_wake_codes.py --jurisdiction zebulon
    .venv/Scripts/python.exe scraper/scrape_wake_codes.py --jurisdiction fuquay-varina
    .venv/Scripts/python.exe scraper/scrape_wake_codes.py --jurisdiction raleigh
    .venv/Scripts/python.exe scraper/scrape_wake_codes.py --jurisdiction garner
    # options: --out <dir>  --limit N (first N articles, for testing)
    #          --force (re-scrape existing files)  --no-headless

Idempotent & resumable: skips an Article/Chapter whose .md already exists
(unless --force). Polite: small delay between navigations.
"""
import argparse
import datetime
import pathlib
import re
import sys
import time

from bs4 import BeautifulSoup, NavigableString
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent
CORPUS = ROOT / "corpora" / "building-codes"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
SCRAPED = "2026-07-08"
DELAY = 0.8  # polite delay between navigations (s)

# stdout as utf-8 so section symbols etc. print without crashing the console
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


# --------------------------------------------------------------------------- #
# shared helpers
# --------------------------------------------------------------------------- #
def slugify(text: str) -> str:
    text = re.sub(r"[^\w\s-]", "", (text or "")).strip().lower()
    return re.sub(r"[-\s]+", "-", text)[:80] or "section"


INLINE_TAGS = {"a", "span", "b", "strong", "i", "em", "u", "sub", "sup", "font",
               "abbr", "small", "mark", "q", "cite", "time", "label", "s",
               "ins", "del", "code", "wbr", "button"}


def _render(node) -> str:
    """Serialize an element to text: block children get newline boundaries,
    inline children (defined-term links, emphasis, etc.) stay in the sentence."""
    parts = []
    for child in node.children:
        if isinstance(child, NavigableString):
            parts.append(str(child))
        elif child.name in ("h1", "h2", "h3", "h4", "h5", "h6"):
            level = int(child.name[1])
            txt = re.sub(r"\s+", " ", child.get_text(" ", strip=True))
            parts.append("\n\n" + "#" * min(level + 1, 6) + " " + txt + "\n\n")
        elif child.name == "table":
            rows = []
            for tr in child.find_all("tr"):
                cells = [re.sub(r"\s+", " ", c.get_text(" ", strip=True))
                         for c in tr.find_all(["td", "th"])]
                if any(cells):
                    rows.append(" | ".join(cells))
            parts.append("\n" + "\n".join(rows) + "\n")
        elif child.name == "br":
            parts.append("\n")
        elif child.name in INLINE_TAGS:
            parts.append(_render(child))
        else:  # block-level element
            parts.append("\n" + _render(child) + "\n")
    return "".join(parts)


def _tidy_line(ln: str) -> str:
    ln = re.sub(r"[ \t]+", " ", ln.replace("\xa0", " ")).strip()
    ln = re.sub(r"\s+([,.;:)])", r"\1", ln)   # stray space before punctuation
    ln = re.sub(r"\(\s+", "(", ln)
    return ln


def html_to_md(html: str) -> str:
    """Convert a fragment of ordinance HTML to readable plain text/markdown.

    Block elements become paragraph breaks; inline elements stay inline so that
    Garner's defined-term glossary links don't shatter sentences. Headings become
    markdown headings and tables become pipe-delimited rows.
    """
    if not html:
        return ""
    soup = BeautifulSoup(html, "lxml")
    for t in soup(["script", "style"]):
        t.decompose()
    root = soup.body or soup
    text = _render(root)
    out, blank = [], False
    for ln in text.splitlines():
        ln = _tidy_line(ln)
        if not ln:
            if not blank and out:
                out.append("")
            blank = True
            continue
        blank = False
        out.append(ln)
    return "\n".join(out).strip()


def header(ordinance: str, part_title: str, url: str) -> str:
    return (f"# {ordinance} — {part_title}\n"
            f"Source: {url}\n"
            f"Ordinance: {ordinance}\n"
            f"Scraped {SCRAPED} via Playwright (docrag scraper/scrape_wake_codes.py)\n"
            f"\n---\n\n")


def write_md(out_dir: pathlib.Path, prefix: str, slug: str, body: str) -> pathlib.Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    # ensure a blank line before every heading, then collapse runs of blanks
    body = re.sub(r"(?<!\n)\n(#{1,6} )", r"\n\n\1", body)
    body = re.sub(r"\n{3,}", "\n\n", body)
    path = out_dir / f"{prefix}__{slug}.md"
    path.write_text(body, encoding="utf-8")
    return path


def new_page(pw, headless):
    browser = pw.chromium.launch(headless=headless)
    ctx = browser.new_context(user_agent=UA, viewport={"width": 1400, "height": 1000})
    return browser, ctx, ctx.new_page()


# --------------------------------------------------------------------------- #
# Municode SPA  (shared by wake-county + wendell)
# --------------------------------------------------------------------------- #
WAKE_BASE = ("https://library.municode.com/nc/wake_county/codes/"
             "unified_development_ordinance")
WAKE_ORD = "Unified Development Ordinance of the County of Wake, North Carolina"

WENDELL_BASE = "https://library.municode.com/nc/wendell/codes/code_of_ordinances"
WENDELL_ORD = "Unified Development Ordinance (UDO) of the Town of Wendell, North Carolina"


def _scrape_municode(base_url, root_node, ordinance, prefix,
                     out_dir, headless, limit, force, tag):
    """Generic Municode-SPA scraper.

    Navigate the SPA by ?nodeId=<root_node> to get the flat descendant-doc list
    (used only to enumerate the top-level Articles/Chapters -- the ids matching
    ^<root_node>_<one-token>$), then one navigation per child node to pull all
    of its sections WITH content. Reused for wake-county (UNDEOR) and wendell
    (UNDEORUD): identical mechanics, different base URL + root node.
    """
    results = []
    child_re = re.compile(rf"^{re.escape(root_node)}_[^_]+$")
    with sync_playwright() as pw:
        browser, ctx, page = new_page(pw, headless)
        cap = {}

        def on_resp(r):
            try:
                if "/api/CodesContent?" in r.url and r.status == 200:
                    m = re.search(r"nodeId=([^&]+)", r.url)
                    if m:
                        cap[m.group(1)] = r.json()
            except Exception:
                pass
        page.on("response", on_resp)

        def load_node(node_id):
            """Navigate to a node and wait until its CodesContent is captured.
            Uses domcontentloaded (Municode never reaches networkidle: it polls
            in the background), then polls the response cache."""
            cap.pop(node_id, None)
            url = f"{base_url}?nodeId={node_id}"
            for attempt in range(3):
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=60000)
                except Exception:
                    pass
                for _ in range(40):
                    if node_id in cap:
                        return url
                    page.wait_for_timeout(500)
            return url

        # 1) load the ordinance body node -> flat list of every descendant doc
        print(f"[{tag}] loading ordinance body ({root_node}) ...")
        load_node(root_node)
        docs = cap.get(root_node, {}).get("Docs", [])
        articles = [d for d in docs if child_re.match(d["Id"])]
        articles.sort(key=lambda d: d.get("DocOrderId", 0))
        print(f"[{tag}] found {len(articles)} top-level articles/chapters")
        if not articles:
            print(f"[{tag}] WARNING: no child nodes under {root_node}; the node "
                  f"may be a stub or the id is wrong.")
        if limit:
            articles = articles[:limit]

        # 2) one navigation per article -> all its sections, with content
        for i, art in enumerate(articles, 1):
            aid = art["Id"]
            atitle = (art.get("Title") or aid).strip()
            slug = slugify(atitle)
            path = out_dir / f"{prefix}__{slug}.md"
            if path.exists() and not force:
                print(f"  [{i:02d}/{len(articles)}] skip (exists) {path.name}")
                results.append((atitle, path, path.stat().st_size))
                continue

            url = load_node(aid)
            adocs = cap.get(aid, {}).get("Docs", [])
            adocs = [d for d in adocs if d["Id"] == aid or d["Id"].startswith(aid + "_")]
            adocs.sort(key=lambda d: d.get("DocOrderId", 0))

            parts = [header(ordinance, atitle, url)]
            for d in adocs:
                title = (d.get("Title") or "").strip()
                content = html_to_md(d.get("Content") or "")
                if d["Id"] == aid:
                    if content:
                        parts.append(content + "\n")
                    continue
                if title:
                    parts.append(f"## {title}\n")
                if content:
                    parts.append(content + "\n")
            body = "\n".join(parts).strip() + "\n"
            path = write_md(out_dir, prefix, slug, body)
            words = len(body.split())
            print(f"  [{i:02d}/{len(articles)}] {path.name}  {len(adocs)} docs  {words} words")
            results.append((atitle, path, path.stat().st_size))
            time.sleep(DELAY)
        browser.close()
    return results


def scrape_wake(out_dir, headless, limit, force):
    return _scrape_municode(WAKE_BASE, "UNDEOR", WAKE_ORD, "wake-county-udo",
                            out_dir, headless, limit, force, "wake")


def scrape_wendell(out_dir, headless, limit, force):
    return _scrape_municode(WENDELL_BASE, "UNDEORUD", WENDELL_ORD, "wendell-udo",
                            out_dir, headless, limit, force, "wendell")


# --------------------------------------------------------------------------- #
# Raleigh  (Drupal book export)
# --------------------------------------------------------------------------- #
RAL_HOME = "https://udo.raleighnc.gov/"
RAL_ORD = "Part 10: Unified Development Ordinance, City of Raleigh, North Carolina"


def scrape_raleigh(out_dir, headless, limit, force):
    prefix = "raleigh-udo"
    results = []
    with sync_playwright() as pw:
        browser, ctx, page = new_page(pw, headless)

        print("[raleigh] loading home page for chapter list ...")
        page.goto(RAL_HOME, wait_until="domcontentloaded", timeout=120000)
        page.wait_for_timeout(2500)
        links = page.eval_on_selector_all(
            "a[href]",
            "els=>els.map(e=>[e.textContent.trim(), e.getAttribute('href')])")
        chapters, seen = [], set()
        for text, href in links:
            if href and re.match(r"^/chapter-\d+", href) and href not in seen:
                seen.add(href)
                chapters.append((text, href))
        print(f"[raleigh] found {len(chapters)} chapters")
        if limit:
            chapters = chapters[:limit]

        for i, (ctitle, href) in enumerate(chapters, 1):
            ctitle = ctitle.strip()
            slug = slugify(ctitle)
            path = out_dir / f"{prefix}__{slug}.md"
            if path.exists() and not force:
                print(f"  [{i:02d}/{len(chapters)}] skip (exists) {path.name}")
                results.append((ctitle, path, path.stat().st_size))
                continue

            chap_url = RAL_HOME.rstrip("/") + href
            page.goto(chap_url, wait_until="domcontentloaded", timeout=120000)
            page.wait_for_timeout(1500)
            export = page.eval_on_selector_all(
                "a[href]",
                "els=>els.map(e=>e.getAttribute('href')).filter(h=>h && h.indexOf('/book/export/html/')>=0)")
            if not export:
                print(f"  [{i:02d}/{len(chapters)}] NO export link for {ctitle}")
                continue
            exp_url = RAL_HOME.rstrip("/") + export[0]
            page.goto(exp_url, wait_until="domcontentloaded", timeout=120000)
            page.wait_for_timeout(1500)
            soup = BeautifulSoup(page.content(), "lxml")
            # NB: do not strip <header>/<footer> here — Drupal wraps each
            # section title in a <header>, so removing them drops all headings.
            for sel in ["script", "style", "nav", "#toolbar-administration",
                        ".contextual", ".book-navigation"]:
                for t in soup.select(sel):
                    t.decompose()
            container = (soup.select_one(".region-content") or soup.select_one("main")
                         or soup.body)
            body = header(RAL_ORD, ctitle, exp_url) + html_to_md(str(container))
            body = body.strip() + "\n"
            path = write_md(out_dir, prefix, slug, body)
            words = len(body.split())
            print(f"  [{i:02d}/{len(chapters)}] {path.name}  {words} words  (export {export[0]})")
            results.append((ctitle, path, path.stat().st_size))
            time.sleep(DELAY)
        browser.close()
    return results


# --------------------------------------------------------------------------- #
# American Legal  (shared by garner + zebulon)
# --------------------------------------------------------------------------- #
GAR_HOST = "https://codelibrary.amlegal.com"
GAR_CLIENT = "garner/latest/garner_nc"
GAR_ROOT_NODE = "0-0-0-5458"
GAR_ORD = "Unified Development Ordinance of the Town of Garner, North Carolina"

ZEB_CLIENT = "zebulon/latest/zebulon_nc_udo"
ZEB_ROOT_NODE = "0-0-0-1"
ZEB_ORD = "Unified Development Ordinance of the Town of Zebulon, North Carolina"


def _gar_tocchain(page, uuid, node):
    """Return the toc-chain array for `node` via in-page fetch (API works here)."""
    import json
    url = f"{GAR_HOST}/api/toc-chain/{uuid}/{node}/"
    raw = page.evaluate(
        """async (u) => { const r = await fetch(u, {headers:{'Accept':'application/json'}});
                          return r.ok ? await r.text() : ''; }""", url)
    return json.loads(raw) if raw else []


def _gar_children(chain, node):
    """From a toc-chain array, the children of `node` (the array element whose
    doc_id == node carries the expanded children)."""
    for el in chain:
        if el.get("doc_id") == node:
            return el.get("children") or []
    return []


def _scrape_amlegal(client_path, root_node, ordinance, prefix,
                    out_dir, headless, limit, force, tag):
    """Generic American Legal scraper.

    Walk the /api/toc-chain/<uuid>/<node>/ tree (BFS to leaf sections), then
    visit each leaf page and slice out its own <div id=rid-...> content blocks,
    assembling one file per top-level Article. Reused for garner and zebulon:
    identical mechanics, different client path segment + root node.
    """
    root_url = f"{GAR_HOST}/codes/{client_path}/{root_node}"

    def node_url(nid):
        return f"{GAR_HOST}/codes/{client_path}/{nid}"

    results = []
    with sync_playwright() as pw:
        browser, ctx, page = new_page(pw, headless)

        print(f"[{tag}] loading root for code uuid ...")
        uuid_box = {}

        def on_req(r):
            m = re.search(r"/api/toc-chain/([0-9a-f-]{36})/", r.url)
            if m:
                uuid_box["u"] = m.group(1)
        page.on("request", on_req)
        page.goto(root_url, wait_until="domcontentloaded", timeout=120000)
        for _ in range(20):
            if "u" in uuid_box:
                break
            page.wait_for_timeout(500)
        uuid = uuid_box.get("u")
        if not uuid:
            raise SystemExit(f"[{tag}] could not determine code uuid")
        print(f"[{tag}] code uuid {uuid}")

        # top-level nodes (articles + appendices)
        root_chain = _gar_tocchain(page, uuid, root_node)
        top = _gar_children(root_chain, root_node)
        print(f"[{tag}] {len(top)} top-level nodes")

        # gather toc-node ids used as content-slice boundaries. Seed with every
        # top-level id so the last leaf of an article stops at the next article
        # heading (which hasn't been BFS'd yet).
        all_ids = {t["doc_id"] for t in top}

        def bfs(node):
            all_ids.add(node)
            chain = _gar_tocchain(page, uuid, node)
            kids = _gar_children(chain, node)
            leaves = []
            if not kids:
                return [node]
            for k in kids:
                leaves += bfs(k["doc_id"])
                time.sleep(0.15)
            return leaves

        tops = top[:limit] if limit else top
        for i, t in enumerate(tops, 1):
            tid = t["doc_id"]
            ttitle = (t.get("title") or tid).strip()
            slug = slugify(ttitle)
            path = out_dir / f"{prefix}__{slug}.md"
            if path.exists() and not force:
                print(f"  [{i:02d}/{len(tops)}] skip (exists) {path.name}")
                results.append((ttitle, path, path.stat().st_size))
                continue

            # Recycle the page between articles: American Legal's React app leaks
            # memory across many navigations and degrades to a crawl. A fresh page
            # (re-anchored on the code origin so in-page API fetch still works)
            # keeps each article fast.
            try:
                page.close()
            except Exception:
                pass
            page = ctx.new_page()
            page.goto(root_url, wait_until="domcontentloaded", timeout=120000)
            page.wait_for_timeout(1500)

            all_ids.add(tid)
            leaves = bfs(tid)
            print(f"  [{i:02d}/{len(tops)}] {ttitle[:45]} -> {len(leaves)} leaf sections")

            parts = [header(ordinance, ttitle, node_url(tid))]
            emitted = set()
            for leaf in leaves:
                block_md = _gar_leaf_content(page, leaf, all_ids, emitted, node_url)
                if block_md:
                    parts.append(block_md)
                time.sleep(DELAY)
            body = "\n".join(parts).strip() + "\n"
            path = write_md(out_dir, prefix, slug, body)
            words = len(body.split())
            print(f"       wrote {path.name}  {words} words")
            results.append((ttitle, path, path.stat().st_size))
        browser.close()
    return results


def scrape_garner(out_dir, headless, limit, force):
    return _scrape_amlegal(GAR_CLIENT, GAR_ROOT_NODE, GAR_ORD, "garner-udo",
                           out_dir, headless, limit, force, "garner")


def scrape_zebulon(out_dir, headless, limit, force):
    return _scrape_amlegal(ZEB_CLIENT, ZEB_ROOT_NODE, ZEB_ORD, "zebulon-udo",
                           out_dir, headless, limit, force, "zebulon")


def _gar_leaf_content(page, leaf, toc_ids, emitted, node_url):
    """Visit a leaf node page and return its own content (markdown).

    The leaf page renders flat <div id="rid-<docId>"> content blocks in
    document order. The leaf's own content = the block #rid-<leaf> and every
    following sibling block up to the next block whose id is another toc node.
    A global `emitted` set guards against window overlap between leaves.
    """
    ok = False
    for attempt in range(3):
        try:
            page.goto(node_url(leaf), wait_until="domcontentloaded", timeout=60000)
            ok = True
            break
        except Exception:
            page.wait_for_timeout(1000)
    if not ok:
        print(f"       ! failed to load leaf {leaf}")
        return ""
    for _ in range(12):
        if page.query_selector(f"#rid-{leaf}"):
            break
        page.wait_for_timeout(400)
    html = page.content()
    soup = BeautifulSoup(html, "lxml")
    body = soup.select_one(".codenav__section-body")
    if not body:
        return ""
    # drop hidden definition popups, and collapse each defined-term popup button
    # (and its block wrapper div) down to plain inline text so it flows in-line.
    for drawer in body.select(".annotation-drawer"):
        drawer.decompose()
    for btn in body.select("button"):
        term = btn.get_text(" ", strip=True)
        wrapper = btn.parent
        if (wrapper and wrapper.name == "div"
                and len(wrapper.find_all(recursive=False)) == 1
                and wrapper.get_text(" ", strip=True) == term):
            wrapper.replace_with(term)
        else:
            btn.replace_with(term)
    boxes = body.select("div[id^=rid-]")
    start = next((k for k, b in enumerate(boxes) if b.get("id") == f"rid-{leaf}"), None)
    chosen = []
    if start is None:
        # window did not include the leaf's own heading; take not-yet-emitted
        # blocks up to the first other toc node
        for b in boxes:
            rid = b.get("id", "")[4:]
            if rid in toc_ids and rid != leaf and chosen:
                break
            if rid and rid not in emitted:
                chosen.append(b)
    else:
        for b in boxes[start:]:
            rid = b.get("id", "")[4:]
            if rid in toc_ids and rid != leaf and b is not boxes[start]:
                break
            chosen.append(b)
    blocks, seen_here = [], False
    for b in chosen:
        rid = b.get("id", "")[4:]
        if rid in emitted:
            continue
        emitted.add(rid)
        seen_here = True
        # Each rbox is one paragraph. Collapse it to a single line unless it
        # carries real structure (a heading or a table), which html_to_md keeps.
        if b.find("table") or b.find(["h1", "h2", "h3", "h4", "h5", "h6"]):
            md = html_to_md(str(b))
        else:
            md = _tidy_line(re.sub(r"\s+", " ", b.get_text(" ", strip=True)))
        if md:
            blocks.append(md)
    if not seen_here:
        return ""
    return "\n\n".join(blocks)


# --------------------------------------------------------------------------- #
# Fuquay-Varina  (FlippingBook text layer -- Municode Part 9 is only a stub)
# --------------------------------------------------------------------------- #
FUQUAY_VIEW = "https://online.flippingbook.com/view/1031340238/"
FUQUAY_ORD = ("Part 9 Land Development Ordinance, Town of Fuquay-Varina, "
              "North Carolina")
_FB_STX = "\x02"  # FlippingBook search-index field separator


def _fb_parse_search(xml):
    """FlippingBook search index (search####.xml) -> word tokens in page
    reading order. Each content line is 'WORD\\x02<coords...>'; the first line
    is a bare word-count header (no \\x02), which we skip."""
    words = []
    for ln in xml.split("\n"):
        ln = ln.strip("\r")
        if _FB_STX not in ln:
            continue
        w = ln.split(_FB_STX, 1)[0].strip()
        if w:
            words.append(w)
    return words


def scrape_fuquay(out_dir, headless, limit, force):
    import json
    prefix = "fuquay-varina-ldo"
    results = []
    with sync_playwright() as pw:
        browser, ctx, page = new_page(pw, headless)
        tmpl = {}

        def on_resp(r):
            u = r.url
            if "flash/search/search" in u and "search" not in tmpl:
                tmpl["search"] = u
            elif "html/workspace.json" in u:
                tmpl["workspace"] = u
            elif "common/pager.json" in u:
                tmpl["pager"] = u
        page.on("response", on_resp)

        print("[fuquay] NOTE: Municode Part 9 (COOR_PT9LADEOR) is only a stub "
              "that links offsite; the LDO text lives in an 898-page "
              "FlippingBook. Scraping its search text layer instead.")
        print("[fuquay] loading FlippingBook viewer ...")
        for _ in range(3):
            try:
                page.goto(FUQUAY_VIEW, wait_until="domcontentloaded", timeout=90000)
            except Exception:
                pass
            for _ in range(60):
                if {"search", "workspace"} <= set(tmpl):
                    break
                page.wait_for_timeout(500)
            if {"search", "workspace"} <= set(tmpl):
                break
        if "search" not in tmpl or "workspace" not in tmpl:
            print(f"[fuquay] FAILED: could not capture FlippingBook resources "
                  f"(captured {sorted(tmpl)}); aborting cleanly.")
            browser.close()
            return results

        def fetch(u):
            return page.evaluate(
                """async (u) => { try { const r = await fetch(u);
                     return r.ok ? await r.text() : ('ERR '+r.status); }
                     catch(e){ return 'EXC '+e; } }""", u)

        ws = json.loads(fetch(tmpl["workspace"]))
        toc = ws.get("toc", {}).get("children", [])
        ldo = next((c for c in toc
                    if "Land Development" in (c.get("title") or "")
                    and c.get("children")), None)
        if not ldo:
            print("[fuquay] FAILED: no 'Land Development Ordinance' node in the "
                  "FlippingBook TOC; aborting cleanly.")
            browser.close()
            return results
        all_kids = [a for a in ldo["children"] if a.get("page")]
        arts = sorted(all_kids, key=lambda a: a["page"])

        # total page count (end of the last article)
        total = None
        if "pager" in tmpl:
            try:
                pg = json.loads(fetch(tmpl["pager"]))
                nums = [int(k) for k in pg.get("pages", {}) if str(k).isdigit()]
                total = max(nums) if nums else None
            except Exception:
                total = None
        if not total:
            total = arts[-1]["page"] + 40  # fallback slack
        print(f"[fuquay] {len(arts)} articles/appendices across {total} pages")

        # per-page word cache so adjacent ranges never refetch a page
        pcache = {}

        def page_words(pn):
            if pn in pcache:
                return pcache[pn]
            u = re.sub(r"search\d{4}\.xml", f"search{pn:04d}.xml", tmpl["search"])
            raw = ""
            for _ in range(3):
                raw = fetch(u)
                if raw and not raw.startswith(("ERR", "EXC")):
                    break
                page.wait_for_timeout(600)
            good = raw and not raw.startswith(("ERR", "EXC"))
            words = _fb_parse_search(raw) if good else []
            pcache[pn] = words
            time.sleep(0.04)
            return words

        run_arts = arts[:limit] if limit else arts
        for i, art in enumerate(run_arts, 1):
            atitle = re.sub(r"\s+", " ", (art.get("title") or f"section-{i}")).strip()
            slug = slugify(atitle)
            path = out_dir / f"{prefix}__{slug}.md"
            if path.exists() and not force:
                print(f"  [{i:02d}/{len(run_arts)}] skip (exists) {path.name}")
                results.append((atitle, path, path.stat().st_size))
                continue

            start = art["page"]
            higher = [a["page"] for a in all_kids if a["page"] > start]
            end = (min(higher) - 1) if higher else total

            page_blocks, misses = [], 0
            for pn in range(start, end + 1):
                w = page_words(pn)
                if w:
                    page_blocks.append(f"[p. {pn}]\n" + " ".join(w))
                else:
                    misses += 1
            body_text = "\n\n".join(page_blocks)

            src = f"{FUQUAY_VIEW} (pages {start}-{end})"
            caveat = (
                "_Extracted from the FlippingBook text layer of Fuquay-Varina's "
                "official LDO PDF; Municode's Part 9 carries only a pointer to it. "
                "Word-level text: real content, but headings/tables and multi-column "
                "reading order are approximate. Page markers [p. N] preserved._\n\n")
            body = (header(FUQUAY_ORD, atitle, src) + caveat + body_text).strip() + "\n"
            path = write_md(out_dir, prefix, slug, body)
            words = len(body.split())
            miss_note = f"  ({misses} pages empty)" if misses else ""
            print(f"  [{i:02d}/{len(run_arts)}] {path.name}  pages {start}-{end}  "
                  f"{words} words{miss_note}")
            results.append((atitle, path, path.stat().st_size))
        browser.close()
    return results


# --------------------------------------------------------------------------- #
JOBS = {
    "wake-county": (scrape_wake, "wake-county"),
    "raleigh": (scrape_raleigh, "raleigh"),
    "garner": (scrape_garner, "garner"),
    "wendell": (scrape_wendell, "wendell"),
    "zebulon": (scrape_zebulon, "zebulon"),
    "fuquay-varina": (scrape_fuquay, "fuquay-varina"),
}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--jurisdiction", required=True, choices=sorted(JOBS))
    ap.add_argument("--out", help="output dir (default: corpora/building-codes/<jurisdiction>)")
    ap.add_argument("--limit", type=int, default=0, help="only first N articles/chapters")
    ap.add_argument("--force", action="store_true", help="re-scrape existing files")
    ap.add_argument("--no-headless", dest="headless", action="store_false")
    ap.set_defaults(headless=True)
    args = ap.parse_args()

    fn, default_sub = JOBS[args.jurisdiction]
    out_dir = pathlib.Path(args.out) if args.out else (CORPUS / default_sub)
    out_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    results = fn(out_dir, args.headless, args.limit, args.force)

    print(f"\n===== {args.jurisdiction} summary =====")
    total_bytes = 0
    total_words = 0
    for title, path, size in results:
        words = len(path.read_text(encoding="utf-8").split()) if path.exists() else 0
        total_bytes += size
        total_words += words
        print(f"  {path.name:60} {words:>7} words  {size:>8} bytes")
    print(f"  {'TOTAL':60} {total_words:>7} words  {total_bytes:>8} bytes "
          f"across {len(results)} files  ({time.time()-t0:.0f}s)")


if __name__ == "__main__":
    main()
