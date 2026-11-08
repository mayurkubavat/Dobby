"""``pinlet triage``: hand a Pinlet problem to your own AI tool, with the facts it needs.

Gathers what someone debugging Pinlet would ask for first (``pinlet doctor``,
each client's permission level, the admin policy, what the agents did lately
and the end of Pinlet's logs) into one Markdown report under
``~/.pinlet/logs/``, then starts Claude Code, Codex, Gemini CLI or Cursor's
``agent`` with a short prompt that points at the report and its playbook.

Nothing leaves the computer unless that AI tool sends it. Log lines that look
like typed text or note text are redacted, and the journal never holds tool
arguments to begin with.

    pinlet triage                 write the report and start the first AI tool found
    pinlet triage --agent codex   pick the tool (claude, codex, gemini, cursor)
    pinlet triage --print         print the report instead of starting anything
    pinlet triage "clicks miss"   add what you saw to the report
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path

PLAYBOOK = """\
## Playbook

You are helping the person debug Pinlet, the local MCP server that lets AI
tools use this computer's mouse, keyboard and screen. Work through this in order:

1. Read the whole report. Start from what the person said they saw, if anything.
2. Fix what `pinlet doctor` marks FAIL first, then warn. Each line gives a fix.
3. If an AI tool shows Pinlet as connected but does nothing, the usual cause is
   that the tool never called Pinlet in that session: restart that tool.
4. If clicks or typing are refused, check the permission level and policy
   sections: Ask for risky and Ask everything put an Allow/Deny card on screen,
   and an admin policy can block an app outright.
5. Look at the end of the logs for the first error, not the last one.
6. Say what you think is wrong and why before changing anything. Ask before you
   change Pinlet's settings, edit `~/.pinlet`, or reinstall.
7. Do not paste this report anywhere outside this computer without asking.
"""

# Where each AI tool's command line takes a first prompt.
AGENTS: dict[str, tuple[str, ...]] = {
    "claude": ("claude",),
    "codex": ("codex",),
    "gemini": ("gemini", "-i"),
    "cursor": ("agent",),
}
ORDER = ("claude", "codex", "gemini", "cursor")

_REDACT = re.compile(
    r"""(?P<key>["']?(?:text|note|value|password|secret|token)["']?\s*[:=]\s*)"""
    r"""(?P<val>"[^"]*"|'[^']*'|\S+)""",
    re.IGNORECASE,
)


def redact(line: str) -> str:
    """Hides values after ``text=``/``"note":`` and similar keys in a log line."""
    return _REDACT.sub(lambda m: f"{m.group('key')}[redacted]", line)


def tail(path: Path, lines: int) -> list[str]:
    """The last ``lines`` lines of a text file, redacted ([] when it is missing)."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - 256 * 1024))
            data = fh.read().decode("utf-8", "replace")
    except OSError:
        return []
    return [redact(x) for x in data.splitlines()[-lines:]]


def _section(title: str, body: str) -> str:
    return f"## {title}\n\n{body.rstrip() or '(nothing)'}\n"


def _fenced(lines: Sequence[str]) -> str:
    return "```\n" + "\n".join(lines) + "\n```" if lines else "(empty or missing)"


def report(said: str = "", log_lines: int = 80, home: Path | None = None) -> str:
    """The triage report as Markdown: what the person saw, checks, levels, policy, activity, logs."""
    from pinlet import __version__, doctor, journal, mode, policy
    from pinlet.hub import paths

    parts = [
        f"# Pinlet triage report\n\nPinlet {__version__} on {sys.platform}, "
        f"Python {sys.version.split()[0]}, written {time.strftime('%Y-%m-%d %H:%M:%S %Z')}.\n",
        _section("What the person saw", said or "Not given. Ask them what went wrong."),
    ]
    try:
        parts.append(
            _section("pinlet doctor", _fenced(doctor.report(doctor.checks(home)).splitlines()))
        )
    except Exception as exc:  # noqa: BLE001 - a report with a hole beats no report
        parts.append(_section("pinlet doctor", f"doctor failed: {exc}"))
    try:
        info = mode.overview()
        levels = info["modes"] or {}
        lines = [f"- {k}: {v}" for k, v in sorted(levels.items())] or ["- every client: default"]
        lines.append(f"- admin floor: {info['floor'] or 'none'}")
        parts.append(_section("Permission levels", "\n".join(lines)))
    except Exception as exc:  # noqa: BLE001
        parts.append(_section("Permission levels", f"could not read: {exc}"))
    try:
        parts.append(_section("Admin policy", _fenced(policy.describe(policy.current()))))
    except Exception as exc:  # noqa: BLE001
        parts.append(_section("Admin policy", f"could not read: {exc}"))
    try:
        lines = [
            f"- {g['end'] or g['start']}: {g['text']}" for g in journal.summarize(journal.read(400))
        ]
        parts.append(_section("What the agents did lately", "\n".join(lines[-30:])))
    except Exception as exc:  # noqa: BLE001
        parts.append(_section("What the agents did lately", f"could not read: {exc}"))
    logs = Path(os.path.expanduser("~/.pinlet/logs"))
    for name, where in (
        ("Shared Pinlet log", paths.daemon_log()),
        ("Tool log", logs / "server.log"),
    ):
        parts.append(
            _section(f"{name} ({where}, last {log_lines} lines)", _fenced(tail(where, log_lines)))
        )
    parts.append(PLAYBOOK)
    return "\n".join(parts)


def write(text: str, folder: Path | None = None) -> Path:
    """Saves the report as ``triage-<time>.md`` (readable by this user only)."""
    folder = folder or Path(os.path.expanduser("~/.pinlet/logs"))
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"triage-{time.strftime('%Y%m%d-%H%M%S')}.md"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def pick_agent(wanted: str = "", which: Callable[[str], str | None] | None = None) -> str | None:
    """The AI tool to start: ``wanted`` if it is installed, else the first of ``ORDER`` found."""
    which = which or shutil.which
    for key in [wanted] if wanted else ORDER:
        if key in AGENTS and which(AGENTS[key][0]):
            return key
    return None


def prompt_for(path: Path) -> str:
    """The first message for the AI tool: read the report, follow its playbook."""
    return (
        f"Pinlet is misbehaving on this computer. Read the triage report at {path} "
        "and follow the playbook at its end. Start by telling me what you think is wrong."
    )


def main(argv: Sequence[str] | None = None, run: Callable[[list[str]], int] | None = None) -> int:
    """Runs ``pinlet triage``. Returns the AI tool's exit code, 0 after --print, 1 when none is found."""
    parser = argparse.ArgumentParser(prog="pinlet triage", description=__doc__.splitlines()[0])
    parser.add_argument("said", nargs="*", help="what you saw go wrong")
    parser.add_argument("--agent", default="", choices=["", *ORDER], help="the AI tool to start")
    parser.add_argument("--print", action="store_true", help="print the report; start nothing")
    parser.add_argument("--lines", type=int, default=80, help="log lines to include")
    args = parser.parse_args(list(argv or []))
    text = report(" ".join(args.said).strip(), max(1, args.lines))
    if args.print:
        print(text)
        return 0
    path = write(text)
    agent = pick_agent(args.agent)
    if agent is None:
        wanted = f"{args.agent} is not installed. " if args.agent else ""
        print(
            f"Wrote {path}.\n{wanted}No Claude Code, Codex, Gemini CLI or Cursor agent found on PATH. "
            "Open the report in any AI tool and ask it to follow the playbook at the end."
        )
        return 1
    command = [*AGENTS[agent], prompt_for(path)]
    print(f"Wrote {path}. Starting {agent}…", flush=True)
    if run is not None:
        return run(command)
    return subprocess.call(command)  # interactive: the tool takes over this terminal
