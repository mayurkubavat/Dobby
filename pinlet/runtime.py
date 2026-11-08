"""Where Pinlet's own Python lives, and the one command every AI tool runs.

Pinlet installs itself into a folder it owns instead of depending on a source
checkout staying put:

* macOS: ``~/Library/Application Support/Pinlet``
* Linux: ``$XDG_DATA_HOME/pinlet`` (``~/.local/share/pinlet``)

Inside it, ``bin/uv`` (when uv was not already installed), ``python/`` (the
Python uv downloaded), ``src/`` (the Pinlet source it was installed from) and
``runtime/`` (the virtual environment). ``scripts/install.sh`` and Pinlet.app
create it.

AI tools never point at that venv directly. They run ``~/.pinlet/bin/pinlet``, a
small launcher that tries the Python it was written for, then the managed
runtime, and otherwise prints how to fix it. Moving a checkout or reinstalling
then never breaks an AI tool's config.

Stdlib only: ``pinlet setup`` and ``pinlet doctor`` use it before anything else imports.
"""

from __future__ import annotations

import os
import shlex
import sys
from collections.abc import Mapping
from pathlib import Path

LAUNCHER_MARK = "# Written by Pinlet"


def data_dir(env: Mapping[str, str] | None = None, platform: str = sys.platform) -> Path:
    """The folder Pinlet installs its own runtime into (``PINLET_DATA_DIR`` overrides it)."""
    env = os.environ if env is None else env
    if env.get("PINLET_DATA_DIR"):
        return Path(os.path.expanduser(env["PINLET_DATA_DIR"]))
    if platform == "darwin":
        return Path(os.path.expanduser("~/Library/Application Support/Pinlet"))
    xdg = env.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path(os.path.expanduser("~/.local/share"))
    return base / "pinlet"


def runtime_python(env: Mapping[str, str] | None = None, platform: str = sys.platform) -> Path:
    """The managed runtime's Python (may not exist yet)."""
    return data_dir(env, platform) / "runtime" / "bin" / "python"


def launcher_path(env: Mapping[str, str] | None = None) -> Path:
    """The stable command AI tools run: ``~/.pinlet/bin/pinlet``."""
    env = os.environ if env is None else env
    if env.get("PINLET_LAUNCHER"):
        return Path(os.path.expanduser(env["PINLET_LAUNCHER"]))
    return Path(os.path.expanduser("~/.pinlet/bin/pinlet"))


def is_managed(python: str | os.PathLike[str] = sys.executable) -> bool:
    """True when ``python`` belongs to the runtime Pinlet installed for itself."""
    try:
        Path(python).absolute().relative_to(data_dir() / "runtime")
    except ValueError:
        return False
    return True


def launcher_text(preferred: str, fallback: str) -> str:
    """The launcher script: ``preferred`` Python, else ``fallback``, else a clear error."""
    candidates = [preferred] if preferred == fallback else [preferred, fallback]
    quoted = " ".join(shlex.quote(c) for c in candidates)
    return (
        "#!/bin/sh\n"
        f"{LAUNCHER_MARK}. AI tools run this to reach Pinlet; `pinlet setup` rewrites it.\n"
        f"for py in {quoted}; do\n"
        # The venv's own console script, so a `pinlet` folder in the current directory
        # never shadows the installed package (as `python -m` would let it).
        '  if [ -x "${py%/*}/pinlet" ]; then exec "${py%/*}/pinlet" "$@"; fi\n'
        '  if [ -x "$py" ]; then exec "$py" -m pinlet.cli "$@"; fi\n'
        "done\n"
        'echo "Pinlet: its Python is gone. Open Pinlet.app, or run the Pinlet installer'
        ' again (scripts/install.sh)." >&2\n'
        "exit 127\n"
    )


def launcher_targets(path: Path | None = None) -> list[str]:
    """The Pythons an existing launcher tries, in order ([] if it is not ours)."""
    path = path or launcher_path()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    if LAUNCHER_MARK not in text:
        return []
    for line in text.splitlines():
        if line.startswith("for py in ") and line.endswith("; do"):
            return shlex.split(line[len("for py in ") : -len("; do")])
    return []


def write_launcher(python: str | None = None, path: Path | None = None) -> Path:
    """Writes the launcher for ``python`` (default: this interpreter) and returns its path.

    A launcher someone else put there (no Pinlet mark) is left alone.
    """
    path = path or launcher_path()
    python = python or sys.executable
    text = launcher_text(python, str(runtime_python()))
    try:
        if path.read_text(encoding="utf-8") == text:
            return path
    except OSError:
        pass
    if path.exists() and not launcher_targets(path):
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.chmod(tmp, 0o755)
    os.replace(tmp, path)
    return path


def launcher_works(path: Path | None = None) -> bool:
    """True when the launcher exists and at least one Python it tries is there."""
    path = path or launcher_path()
    return path.is_file() and any(os.access(p, os.X_OK) for p in launcher_targets(path))
