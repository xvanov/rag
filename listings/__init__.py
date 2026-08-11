"""listings -- the Scout / Listings Intelligence package.

Third docrag instance in this repo (after `docrag`=building-codes and
`propkb`=properties): a typed SQLite analytics layer (`store.py`) + a
swappable data-adapter layer (`sources/`) over listing data, plus (later
loops) a semantic index over listing description text, buildability
verdicts, commute ranking, scoring, and a saved-thesis monitor. See
`PRD-listings.md` for the full spec.
"""

from __future__ import annotations
