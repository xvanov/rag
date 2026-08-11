"""listings.monitor -- E8 standing deal monitor + NTFY notifications.

A saved *thesis* (``store.save_thesis``) is a semantic query + hard filters +
commute destinations + per-strategy thresholds. ``run_thesis`` re-runs one
thesis against the current listings store and alerts ONLY on a listing that is
NEW (never alerted for this thesis before) AND clears all three gates:

    matched     -- it came back from search() at all (hard filters + the
                   semantic query already did this work; see listings.search).
    reachable   -- commute to the thesis's destinations is within
                   thresholds.max_commute_min, IF that threshold is set AND a
                   commute provider was actually available. A provider/data
                   gap is recorded as "unknown", never treated as a fail --
                   we don't want a missing Google/TravelTime key to silently
                   suppress every alert (PRD sec.8: commute needs a paid key).
    buildable   -- the feasibility verdict shows > 0 by-right units, IF
                   thresholds.require_buildable is set. Undetermined
                   buildability with require_buildable=True fails CLOSED
                   (PRD sec.9: never silently assume max density / buildable).
    pencils     -- the requested strategy's score >= thresholds.min_score, IF
                   that threshold is set. An insufficient-data score also
                   fails closed here -- "pencils out" is the whole point of
                   this alert, so an unscored candidate cannot claim it.

Dedup mirrors ``propkb.email``'s ``.processed.json`` pattern, but the "seen"
ledger already exists as a first-class typed table (``theses``/``thesis_hits``,
PRD sec.7) instead of a JSON sidecar file: ``store.thesis_seen`` /
``store.record_thesis_hit`` are the exact same idea (never re-alert on the
same (thesis, listing) pair) implemented on the typed store this package
already has, rather than reinventing a second dedup mechanism.

Thesis spec shape (``theses.spec_json``, set via ``store.save_thesis``):

    {"query": <semantic query str>, "filters": {<store._filter_clause hard
     filters>}, "destinations": [{"name", "lat", "lon"}, ...],
     "scenario": "typical" | "best" | "peak", "strategy": <one of
     score.STRATEGIES>, "location": <docrag location override, optional>,
     "thresholds": {"min_score": int | None, "max_commute_min": float | None,
                    "require_buildable": bool}}

NOTIFICATION is injectable on purpose: ``run_thesis``/``run_all`` take a
``notifier(title, message)`` callable. Tests ALWAYS pass a fake -- a real push
is never sent from a test. Without one, the default path posts to ntfy.sh via
stdlib ``urllib`` (mirrors ``propkb.email.notify``'s ntfy branch), gated on
``settings.ntfy_topic()``; a missing topic records a gap and skips the HTTP
call entirely rather than crashing or firing at an unconfigured endpoint.

FHA FIREWALL (PRD sec.9): this module ranks/alerts purely on
match+reachable+buildable+score. It must NEVER import ``listings.agents`` (in
any form) or reference the listing-agent name-inference signal walled off in
that module -- enforced by ``tests/_fha_firewall.py::assert_fha_firewall``
against this file. Keep it that way in any edit here.

Every rationale ends with a "Research, not investment advice -- verify
locally" note (PRD sec.9/sec.4 use case 6): this is a decision-support alert,
not an autonomous buy signal.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Optional

from . import enrich as _enrich
from . import search as _search
from . import settings
from . import store

# How many semantic-search candidates to pull per thesis run before applying
# the reachable/buildable/pencils gates. Generous for a personal-scale
# Triangle corpus (mirrors listings.search's own defaults).
_SEARCH_TOP_K = 20

Notifier = Callable[[str, str], Any]


# ---------------------------------------------------------------------------
# notification
# ---------------------------------------------------------------------------

def notify_ntfy(topic: str, title: str, message: str) -> bool:
    """POST one push to https://ntfy.sh/<topic> via stdlib urllib (mirrors
    propkb.email.notify()'s ntfy branch). Best-effort: any network/HTTP
    failure is swallowed and reported as False -- a notification failure must
    never crash the monitor loop. Never called in a test (see _send_notification)."""
    if not topic:
        return False
    try:
        import urllib.request
        req = urllib.request.Request(
            "https://ntfy.sh/" + topic,
            data=message.encode("utf-8"),
            headers={"Title": title},
        )
        urllib.request.urlopen(req, timeout=10)
        return True
    except Exception:  # noqa: BLE001 -- push delivery is best-effort
        return False


def _send_notification(title: str, message: str,
                        notifier: Optional[Notifier] = None) -> tuple[bool, Optional[str]]:
    """Deliver one new-hit alert. Returns (sent, gap_or_None).

    ``notifier`` is the injection point tests use (a fake ``callable(title,
    message)``) -- when the caller supplies one, call it directly regardless
    of NTFY_TOPIC, since the caller explicitly owns delivery. Without one,
    fall back to the real ntfy.sh path gated on settings.ntfy_topic(); a
    missing topic records a gap and skips the HTTP call rather than posting to
    an unconfigured/empty endpoint."""
    if notifier is not None:
        try:
            notifier(title, message)
            return True, None
        except Exception as e:  # noqa: BLE001 -- a notifier failure is a gap, not a crash
            return False, "notifier raised: %s" % e
    topic = settings.ntfy_topic()
    if not topic:
        return False, "notify requested but no NTFY_TOPIC configured (settings.ntfy_topic())"
    ok = notify_ntfy(topic, title, message)
    return ok, (None if ok else "ntfy post to topic %r failed" % topic)


# ---------------------------------------------------------------------------
# thesis loading (store.py has no single-row getter -- list_theses() decodes
# spec_json for every saved thesis; this mirrors that same decode for one id)
# ---------------------------------------------------------------------------

def _load_thesis(conn, thesis_id: int) -> dict:
    row = conn.execute("SELECT * FROM theses WHERE id = ?", (thesis_id,)).fetchone()
    if row is None:
        raise ValueError("no thesis with id %r" % thesis_id)
    d = dict(row)
    raw = d.get("spec_json")
    try:
        d["spec_json"] = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        d["spec_json"] = {}
    return d


# ---------------------------------------------------------------------------
# threshold gates
# ---------------------------------------------------------------------------

def _check_reachable(commute_res: Optional[dict],
                      max_commute_min: Optional[float]) -> tuple[str, Optional[str]]:
    """Returns ("pass" | "fail" | "unknown", note). "unknown" (no provider, or
    a destination that never resolved) is NOT a fail -- a missing commute key
    must not silently suppress every alert."""
    if max_commute_min is None:
        return "pass", None
    if not commute_res or not commute_res.get("provider"):
        return "unknown", ("commute unavailable (no provider configured / no destinations); "
                           "max_commute_min=%s not verified" % max_commute_min)
    minutes = commute_res.get("max_minutes")
    if minutes is None:
        return "unknown", ("commute minutes unavailable for one or more destinations; "
                           "max_commute_min=%s not verified" % max_commute_min)
    if minutes <= max_commute_min:
        return "pass", None
    return "fail", "max commute %.1f min exceeds max_commute_min=%s" % (minutes, max_commute_min)


def _check_buildable(buildability: dict, require_buildable: bool) -> tuple[bool, Optional[str]]:
    """PRD sec.9: undetermined buildability with require_buildable=True fails
    CLOSED -- never treat "don't know" as "yes" for an alert."""
    if not require_buildable:
        return True, None
    units = buildability.get("buildable_units")
    if units is not None:
        if units > 0:
            return True, None
        return False, "buildability verdict reports %s by-right unit(s) (require_buildable)" % units
    if buildability.get("buildable") is True:
        return True, None
    return False, "buildability undetermined; require_buildable cannot be confirmed"


def _check_pencils(score_entry: Optional[dict], min_score: Optional[float]) -> tuple[bool, Optional[str]]:
    """An insufficient-data score also fails closed -- "pencils out" is the
    whole point of this alert, so an unscored candidate cannot claim it."""
    if min_score is None:
        return True, None
    score_val = (score_entry or {}).get("score")
    if score_val is None:
        return False, "strategy score is insufficient-data; cannot confirm min_score=%s" % min_score
    if score_val >= min_score:
        return True, None
    return False, "score %s is below min_score=%s" % (score_val, min_score)


# ---------------------------------------------------------------------------
# rationale (one paragraph, cited -- PRD sec.4 use case 6)
# ---------------------------------------------------------------------------

def _format_citation(c: Any) -> str:
    if isinstance(c, dict):
        n, designation = c.get("n"), c.get("designation")
        if n is not None and designation:
            return "[%s] %s" % (n, designation)
        return str(designation or c)
    return str(c)


def _build_rationale(cand: dict, spec: dict, result: dict, buildability: dict,
                      score_entry: dict, reach_status: str, gaps: list[str]) -> str:
    address = cand.get("address") or ("listing #%s" % cand.get("id"))
    query = spec.get("query") or "the saved thesis criteria"
    strategy = spec.get("strategy") or "land-to-build"

    units = buildability.get("buildable_units")
    if units is not None:
        buildable_bit = "buildable for %s by-right unit(s)" % units
    elif buildability.get("buildable") is True:
        buildable_bit = "buildable (unit count undetermined)"
    else:
        buildable_bit = "of undetermined buildability"

    citations = result.get("citations") or []
    cited = "; ".join(_format_citation(c) for c in citations) or "no cited provision on record"
    buildable_summary = result.get("buildable_summary") or "no buildability summary available"

    commute = result.get("commute") or {}
    max_minutes = commute.get("max_minutes")
    if reach_status == "pass":
        commute_bit = "reachable within the thesis's commute threshold (%.1f min max leg)" % max_minutes \
            if max_minutes is not None else "reachable within the thesis's commute threshold"
    elif reach_status == "unknown":
        commute_bit = "commute could not be verified (no provider/destination data)"
    else:
        commute_bit = "commute exceeds the thesis's threshold"

    score = score_entry.get("score")
    verdict = score_entry.get("verdict") or "insufficient-data"
    score_bit = ("scored %s/100 (%s)" % (score, verdict)) if score is not None else ("scored %s" % verdict)

    gap_bit = ("; open gaps: " + "; ".join(gaps)) if gaps else ""

    return (
        "%s matched the thesis query \"%s\"; it is %s -- %s (cited: %s); "
        "%s; and %s under the %s strategy. Research, not investment advice -- "
        "verify buildability, commute, and the pro forma locally before acting%s."
        % (address, query, buildable_bit, buildable_summary, cited,
           commute_bit, score_bit, strategy, gap_bit)
    )


# ---------------------------------------------------------------------------
# public interface
# ---------------------------------------------------------------------------

def run_thesis(conn, thesis_id: int, notify: bool = True,
               notifier: Optional[Notifier] = None) -> list[dict]:
    """Re-run one saved thesis; alert on newly-qualifying listings only.

    Returns the list of NEW hit dicts (each with its rationale, score,
    reachability, and any gaps). Already-alerted (thesis_id, listing_id)
    pairs are skipped via ``store.thesis_seen`` -- a listing alerts once per
    thesis, ever (mirrors propkb.email's ``.processed.json`` dedup)."""
    thesis = _load_thesis(conn, thesis_id)
    spec = thesis["spec_json"] or {}
    query = spec.get("query") or ""
    filters = spec.get("filters")
    destinations = spec.get("destinations")
    scenario = spec.get("scenario") or "typical"
    strategy = spec.get("strategy") or "land-to-build"
    location = spec.get("location")
    thresholds = spec.get("thresholds") or {}
    min_score = thresholds.get("min_score")
    max_commute_min = thresholds.get("max_commute_min")
    require_buildable = bool(thresholds.get("require_buildable"))

    candidates = _search.search(conn, query, filters=filters, top_k=_SEARCH_TOP_K, semantic=True)

    new_hits: list[dict] = []
    for cand in candidates:
        listing_id = cand["id"]
        if store.thesis_seen(conn, thesis_id, listing_id):
            continue

        result = _enrich.enrich_listing(
            conn, listing_id, destinations=destinations, scenario=scenario,
            strategies=(strategy,), location=location,
        )
        enr_row = store.get_enrichment(conn, listing_id) or {}
        try:
            buildability = json.loads(enr_row.get("buildability_json") or "{}")
        except (TypeError, ValueError):
            buildability = {}
        if not isinstance(buildability, dict):
            buildability = {}

        score_entry = (result.get("scores") or {}).get(strategy) or {}
        reach_status, reach_note = _check_reachable(result.get("commute"), max_commute_min)
        build_ok, build_note = _check_buildable(buildability, require_buildable)
        pencils_ok, pencils_note = _check_pencils(score_entry, min_score)

        gaps = list(result.get("gaps") or [])
        for note in (reach_note, build_note, pencils_note):
            if note:
                gaps.append(note)

        passed = build_ok and pencils_ok and reach_status != "fail"
        if not passed:
            continue

        rationale = _build_rationale(cand, spec, result, buildability, score_entry,
                                     reach_status, gaps)
        if not store.record_thesis_hit(conn, thesis_id, listing_id, rationale):
            continue  # lost a race with another run -- already recorded, don't double-alert

        hit = {
            "thesis_id": thesis_id,
            "listing_id": listing_id,
            "address": cand.get("address"),
            "rationale": rationale,
            "score": score_entry.get("score"),
            "verdict": score_entry.get("verdict"),
            "reachable": reach_status,
            "gaps": gaps,
            "notified": False,
        }

        if notify:
            title = "Scout match: %s" % (cand.get("address") or ("listing #%s" % listing_id))
            sent, notif_gap = _send_notification(title, rationale, notifier)
            hit["notified"] = sent
            if notif_gap:
                hit["gaps"].append(notif_gap)

        new_hits.append(hit)

    store.touch_thesis_last_run(conn, thesis_id)
    return new_hits


def run_all(conn, notify: bool = True, notifier: Optional[Notifier] = None) -> dict:
    """Re-run every saved thesis. Returns a summary dict:
    {"theses_run": int, "new_hits": int, "results": {thesis_id: [hit, ...]}}."""
    results: dict[int, list[dict]] = {}
    total_new = 0
    for thesis in store.list_theses(conn):
        hits = run_thesis(conn, thesis["id"], notify=notify, notifier=notifier)
        results[thesis["id"]] = hits
        total_new += len(hits)
    return {"theses_run": len(results), "new_hits": total_new, "results": results}
