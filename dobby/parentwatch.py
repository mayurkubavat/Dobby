"""Exit when the parent process goes away.

A Dobby server outlives a crashed client, and an overlay helper outlives a killed
server, whenever the pipe between them is held open by someone else.

Both then sit there forever. This watch notices the parent is gone (the process was re-parented,
or the parent pid no longer exists) and runs ``on_orphan`` once.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable

from dobby.control.lease import pid_alive


def parent_gone(
    parent: int, current: int | None = None, alive: Callable[[int], bool] = pid_alive
) -> bool:
    current = os.getppid() if current is None else current
    return current != parent or not alive(parent)


def watch_parent(
    on_orphan: Callable[[], None], interval: float = 2.0, parent: int | None = None
) -> threading.Thread | None:
    """Start a daemon thread that calls ``on_orphan`` once the parent is gone.

    Returns None when there is no real parent to watch (already orphaned, or pid 1).
    """
    parent = os.getppid() if parent is None else parent
    if parent <= 1:
        return None

    def run() -> None:
        while True:
            time.sleep(interval)
            if parent_gone(parent):
                on_orphan()
                return

    thread = threading.Thread(target=run, name="parent-watch", daemon=True)
    thread.start()
    return thread
