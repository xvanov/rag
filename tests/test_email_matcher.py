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
STAR = "7412-star-dr-durham"   # "Star" is a substring of common words (startup/starting)

_FACTS = {
    CLERMONT: 'property:\n  pin: "0728808242"\n  reid: "153775"\n'
              '  address: 1621 Clermont Rd, Durham, NC 27713\n',
    PINE: 'property:\n  pin: "0707680500"\n  reid: "143264"\n'
          '  address: 212 Pine Dr, Durham, NC 27713\n',
    STAR: 'property:\n  pin: "0707589331"\n  reid: "143262"\n'
          '  address: 7412 Star Dr, Durham, NC 27713\n',
}


@contextlib.contextmanager
def _two_props():
    """Same-ZIP (27713) properties in an isolated temp PROPKB_ROOT."""
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


# ---- token-boundary matching (the "Star"/"startup" misclassification) ----------

def test_street_name_not_matched_as_substring_of_common_word():
    """THE bug: a newsletter about an 'AI startup' / 'starting to ...' filed under
    7412 STAR Dr because 'star' was substring-matched inside 'startup'/'starting'.
    Matching is on whole tokens, so these must score 0 and file nowhere."""
    with _two_props():
        subj = "ChatGPT's newest most powerful launch incoming"
        body = ("A european startup just revealed ... they're starting to touch "
                "AGI. AI chip startup SambaNova ... ai start-up kaon.ai raises ...")
        assert pmail._relevance_score(STAR, subj + " " + body) == 0
        assert pmail.is_relevant(STAR, subj, body, "") is False


def test_whole_token_street_name_still_matches():
    """Guard against over-correction: a real 'Star Dr' reference must still file."""
    with _two_props():
        subj = "7412 Star Dr - soil evaluation"
        body = "Regarding the lot at 7412 Star Dr, Durham NC 27713 (PIN 0707589331)."
        assert pmail.is_relevant(STAR, subj, body, "") is True
        assert pmail.is_relevant(PINE, subj, body, "") is False


def test_number_not_matched_inside_longer_number_or_url():
    """A ZIP/REID/PIN must match as a whole token, not as a digit run embedded in a
    longer number, order id, or URL path."""
    with _two_props():
        # 27713 inside 1277130; 143262 (STAR reid) inside 91432620; PIN as substring
        hay = ("order 1277130 tracking 91432620999 "
               "https://x.com/p/0707589331000/checkout")
        assert pmail._relevance_score(STAR, hay) == 0
        assert pmail._relevance_score(PINE, hay) == 0


def test_star_email_routes_to_star_not_pine():
    with _two_props():
        subj = "7412 Star Dr (REID 143262) access"
        body = "About 7412 Star Dr, Durham NC 27713, REID 143262 ..."
        assert pmail.is_relevant(STAR, subj, body, "") is True
        assert pmail.is_relevant(PINE, subj, body, "") is False
        assert pmail.is_relevant(CLERMONT, subj, body, "") is False


# ---- self-notification loop guard (propkb must not ingest its own mail) --------

def _msg(subject, body="", headers=None):
    from email.message import EmailMessage
    em = EmailMessage()
    em["Subject"] = subject
    for k, v in (headers or {}).items():
        em[k] = v
    em.set_content(body)
    return em


def test_notification_subject_is_autogen():
    """THE loop: a '[propkb] New mail for 7412-star-dr-durham (2)' notice contains
    the slug tokens (7412, star) and would file + spawn another notice forever."""
    m = _msg("[propkb] New mail for 7412-star-dr-durham (2)",
             "Filed 2 message(s): - 2026-07-08: ...")
    assert pmail._is_propkb_autogen(m) is True


def test_autogen_header_detected_regardless_of_subject():
    m = _msg("7412 Star Dr soil results", headers={pmail.AUTOGEN_HEADER: "notification"})
    assert pmail._is_propkb_autogen(m) is True


def test_real_property_mail_is_not_autogen():
    m = _msg("7412 Star Dr (REID 143262) access",
             "About 7412 Star Dr, Durham NC 27713 ...")
    assert pmail._is_propkb_autogen(m) is False


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
