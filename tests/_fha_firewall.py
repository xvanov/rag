"""Shared FHA-firewall assertion (PRD sec.9).

The realtor name->ethnicity ``demographic_signal`` lives ONLY on the ``agents``
table and MUST be hard-blocked from every housing-decision path. Concretely, the
decision modules (``listings/score.py``, ``listings/monitor.py``) must not import
the ``agents`` module in any form and must not name a demographic/ethnicity field.

This uses the AST to catch EVERY import spelling -- including the idiomatic
sibling forms a substring/line regex misses:
    import listings.agents
    from listings.agents import X
    from listings import agents          # <- sibling form
    from . import agents                 # <- relative sibling form
    from .agents import X

Reuse this from any decision module's test so the firewall is enforced
identically everywhere (import it: ``from _fha_firewall import assert_fha_firewall``).
"""

from __future__ import annotations

import ast

_BANNED_FIELD_TOKENS = ("demographic", "ethnicity", "race_signal", "name_inferred")


def assert_fha_firewall(module_path: str) -> None:
    """Raise AssertionError if `module_path` imports the agents module or names
    a demographic/ethnicity field. Safe to call from pytest or a plain runner."""
    with open(module_path, "r", encoding="utf-8") as f:
        source = f.read()

    lowered = source.lower()
    for tok in _BANNED_FIELD_TOKENS:
        assert tok not in lowered, (
            "%s references banned FHA field token %r (PRD sec.9 firewall)"
            % (module_path, tok))

    tree = ast.parse(source, filename=module_path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not _names_agents(alias.name), \
                    "%s imports the agents module (%r) -- FHA firewall" % (module_path, alias.name)
        elif isinstance(node, ast.ImportFrom):
            # `from X import a, b` (module X) OR `from . import agents` (name).
            mod = node.module or ""
            assert not _names_agents(mod), \
                "%s imports from the agents module (%r) -- FHA firewall" % (module_path, mod)
            for alias in node.names:
                assert alias.name != "agents", (
                    "%s does `from %s import agents` -- FHA firewall"
                    % (module_path, mod or "."))


def _names_agents(dotted: str) -> bool:
    """True if a dotted module path's LAST component is `agents`
    (agents, listings.agents, .agents, package.sub.agents)."""
    return bool(dotted) and dotted.split(".")[-1] == "agents"
