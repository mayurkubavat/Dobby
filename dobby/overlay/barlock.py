"""Which overlay helper owns the idle control bar (and menu bar icon, if on)."""

from __future__ import annotations

import fcntl
import os

BAR_LOCK = os.path.expanduser("~/.dobby/overlay/bar.lock")


class BarOwner:
    """Several Dobby servers can run at once (one per app or session), each
    with its own helper. Only the helper holding this lock shows the idle
    "ready" bar (and the menu bar icon, if on); the others show their bar only while
    they work, are paused or ask Allow/Deny. The lock is released when the
    helper exits, and another helper takes over on its next try.
    """

    def __init__(self, path: str = BAR_LOCK) -> None:
        self.path = path
        self.fh = None

    @property
    def owned(self) -> bool:
        return self.fh is not None

    def try_acquire(self) -> bool:
        if self.fh is not None:
            return True
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            fh = open(self.path, "a+")
        except OSError:
            return False
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return False
        self.fh = fh
        return True

    def release(self) -> None:
        if self.fh is not None:
            try:
                fcntl.flock(self.fh, fcntl.LOCK_UN)
                self.fh.close()
            except OSError:
                pass
            self.fh = None


def waiting_dir(bar_lock: str = BAR_LOCK) -> str:
    """Where helpers mark that their AI waits for a wake (next to the bar lock)."""
    return os.path.join(os.path.dirname(bar_lock), "waiting")


def mark_waiting(path: str, pid: int, on: bool) -> None:
    """Create or remove ``<path>/<pid>`` while this helper's AI waits."""
    marker = os.path.join(path, str(pid))
    try:
        if on:
            os.makedirs(path, exist_ok=True)
            open(marker, "a").close()
        else:
            os.unlink(marker)
    except OSError:
        pass


def others_waiting(path: str, own_pid: int) -> bool:
    """True if another live helper's AI waits for a wake. Drops stale markers."""
    try:
        names = os.listdir(path)
    except OSError:
        return False
    found = False
    for name in names:
        if not name.isdigit() or int(name) == own_pid:
            continue
        try:
            os.kill(int(name), 0)
        except ProcessLookupError:
            mark_waiting(path, int(name), False)
            continue
        except OSError:
            pass  # alive, owned by someone else
        found = True
    return found


def showing_dir(bar_lock: str = BAR_LOCK) -> str:
    """Where helpers mark that they show a bar other than the plain idle one.

    The bar owner hides its idle "sleeping" bar while any other helper shows
    one, so a working Dobby's bar never lands on top of it.
    """
    return os.path.join(os.path.dirname(bar_lock), "showing")


def others_showing(path: str, own_pid: int) -> bool:
    """True if another live helper shows a bar. Drops stale markers."""
    return others_waiting(path, own_pid)


class BarRow:
    """Which row of the screen this helper's bar uses while it is visible.

    Several helpers can show a bar at once (two Dobbys working, one asking
    Allow/Deny). Each takes the lowest free row (an flock on ``rows/<n>``
    next to the bar lock) and the bar sits that many rows down from the top,
    so bars stack instead of overlapping. Calling :meth:`claim` again moves
    the bar up when a lower row frees. The OS drops the locks when a helper
    exits.
    """

    def __init__(self, bar_lock: str = BAR_LOCK, max_rows: int = 6) -> None:
        self.dir = os.path.join(os.path.dirname(bar_lock), "rows")
        self.max_rows = max_rows
        self.row: int = -1
        self.fh = None

    def _try(self, n: int):
        try:
            os.makedirs(self.dir, exist_ok=True)
            fh = open(os.path.join(self.dir, str(n)), "a+")
        except OSError:
            return None
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return None
        return fh

    def claim(self) -> int:
        """The row to draw on: the current one, or a lower one that is free now."""
        limit = self.row if self.fh is not None else self.max_rows
        for n in range(limit):
            fh = self._try(n)
            if fh is not None:
                self.release()
                self.fh, self.row = fh, n
                return n
        if self.fh is not None:
            return self.row
        return 0  # every row taken (or no lock dir): overlap rather than vanish

    def release(self) -> None:
        if self.fh is not None:
            try:
                fcntl.flock(self.fh, fcntl.LOCK_UN)
                self.fh.close()
            except OSError:
                pass
        self.fh = None
        self.row = -1
