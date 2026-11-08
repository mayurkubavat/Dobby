"""Screen state: one numbered, text-only view of the active window.

Merges the accessibility tree and OCR into a short list of elements with
stable ids (``e1``, ``e2``...), coarse positions and the operations that are
valid right now, stamped with an observation generation. A decider (local
Gemma through :mod:`pinlet.gemma.decide`, or Claude) chooses from that
list; the executor re-binds the chosen element on a fresh observation before
acting. Pattern after the fka.dev Jev harness for macOS.
"""

from pinlet.screen_state.state import (
    EDITABLE_ROLES,
    ScreenElement,
    ScreenState,
    StateTracker,
    build_state,
    rebind,
)

__all__ = [
    "EDITABLE_ROLES",
    "ScreenElement",
    "ScreenState",
    "StateTracker",
    "build_state",
    "rebind",
]
