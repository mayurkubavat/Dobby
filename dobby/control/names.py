"""A friendly first name for each running Dobby instead of ``Dobby-<pid>``.

Each Dobby (a private server, or one client of the shared Dobby) takes the
first name from the list that no other running Dobby uses, so the menu and
the bar read "Tibble · Claude Code" and "Pip · Antigravity".

Your own names: ``~/.dobby/names.txt`` (one per line, ``#`` comments) or
``DOBBY_NAMES="Ada,Grace,Linus"``. ``DOBBY_NAMES=off`` keeps ``Dobby-<pid>``.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from pathlib import Path

# Original house-elf-style names (made up, not from any book or film).
BUILTIN: list[str] = [
    "Tibble",
    "Pip",
    "Mopsy",
    "Bramble",
    "Nib",
    "Fennel",
    "Thimble",
    "Pockets",
    "Wisp",
    "Juniper",
    "Button",
    "Clover",
    "Sprocket",
    "Tansy",
    "Quill",
    "Pebble",
    "Hazel",
    "Nutmeg",
    "Flick",
    "Bodkin",
    "Tuck",
    "Sorrel",
    "Biscuit",
    "Linden",
    "Mote",
    "Rumble",
    "Fig",
    "Teasel",
    "Wren",
    "Dapple",
    "Toffee",
    "Kettle",
    "Ember",
    "Moss",
    "Barley",
    "Sprig",
    "Cobble",
    "Rue",
    "Bobbin",
    "Nettle",
]
SEPARATOR = " · "
_OFF = {"0", "off", "false", "no", "none"}


def pool(env: Mapping[str, str] | None = None, names_file: Path | None = None) -> list[str]:
    """The names to hand out, in order (empty = names are off)."""
    env = os.environ if env is None else env
    raw = (env.get("DOBBY_NAMES") or "").strip()
    if raw.lower() in _OFF:
        return []
    if raw:
        return _clean(raw.split(","))
    path = names_file or Path(os.path.expanduser("~/.dobby/names.txt"))
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return list(BUILTIN)
    picked = _clean(line.split("#", 1)[0] for line in lines)
    return picked or list(BUILTIN)


def _clean(items: Iterable[str]) -> list[str]:
    out: list[str] = []
    for item in items:
        name = " ".join(item.split())[:24]
        if name and name not in out:
            out.append(name)
    return out


def pick(taken: Iterable[str], pid: int, names: list[str] | None = None) -> str:
    """The first name nobody running uses; past the end of the list, a numbered one ("Tibble 2").

    With names off: ``Dobby-<pid>``.
    """
    names = pool() if names is None else names
    if not names:
        return f"Dobby-{pid}"
    used = {t for t in taken if t}
    for name in names:
        if name not in used:
            return name
    n = 2
    while True:
        for name in names:
            candidate = f"{name} {n}"
            if candidate not in used:
                return candidate
        n += 1


def display(first: str, client: str | None) -> str:
    """Display name such as "Tibble · Claude Code", or just "Tibble" before the client says who it is."""
    client = (client or "").strip()
    if not client or client == first:
        return first
    return f"{first}{SEPARATOR}{client}"
