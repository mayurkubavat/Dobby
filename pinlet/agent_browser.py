"""``pinlet browser``: a browser of the AI's own, for background mode.

Your Chrome windows and the AI's would otherwise live in one app: keys the AI
sends could land in your window, and its clicks are judged against whichever
window of the app is on top. This starts a second, separate copy of the
browser with its own profile folder (``~/.pinlet/agent-browser``), in the
background, so the AI has a browser nobody else types in. Sign in there once
for the sites the AI should use; the profile is kept between runs.

    pinlet browser                 open the AI's browser on a blank page
    pinlet browser https://…       open it on a page
    pinlet browser --app "Brave Browser"

A browser puts itself in front when it starts, whatever it is told, so the
app you were using is put back in front a moment later. The new window still
opens on the Desktop you are looking at: move it to the Desktop the AI works
on once (Mission Control, or drag it to the screen edge).

macOS only (background mode is). Stdlib only.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path

# Chromium-family browsers, in the order they are tried.
BROWSERS = ("Google Chrome", "Chromium", "Brave Browser", "Microsoft Edge", "Arc")
_APP_DIRS = ("/Applications", "~/Applications")


def profile_dir(env: dict[str, str] | None = None) -> Path:
    """Where the AI's browser keeps its profile."""
    env = dict(os.environ) if env is None else env
    home = Path(env.get("PINLET_HOME") or Path(env.get("HOME", "~")).expanduser() / ".pinlet")
    return home / "agent-browser"


def find_browser(wanted: str | None = None, app_dirs: Sequence[str] = _APP_DIRS) -> str | None:
    """The name of an installed Chromium-family browser (``wanted`` first), or None."""
    names = (wanted,) if wanted else BROWSERS
    for name in names:
        for folder in app_dirs:
            if (Path(folder).expanduser() / f"{name}.app").exists():
                return name
    return None


def command(app: str, profile: Path, url: str) -> list[str]:
    """The ``open`` call: a new instance (-n) that does not come to the front (-g)."""
    return [
        "/usr/bin/open",
        "-g",
        "-n",
        "-a",
        app,
        "--args",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        # Build the page's accessibility tree from the start: it is how the AI
        # reads and presses things without the pointer.
        "--force-renderer-accessibility",
        url,
    ]


_LSAPPINFO = "/usr/bin/lsappinfo"


def front_app_path(run=subprocess.run) -> str | None:
    """The bundle path of the app in front (asked fresh), or None when unknown."""
    try:
        asn = run([_LSAPPINFO, "front"], capture_output=True, text=True, timeout=2).stdout.strip()
        if not asn:
            return None
        out = run(
            [_LSAPPINFO, "info", "-only", "bundlepath", asn],
            capture_output=True,
            text=True,
            timeout=2,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    _, _, value = out.partition("=")
    path = value.strip().strip('"')
    return path if path.endswith(".app") else None


def give_front_back(path: str | None, run=subprocess.run, sleep=time.sleep) -> None:
    """Puts the app the person was using back in front after the browser took it.

    Twice, because a browser may come forward a second time when its window is ready.
    """
    if not path:
        return
    for wait in (1.0, 1.5):
        sleep(wait)
        if front_app_path(run) == path:
            continue
        try:
            run(["/usr/bin/open", "-a", path], check=False, timeout=5)
        except (OSError, subprocess.SubprocessError):
            return


def main(argv: Sequence[str] | None = None) -> int:
    """Runs ``pinlet browser``. Returns the process exit code."""
    parser = argparse.ArgumentParser(
        prog="pinlet browser", description=(__doc__ or "").split("\n\n")[0]
    )
    parser.add_argument("url", nargs="?", default="about:blank")
    parser.add_argument("--app", help='browser to use, for example "Brave Browser"')
    parser.add_argument("--print", action="store_true", help="show the command, do not run it")
    args = parser.parse_args(list(argv or []))
    if sys.platform != "darwin" and not args.print:
        print("pinlet browser works on macOS only.", file=sys.stderr)
        return 2
    app = find_browser(args.app) or args.app
    if app is None:
        print(
            "No Chromium-family browser found in /Applications. Install Google Chrome, or "
            'name one with --app "<name>".',
            file=sys.stderr,
        )
        return 1
    profile = profile_dir()
    cmd = command(app, profile, args.url)
    if args.print:
        print(" ".join(f'"{c}"' if " " in c else c for c in cmd))
        return 0
    profile.mkdir(parents=True, exist_ok=True)
    before = front_app_path()
    done = subprocess.run(cmd, check=False)
    if done.returncode == 0:
        give_front_back(before)
        print(
            f"Opened {app} for the AI (profile {profile}) and put your app back in front.\n"
            "Move its window to the Desktop the AI works on; the AI finds it with list_windows."
        )
    return done.returncode
