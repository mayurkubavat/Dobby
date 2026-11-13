"""How fast Pinlet acts: at a pace a person can watch and stop, or as fast as it can.

``PINLET_PACE`` (Settings > General > Pace in Pinlet.app):

* ``human`` (the default): each step starts at least 0.7 s after the last,
  the pointer rests on its target for a moment before a click, and text is
  typed at a readable speed. The person can follow the bar and the pointer
  and reach Stop in time.
* ``fast``: no added waits.

The waits are short on purpose: an AI already spends seconds looking and
thinking between steps, so the pace mostly shows inside tools that do
several steps at once.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass

HUMAN, FAST = "human", "fast"


@dataclass(frozen=True)
class Pace:
    """The waits of one pace, in seconds (typing in milliseconds a character)."""

    name: str
    gap_s: float = 0.0  # least time from one step's end to the next step's start
    dwell_s: float = 0.0  # the pointer rests on its target this long before a click or drag
    quiet_dwell_s: float = 0.0  # background mode: the bar names the step this long first
    type_ms: float = 12.0  # per character
    type_cap_s: float = 0.0  # long text speeds up so it never takes longer than this (0: no cap)

    def typing_delay_ms(self, text: str) -> float:
        """Milliseconds per character for ``text``."""
        if not self.type_cap_s or not text:
            return self.type_ms
        return max(8.0, min(self.type_ms, self.type_cap_s * 1000.0 / len(text)))


PACES = {
    HUMAN: Pace(HUMAN, gap_s=0.7, dwell_s=0.25, quiet_dwell_s=0.45, type_ms=45.0, type_cap_s=8.0),
    FAST: Pace(FAST),
}


def current(env: Mapping[str, str] | None = None) -> Pace:
    """The pace in force (``PINLET_PACE``; anything unknown is ``human``)."""
    env = os.environ if env is None else env
    return PACES.get(env.get("PINLET_PACE", "").strip().lower(), PACES[HUMAN])


class Pacer:
    """Keeps the gap between steps. One per overlay HAL."""

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._clock, self._sleep = clock, sleep
        self._last_end: float | None = None

    def before(self, pace: Pace) -> float:
        """Wait until the gap since the last step has passed; returns the seconds waited."""
        if self._last_end is None or pace.gap_s <= 0:
            return 0.0
        wait = self._last_end + pace.gap_s - self._clock()
        if wait > 0:
            self._sleep(wait)
            return wait
        return 0.0

    def dwell(self, seconds: float) -> None:
        """Rest for a moment (the pointer on its target, or the bar naming the step)."""
        if seconds > 0:
            self._sleep(seconds)

    def done(self) -> None:
        """A step just ended."""
        self._last_end = self._clock()
