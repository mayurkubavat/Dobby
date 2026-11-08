"""Allow/Deny prompts that Pinlet.app shows as native cards and notifications.

When the Mac app owns the shared Pinlet (``PINLET_APP_OWNED=1``) and is
polling the control socket, :meth:`OverlayClient.confirm` hands the question
here instead of drawing the overlay's pill. The app reads
:func:`pending` (sent with every ``topbar`` reply), shows the card and answers
with the control op ``answer``. The card's "Deny and say why" sends the person's words with the Deny; the AI
gets them as ``person_said`` (see :mod:`pinlet.handoff`). The card counts down ``secs``; a question
nobody answered then is "missed" and keeps waiting in the menu, with Allow and
Deny there, for up to ``PINLET_PROMPT_HOLD`` seconds (default 300) before it is
a Deny. For "may Pinlet use this app?" questions the answer can carry
``always``, which saves the app as allowed in ``~/.pinlet/apps.json``.

If the app stops polling (quit, crashed, an old build) the overlay's own pill
is used again, so a prompt is never shown nowhere.
"""

from __future__ import annotations

import itertools
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any

LISTEN_WINDOW_S = 3.0  # the app polls every second; three misses means it is gone
DEFAULT_HOLD_S = 300.0  # a missed question waits in the menu this long

_ids = itertools.count(1)
_lock = threading.Lock()
_pending: dict[int, Prompt] = {}
_last_poll = 0.0
MAX_REASON = 500
# The Deny reason of the last question asked from this thread: the tool that
# asked raises OverlayDenied right after, in the same thread, and
# pinlet.handoff hands the reason to the AI.
_local = threading.local()


@dataclass
class Prompt:
    """An Allow/Deny question waiting for an answer in Pinlet.app."""

    id: int
    text: str
    detail: str = ""
    allow: str = "Allow"
    deny: str = "Deny"
    kind: str = "danger"  # "danger" (a risky step) or "access" (may Pinlet use an app?)
    pid: int | None = None  # the client whose Pinlet asks, when known
    name: str = ""  # "Tibble · Claude Code"
    app: str = ""  # for "access": the app Pinlet asks to use
    created: float = field(default_factory=time.time)
    card_deadline: float = 0.0  # the card counts down to this
    deadline: float = 0.0  # then it waits in the menu until this
    answer: bool | None = None
    always: bool = False
    reason: str = ""  # what the person typed with a Deny ("use the test account")
    done: threading.Event = field(default_factory=threading.Event, repr=False)

    def as_dict(self, now: float | None = None) -> dict[str, Any]:
        """The prompt as JSON for the app, with the seconds left before it times out."""
        now = time.time() if now is None else now
        return {
            "id": self.id,
            "text": self.text,
            "detail": self.detail,
            "allow": self.allow,
            "deny": self.deny,
            "kind": self.kind,
            "pid": self.pid,
            "name": self.name,
            "app": self.app,
            "secs_left": round(max(0.0, self.card_deadline - now), 1),
            "missed": now >= self.card_deadline,
            "hold_left": round(max(0.0, self.deadline - now), 1),
        }


def enabled() -> bool:
    """True when Pinlet.app owns this engine and app prompts are not turned off."""
    return (
        os.environ.get("PINLET_APP_OWNED") == "1"
        and os.environ.get("PINLET_APP_PROMPTS", "1") != "0"
    )


def hold_seconds() -> float:
    """How long a missed question waits in the menu (``PINLET_PROMPT_HOLD``; 0 = no waiting)."""
    try:
        return max(0.0, float(os.environ.get("PINLET_PROMPT_HOLD", DEFAULT_HOLD_S)))
    except ValueError:
        return DEFAULT_HOLD_S


def note_poll(now: float | None = None) -> None:
    """The app asked for the menu: it is alive and will show prompts."""
    global _last_poll
    _last_poll = time.monotonic() if now is None else now


def app_listening(now: float | None = None) -> bool:
    """True when the app has polled the menu recently enough to show a prompt."""
    now = time.monotonic() if now is None else now
    return enabled() and _last_poll > 0 and now - _last_poll <= LISTEN_WINDOW_S


def _who() -> dict[str, Any]:
    try:
        from . import sessions

        info = sessions.CURRENT.get()
    except Exception:  # noqa: BLE001
        info = None
    if info is None:
        return {}
    return {"pid": info.pid, "name": info.name}


def ask(
    text: str,
    secs: float = 30.0,
    detail: str = "",
    allow: str = "Allow",
    deny: str = "Deny",
    kind: str = "danger",
    app: str = "",
) -> bool:
    """Show the question in the app and wait for it. False on Deny or timeout.

    The card counts down ``secs``; after that the question waits in the menu
    until :func:`hold_seconds` have passed since it was asked.
    """
    secs = max(1.0, float(secs))
    total = max(secs, hold_seconds())
    p = Prompt(
        id=next(_ids),
        text=text,
        detail=detail or "",
        allow=allow or "Allow",
        deny=deny or "Deny",
        kind=kind or "danger",
        app=app or "",
        **_who(),
    )
    p.card_deadline = p.created + secs
    p.deadline = p.created + total
    with _lock:
        _pending[p.id] = p
    _local.reason = ""
    try:
        p.done.wait(total)
    finally:
        with _lock:
            _pending.pop(p.id, None)
    if not p.answer:
        _local.reason = p.reason
    return bool(p.answer)


def take_reason() -> str:
    """The person's words with this thread's last Deny, once ("" when they typed none)."""
    reason = getattr(_local, "reason", "") or ""
    _local.reason = ""
    return reason


def answer(prompt_id: int, ok: bool, always: bool = False, reason: str = "") -> bool:
    """The app's answer. False when that prompt is no longer waiting.

    ``always`` with Allow on an "access" question also saves the app as allowed
    from now on (``~/.pinlet/apps.json``). ``reason`` with a Deny is what the
    person typed for the AI; it reaches the AI as ``person_said``.
    """
    with _lock:
        p = _pending.get(int(prompt_id))
    if p is None or p.done.is_set():
        return False
    p.answer = bool(ok)
    if not ok:
        p.reason = " ".join(str(reason or "").split())[:MAX_REASON]
    p.always = bool(always and ok and p.kind == "access" and p.app)
    if p.always:
        try:
            from pinlet.overlay.grants import remember

            remember(p.app)
        except OSError:
            p.always = False
    p.done.set()
    return True


def deny_all() -> int:
    """Stop / Quit: every waiting question is a Deny."""
    with _lock:
        waiting = list(_pending.values())
    for p in waiting:
        if not p.done.is_set():
            p.answer = False
            p.done.set()
    return len(waiting)


def pending(now: float | None = None) -> list[dict[str, Any]]:
    """The unanswered prompts, oldest first, as JSON for the app."""
    with _lock:
        waiting = sorted(_pending.values(), key=lambda p: p.id)
    return [p.as_dict(now) for p in waiting if not p.done.is_set()]


def _reset_for_tests() -> None:
    global _last_poll
    deny_all()
    with _lock:
        _pending.clear()
    _last_poll = 0.0
