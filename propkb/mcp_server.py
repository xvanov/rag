"""propkb MCP server -- property knowledge-base tools (stdio).

A SECOND docrag instance, bound to the property data/index roots, exposed as its
own MCP server so a Claude agent can query a property's whole record separately
from the building-codes corpus. Tools: property_list / property_status /
property_ask / property_sources.

Run:  python -m propkb.mcp_server
Register in .mcp.json alongside the docrag server.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from concurrent.futures import TimeoutError as _FTimeout

# stdio hygiene (same rationale as docrag.mcp_server: stdout is the JSON-RPC
# channel; force all logging to stderr and silence INFO).
os.environ.setdefault("PYTHONUNBUFFERED", "1")
logging.basicConfig(level=logging.WARNING, stream=sys.stderr, force=True)
logging.disable(logging.INFO)
os.environ.setdefault("DOCRAG_RERANK", "0")

# CRITICAL: bind the engine to the PROPERTY instance BEFORE importing docrag
# modules, so every docrag call in this process uses properties/ + properties/.index.
from . import settings as _ps           # noqa: E402
_ps.apply_docrag_env()

from mcp.server.fastmcp import FastMCP   # noqa: E402

from . import store                      # noqa: E402
# Eager heavy imports in the main thread (numpy/sqlite-vec) -- see docrag notes.
from docrag.answer import answer as _answer   # noqa: E402
from docrag.query import rag_query            # noqa: E402

mcp = FastMCP("propkb")


def _tool_timeout() -> float:
    from docrag import settings as _s
    return float(_s.get("DOCRAG_TOOL_TIMEOUT", 150) or 150)


async def _run_bounded(fn):
    loop = asyncio.get_event_loop()
    try:
        return await asyncio.wait_for(loop.run_in_executor(None, fn),
                                      timeout=_tool_timeout())
    except (asyncio.TimeoutError, TimeoutError) as e:
        raise _FTimeout from e


@mcp.tool()
def property_list() -> str:
    """List all properties in the knowledge base (valid `slug` values)."""
    props = store.list_properties()
    return ("Properties: " + ", ".join(props)) if props else \
        "No properties yet. Create with: python -m propkb new --slug <slug> --address ..."


@mcp.tool()
def property_status(slug: str) -> str:
    """Return the property's CURRENT UNDERSTANDING + PLAN next-actions.

    Use this for a status read. For a specific question, use property_ask.
    """
    p = store.paths(slug)
    if not os.path.isdir(p["dir"]):
        return "No such property %r. Known: %s" % (slug, ", ".join(store.list_properties()))
    out = []
    for label, key in (("UNDERSTANDING", "understanding"), ("PLAN", "plan")):
        try:
            with open(p[key], "r", encoding="utf-8") as f:
                out.append("===== %s.md =====\n%s" % (label, f.read().strip()))
        except OSError:
            pass
    return "\n\n".join(out) or "(no understanding/plan written yet)"


@mcp.tool()
async def property_ask(question: str, slug: str, top_k: int = 12) -> str:
    """Ask a GROUNDED, CITED question over a property's whole record (facts,
    timeline, understanding, plan, and all filed source documents/emails).

    Args:
        question: natural-language question about the property.
        slug: which property (see property_list).
        top_k: passages to retrieve (3-20).
    """
    question = (question or "").strip()
    slug = (slug or "").strip().lower()
    if not question:
        return "ERROR: empty question."
    if not os.path.isdir(store.paths(slug)["dir"]):
        return "No such property %r. Known: %s" % (slug, ", ".join(store.list_properties()))
    top_k = max(3, min(int(top_k or 12), 20))

    def _work():
        return _answer(corpus=slug, query=question, top_k=top_k, balance=False)

    try:
        res = await _run_bounded(_work)
    except _FTimeout:
        return "ERROR: property_ask timed out (>%ds)." % int(_tool_timeout())
    except FileNotFoundError:
        return "ERROR: property %r has no built index. Run: python -m propkb index --slug %s" % (slug, slug)
    except Exception as e:  # noqa: BLE001
        return "ERROR: property_ask failed: %s" % e

    if res.get("refused"):
        return ("No grounded answer (%s). The property record does not cover this."
                % res.get("refusal_reason"))
    ans = (res.get("answer") or "").strip()
    auth = res.get("authorities") or []
    if auth:
        ans += "\n\nSources:\n" + "\n".join(
            "  [%s] %s" % (a.get("n"), a.get("designation") or "") for a in auth)
    return ans


@mcp.tool()
async def property_sources(question: str, slug: str, top_k: int = 12) -> str:
    """Retrieve raw passages from a property's record without LLM synthesis."""
    question = (question or "").strip()
    slug = (slug or "").strip().lower()
    if not question:
        return "ERROR: empty question."
    if not os.path.isdir(store.paths(slug)["dir"]):
        return "No such property %r." % slug
    top_k = max(3, min(int(top_k or 12), 20))

    def _work():
        return rag_query(slug, question, top_k=top_k, balance=False)

    try:
        r = await _run_bounded(_work)
    except _FTimeout:
        return "ERROR: retrieval timed out."
    except Exception as e:  # noqa: BLE001
        return "ERROR: retrieval failed: %s" % e
    results = r.get("results") or []
    if not results:
        return "No passages found (status: %s)." % r.get("status")
    out = []
    for i, c in enumerate(results, 1):
        out.append("[%d] %s\n%s" % (i, c.get("source_file") or "?",
                                    (c.get("text") or "").strip()[:1000]))
    return "\n\n".join(out)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
