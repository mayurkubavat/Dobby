"""Pinlet -> person -> Pinlet handoff, end to end, as an AI client sees it.

These guard the flow an MCP client such as Claude goes through when the person
steps in: Stop, Deny, Pause, take-over with the mouse or keyboard, then waking
Pinlet to hand control back. Each test drives the real ``OverlayClient`` against
a scripted helper process (no AppKit, no X11, no real input) and calls tools
through ``PinletServer.call_tool``, so the answer checked is exactly what the
AI client receives.

What must hold:

* The AI is told *which* handoff happened (``handoff`` = stopped, denied,
  interrupted, paused), never a generic "action failed", and no real input
  reaches the desktop for that step.
* A Stop ends the task it was pressed for, not one started minutes later.
* After the person hands back (wake), Pinlet's next step runs at once.
* The helper only treats real actions (clicks, drags, scrolls, keys) as a
  take-over, never a mouse move toward the bar or an Allow/Deny prompt.
* Once the task is over, the "thinking" glow and the agent pointer go away.
"""

from __future__ import annotations

import asyncio
import json
import sys
import textwrap
import time
from typing import Any

import pytest

from pinlet.handoff import classify, is_handoff, kind_of
from pinlet.overlay import OverlayClient, OverlayHAL
from pinlet.overlay import protocol as p
from pinlet.overlay.client import OverlayDenied, OverlayInterrupted, OverlayPaused, OverlayStopped
from pinlet.overlay.menu import idle_cleanup, takes_over

# A helper that answers like the real one. ``intent "EMIT:a,b"`` and
# ``sleep.wait`` with reason ``EMIT:...`` make it send those events, the way a
# person's clicks on the bar would. Allow/Deny is answered from argv[2].
HELPER = (
    textwrap.dedent("""
    import json, sys
    log = open(sys.argv[1], "a")
    answer = sys.argv[2] == "allow"
    print(json.dumps({"ready": True, "version": %d}), flush=True)
    for line in sys.stdin:
        log.write(line); log.flush()
        msg = json.loads(line)
        text = msg.get("text") or msg.get("reason") or ""
        if msg.get("op") in ("intent", "sleep.wait") and text.startswith("EMIT:"):
            for name in text[5:].split(","):
                print(json.dumps({"event": name}), flush=True)
        if msg.get("op") == "confirm":
            print(json.dumps({"ack": msg["id"], "ok": answer}), flush=True)
            continue
        if "id" in msg:
            print(json.dumps({"ack": msg["id"], "ok": True}), flush=True)
        if msg.get("op") == "bye":
            break
""")
    % p.PROTOCOL_VERSION
)


class DesktopSpy:
    """Stands in for the real HAL: records the input that reached the desktop."""

    def __init__(self) -> None:
        self.input: list[tuple] = []

    def mouse_click(self, x, y, button="left", click_count=1, *a, **k):
        self.input.append(("click", x, y))

    def mouse_move(self, x, y, *a, **k):
        self.input.append(("move", x, y))

    def keyboard_hotkey(self, keys, *a, **k):
        self.input.append(("hotkey", tuple(keys)))

    def keyboard_type(self, text, *a, **k):
        self.input.append(("type", text))

    def keyboard_press_key(self, key, *a, **k):
        self.input.append(("key", key))

    def get_active_window(self):
        return None


def _client(tmp_path, answer="deny", **kw) -> OverlayClient:
    script = tmp_path / "helper.py"
    script.write_text(HELPER)
    return OverlayClient(
        cmd=[sys.executable, str(script), str(tmp_path / "ops.jsonl"), answer],
        ack_timeout=1.0,
        startup_timeout=5,
        **kw,
    )


def person(client: OverlayClient, *events: str) -> None:
    """The person does something on the bar / desktop; wait until Pinlet has read it."""
    client.intent("EMIT:" + ",".join(events))
    client.move(1, 1, ms=0)  # a round trip: the events were read before the ack


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """The server with a real overlay client in front of a desktop spy."""
    srv = pytest.importorskip("pinlet.server")
    clients = []

    def make(answer="deny", **kw):
        client = _client(tmp_path, answer, **kw)
        clients.append(client)
        desktop = DesktopSpy()
        hal = OverlayHAL(desktop, client)
        monkeypatch.setattr(srv, "hal", hal)
        monkeypatch.setattr(srv.controller, "hal", hal)
        monkeypatch.setattr(srv.trajectory_engine, "hal", hal)
        monkeypatch.setattr(srv, "_control_or_none", lambda: None)
        monkeypatch.setattr(srv.toolset_manager, "is_tool_active", lambda name: True)
        client.move(0, 0, ms=0)  # start the helper
        return client, desktop

    yield srv, make
    for c in clients:
        c.close()


def call(srv, name: str, **args: Any) -> dict[str, Any]:
    """Call a tool the way an MCP client does and return what it receives."""
    result = asyncio.run(srv.mcp_server.call_tool(name, args))
    if getattr(result, "is_error", getattr(result, "isError", False)):
        text = " ".join(getattr(c, "text", "") for c in result.content)
        return {"tool_error": text}
    data = getattr(result, "structured_content", None)
    if data is None:
        data = getattr(result, "structuredContent", None)
    if data is None:  # unstructured: the dict comes back as JSON text
        data = json.loads(result.content[0].text)
    return data.get("result", data) if isinstance(data, dict) and set(data) == {"result"} else data


def clicks(desktop: DesktopSpy) -> list[tuple]:
    return [i for i in desktop.input if i[0] == "click"]


# ------------------------------------------------- what the AI is told
def test_stop_mid_task_tells_the_ai_to_end_the_task(rig):
    srv, make = rig
    client, desktop = make()
    assert call(srv, "click", x=10, y=10, verify_delta=False)["success"] is True
    person(client, "stop")
    out = call(srv, "click", x=20, y=20, verify_delta=False)
    assert out["handoff"] == "stopped" and out["retry_ok"] is False and out["success"] is False
    assert "Do not retry" in out["note"]
    assert clicks(desktop) == [("click", 10, 10)]  # the second click never happened
    # Stop is reported once: the person asking again starts cleanly.
    assert call(srv, "click", x=30, y=30, verify_delta=False)["success"] is True


def test_deny_tells_the_ai_not_to_find_another_way(rig):
    srv, make = rig
    _, desktop = make(answer="deny")
    out = call(srv, "hotkey", combo="cmd+q", verify_delta=False)
    assert out["handoff"] == "denied" and out["retry_ok"] is False
    assert "another way" in out["note"]
    assert ("hotkey", ("cmd", "q")) not in desktop.input


def test_allow_runs_the_risky_step(rig):
    srv, make = rig
    _, desktop = make(answer="allow")
    out = call(srv, "hotkey", combo="cmd+q", verify_delta=False)
    assert out.get("success") is True and "handoff" not in out
    assert ("hotkey", ("cmd", "q")) in desktop.input


def test_take_over_skips_the_step_and_asks_for_a_fresh_look(rig):
    srv, make = rig
    client, desktop = make()
    person(client, "user_input", "user_idle")  # used the mouse, then let go
    out = call(srv, "click", x=40, y=40, verify_delta=False)
    assert out["handoff"] == "interrupted" and out["look_again"] is True and out["retry_ok"] is True
    assert clicks(desktop) == []
    # After looking again, the AI's next step goes ahead.
    assert call(srv, "click", x=41, y=41, verify_delta=False)["success"] is True
    assert clicks(desktop) == [("click", 41, 41)]


def test_long_pause_is_reported_as_paused_not_stopped(rig, monkeypatch):
    srv, make = rig
    client, desktop = make()
    real = client.checkpoint
    monkeypatch.setattr(client, "checkpoint", lambda max_wait=120.0: real(max_wait=0.3))
    person(client, "pause")
    out = call(srv, "click", x=50, y=50, verify_delta=False)
    assert out["handoff"] == "paused" and "wait_for_wake" in out["note"]
    assert clicks(desktop) == []


def test_pause_then_resume_without_touching_anything_continues(rig):
    srv, make = rig
    client, desktop = make()
    person(client, "pause")
    import threading

    threading.Timer(0.3, lambda: client.intent("EMIT:resume")).start()
    out = call(srv, "click", x=60, y=60, verify_delta=False)
    assert out["success"] is True and clicks(desktop) == [("click", 60, 60)]


def test_compound_tools_report_handoffs_too(rig):
    srv, make = rig
    client, desktop = make()
    person(client, "stop")
    out = call(srv, "click_and_type", text="hello", x=5, y=5, verify_delta=False)
    assert out["handoff"] == "stopped"
    assert desktop.input == [] or all(i[0] == "move" for i in desktop.input)


def test_trajectory_stops_at_a_handoff_even_with_continue_on_failure(rig):
    srv, make = rig
    client, desktop = make()
    person(client, "stop")
    steps = [
        {
            "step_id": f"c{i}",
            "action": "click",
            "parameters": {"x": i, "y": i},
            "verify_delta": False,
            "continue_on_failure": True,
            "retry_count": 3,
        }
        for i in (1, 2)
    ]
    out = call(
        srv, "execute_trajectory", trajectory_spec={"trajectory_name": "two clicks", "steps": steps}
    )
    assert out["handoff"] == "stopped"
    assert clicks(desktop) == []


# --------------------------------------------- Stop belongs to one task
def test_a_stale_stop_does_not_kill_the_next_task(rig):
    srv, make = rig
    client, desktop = make(stop_ttl=0.2)
    person(client, "stop")  # pressed after the last task, while Pinlet idled
    time.sleep(0.3)
    assert call(srv, "click", x=70, y=70, verify_delta=False)["success"] is True
    assert clicks(desktop) == [("click", 70, 70)]


def test_a_stale_stop_does_not_end_the_next_wait_for_wake(tmp_path):
    client = _client(tmp_path, stop_ttl=0.2)
    try:
        person(client, "stop")
        time.sleep(0.3)
        assert client.wait_for_wake(timeout_s=0.3)["timed_out"]
    finally:
        client.close()


# ------------------------------------------- the full round trip
def test_pinlet_person_pinlet_round_trip(rig, monkeypatch):
    """Pinlet works, the person takes over, the AI waits, the person wakes Pinlet, Pinlet goes on."""
    srv, make = rig
    client, desktop = make()
    assert call(srv, "click", x=1, y=1, verify_delta=False)["success"]

    person(client, "user_input", "user_idle")
    assert call(srv, "click", x=2, y=2, verify_delta=False)["handoff"] == "interrupted"

    # The AI hands over and waits; the person clicks the sleeping bar.
    monkeypatch.setattr(srv, "_control_or_none", lambda: None)
    woke = asyncio.run(srv.wait_for_wake(timeout_s=5, reason="EMIT:wake"))
    assert woke["woken"] is True and "look at the screen again" in woke["note"]

    t0 = time.monotonic()
    assert call(srv, "click", x=3, y=3, verify_delta=False)["success"] is True
    assert time.monotonic() - t0 < 2.0  # not held by the old take-over
    assert clicks(desktop) == [("click", 1, 1), ("click", 3, 3)]


def test_takeover_then_wake_while_paused_hands_back_cleanly(tmp_path):
    client = _client(tmp_path)
    try:
        person(client, "pause", "user_input")
        assert client.wait_for_wake(timeout_s=5, reason="EMIT:resume,user_idle,wake")["woken"]
        client.checkpoint(max_wait=0.5)  # neither blocked nor skipped
    finally:
        client.close()


# ----------------------------------------------- classification
def test_classification_covers_every_handoff():
    from pinlet.control import ControlBusy, ControlChanged, ControlStopped

    cases = {
        OverlayStopped("x"): "stopped",
        OverlayDenied("x"): "denied",
        OverlayInterrupted("x"): "interrupted",
        OverlayPaused("x"): "paused",
        ControlStopped("x"): "stopped",
        ControlChanged("x"): "control_changed",
        ControlBusy("x"): "control_busy",
    }
    for exc, kind in cases.items():
        assert kind_of(exc) == kind and is_handoff(exc)
        res = classify(exc)
        assert res["handoff"] == kind and res["ok"] is False and res["note"]
    assert classify(ValueError("x")) is None and not is_handoff(RuntimeError("x"))


def test_ordinary_errors_still_fail_normally(rig):
    srv, make = rig
    _, desktop = make()

    def boom(*a, **k):
        raise RuntimeError("no such window")

    desktop.mouse_click = boom
    out = call(srv, "click", x=1, y=1, verify_delta=False)
    assert out["success"] is False and "handoff" not in out and "no such window" in out["message"]


def test_tools_that_do_not_return_a_dict_raise_with_the_advice():
    from pinlet.server import ToolError, _handoff_guard

    def lister() -> list[dict[str, Any]]:
        raise OverlayStopped("stopped")

    with pytest.raises(ToolError, match=r"\[stopped\].*Do not retry"):
        _handoff_guard(lister)()


def test_every_registered_tool_is_guarded():
    srv = pytest.importorskip("pinlet.server")
    for info in srv.mcp_server._tool_manager.list_tools():
        fn = getattr(info, "fn", None)
        assert fn is not None and getattr(fn, "__wrapped__", None) is not None, info.name


# ---------------------------------------------- helper-side rules
_NOT_IDLE = dict(working=True, paused=False, takeover=False, turbo=False, confirming=False)


@pytest.mark.parametrize("kind", ["click", "drag", "scroll", "key"])
def test_real_actions_take_over_while_pinlet_works(kind):
    assert takes_over(kind, **_NOT_IDLE)


def test_moving_the_mouse_never_takes_over():
    # e.g. reaching for the bar's Pause: it must not turn into "Resume now" under the pointer
    assert not takes_over("move", **_NOT_IDLE)
    assert not takes_over("move", **{**_NOT_IDLE, "paused": True})


def test_answering_allow_deny_is_not_a_take_over():
    for kind in ("click", "key", "scroll"):
        assert not takes_over(kind, **{**_NOT_IDLE, "confirming": True})


def test_idle_pinlet_and_turbo_ignore_the_person():
    assert not takes_over("click", **{**_NOT_IDLE, "working": False})
    assert not takes_over("click", **{**_NOT_IDLE, "turbo": True})
    assert takes_over("click", **{**_NOT_IDLE, "turbo": True, "paused": True})


def _cleanup(**kw):
    base = dict(
        since_action=60.0,
        active_secs=8.0,
        linger_secs=20.0,
        cursor_state="thinking",
        pointer_visible=True,
        awake=False,
        halted=False,
        confirming=False,
    )
    base.update(kw)
    return idle_cleanup(**base)


def test_finished_task_puts_the_glow_and_pointer_away():
    assert _cleanup() == (True, True)
    assert _cleanup(cursor_state="idle") == (False, True)
    assert _cleanup(pointer_visible=False) == (True, False)


def test_nothing_is_put_away_mid_task_or_while_the_person_is_involved():
    assert _cleanup(since_action=10.0) == (False, False)  # the AI is thinking between steps
    assert _cleanup(awake=True) == (False, False)
    assert _cleanup(halted=True) == (False, False)
    assert _cleanup(confirming=True) == (False, False)


# --------------------------------------------------------- Linux wiring
def test_linux_desktop_gets_the_overlay(monkeypatch):
    import types

    from pinlet.hal import factory

    seen = []
    fake = types.ModuleType("pinlet.hal.linux_x11")
    fake.build_hal = lambda: DesktopSpy()
    monkeypatch.setitem(sys.modules, "pinlet.hal.linux_x11", fake)
    import pinlet.overlay as ov

    monkeypatch.setattr(ov, "wrap_hal", lambda hal: seen.append(hal) or hal)
    monkeypatch.setattr(factory, "_quiescence_for", lambda kind, backend: None)
    factory._build("linux", None)
    assert seen, "Linux X11 never wrapped the HAL in the overlay: no Stop, Allow/Deny or wake"


def test_resume_hands_back_to_an_ai_that_waits_after_a_pause(tmp_path):
    """The AI was told "paused" and called wait_for_wake: the person's Resume wakes it."""
    client = _client(tmp_path)
    try:
        person(client, "pause")
        r = client.wait_for_wake(timeout_s=5, reason="EMIT:resume")
        assert r["woken"] and r["waited_s"] < 3
        client.checkpoint(max_wait=0.5)
    finally:
        client.close()


def test_pause_and_resume_while_waiting_is_not_a_wake(tmp_path):
    client = _client(tmp_path)
    try:
        assert client.wait_for_wake(timeout_s=0.4, reason="EMIT:pause,resume")["timed_out"]
    finally:
        client.close()
