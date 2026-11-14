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


THOUGHT_CHARS = 90
# Bidi overrides and isolates can make text read differently from what it says.
_BIDI = dict.fromkeys(map(ord, "‪‫‬‭‮⁦⁧⁨⁩‎‏"))


def clean_thought(text: str, limit: int = THOUGHT_CHARS) -> str:
    """An AI's few words for the companion's cloud, made safe to show.

    Control characters and bidi overrides are dropped, whitespace collapses
    and the text is cut to ``limit`` characters. It stays plain text: the
    cloud never turns it into a link or a button.
    """
    text = str(text or "").translate(_BIDI)
    text = "".join(c if c.isprintable() else " " for c in text)
    return short(text, limit)
