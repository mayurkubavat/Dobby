"""Turbo (default) or secure: how much Dobby asks before it acts.

* ``turbo`` (default): no permission prompts. Dobby does not ask before using
  an app, before risky shortcuts or before clicking Send / Delete / Pay, and
  it keeps going when the person touches the mouse. Stop and Pause still work.
* ``secure``: every permission UI is on. "Let Dobby use …?" once per app
  (``dobby.overlay.grants``, ``~/.dobby/apps.json``), Allow/Deny for risky
  shortcuts and consequential buttons, no typing into password fields, and
  Dobby pauses when the person takes over.

Pick secure with ``dobby --secure`` or ``DOBBY_MODE=secure`` (``DOBBY_SECURE=1``
works too) in the MCP client's config. The mode is fixed for the life of the
server so an AI cannot switch it off mid-task.
"""

from __future__ import annotations

import contextlib
import contextvars
import os
import sys
from typing import Iterator, Mapping, Optional, Sequence

TURBO, SECURE = "turbo", "secure"
_TRUE = {"1", "true", "yes", "on"}


# In the shared Dobby (``dobby serve``) each connected client has its own
# mode, set for the duration of its session (see dobby.hub).
_SESSION_MODE: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("dobby_mode", default=None)


@contextlib.contextmanager
def session_mode(mode: Optional[str]) -> Iterator[None]:
    """Run the block (and the tool calls it starts) in ``mode``."""
    token = _SESSION_MODE.set(mode if mode in (TURBO, SECURE) else None)
    try:
        yield
    finally:
        _SESSION_MODE.reset(token)


def security_mode(env: Optional[Mapping[str, str]] = None, argv: Optional[Sequence[str]] = None) -> str:
    if env is None and argv is None:
        current = _SESSION_MODE.get()
        if current is not None:
            return current
    env = os.environ if env is None else env
    argv = sys.argv if argv is None else argv
    if "--secure" in argv:
        return SECURE
    if "--turbo" in argv:
        return TURBO
    mode = env.get("DOBBY_MODE", "").strip().lower()
    if mode in (TURBO, SECURE):
        return mode
    if env.get("DOBBY_SECURE", "").strip().lower() in _TRUE:
        return SECURE
    return TURBO


def is_secure(env: Optional[Mapping[str, str]] = None, argv: Optional[Sequence[str]] = None) -> bool:
    return security_mode(env, argv) == SECURE
