"""Tests for listings.handoff -- promote a Scout listing into a propkb property.

UNIT tests run fully offline: propkb.store.create and propkb.acquire.acquire
are monkeypatched with recording fakes, and PROPKB_ROOT/LISTINGS_ROOT are both
pointed at fresh temp dirs, so no network call and no real repo directory is
ever touched. They assert:
  * slug derivation from a sample listing address matches the convention of
    real property slugs already in the repo (1621-clermont-rd-durham etc.)
  * propkb.store.create + propkb.acquire.acquire are called with the right
    args (slug, address, the location mapped from planning_jurisdiction, and
    the parcel_pin passthrough)
  * the sources/web/ note is written with the listing's price/remarks and a
    buildability section
  * the returned `next` command names /property-research <slug>
  * `created` is True the first time and False on a repeat promote() (create
    is idempotent)
  * an unknown listing_id raises ValueError; an acquire() failure (raised or
    an {"error": ...} result) records a gap and never raises

Runs with plain Python (no pytest needed):
    .venv/Scripts/python.exe tests/test_listings_handoff.py
...and is also collectable by pytest.
"""

from __future__ import annotations

import contextlib
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from propkb import acquire as _pk_acquire   # noqa: E402
from propkb import store as _pk_store       # noqa: E402
from listings import handoff, store         # noqa: E402

try:
    import pytest                            # noqa: E402
except ImportError:                          # plain-python harness
    pytest = None


_SAMPLE_LISTING = {
    "source": "sample", "source_id": "SAMPLE-0001",
    "mls_number": "TMLS0000001", "status": "active",
    "address": "212 Pine Dr, Durham, NC 27713",
    "lat": 35.9312, "lon": -78.8654,
    "parcel_pin": "0707680500", "planning_jurisdiction": "durham-nc",
    "price": 245000, "list_date": "2026-05-20", "dom": 5,
    "beds": 3, "baths": 2, "sqft": 1400, "lot_sqft": 20000, "lot_acres": 0.46,
    "property_type": "single_family", "year_built": 1985,
    "description_text": "Existing septic and well; flag lot; ADU potential.",
    "list_agent_name": "Jamie Rivers", "list_agent_id": "AGT-2002",
    "brokerage": "Triangle Realty", "photos_json": [], "raw_json": "{}",
}


class _Recorder:
    """Records propkb.store.create + propkb.acquire.acquire calls.

    ``create`` delegates to the REAL implementation (offline -- it only ever
    touches PROPKB_ROOT, which the caller has pointed at a temp dir) so
    dir/idempotency behavior stays realistic. ``acquire`` is fully canned --
    no network."""

    def __init__(self, acquire_result=None, acquire_raises=None):
        self.create_calls = []
        self.acquire_calls = []
        self.acquire_result = acquire_result if acquire_result is not None \
            else {"slug": None, "location": None, "parcel": {"PIN": "x"}, "gaps": []}
        self.acquire_raises = acquire_raises
        self._saved = {}

    def _fake_create(self, slug, address):
        self.create_calls.append({"slug": slug, "address": address})
        real = self._saved[(_pk_store, "create")]
        return real(slug, address)

    def _fake_acquire(self, slug, location=_pk_acquire.DEFAULT_LOCATION,
                      pin=None, address=None):
        self.acquire_calls.append({"slug": slug, "location": location,
                                   "pin": pin, "address": address})
        if self.acquire_raises:
            raise self.acquire_raises
        return dict(self.acquire_result)

    @contextlib.contextmanager
    def apply(self):
        targets = [(_pk_store, "create", self._fake_create),
                   (_pk_acquire, "acquire", self._fake_acquire)]
        for mod, name, fn in targets:
            self._saved[(mod, name)] = getattr(mod, name)
        for mod, name, fn in targets:
            setattr(mod, name, fn)
        try:
            yield self
        finally:
            for (mod, name), orig in self._saved.items():
                setattr(mod, name, orig)


@contextlib.contextmanager
def _tmp_roots():
    """Isolated tmp PROPKB_ROOT + LISTINGS_ROOT so no real repo dir is touched."""
    prev_p = os.environ.get("PROPKB_ROOT")
    prev_l = os.environ.get("LISTINGS_ROOT")
    with tempfile.TemporaryDirectory() as tmp_p, tempfile.TemporaryDirectory() as tmp_l:
        os.environ["PROPKB_ROOT"] = tmp_p
        os.environ["LISTINGS_ROOT"] = tmp_l
        try:
            yield tmp_p, tmp_l
        finally:
            for var, prev in (("PROPKB_ROOT", prev_p), ("LISTINGS_ROOT", prev_l)):
                if prev is None:
                    os.environ.pop(var, None)
                else:
                    os.environ[var] = prev


def _conn_with_sample(listing=None):
    conn = store.connect()
    store.init_db(conn)
    listing_id = store.upsert_listing(conn, dict(listing or _SAMPLE_LISTING))
    return conn, listing_id


# --------------------------------------------------------------------------- #
# slug derivation                                                             #
# --------------------------------------------------------------------------- #

def test_slugify_matches_existing_property_slug_convention():
    assert handoff._slugify_address("212 Pine Dr, Durham, NC 27713") == "212-pine-dr-durham"
    assert handoff._slugify_address("1621 Clermont Rd, Durham, NC 27713") == "1621-clermont-rd-durham"
    assert handoff._slugify_address("7412 Star Dr, Durham, NC 27713") == "7412-star-dr-durham"


def test_promote_derives_slug_when_not_given():
    with _tmp_roots():
        conn, listing_id = _conn_with_sample()
        rec = _Recorder()
        with rec.apply():
            result = handoff.promote(conn, listing_id)
        conn.close()
    assert result["slug"] == "212-pine-dr-durham"
    assert rec.create_calls[0]["slug"] == "212-pine-dr-durham"


def test_promote_uses_explicit_slug():
    with _tmp_roots():
        conn, listing_id = _conn_with_sample()
        rec = _Recorder()
        with rec.apply():
            result = handoff.promote(conn, listing_id, slug="custom-slug")
        conn.close()
    assert result["slug"] == "custom-slug"
    assert rec.create_calls[0]["slug"] == "custom-slug"


# --------------------------------------------------------------------------- #
# create + acquire called with the right args                                 #
# --------------------------------------------------------------------------- #

def test_create_and_acquire_called_with_right_args():
    with _tmp_roots():
        conn, listing_id = _conn_with_sample()
        rec = _Recorder()
        with rec.apply():
            handoff.promote(conn, listing_id)
        conn.close()
    assert rec.create_calls == [{"slug": "212-pine-dr-durham",
                                 "address": "212 Pine Dr, Durham, NC 27713"}]
    assert len(rec.acquire_calls) == 1
    call = rec.acquire_calls[0]
    assert call["slug"] == "212-pine-dr-durham"
    assert call["location"] == "durham-nc"
    assert call["pin"] == "0707680500"
    assert call["address"] == "212 Pine Dr, Durham, NC 27713"


def test_acquire_location_mapped_from_wake_short_code():
    listing = dict(_SAMPLE_LISTING)
    listing.update({"source_id": "SAMPLE-0099",
                    "address": "1 Fayetteville St, Raleigh, NC 27601",
                    "planning_jurisdiction": "RA", "parcel_pin": None})
    with _tmp_roots():
        conn, listing_id = _conn_with_sample(listing)
        rec = _Recorder()
        with rec.apply():
            handoff.promote(conn, listing_id)
        conn.close()
    assert rec.acquire_calls[0]["location"] == "raleigh-nc"


# --------------------------------------------------------------------------- #
# web note                                                                    #
# --------------------------------------------------------------------------- #

def test_web_note_written_with_listing_context():
    with _tmp_roots():
        conn, listing_id = _conn_with_sample()
        rec = _Recorder()
        with rec.apply():
            result = handoff.promote(conn, listing_id)
        conn.close()
        assert os.path.isfile(result["web_note"])
        with open(result["web_note"], "r", encoding="utf-8") as f:
            text = f.read()
    assert "212 Pine Dr" in text
    assert "245000" in text
    assert "Existing septic and well" in text
    assert "not yet enriched" in text   # no enrichment row for this listing


# --------------------------------------------------------------------------- #
# next command                                                                #
# --------------------------------------------------------------------------- #

def test_next_points_to_property_research():
    with _tmp_roots():
        conn, listing_id = _conn_with_sample()
        rec = _Recorder()
        with rec.apply():
            result = handoff.promote(conn, listing_id)
        conn.close()
    assert result["next"] == "/property-research 212-pine-dr-durham"


# --------------------------------------------------------------------------- #
# created flag (idempotent create)                                            #
# --------------------------------------------------------------------------- #

def test_created_true_first_time_false_on_repeat():
    with _tmp_roots():
        conn, listing_id = _conn_with_sample()
        rec = _Recorder()
        with rec.apply():
            first = handoff.promote(conn, listing_id)
            second = handoff.promote(conn, listing_id)
        conn.close()
    assert first["created"] is True
    assert second["created"] is False


# --------------------------------------------------------------------------- #
# error handling                                                              #
# --------------------------------------------------------------------------- #

def test_unknown_listing_id_raises():
    with _tmp_roots():
        conn = store.connect()
        store.init_db(conn)
        raised = False
        try:
            handoff.promote(conn, 999999)
        except ValueError:
            raised = True
        finally:
            conn.close()
    assert raised


def test_acquire_exception_records_gap_not_raise():
    with _tmp_roots():
        conn, listing_id = _conn_with_sample()
        rec = _Recorder(acquire_raises=RuntimeError("network down"))
        with rec.apply():
            result = handoff.promote(conn, listing_id)
        conn.close()
    assert any("network down" in g for g in result["gaps"])
    assert result["slug"] == "212-pine-dr-durham"   # still prepared despite the failure


def test_acquire_error_result_records_gap():
    with _tmp_roots():
        conn, listing_id = _conn_with_sample()
        rec = _Recorder(acquire_result={"error": "no parcel match"})
        with rec.apply():
            result = handoff.promote(conn, listing_id)
        conn.close()
    assert any("no parcel match" in g for g in result["gaps"])


# --------------------------------------------------------------------------- #
# plain-python harness                                                        #
# --------------------------------------------------------------------------- #

def _main() -> int:
    tests = [(k, v) for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for name, t in tests:
        try:
            t()
            print("PASS", name)
        except AssertionError as e:
            failed += 1
            print("FAIL", name, "--", e or "assertion failed")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print("ERROR", name, "--", repr(e))
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_main())
