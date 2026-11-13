"""How Pinlet reads on/off settings from environment variables.

One vocabulary for every ``PINLET_*`` switch: ``1/true/yes/on`` turn a thing
on, ``0/false/no/off`` turn it off, in any letter case and with stray spaces
ignored. Anything else, including unset, is neither, so each caller decides
its own default.

Stdlib only: imported by modules that must stay light (``mode``, ``policy``).
"""

from __future__ import annotations

from collections.abc import Mapping

TRUE = frozenset({"1", "true", "yes", "on"})
FALSE = frozenset({"0", "false", "no", "off"})


def value(env: Mapping[str, str], name: str) -> str:
    """The setting ``name`` from ``env``, stripped and lowercased ("" when unset)."""
    return env.get(name, "").strip().lower()


def is_true(env: Mapping[str, str], name: str) -> bool:
    """Whether ``name`` is set to one of the "on" words."""
    return value(env, name) in TRUE


def is_false(env: Mapping[str, str], name: str) -> bool:
    """Whether ``name`` is set to one of the "off" words."""
    return value(env, name) in FALSE


def under_test_or_replay(env: Mapping[str, str]) -> bool:
    """Whether this process runs under pytest or replays a recorded session.

    The overlay and shared control are off by default in both, so a test run
    never draws on the desktop or waits for another Pinlet.
    """
    # PINLET_REPLAY has always been compared without stripping spaces.
    return "PYTEST_CURRENT_TEST" in env or env.get("PINLET_REPLAY", "").lower() in TRUE
