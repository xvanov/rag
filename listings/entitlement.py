"""listings.entitlement -- E10 entitlement-path playbook (stretch).

For a development ``goal`` a listing's zoning may not permit by right, this
asks the SAME ``building-codes`` docrag corpus ``feasibility.py`` already
grounds buildability verdicts against -- which discretionary path applies
(variance / special-use permit / rezoning), the responsible board, the
typical process, and the NCGS 160D hooks -- with every substantive fact
carrying its docrag citation. Productizes the existing propkb BOTTLENECK
playbook logic (PRD sec.5 E10) for Scout listings.

REUSE, DON'T REBUILD: this module adds NO new ArcGIS/docrag plumbing of its
own. Parcel/zoning/location resolution comes from ``feasibility.assess()``
(the same in-memory, no-KB-pollution pull); the docrag call itself is
``feasibility._ask()``, which already wraps the cross-instance
``_building_codes_env()`` swap + the location-filtered balanced retrieval
feasibility.py uses. We only add the entitlement-specific QUESTIONS and light
text extraction (responsible board, recommended mechanism, a timeline phrase)
over the grounded answers -- never a fabricated fact.

Flow: ask ONE "is this by-right, and if not which mechanism?" question first.
If the grounded answer says by-right, stop there (no discretionary review to
play out). Otherwise ask the three mechanism-specific questions (variance /
special-use permit / rezoning) and build a path_options list from whichever
of those docrag actually grounded. No hardcoded answers: every query is
assembled from the listing's actual zoning code (via feasibility.assess) and
the caller's ``goal`` text.

Best-effort throughout: a docrag refusal or an unresolved parcel records a gap
and never crashes (mirrors feasibility.py's ``# noqa: BLE001`` style).
Research, not legal advice (PRD sec.9).

Public API:
    entitlement_path(listing: dict, goal: str, location: str | None = None) -> dict
"""

from __future__ import annotations

import re

from . import feasibility

# Same research-not-advice disclaimer feasibility.py stamps on every verdict.
DISCLAIMER = feasibility.DISCLAIMER

_MECHANISMS = ("variance", "special_use_permit", "rezoning")


def _questions(goal: str, zoning_code: str | None) -> dict:
    """Build the entitlement questions from the goal + the parcel's ACTUAL
    zoning code (no hardcoded values -- assembled from live facts only,
    mirrors feasibility._questions)."""
    zp = feasibility._zone_phrase(zoning_code)
    g = (goal or "the proposed development").strip()
    return {
        "applicability":
            "Is %s permitted by right in %s under the applicable UDO? If "
            "not, is the appropriate discretionary path a variance, a "
            "special use permit (or conditional use permit), or a rezoning?"
            % (g, zp),
        "variance":
            "Under NCGS 160D and the applicable UDO, when is a variance "
            "required for %s in %s, which board hears variance requests, "
            "and what is the typical process?" % (g, zp),
        "special_use_permit":
            "Under NCGS 160D and the applicable UDO, when is a special use "
            "permit or conditional use permit required for %s in %s, which "
            "board approves it, and what is the typical process?" % (g, zp),
        "rezoning":
            "Under NCGS 160D and the applicable UDO, when would %s require "
            "a rezoning (legislative or conditional) in %s, which board "
            "approves it, and what is the typical process and timeline?"
            % (g, zp),
    }


# Responsible-board labels we know to look for, read off the grounded
# answer's own words -- never invented. Leftmost mention in the text wins
# (the extraction below scans by position, not by this list's order).
_BOARD_PATTERNS = (
    ("Board of Adjustment", re.compile(r"board of adjustment", re.I)),
    ("Board of Commissioners", re.compile(r"board of commissioners", re.I)),
    ("Planning Commission", re.compile(r"planning (?:commission|board)", re.I)),
    ("City Council", re.compile(r"city council", re.I)),
    ("Town Council", re.compile(r"town council", re.I)),
    ("Governing Board", re.compile(r"governing board", re.I)),
)


def _extract_board(text: str | None) -> str | None:
    """Best-effort responsible-board label -- the one mentioned EARLIEST in
    the grounded answer (usually the approving body, ahead of a recommending
    body mentioned later). None when no known board name appears."""
    if not text:
        return None
    best: tuple[str, int] | None = None
    for label, pat in _BOARD_PATTERNS:
        m = pat.search(text)
        if m and (best is None or m.start() < best[1]):
            best = (label, m.start())
    return best[0] if best else None


_NOT_BY_RIGHT_RE = re.compile(r"not\s+(?:permitted|allowed)\s+by\s+right", re.I)
_BY_RIGHT_RE = re.compile(r"\b(?:permitted|allowed)\s+by\s+right\b", re.I)
_MECH_KEYWORDS = (
    ("variance", re.compile(r"\bvariance\b", re.I)),
    ("special_use_permit", re.compile(
        r"special[-\s]use permit|conditional[-\s]use permit|"
        r"special use|conditional use", re.I)),
    ("rezoning", re.compile(r"rezon(?:e|ing)|zoning map amendment", re.I)),
)


def _detect_recommended(text: str | None) -> str | None:
    """The mechanism the applicability answer ITSELF points to, read off its
    own words -- "by_right" if it affirmatively says so (and doesn't also say
    "not ... by right"), else the first discretionary mechanism it names,
    else None (undetermined)."""
    if not text:
        return None
    if _NOT_BY_RIGHT_RE.search(text) is None and _BY_RIGHT_RE.search(text):
        return "by_right"
    for key, pat in _MECH_KEYWORDS:
        if pat.search(text):
            return key
    return None


_TIMELINE_RE = re.compile(r"\b\d+\s*(?:to\s*\d+\s*)?(?:days?|weeks?|months?)\b",
                          re.I)


def _timeline_note(texts: list[str]) -> str:
    """A grounded timeline phrase if any mechanism answer states one; else a
    generic verify-locally note. Never a fabricated duration."""
    for t in texts:
        m = _TIMELINE_RE.search(t or "")
        if m:
            return ("Grounded provisions mention a timeline of \"%s\"; "
                    "confirm the current schedule and fees with the "
                    "jurisdiction's planning department." % m.group(0))
    return ("Timeline and fees were not stated in the retrieved provisions; "
            "verify the current process, timeline, and fees directly with "
            "the jurisdiction's planning department.")


def entitlement_path(listing: dict, goal: str, location: str | None = None) -> dict:
    """Entitlement-path playbook for a development ``goal`` on a listing.

    Resolves the governing location + zoning code via ``feasibility.assess``
    (in-memory, no KB files written), asks whether ``goal`` is by-right, and
    -- only when it isn't (or that can't be determined) -- asks each
    discretionary mechanism (variance / special-use permit / rezoning) when
    it applies, the responsible board, and the typical process, each cited.
    Best-effort: docrag refusals or an unresolved parcel record gaps and
    never raise.
    """
    listing = listing or {}
    verdict = feasibility.assess(listing, location=location)
    resolved = verdict.get("location") or feasibility.DEFAULT_LOCATION
    zoning_code = verdict.get("zoning_code")

    result: dict = {
        "goal": goal, "location": resolved,
        "path_options": [], "recommended": None, "timeline_note": None,
        "citations": [], "gaps": list(verdict.get("gaps") or []),
        "disclaimer": DISCLAIMER,
    }

    questions = _questions(goal, zoning_code)
    seen_cites: set = set()
    all_cites: list[dict] = []

    def _collect(res: dict) -> None:
        for c in feasibility._citations_of(res):
            key = (c.get("n"), c.get("designation"))
            if key not in seen_cites:
                seen_cites.add(key)
                all_cites.append(c)

    app_res = feasibility._ask(questions["applicability"], resolved)
    applicability_text = None
    if app_res.get("refused") or not app_res.get("answer"):
        result["gaps"].append(
            "applicability: docrag gave no grounded answer (%s)"
            % (app_res.get("refusal_reason") or "refused"))
    else:
        _collect(app_res)
        applicability_text = app_res["answer"]

    signal = _detect_recommended(applicability_text)

    if signal == "by_right":
        result["recommended"] = "by_right"
        result["timeline_note"] = (
            "Appears permitted by right based on the grounded answer; no "
            "discretionary review identified. Still confirm zoning "
            "compliance and required permits before building.")
        result["citations"] = all_cites
        return result

    # Not clearly by-right (or undetermined) -- ask each discretionary path.
    mechanism_texts: list[str] = []
    for mech in _MECHANISMS:
        res = feasibility._ask(questions[mech], resolved)
        if res.get("refused") or not res.get("answer"):
            result["gaps"].append(
                "%s: docrag gave no grounded answer (%s)"
                % (mech, res.get("refusal_reason") or "refused"))
            continue
        _collect(res)
        mechanism_texts.append(res["answer"])
        board = _extract_board(res["answer"])
        if board is None:
            result["gaps"].append(
                "%s: could not identify the responsible board from the "
                "grounded answer" % mech)
        cites = feasibility._citations_of(res)
        result["path_options"].append({
            "mechanism": mech, "board": board,
            "when_required": res["answer"],
            "citation": cites[0].get("designation") if cites else None,
        })

    if signal in _MECHANISMS:
        result["recommended"] = signal
    elif result["path_options"]:
        # Applicability was inconclusive/refused but at least one mechanism
        # question grounded an answer -- surface it rather than nothing.
        result["recommended"] = result["path_options"][0]["mechanism"]
        result["gaps"].append(
            "recommended: applicability answer did not name a mechanism; "
            "defaulted to the first grounded path option")
    else:
        result["gaps"].append(
            "recommended: could not determine the recommended mechanism "
            "(applicability answer inconclusive and no mechanism answers "
            "were grounded)")

    result["timeline_note"] = _timeline_note(mechanism_texts)
    result["citations"] = all_cites
    return result
