"""listings.score -- E6 feasibility-adjusted investment scoring.

Per-strategy pro-forma (PRD sec.6/sec.5 E6): turns a listing + its enrichment
into a 0-100 score + verdict + a fully itemized pro forma, for the four
strategies phased in the PRD -- land-to-build (ships first, leads on the
zoning/feasibility moat), buy-and-hold rental, fix-and-flip, short-term
rental. The whole point of E6 is that the upside must be conditioned on what
is *legally buildable* (by-right units from the feasibility verdict), not
just as-is comps -- see ``_parse_buildability`` and ``_score_land_to_build``.

Pure, deterministic, side-effect-free: no network, no LLM, stdlib only
(``math``/``json``). Every external number (market comps, per-strategy
assumptions) comes in through the ``market``/``assumptions`` parameters with
documented defaults -- nothing here is hardcoded to a specific listing, and
every non-market number actually used is echoed back in the ``assumptions``
list on the result so the caller can see exactly what the score depends on.

This module never reads or references listing-agent rollup data (``agents``
table / ``agents.py``) in any form -- it only ever sees ``listing`` and
``enrichment`` rows and the caller-supplied ``market``/``assumptions``
dicts. That boundary is intentional and load-bearing (PRD sec.9); keep it
that way in any edit here.

Research, not investment advice -- every result carries a ``disclaimer`` and
a ``gaps`` list of what would need to be verified before relying on it.
"""

from __future__ import annotations

import json
from typing import Any, Optional

_DISCLAIMER = "Research, not investment advice."

# ---------------------------------------------------------------------------
# Per-strategy assumption defaults. Every value here is a generic market/cost
# rule-of-thumb, never a per-listing number -- callers override via the
# ``assumptions`` dict, and whichever ones actually get used (default or
# override) are echoed into the result's ``assumptions`` list.
# ---------------------------------------------------------------------------

_DEFAULTS: dict[str, dict[str, float]] = {
    "land-to-build": {
        # Fallback sale price per finished new-build unit when no market
        # comps are supplied (normally overridden by market["price"]["median"]).
        "home_sale_price_per_unit": 350_000.0,
        "build_cost_per_sqft": 165.0,
        "target_home_size_sqft": 1800.0,
        "soft_cost_pct": 0.15,        # design/permitting/financing, % of hard build cost
        "cost_to_unlock_per_unit": 15_000.0,  # site prep/utility taps/subdivision per unit
        "required_profit_margin_pct": 0.15,   # developer margin on GDV (residual land value method)
    },
    "buy-hold": {
        # Fallback monthly rent as a fraction of price ("1%-rule"-ish, kept
        # conservative) when no rent comp is supplied via market.
        "monthly_rent_pct_of_price": 0.007,
        "vacancy_rate": 0.07,
        "operating_expense_ratio": 0.40,   # taxes/insurance/maintenance/mgmt, % of EGI
        "down_payment_pct": 0.25,
        "mortgage_rate": 0.07,
        "loan_term_years": 30,
        "closing_cost_pct": 0.03,
    },
    "flip": {
        "rehab_cost_per_sqft": 45.0,
        "default_sqft_assumed": 1500.0,   # only used if the listing itself has no sqft
        "arv_premium_pct": 0.0,           # ARV uplift over purchase when no $/sf comps exist
        "carrying_cost_monthly_pct": 0.01,   # financing+holding, % of (purchase+rehab)/month
        "carrying_cost_months": 6.0,
        "selling_cost_pct": 0.08,         # agent commission + closing on the resale
    },
    "str": {
        "adr_default": 150.0,
        "occupancy_rate": 0.55,
        "operating_expense_ratio": 0.35,  # cleaning/mgmt/utilities/supplies, % of revenue
    },
}

_STRATEGIES = ("land-to-build", "buy-hold", "flip", "str")


# ---------------------------------------------------------------------------
# small numeric/assumption helpers
# ---------------------------------------------------------------------------

def _num(x: Any) -> Optional[float]:
    """Best-effort float coercion; None (never raises) on anything unusable."""
    if x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def _verdict_from_score(score: Optional[int]) -> str:
    if score is None:
        return "insufficient-data"
    if score >= 70:
        return "strong"
    if score >= 40:
        return "marginal"
    return "weak"


def _merge_assumptions(strategy: str, overrides: Optional[dict]) -> dict:
    return {**_DEFAULTS[strategy], **(overrides or {})}


def _record(notes: list[str], overrides: dict, key: str, value: Any, why: str) -> None:
    """Append a human-readable ``assumptions`` line for a non-market number
    actually used, tagging whether it came from the caller or the default."""
    src = "override" if key in overrides else "default"
    notes.append("%s=%s (%s; %s)" % (key, value, src, why))


# ---------------------------------------------------------------------------
# buildability parsing -- the feasibility-adjustment core of E6
# ---------------------------------------------------------------------------

def _format_citation(c: Any) -> str:
    if isinstance(c, dict):
        n, designation = c.get("n"), c.get("designation")
        if n is not None and designation:
            return "[%s] %s" % (n, designation)
        return str(designation or c)
    return str(c)


def _parse_buildability(enrichment: Optional[dict]) -> dict:
    """Normalize the buildability verdict out of an enrichment row.

    ``enrichment`` mirrors ``store.get_enrichment`` -- ``buildability_json``
    comes back as a raw TEXT column (a JSON string), not pre-decoded, plus a
    separate ``buildability_cited`` citation-string column. feasibility.py's
    settled shape (confirmed with its author) is mostly free-text docrag
    prose per zoning topic, NOT parsed scalars:

        zoning_code, flood_zone, in_floodway, watershed,
        allowed_uses, by_right_density, setbacks, height, parking  (str prose)
        citations: list[{"n": int, "designation": str}]
        buildable_summary: str, gaps: list[str], disclaimer: str

    ``by_right_density`` is prose ("RS-8 permits detached houses by right;
    min lot 8,000 sf...") -- there is deliberately no boolean "buildable" or
    integer "buildable_units" field to regex out of it. The one numeric field
    this scorer actually needs to gate land-to-build upside, ``by_right_units:
    int | None``, is being added to feasibility.py directly (grounded off the
    parcel's lot size + zoning, where that reasoning belongs) rather than
    inferred here from prose -- read it opportunistically so this module
    keeps working (falling back to "no unit count yet" -> insufficient-data,
    per the PRD's "do not silently assume max density" rule) whether or not
    it has landed yet.

    Returns:
        {"available": bool,        # a verdict could actually be parsed
         "buildable": bool | None,  # derived from by_right_units's sign; None if absent
         "buildable_units": int | None,
         "by_right": None,          # no boolean field exists in the real schema (see above)
         "citations": list[str],    # formatted "[n] designation" strings
         "buildable_summary": str | None,
         "verdict_gaps": list[str], # feasibility.py's own reported gaps
         "source": "buildability verdict" | "assumed"}

    ``available=False`` (missing/unparseable verdict) is deliberately
    distinct from an ``available=True`` verdict that has no ``by_right_units``
    yet -- the former means feasibility never ran, the latter means it ran
    but couldn't (yet) ground a number.
    """
    empty = {
        "available": False, "buildable": None, "buildable_units": None,
        "by_right": None, "citations": [], "buildable_summary": None,
        "verdict_gaps": [], "source": "assumed",
    }
    if not enrichment:
        return empty

    raw = enrichment.get("buildability_json")
    data: dict = {}
    if isinstance(raw, dict):
        data = raw
    elif isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, dict):
            data = parsed
    if not data:
        return empty

    # by_right_units doesn't exist in the schema yet as of this writing
    # (feasibility.py is adding it on request); accept a couple of very
    # close aliases too so a small naming change doesn't silently break this.
    buildable_units = None
    for key in ("by_right_units", "buildable_units", "max_by_right_units"):
        if data.get(key) is not None:
            buildable_units = data[key]
            break
    buildable = (buildable_units > 0) if buildable_units is not None else None

    # Citations come from the verdict's own structured list. The separate
    # ``buildability_cited`` column is a provenance string (PRD sec.7), NOT a
    # citation list element -- don't fold it in here (it used to append the
    # 0/1 flag some writers store, polluting the list). The gap gate below only
    # cares whether the verdict carried ANY structured citation.
    citations = [_format_citation(c) for c in (data.get("citations") or [])]

    return {
        "available": True,
        "buildable": buildable,
        "buildable_units": buildable_units,
        "by_right": None,
        "citations": citations,
        "buildable_summary": data.get("buildable_summary"),
        "verdict_gaps": list(data.get("gaps") or []),
        "source": "buildability verdict",
    }


def _feasibility_used(feas: dict) -> dict:
    return {
        "buildable_units": feas["buildable_units"],
        "by_right": feas["by_right"],
        "source": feas["source"],
    }


# ---------------------------------------------------------------------------
# land-to-build (PRD sec.6: residual land value / max-bid model)
# ---------------------------------------------------------------------------

def _score_land_to_build(listing: dict, enrichment: dict, market: dict,
                          assumptions: dict) -> dict:
    a = _merge_assumptions("land-to-build", assumptions)
    notes: list[str] = []
    gaps: list[str] = []
    purchase_price = _num(listing.get("price"))
    feas = _parse_buildability(enrichment)

    if not feas["available"]:
        # PRD sec.9: "if buildability is missing, verdict=insufficient-data
        # ... do not silently assume max density." No verdict at all -> stop
        # here rather than guessing a unit count.
        gaps.append(
            "no buildability verdict available (enrichment.buildability_json "
            "missing/unparseable); cannot condition land-to-build upside on "
            "legally buildable units without one -- run feasibility.py first"
        )
        return {
            "score": None,
            "verdict": "insufficient-data",
            "pro_forma": {"purchase_price": purchase_price, "buildable_units": None},
            "feasibility_used": _feasibility_used(feas),
            "assumptions": notes,
            "gaps": gaps,
        }

    if feas["buildable"] is False or (
        feas["buildable_units"] is not None and feas["buildable_units"] <= 0
    ):
        # A definitive "no" -- known, not missing, so this is a real verdict:
        # weak, not insufficient-data, but still NOT a full-density score.
        gaps.append(
            "buildability verdict says not buildable / 0 by-right units; "
            "land-to-build upside is not supportable at this site"
        )
        gaps.extend("feasibility: %s" % g for g in feas["verdict_gaps"])
        return {
            "score": 0,
            "verdict": "weak",
            "pro_forma": {
                "purchase_price": purchase_price, "buildable_units": 0,
                "gross_realizable": 0.0, "build_cost": 0.0, "soft_costs": 0.0,
                "cost_to_unlock": 0.0, "required_profit": 0.0,
                "max_bid": 0.0, "residual_land_value": 0.0, "profit": None,
                "margin_pct": None,
            },
            "feasibility_used": _feasibility_used(feas),
            "assumptions": notes,
            "gaps": gaps,
        }

    buildable_units = feas["buildable_units"]
    if buildable_units is None:
        # Verdict exists but didn't report a unit count -- same "don't
        # silently assume max density" rule applies.
        gaps.append(
            "buildability verdict present but did not report a buildable-unit "
            "count; cannot size the land-to-build upside without one"
        )
        gaps.extend("feasibility: %s" % g for g in feas["verdict_gaps"])
        return {
            "score": None,
            "verdict": "insufficient-data",
            "pro_forma": {"purchase_price": purchase_price, "buildable_units": None},
            "feasibility_used": _feasibility_used(feas),
            "assumptions": notes,
            "gaps": gaps,
        }

    market_price = (market or {}).get("price") or {}
    home_sale_price = _num(market_price.get("median"))
    if home_sale_price is None:
        home_sale_price = a["home_sale_price_per_unit"]
        _record(notes, assumptions, "home_sale_price_per_unit", home_sale_price,
                "no market comps supplied")
        gaps.append("no market comps supplied; using default home_sale_price_per_unit")

    build_cost_per_sqft = a["build_cost_per_sqft"]
    _record(notes, assumptions, "build_cost_per_sqft", build_cost_per_sqft, "hard construction cost")
    target_size = a["target_home_size_sqft"]
    _record(notes, assumptions, "target_home_size_sqft", target_size, "assumed finished size per unit")
    soft_cost_pct = a["soft_cost_pct"]
    _record(notes, assumptions, "soft_cost_pct", soft_cost_pct, "design/permitting/financing overhead")
    cost_to_unlock_per_unit = a["cost_to_unlock_per_unit"]
    _record(notes, assumptions, "cost_to_unlock_per_unit", cost_to_unlock_per_unit,
            "site prep/utility taps/subdivision cost to realize each unit")
    required_profit_margin_pct = a["required_profit_margin_pct"]
    _record(notes, assumptions, "required_profit_margin_pct", required_profit_margin_pct,
            "developer margin on gross realizable value")

    gross_realizable = home_sale_price * buildable_units
    build_cost = build_cost_per_sqft * target_size * buildable_units
    soft_costs = soft_cost_pct * build_cost
    cost_to_unlock = cost_to_unlock_per_unit * buildable_units
    required_profit = required_profit_margin_pct * gross_realizable
    max_bid = gross_realizable - build_cost - soft_costs - cost_to_unlock - required_profit

    profit = None
    margin_pct = None
    if purchase_price is not None:
        profit = gross_realizable - purchase_price - build_cost - soft_costs - cost_to_unlock
        margin_pct = (profit / gross_realizable * 100) if gross_realizable else None

    score: Optional[int]
    verdict: str
    if purchase_price is None:
        score = None
        verdict = "insufficient-data"
        gaps.append("listing has no price; cannot compare it to the computed max bid")
    elif max_bid <= 0:
        score = 0
        verdict = "weak"
        gaps.append(
            "residual land value is at or below zero at these cost/profit "
            "assumptions -- the project does not pencil at any bid"
        )
    else:
        cushion_pct = (max_bid - purchase_price) / max_bid * 100
        score = int(round(_clamp(50 + cushion_pct)))
        verdict = "weak" if purchase_price > max_bid else _verdict_from_score(score)

    if not feas["citations"]:
        gaps.append("buildability verdict has no cited authority; verify before relying on the unit count")
    for g in feas["verdict_gaps"]:
        if g not in gaps:
            gaps.append("feasibility: %s" % g)

    return {
        "score": score,
        "verdict": verdict,
        "pro_forma": {
            "purchase_price": purchase_price,
            "buildable_units": buildable_units,
            "home_sale_price_per_unit": home_sale_price,
            "gross_realizable": gross_realizable,
            "build_cost": build_cost,
            "soft_costs": soft_costs,
            "cost_to_unlock": cost_to_unlock,
            "required_profit": required_profit,
            "max_bid": max_bid,
            "residual_land_value": max_bid,
            "profit": profit,
            "margin_pct": margin_pct,
        },
        "feasibility_used": _feasibility_used(feas),
        "assumptions": notes,
        "gaps": gaps,
    }


# ---------------------------------------------------------------------------
# buy-and-hold rental (cap rate / cash-on-cash)
# ---------------------------------------------------------------------------

def _mortgage_payment(principal: float, monthly_rate: float, n_payments: float) -> float:
    if not principal or n_payments <= 0:
        return 0.0
    if monthly_rate == 0:
        return principal / n_payments
    factor = (1 + monthly_rate) ** n_payments
    return principal * (monthly_rate * factor) / (factor - 1)


def _score_buy_hold(listing: dict, enrichment: dict, market: dict, assumptions: dict) -> dict:
    a = _merge_assumptions("buy-hold", assumptions)
    notes: list[str] = []
    gaps: list[str] = []
    feas = _parse_buildability(enrichment)
    price = _num(listing.get("price"))

    market_rent = (market or {}).get("rent") or {}
    monthly_rent = _num(market_rent.get("median"))
    if monthly_rent is None:
        pct = a["monthly_rent_pct_of_price"]
        _record(notes, assumptions, "monthly_rent_pct_of_price", pct, "no rent comp supplied")
        gaps.append("no market rent comp supplied; using monthly_rent_pct_of_price of listing price")
        monthly_rent = (price * pct) if price is not None else None

    if feas["available"] and feas["buildable_units"] and feas["buildable_units"] > 1:
        gaps.append(
            "buildability verdict allows %s units (ADU/subdivision potential); "
            "this pro forma scores only the AS-IS unit -- rerun with a per-unit "
            "rent to size the additional legal upside" % feas["buildable_units"]
        )

    down_payment_pct = a["down_payment_pct"]
    _record(notes, assumptions, "down_payment_pct", down_payment_pct, "financing assumption")
    mortgage_rate = a["mortgage_rate"]
    _record(notes, assumptions, "mortgage_rate", mortgage_rate, "financing assumption")
    loan_term_years = a["loan_term_years"]
    _record(notes, assumptions, "loan_term_years", loan_term_years, "financing assumption")
    closing_cost_pct = a["closing_cost_pct"]
    _record(notes, assumptions, "closing_cost_pct", closing_cost_pct, "acquisition cost assumption")
    vacancy_rate = a["vacancy_rate"]
    _record(notes, assumptions, "vacancy_rate", vacancy_rate, "operating assumption")
    opex_ratio = a["operating_expense_ratio"]
    _record(notes, assumptions, "operating_expense_ratio", opex_ratio, "operating assumption")

    annual_rent = monthly_rent * 12 if monthly_rent is not None else None
    egi = annual_rent * (1 - vacancy_rate) if annual_rent is not None else None
    operating_expenses = egi * opex_ratio if egi is not None else None
    noi = (egi - operating_expenses) if egi is not None else None
    cap_rate_pct = (noi / price * 100) if noi is not None and price else None

    down_payment = price * down_payment_pct if price is not None else None
    closing_costs = price * closing_cost_pct if price is not None else None
    loan_amount = (price - down_payment) if price is not None and down_payment is not None else None
    monthly_debt_service = (
        _mortgage_payment(loan_amount, mortgage_rate / 12, loan_term_years * 12)
        if loan_amount is not None else None
    )
    annual_debt_service = monthly_debt_service * 12 if monthly_debt_service is not None else None
    cash_flow = (noi - annual_debt_service) if noi is not None and annual_debt_service is not None else None
    cash_invested = (
        (down_payment + closing_costs)
        if down_payment is not None and closing_costs is not None else None
    )
    cash_on_cash_pct = (
        cash_flow / cash_invested * 100
        if cash_flow is not None and cash_invested else None
    )

    if cash_on_cash_pct is not None:
        score = int(round(_clamp(cash_on_cash_pct * 8)))
    elif cap_rate_pct is not None:
        score = int(round(_clamp(cap_rate_pct * 10)))
        gaps.append("could not compute cash-on-cash (missing price/financing inputs); scored on cap rate only")
    else:
        score = None
        gaps.append("missing price and/or rent; cannot score buy-hold")
    verdict = _verdict_from_score(score)

    return {
        "score": score,
        "verdict": verdict,
        "pro_forma": {
            "purchase_price": price,
            "monthly_rent": monthly_rent,
            "annual_rent": annual_rent,
            "effective_gross_income": egi,
            "operating_expenses": operating_expenses,
            "noi": noi,
            "cap_rate_pct": cap_rate_pct,
            "down_payment": down_payment,
            "closing_costs": closing_costs,
            "annual_debt_service": annual_debt_service,
            "cash_flow": cash_flow,
            "cash_invested": cash_invested,
            "cash_on_cash_pct": cash_on_cash_pct,
        },
        "feasibility_used": _feasibility_used(feas),
        "assumptions": notes,
        "gaps": gaps,
    }


# ---------------------------------------------------------------------------
# fix-and-flip (ARV - purchase - rehab - carrying - selling = profit)
# ---------------------------------------------------------------------------

def _score_flip(listing: dict, enrichment: dict, market: dict, assumptions: dict) -> dict:
    a = _merge_assumptions("flip", assumptions)
    notes: list[str] = []
    gaps: list[str] = []
    feas = _parse_buildability(enrichment)
    purchase_price = _num(listing.get("price"))
    sqft = _num(listing.get("sqft"))

    market_psf = (market or {}).get("price_per_sqft") or {}
    psf_median = _num(market_psf.get("median"))
    if psf_median is not None and sqft:
        arv = psf_median * sqft
    else:
        premium = a["arv_premium_pct"]
        _record(notes, assumptions, "arv_premium_pct", premium, "no $/sf market comps supplied")
        gaps.append(
            "no $/sf market comps (or listing has no sqft); ARV assumed from "
            "purchase price + arv_premium_pct -- supply market comps for a real verdict"
        )
        arv = purchase_price * (1 + premium) if purchase_price is not None else None

    if not sqft:
        sqft = a["default_sqft_assumed"]
        _record(notes, assumptions, "default_sqft_assumed", sqft, "listing has no sqft")
        gaps.append("listing has no sqft; assumed default_sqft_assumed for rehab sizing")

    rehab_cost_per_sqft = a["rehab_cost_per_sqft"]
    _record(notes, assumptions, "rehab_cost_per_sqft", rehab_cost_per_sqft, "rehab budget assumption")
    carrying_pct = a["carrying_cost_monthly_pct"]
    _record(notes, assumptions, "carrying_cost_monthly_pct", carrying_pct, "financing+holding assumption")
    carrying_months = a["carrying_cost_months"]
    _record(notes, assumptions, "carrying_cost_months", carrying_months, "hold-period assumption")
    selling_cost_pct = a["selling_cost_pct"]
    _record(notes, assumptions, "selling_cost_pct", selling_cost_pct, "commission+closing on resale")

    rehab_cost = rehab_cost_per_sqft * sqft
    carrying_cost = (
        (purchase_price + rehab_cost) * carrying_pct * carrying_months
        if purchase_price is not None else None
    )
    selling_costs = arv * selling_cost_pct if arv is not None else None

    profit = None
    margin_pct = None
    if None not in (arv, purchase_price, carrying_cost, selling_costs):
        profit = arv - purchase_price - rehab_cost - carrying_cost - selling_costs
        margin_pct = (profit / arv * 100) if arv else None

    if margin_pct is not None:
        score = int(round(_clamp(margin_pct * 5)))
        verdict = "weak" if margin_pct < 0 else _verdict_from_score(score)
    else:
        score = None
        verdict = "insufficient-data"
        gaps.append("missing purchase price/ARV inputs; cannot score flip")

    if feas["available"] and feas["buildable_units"] and feas["buildable_units"] > 1:
        gaps.append(
            "buildability verdict allows %s units (ADU potential); this pro "
            "forma scores the flip as a single resale, not an add-a-unit play"
            % feas["buildable_units"]
        )

    return {
        "score": score,
        "verdict": verdict,
        "pro_forma": {
            "purchase_price": purchase_price,
            "arv": arv,
            "rehab_cost": rehab_cost,
            "carrying_cost": carrying_cost,
            "selling_costs": selling_costs,
            "profit": profit,
            "margin_pct": margin_pct,
        },
        "feasibility_used": _feasibility_used(feas),
        "assumptions": notes,
        "gaps": gaps,
    }


# ---------------------------------------------------------------------------
# short-term rental (revenue from ADR*occupancy less costs)
# ---------------------------------------------------------------------------

def _score_str(listing: dict, enrichment: dict, market: dict, assumptions: dict) -> dict:
    a = _merge_assumptions("str", assumptions)
    notes: list[str] = []
    # PRD sec.12.4: STR scoring needs local STR-ordinance legality, which
    # isn't in this module's inputs -- always flag it as a gap to verify.
    gaps: list[str] = [
        "STR legality (local short-term-rental ordinance/registration) is not "
        "verified by this scorer -- check the governing jurisdiction's STR rules "
        "before relying on this strategy"
    ]
    feas = _parse_buildability(enrichment)
    price = _num(listing.get("price"))

    adr = _num((assumptions or {}).get("adr")) if assumptions else None
    if adr is None:
        adr = a["adr_default"]
        _record(notes, assumptions, "adr_default", adr, "no ADR estimate supplied")
        gaps.append("no ADR (average daily rate) estimate supplied; using adr_default")
    occupancy_rate = a["occupancy_rate"]
    _record(notes, assumptions, "occupancy_rate", occupancy_rate, "operating assumption")
    opex_ratio = a["operating_expense_ratio"]
    _record(notes, assumptions, "operating_expense_ratio", opex_ratio,
            "cleaning/mgmt/utilities, % of revenue")

    annual_revenue = adr * 365 * occupancy_rate
    operating_expenses = annual_revenue * opex_ratio
    noi = annual_revenue - operating_expenses
    cap_rate_pct = (noi / price * 100) if price else None

    if cap_rate_pct is not None:
        score = int(round(_clamp(cap_rate_pct * 10)))
        verdict = _verdict_from_score(score)
    else:
        score = None
        verdict = "insufficient-data"
        gaps.append("listing has no price; cannot compute an STR cap rate")

    return {
        "score": score,
        "verdict": verdict,
        "pro_forma": {
            "purchase_price": price,
            "adr": adr,
            "occupancy_rate": occupancy_rate,
            "annual_revenue": annual_revenue,
            "operating_expenses": operating_expenses,
            "noi": noi,
            "cap_rate_pct": cap_rate_pct,
        },
        "feasibility_used": _feasibility_used(feas),
        "assumptions": notes,
        "gaps": gaps,
    }


_DISPATCH = {
    "land-to-build": _score_land_to_build,
    "buy-hold": _score_buy_hold,
    "flip": _score_flip,
    "str": _score_str,
}


def score(listing: dict, enrichment: Optional[dict] = None, strategy: str = "land-to-build",
          market: Optional[dict] = None, assumptions: Optional[dict] = None) -> dict:
    """Score one listing under one investment strategy (PRD E6).

    ``listing`` is a listings-table row shape (``store.get_listing``).
    ``enrichment`` is the store enrichment row (may carry ``buildability_json``/
    ``zoning_code``/``commute_json``); the land-to-build upside is gated on
    its parsed buildability verdict, never on as-is comps alone. ``market``
    is normally ``stats.compute_stats``'s output (or a filtered slice of it)
    for comps grounding; when absent, conservative documented defaults are
    used and flagged in the result's ``assumptions`` list. See the module
    docstring for the strategy formulas and the FHA-firewall boundary.
    """
    if listing is None:
        raise ValueError("listing is required")
    key = (strategy or "land-to-build").strip().lower().replace("_", "-")
    fn = _DISPATCH.get(key)
    if fn is None:
        raise ValueError("unknown strategy %r (expected one of %s)" % (strategy, sorted(_STRATEGIES)))

    result = fn(listing, enrichment or {}, market or {}, assumptions or {})
    result["strategy"] = key
    result["disclaimer"] = _DISCLAIMER
    return result
