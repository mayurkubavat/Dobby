"""Server-side handle on the overlay helper.

``NullOverlay`` does nothing and is what Dobby uses when the overlay is off,
on Linux/Windows, in tests and in cassette replay. ``OverlayClient`` spawns
``python -m dobby.overlay`` lazily and talks JSON lines to it. Any failure
(helper missing, crashed, too slow) disables the client for the rest of the
session: the overlay must never block or break an action.

The helper also talks back: the control bar, Dobby's menu, the hotkeys and
user take-over send events that :meth:`OverlayClient.checkpoint` turns into a
pause, an :class:`OverlayStopped` or an :class:`OverlayInterrupted` before the
next action.
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import protocol

log = logging.getLogger(__name__)


class OverlayStopped(RuntimeError):
    """Raised before an action when the user pressed Stop or denied a step."""


class OverlayInterrupted(OverlayStopped):
    """The user took over while Dobby worked: the screen may have changed.

    The pending action is dropped; the agent should take a fresh screenshot
    and decide again. Retrying the same coordinates blindly is unsafe.
    """


class OverlayDenied(OverlayStopped):
    """The user answered Deny (or nothing) to an Allow/Deny prompt.

    The AI must not do the step another way; it should ask the user.
    """


class OverlayPaused(OverlayStopped):
    """Dobby stayed paused (or taken over) longer than a step may wait.

    The user has not said stop: the AI can wait with ``wait_for_wake``.
    """


# A Stop older than this when the next action comes was pressed for a task
# that already ended (or while Dobby idled); it must not kill the next task.
STOP_TTL_S = 120.0


class NullOverlay:
    """No-op overlay. Every method returns quickly and reports success."""

    active = False
    pid: Optional[int] = None
    awake = False  # the user woke Dobby from the control bar
    lock_on = False  # the input lock (DOBBY_INPUT_LOCK) is enabled
    background_on = False  # with the lock: press buttons without the real mouse

    def on_wake(self, cb: Callable[[], None]) -> None:
        """Call ``cb`` (on the reader thread) when the user wakes Dobby."""

    def move(self, x: float, y: float, ms: Optional[int] = None, wait: bool = True,
             verb: Optional[str] = None, probe: bool = False) -> bool:
        return True

    def click(self, button: str = "left", count: int = 1) -> bool:
        return True

    def state(self, state: str) -> bool:
        return True

    def intent(self, text: str, ms: Optional[int] = None) -> bool:
        return True

    def box(self, x: float, y: float, w: float, h: float) -> bool:
        return True

    def clear_box(self) -> bool:
        return True

    def risk(self, level: str) -> bool:
        return True

    def scan(self, rect: Optional[tuple] = None) -> bool:
        return True

    def trail_clear(self) -> bool:
        return True

    def set_visible(self, on: bool) -> bool:
        return True

    def confirm(self, text: str, secs: float = 30.0) -> bool:
        return True  # no overlay, no one to ask: keep the old behaviour

    def checkpoint(self, max_wait: float = 120.0) -> None:
        return None

    def wait_for_wake(self, timeout_s: float = 600.0, reason: str = "",
                      cancel: Optional[threading.Event] = None) -> Dict[str, Any]:
        """No overlay, no bar to click: return at once."""
        return {"woken": False, "stopped": False, "timed_out": False, "waited_s": 0.0, "unavailable": True}

    def track(self, window_id: Any = None, pid: Optional[int] = None) -> bool:
        return True

    def input_pass(self, on: bool) -> bool:
        """Dobby's own input runs now (True) or is done (False); see ``inputlock``."""
        return True

    def clear(self) -> bool:
        return True

    def hide(self) -> bool:
        return True

    def show(self) -> bool:
        return True

    def close(self) -> None:
        return None


class OverlayClient(NullOverlay):
    def __init__(
        self,
        cmd: Optional[List[str]] = None,
        config: Optional[Dict[str, Any]] = None,
        ack_timeout: float = 1.5,
        startup_timeout: float = 8.0,
        log_path: Optional[Path] = None,
        track_dir: Optional[Path] = None,
        stop_ttl: float = STOP_TTL_S,
    ) -> None:
        self.cmd = cmd or [sys.executable, "-m", "dobby.overlay"]
        self.config = dict(config or {})
        self.ack_timeout = ack_timeout
        self.startup_timeout = startup_timeout
        self.log_path = log_path
        self.track_dir = track_dir
        self.track_path: Optional[Path] = None
        self._track = None
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._start_lock = threading.RLock()  # one helper even with an eager start
        self._next_id = 1
        self._waiters: Dict[int, threading.Event] = {}
        self._results: Dict[int, bool] = {}
        self._ready = threading.Event()
        self._disabled = False
        # Control state driven by helper events.
        self._cond = threading.Condition()
        self._stop_requested = False
        self._stop_at = 0.0  # monotonic time of the pending Stop
        self.stop_ttl = stop_ttl
        self._paused = False
        self._user_active = False
        self._user_input_seen = False
        self.awake = False
        self._wakes = 0  # "wake" / "stop" events seen, for wait_for_wake
        self._stops = 0
        self._resumes = 0
        self._wake_callbacks: List[Callable[[], None]] = []
        atexit.register(self.close)

    # ----------------------------------------------------------------- state
    @property
    def active(self) -> bool:  # type: ignore[override]
        return not self._disabled and self._proc is not None and self._proc.poll() is None

    @property
    def pid(self) -> Optional[int]:  # type: ignore[override]
        return self._proc.pid if self._proc else None

    def _release_waits(self) -> None:
        with self._cond:
            self._paused = self._user_active = False
            self._cond.notify_all()

    def _disable(self, why: str) -> None:
        if not self._disabled:
            log.warning("overlay disabled: %s", why)
        self._disabled = True
        self._release_waits()

    # --------------------------------------------------------------- process
    def start_async(self) -> None:
        """Start the helper in the background (instead of on the first action)."""
        threading.Thread(target=self._start_quietly, name="dobby-overlay-start", daemon=True).start()

    def _start_quietly(self) -> None:
        try:
            self._ensure_started()
        except Exception as exc:  # noqa: BLE001 - cosmetic
            log.debug("overlay eager start failed: %s", exc)

    # --------------------------------------------------------------- process
    def _ensure_started(self) -> bool:
        with self._start_lock:
            return self._ensure_started_locked()

    def _ensure_started_locked(self) -> bool:
        if self._disabled:
            return False
        if self._proc is not None:
            if self._proc.poll() is None:
                return True
            self._disable(f"helper exited with code {self._proc.returncode}")
            return False
        env = dict(os.environ)
        env["DOBBY_OVERLAY_CONFIG"] = json.dumps(self.config)
        env["DOBBY_SERVER_PID"] = str(os.getpid())
        stderr: Any = subprocess.DEVNULL
        if self.log_path is not None:
            try:
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                stderr = open(self.log_path, "a", buffering=1)
            except OSError:
                stderr = subprocess.DEVNULL
        try:
            self._proc = subprocess.Popen(
                self.cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=stderr,
                text=True,
                bufsize=1,
                env=env,
            )
        except OSError as exc:
            self._disable(f"cannot start helper: {exc}")
            return False
        self._open_track()
        threading.Thread(target=self._read_loop, name="dobby-overlay-reader", daemon=True).start()
        return True

    def _open_track(self) -> None:
        if self.track_dir is None:
            return
        try:
            self.track_dir.mkdir(parents=True, exist_ok=True)
            name = time.strftime("%Y%m%d-%H%M%S") + f"-{os.getpid()}.jsonl"
            self.track_path = self.track_dir / name
            self._track = open(self.track_path, "a", buffering=1)
        except OSError as exc:
            log.debug("overlay: no track log: %s", exc)
            self._track = None

    def _log_track(self, line: str) -> None:
        if self._track is None:
            return
        try:
            msg = json.loads(line)
            msg.pop("id", None)
            if msg.get("op") in ("hide", "show", "ping", "confirm.cancel", "bye"):
                return
            self._track.write(json.dumps({"ts": round(time.time(), 3), **msg}, separators=(",", ":")) + "\n")
        except (OSError, ValueError):
            pass

    def _read_loop(self) -> None:
        proc = self._proc
        assert proc is not None and proc.stdout is not None
        for line in proc.stdout:
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("ready"):
                if msg.get("version") != protocol.PROTOCOL_VERSION:
                    self._disable(f"helper protocol {msg.get('version')} != {protocol.PROTOCOL_VERSION}")
                self._ready.set()
            elif "ack" in msg:
                ev = self._waiters.pop(msg["ack"], None)
                self._results[msg["ack"]] = bool(msg.get("ok"))
                if ev:
                    ev.set()
            elif "event" in msg:
                self._on_event(str(msg["event"]))
        # stdout closed: helper is gone
        self._ready.set()
        for ev in list(self._waiters.values()):
            ev.set()
        self._release_waits()

    def _on_event(self, name: str) -> None:
        with self._cond:
            if name == "stop":
                self._stops += 1
                self._stop_requested = True
                self._stop_at = time.monotonic()
                self._paused = False
            elif name == "pause":
                self._paused = True
            elif name == "resume":
                self._resumes += 1
                self._paused = False
            elif name == "user_input":
                self._user_active = True
                self._user_input_seen = True
            elif name == "user_idle":
                self._user_active = False
            elif name == "wake":
                self._wakes += 1
                self.awake = True
            elif name == "sleep":
                self.awake = False
            self._cond.notify_all()
        if name == "wake":
            for cb in list(self._wake_callbacks):
                try:
                    cb()
                except Exception:  # noqa: BLE001
                    log.exception("overlay: wake callback failed")

    def on_wake(self, cb: Callable[[], None]) -> None:  # type: ignore[override]
        """Call ``cb`` (on the reader thread) when the user wakes Dobby."""
        self._wake_callbacks.append(cb)

    def _send(self, op: str, wait: bool = False, timeout: Optional[float] = None, **fields: Any) -> bool:
        if not self._ensure_started():
            return False
        with self._lock:
            msg_id = self._next_id if wait else None
            if wait:
                self._next_id += 1
            try:
                line = protocol.encode(op, msg_id, **fields)
            except protocol.ProtocolError as exc:
                log.debug("overlay: dropped bad message: %s", exc)
                return False
            ev = threading.Event()
            if msg_id is not None:
                self._waiters[msg_id] = ev
            try:
                assert self._proc is not None and self._proc.stdin is not None
                self._proc.stdin.write(line)
                self._proc.stdin.flush()
            except (BrokenPipeError, OSError, ValueError) as exc:
                self._disable(f"write failed: {exc}")
                return False
            self._log_track(line)
        if msg_id is None:
            return True
        if not self._ready.is_set() and not self._ready.wait(self.startup_timeout):
            self._disable("helper did not become ready")
            return False
        if not ev.wait(timeout if timeout is not None else self.ack_timeout):
            self._waiters.pop(msg_id, None)
            log.debug("overlay: ack timeout for %s", op)
            return False
        return self._results.pop(msg_id, False)

    # ------------------------------------------------------------- control
    def checkpoint(self, max_wait: float = 120.0) -> None:
        """Call before each action: raise if stopped, wait while paused.

        * Stop (bar, menu, hotkey) -> :class:`OverlayStopped`.
        * Pause (bar, menu, hotkey) or user take-over (real mouse or
          keyboard input while Dobby works) -> wait here until resumed, or the
          user has been idle for a moment (auto mode), for at most
          ``max_wait`` seconds.
        * If the user touched anything since the last action -> after the
          wait, :class:`OverlayInterrupted`, so the agent looks again instead
          of replaying a click on a screen that may have changed.
        """
        if not self.active:
            return
        deadline = time.monotonic() + max_wait
        shown_wait = False
        with self._cond:
            if self._stop_requested and time.monotonic() - self._stop_at > self.stop_ttl:
                # Pressed long before this action: it ended an earlier task.
                log.info("overlay: ignoring a Stop pressed %.0f s ago", time.monotonic() - self._stop_at)
                self._stop_requested = False
            while True:
                if self._stop_requested:
                    self._stop_requested = False
                    self._user_input_seen = False
                    raise OverlayStopped("Stopped by the user from the Dobby overlay")
                if not (self._paused or self._user_active):
                    break
                if not shown_wait:
                    shown_wait = True
                    self._cond.release()
                    try:
                        self.state("waiting_user")
                    finally:
                        self._cond.acquire()
                    continue
                left = deadline - time.monotonic()
                if left <= 0:
                    raise OverlayPaused("Dobby stayed paused too long; this step was not done")
                self._cond.wait(min(left, 0.25))
            interrupted = self._user_input_seen
            self._user_input_seen = False
        if shown_wait:
            self.state("idle")
        if interrupted:
            raise OverlayInterrupted(
                "The user used the mouse or keyboard while Dobby was working. "
                "This step was skipped: take a new screenshot and decide again."
            )

    def wait_for_wake(self, timeout_s: float = 600.0, reason: str = "",
                      cancel: Optional[threading.Event] = None) -> Dict[str, Any]:
        """Block until the user wakes Dobby from the bar, presses Stop, or ``timeout_s``.

        The bar reads "Dobby is sleeping · click to wake" while this waits, on
        whichever helper belongs to this server. Returns ``woken``,
        ``stopped``, ``timed_out`` and ``waited_s``; ``unavailable`` when the
        helper cannot run or dies, ``cancelled`` when ``cancel`` is set.

        * Only a wake or Stop that arrives during the wait counts.
        * A Stop pending from before (pressed on the idle bar) is reported
          at once and consumed, like a Stop is reported by one checkpoint.
        * Pause while waiting changes nothing; but when Dobby was already
          paused as the wait began, Resume counts as a wake. A wake ends a pause or
          take-over (the helper sends "resume" / "user_idle" first), and
          clears the take-over flag here, so the AI's first step after the
          wake is not skipped as interrupted: it is told to look again anyway.
        """
        t0 = time.monotonic()
        result: Dict[str, Any] = {"woken": False, "stopped": False, "timed_out": False, "waited_s": 0.0}
        if not self._ensure_started():
            return {**result, "unavailable": True}
        with self._cond:
            if self._stop_requested and time.monotonic() - self._stop_at <= self.stop_ttl:
                self._stop_requested = False
                self._user_input_seen = False
                return {**result, "stopped": True}
            self._stop_requested = False  # a stale Stop ended an earlier task
            wakes, stops, resumes = self._wakes, self._stops, self._resumes
            # Paused before the AI began to wait (it was told "paused"): the
            # person's Resume is the hand-back, as much as a click on the bar.
            resume_wakes = self._paused
        if not self._send("sleep.wait", reason=reason or None):
            return {**result, "unavailable": True}
        deadline = t0 + max(0.0, float(timeout_s))
        try:
            with self._cond:
                while True:
                    if self._stops != stops:
                        self._stop_requested = False  # reported here, not again at the next step
                        self._user_input_seen = False
                        result["stopped"] = True
                        break
                    if self._wakes != wakes or (resume_wakes and self._resumes != resumes):
                        self._paused = self._user_active = self._user_input_seen = False
                        result["woken"] = True
                        break
                    if not self.active:
                        result["unavailable"] = True
                        break
                    if cancel is not None and cancel.is_set():
                        result["cancelled"] = True
                        break
                    left = deadline - time.monotonic()
                    if left <= 0:
                        result["timed_out"] = True
                        break
                    self._cond.wait(min(left, 0.25))
        finally:
            self._send("sleep.done")
        result["waited_s"] = round(time.monotonic() - t0, 2)
        return result

    def confirm(self, text: str, secs: float = 30.0) -> bool:
        """Ask the user Allow/Deny on the pill. False on deny or timeout."""
        if not self._ensure_started():
            return True  # helper unusable: behave like NullOverlay
        ok = self._send("confirm", wait=True, timeout=secs + 1.0, text=text, secs=secs)
        if not ok:
            self._send("confirm.cancel")
        return ok

    # ------------------------------------------------------------------- API
    def move(self, x: float, y: float, ms: Optional[int] = None, wait: bool = True,
             verb: Optional[str] = None, probe: bool = False) -> bool:
        # With ms=None the helper picks the glide time (<= 450 ms) and may
        # do an AX hit test first, so allow for that in the ack timeout.
        timeout = self.ack_timeout + (ms if ms is not None else 450) / 1000.0
        return self._send("cursor.move", wait=wait, timeout=timeout, x=x, y=y, ms=ms,
                          verb=verb, probe=True if probe else None)

    def click(self, button: str = "left", count: int = 1) -> bool:
        return self._send("cursor.click", button=button, count=count)

    def state(self, state: str) -> bool:
        return self._send("cursor.state", state=state)

    def intent(self, text: str, ms: Optional[int] = None) -> bool:
        return self._send("intent", text=text, ms=ms)

    def box(self, x: float, y: float, w: float, h: float) -> bool:
        return self._send("target.box", x=x, y=y, w=w, h=h)

    def clear_box(self) -> bool:
        return self._send("target.clear")

    def risk(self, level: str) -> bool:
        return self._send("risk", level=level)

    def scan(self, rect: Optional[tuple] = None) -> bool:
        if rect is None:
            return self._send("scan")
        x, y, w, h = rect
        return self._send("scan", x=x, y=y, w=w, h=h)

    def trail_clear(self) -> bool:
        return self._send("trail.clear")

    def set_visible(self, on: bool) -> bool:
        return self._send("overlay.visible", on=bool(on))

    def track(self, window_id: Any = None, pid: Optional[int] = None) -> bool:
        wid = protocol.window_id_int(window_id)
        if wid is None and pid is None:
            return False
        return self._send("glow.track", window_id=wid, pid=pid)

    @property
    def lock_on(self) -> bool:  # type: ignore[override]
        return self.config.get("input_lock", "off") != "off"

    @property
    def background_on(self) -> bool:  # type: ignore[override]
        return self.lock_on and bool(self.config.get("background_clicks", True))

    def input_pass(self, on: bool) -> bool:  # type: ignore[override]
        """Let Dobby's own input past the X11 input lock (shield + key grab).

        Only the X11 helper needs this: XTest input would hit its shield
        window like the person's. Waits for the ack before Dobby acts, so the
        shield is open in time; closing it again does not wait. macOS tells
        Dobby's events apart by their tag and needs no round trip.
        """
        if self.config.get("input_lock", "off") == "off" or sys.platform == "darwin" or not self.active:
            return True
        return self._send("input.pass", wait=bool(on), timeout=0.5, on=bool(on))

    def clear(self) -> bool:
        return self._send("glow.clear")

    def hide(self) -> bool:
        if not self.active:
            return True
        return self._send("hide", wait=True)

    def show(self) -> bool:
        if not self.active:
            return True
        return self._send("show")

    def close(self) -> None:
        proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                self._send("bye")
                proc.stdin.close()  # type: ignore[union-attr]
                proc.wait(timeout=1.0)
            except Exception:  # noqa: BLE001 - best effort shutdown
                proc.kill()
        if self._track is not None:
            try:
                self._track.close()
            except OSError:
                pass
            self._track = None
