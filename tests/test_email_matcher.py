"""Regression tests for propkb.email relevance/routing.

Guards the shared-ZIP bug: a 1621 Clermont email must NOT file under 212 Pine just
because both are ZIP 27713. Matching is weighted (PIN/REID 100, address 50, street#
8, name 5, ZIP 1), must clear a floor (>ZIP-alone), and routes to the STRONGEST
matching property.

Runs with plain Python (no pytest needed):
    .venv/Scripts/python.exe tests/test_email_matcher.py
...and is also collectable by pytest if installed.
"""

from __future__ import annotations

import contextlib
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from propkb import store           # noqa: E402
from propkb import email as pmail  # noqa: E402

CLERMONT = "1621-clermont-rd-durham"
PINE = "212-pine-dr-durham"

_FACTS = {
    CLERMONT: 'property:\n  pin: "0728808242"\n  reid: "153775"\n'
              '  address: 1621 Clermont Rd, Durham, NC 27713\n',
    PINE: 'property:\n  pin: "0707680500"\n  reid: "143264"\n'
          '  address: 212 Pine Dr, Durham, NC 27713\n',
}


@contextlib.contextmanager
def _two_props():
    """Two same-ZIP (27713) properties in an isolated temp PROPKB_ROOT."""
    prev = os.environ.get("PROPKB_ROOT")
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["PROPKB_ROOT"] = tmp
        try:
            for slug, facts in _FACTS.items():
                store.create(slug, "")
                with open(store.paths(slug)["facts"], "w", encoding="utf-8") as f:
                    f.write(facts)
            yield tmp
        finally:
            if prev is None:
                os.environ.pop("PROPKB_ROOT", None)
            else:
                os.environ["PROPKB_ROOT"] = prev


def test_shared_zip_email_routes_to_correct_property():
    """The original bug: a 1621 Clermont email (ZIP 27713) filed under 212 Pine."""
    with _two_props():
        subj = "Re: 1621 Clermont Rd (PIN 0728808242) - access requirement"
        body = "Regarding 1621 Clermont Rd, Durham NC 27713, PIN 0728808242 ..."
        assert pmail.is_relevant(CLERMONT, subj, body, "") is True
        assert pmail.is_relevant(PINE, subj, body, "") is False


def test_pine_email_routes_to_pine():
    with _two_props():
        subj = "212 Pine Dr lot - soil test"
        body = "About 212 Pine Dr, Durham 27713 (PIN 0707680500) ..."
        assert pmail.is_relevant(PINE, subj, body, "") is True
        assert pmail.is_relevant(CLERMONT, subj, body, "") is False


def test_zip_alone_never_files():
    """A message sharing ONLY the ZIP (no PIN/street) must not file anywhere."""
    with _two_props():
        subj = "Your order shipped to Durham, NC 27713"
        body = "Package to 27713."
        assert pmail.is_relevant(PINE, subj, body, "") is False
        assert pmail.is_relevant(CLERMONT, subj, body, "") is False


def test_pin_outweighs_shared_zip():
    with _two_props():
        hay = "1621 clermont rd durham nc 27713 pin 0728808242"
        assert pmail._relevance_score(CLERMONT, hay) >= 100   # PIN weight
        assert pmail._relevance_score(PINE, hay) == 1         # ZIP only


def test_street_name_clears_floor_but_zip_does_not():
    with _two_props():
        assert pmail._relevance_score(PINE, "the pine dr parcel") >= pmail._RELEVANCE_FLOOR
        assert pmail._relevance_score(PINE, "mail to 27713") < pmail._RELEVANCE_FLOOR


def test_unrelated_email_files_nowhere():
    with _two_props():
        assert pmail.is_relevant(PINE, "Meeting notes", "Lunch in Raleigh.", "") is False
        assert pmail.is_relevant(CLERMONT, "Meeting notes", "Lunch in Raleigh.", "") is False


def _main() -> int:
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print("PASS", t.__name__)
        except AssertionError as e:
            failed += 1
            print("FAIL", t.__name__, "--", e or "assertion failed")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print("ERROR", t.__name__, "--", repr(e))
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_main())
