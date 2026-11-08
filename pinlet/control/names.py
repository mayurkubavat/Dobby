"""A friendly first name for each running Pinlet instead of ``Pinlet-<pid>``.

Each Pinlet (a private server, or one client of the shared Pinlet) takes the
first name from the list that no other running Pinlet uses, so the menu and
the bar read "Tibble · Claude Code" and "Pip · Antigravity".

Your own names: ``~/.pinlet/names.txt`` (one per line, ``#`` comments) or
``PINLET_NAMES="Ada,Grace,Linus"``. ``PINLET_NAMES=off`` keeps ``Pinlet-<pid>``.
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
    raw = (env.get("PINLET_NAMES") or "").strip()
    if raw.lower() in _OFF:
        return []
    if raw:
        return _clean(raw.split(","))
    path = names_file or Path(os.path.expanduser("~/.pinlet/names.txt"))
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

    With names off: ``Pinlet-<pid>``.
    """
    names = pool() if names is None else names
    if not names:
        return f"Pinlet-{pid}"
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


def is_pool_name(name: str, names: list[str] | None = None) -> bool:
    """True for a name :func:`pick` could hand out ("Tibble", "Tibble 2")."""
    names = pool() if names is None else names
    if name in names:
        return True
    base, _, n = name.rpartition(" ")
    return bool(base) and n.isdigit() and int(n) >= 2 and base in names


def display(first: str, client: str | None) -> str:
    """Display name such as "Tibble · Claude Code", or just "Tibble" before the client says who it is."""
    client = (client or "").strip()
    if not client or client == first:
        return first
    return f"{first}{SEPARATOR}{client}"
