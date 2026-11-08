"""Turn "the person stepped in" exceptions into a clear answer for the AI.

When the person presses Stop, denies an Allow/Deny prompt, pauses Pinlet or
takes over the mouse and keyboard, the HAL raises one of a few exceptions
before the next action. Those must reach the AI client as what they are, not
as a generic "action failed": an AI that only sees "failed" retries, asks
Allow/Deny again, works around a Deny by another route, or carries on after
Stop.

:func:`classify` maps such an exception to a small result dict with a
``handoff`` kind and plain instructions. The server returns it from any tool
(see ``PinletServer.tool``), and catch-all ``except Exception`` blocks re-raise
these with :func:`is_handoff` so they are not swallowed on the way.

Kinds:

* ``stopped``      the person pressed Stop. End the task; do not retry.
* ``denied``       the person said Deny (or did not answer) to a risky step.
                   Do not do that step another way; ask the person.
* ``blocked``      Pinlet may not act in that app (apps.json, or the person
                   said no to "Let Pinlet use …?"). Ask the person.
* ``interrupted``  the person used the mouse or keyboard mid-task. The step
                   was skipped; look at the screen again, then decide.
* ``paused``       the person paused Pinlet and did not resume in time. Wait
                   with ``wait_for_wake`` or ask the person.
* ``control_changed`` another agent acted since this one last looked.
* ``control_busy`` another agent kept the desktop too long.
"""

from __future__ import annotations

from typing import Any


def _types() -> tuple[tuple[type[BaseException], str], ...]:
    # Imported lazily: pinlet.overlay and pinlet.control import optional bits.
    from pinlet.control.controller import ControlBusy, ControlChanged, ControlStopped
    from pinlet.overlay.client import (
        OverlayBlocked,
        OverlayDenied,
        OverlayInterrupted,
        OverlayPaused,
        OverlayStopped,
    )

    # Most specific first: OverlayDenied / OverlayInterrupted / OverlayPaused
    # are OverlayStopped subclasses.
    return (
        (OverlayBlocked, "blocked"),
        (OverlayDenied, "denied"),
        (OverlayInterrupted, "interrupted"),
        (OverlayPaused, "paused"),
        (OverlayStopped, "stopped"),
        (ControlStopped, "stopped"),
        (ControlChanged, "control_changed"),
        (ControlBusy, "control_busy"),
    )


_ADVICE: dict[str, tuple[bool, bool, str]] = {
    # kind: (look_again, retry_ok, what to do)
    "stopped": (
        False,
        False,
        "The person pressed Stop. End this task now and tell them where you stopped. "
        "Do not retry or continue unless they ask again.",
    ),
    "denied": (
        False,
        False,
        "The person denied this step. Do not do it again or reach the same result another way. "
        "Explain what you wanted to do and ask them how to go on.",
    ),
    "blocked": (
        False,
        False,
        "Pinlet is not allowed to do this in that app. Do not try it another way (another tool, "
        "the shell, a shortcut). Tell the person what you wanted to do there; they can allow it in "
        "~/.pinlet/apps.json or do that step themselves.",
    ),
    "interrupted": (
        True,
        True,
        "The person used the mouse or keyboard while Pinlet worked, so this step was skipped. "
        "Take a fresh look at the screen, then decide the next step. Do not replay old coordinates.",
    ),
    "paused": (
        True,
        False,
        "The person paused Pinlet and has not resumed it. Call wait_for_wake to wait for them, "
        "or end your turn and ask what they want.",
    ),
    "control_changed": (
        True,
        True,
        "Another agent used the desktop since your last look. This step was skipped and you "
        "now have control: look at the screen again, then decide.",
    ),
    "control_busy": (
        False,
        True,
        "Another agent is using the desktop. Try again shortly or call control_status.",
    ),
}


def kind_of(exc: BaseException) -> str | None:
    """The handoff kind for ``exc``, or None when it is an ordinary error."""
    try:
        types = _types()
    except Exception:  # noqa: BLE001 - optional modules missing
        return None
    for cls, kind in types:
        if isinstance(exc, cls):
            return kind
    return None


def is_handoff(exc: BaseException) -> bool:
    """True for exceptions that must reach the AI unchanged (re-raise them)."""
    return kind_of(exc) is not None


def classify(exc: BaseException) -> dict[str, Any] | None:
    """A tool result describing the handoff, or None for an ordinary error."""
    kind = kind_of(exc)
    if kind is None:
        return None
    look_again, retry_ok, advice = _ADVICE[kind]
    return {
        "ok": False,
        "success": False,
        "handoff": kind,
        "look_again": look_again,
        "retry_ok": retry_ok,
        "detail": str(exc),
        "note": advice,
    }
