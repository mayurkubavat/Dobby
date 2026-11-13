"""Text helpers shared by the overlay modules.

Stdlib only, so the pure modules (``menu``, ``style``, ``topbar``) and both
native helpers can import it.
"""

from __future__ import annotations


def short(text: str, limit: int) -> str:
    """Collapses whitespace and trims ``text`` to ``limit`` characters.

    Args:
        text: The text to shorten. Runs of whitespace become one space.
        limit: The most characters to return, counting the ellipsis.

    Returns:
        The text itself when it fits, otherwise its first ``limit - 1``
        characters followed by "…".
    """
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
