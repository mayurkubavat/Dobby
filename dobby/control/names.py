"""A friendly first name for each running Dobby instead of ``Dobby-<pid>``.

Each Dobby (a private server, or one client of the shared Dobby) takes the
first name from the list that no other running Dobby uses, so the menu and
the bar read "Hermione · Claude Code" and "Ron · Antigravity".

Your own names: ``~/.dobby/names.txt`` (one per line, ``#`` comments) or
``DOBBY_NAMES="Ada,Grace,Linus"``. ``DOBBY_NAMES=off`` keeps ``Dobby-<pid>``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, List, Mapping, Optional

# Friends of Dobby from the Harry Potter books (Mayur's pick, Nov 2026).
# Names only, for labelling local instances; swap them with names.txt.
BUILTIN: List[str] = [
    "Hermione", "Ron", "Harry", "Luna", "Neville", "Ginny", "Hagrid", "Hedwig", "Fred", "George",
    "Sirius", "Remus", "Tonks", "Minerva", "Albus", "Severus", "Cedric", "Cho", "Fleur", "Viktor",
    "Arthur", "Molly", "Bill", "Charlie", "Percy", "Dean", "Seamus", "Lavender", "Parvati", "Padma",
    "Oliver", "Kingsley", "Aberforth", "Winky", "Kreacher", "Hokey", "Fawkes", "Buckbeak", "Crookshanks", "Norbert",
]
SEPARATOR = " · "
_OFF = {"0", "off", "false", "no", "none"}


def pool(env: Optional[Mapping[str, str]] = None, names_file: Optional[Path] = None) -> List[str]:
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


def _clean(items: Iterable[str]) -> List[str]:
    out: List[str] = []
    for item in items:
        name = " ".join(item.split())[:24]
        if name and name not in out:
            out.append(name)
    return out


def pick(taken: Iterable[str], pid: int, names: Optional[List[str]] = None) -> str:
    """The first name nobody running uses; past the end of the list, a
    numbered one ("Hermione 2"). With names off: ``Dobby-<pid>``."""
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


def display(first: str, client: Optional[str]) -> str:
    """"Hermione · Claude Code", or just "Hermione" before the client says who it is."""
    client = (client or "").strip()
    if not client or client == first:
        return first
    return f"{first}{SEPARATOR}{client}"
