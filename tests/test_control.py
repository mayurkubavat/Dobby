"""Shared control: two agents, one desktop, no races."""

from __future__ import annotations

import itertools
import json
import multiprocessing as mp
import os
import time
from pathlib import Path

import pytest

from pinlet.control import (
    ControlBusy,
    ControlChanged,
    ControlHAL,
    Controller,
    ControlSettings,
    control_enabled,
    settings_from_env,
    wrap_hal,
)
from pinlet.control.lease import Agent, decide, empty_state
from pinlet.control.registry import AgentPidCache, list_agents


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += s


SET = ControlSettings(idle_grace=10, slice=60, max_wait=0, hold_cap=120, aging=15, poll=0.1)


def make(tmp_path, clock, name, pid, priority=0, alive=None, **over):
    settings = ControlSettings(**{**SET.__dict__, **over})
    return Controller(
        tmp_path,
        name=name,
        priority=priority,
        settings=settings,
        pid=pid,
        clock=clock,
        sleep=clock.sleep,
        alive=alive or (lambda p: True),
        register=False,
    )


def act(ctl):
    with ctl.action():
        pass


# ---------------------------------------------------------------- policy
def test_decide_free_then_held():
    st = empty_state()
    a = Agent("a", "A", 1)
    assert decide(st, a, 0, SET, lambda p: True) == (True, "free")
    assert st["epoch"] == 1 and st["holder"]["busy"] is True
    assert decide(st, a, 1, SET, lambda p: True) == (True, "held")
    assert st["epoch"] == 1


def test_second_agent_waits_while_first_works(tmp_path):
    c = Clock()
    a, b = make(tmp_path, c, "Claude", 1), make(tmp_path, c, "Agy", 2)
    act(a)
    c.t += 3
    with pytest.raises(ControlBusy, match="Claude, is using the screen"):
        act(b)


def test_idle_holder_hands_over(tmp_path):
    c = Clock()
    a, b = make(tmp_path, c, "Claude", 1), make(tmp_path, c, "Agy", 2)
    act(a)
    c.t += 10.5
    act(b)
    assert b.status()["you_hold_control"]
    with pytest.raises(ControlBusy, match="Agy, is using the screen"):
        act(a)


def test_slice_forces_turn_even_when_holder_is_active(tmp_path):
    c = Clock()
    a, b = make(tmp_path, c, "Claude", 1), make(tmp_path, c, "Agy", 2)
    act(a)
    for _ in range(10):  # Claude acts every 5.5 s for 55 s; Agy keeps asking
        c.t += 5
        act(a)
        with pytest.raises(ControlBusy):
            act(b)
        c.t += 0.5
    c.t += 6  # 61 s since Claude took control, Claude between steps
    act(b)
    assert b.status()["you_hold_control"]


def test_lone_agent_keeps_control_forever(tmp_path):
    c = Clock()
    a = make(tmp_path, c, "Claude", 1)
    act(a)
    c.t += 3600
    act(a)
    assert a.status()["holder"]["held_s"] >= 3600


def test_busy_step_is_never_split(tmp_path):
    c = Clock()
    a, b = make(tmp_path, c, "Claude", 1), make(tmp_path, c, "Agy", 2)
    with a.tool_scope():
        act(a)  # click
        c.t += 100  # long tool, past idle grace and slice
        with pytest.raises(ControlBusy):
            act(b)
        act(a)  # type, same turn
    c.t += 10.5
    act(b)


def test_dead_holder_loses_lease_at_once(tmp_path):
    c = Clock()
    dead = set()
    alive = lambda p: p not in dead
    a = make(tmp_path, c, "Claude", 1, alive=alive)
    b = make(tmp_path, c, "Agy", 2, alive=alive)
    with a.tool_scope():
        act(a)
        dead.add(1)  # crashed mid-tool
        act(b)


def test_release_hands_over_immediately(tmp_path):
    c = Clock()
    a, b = make(tmp_path, c, "Claude", 1), make(tmp_path, c, "Agy", 2)
    act(a)
    assert a.release() is True
    act(b)


def test_handoff_after_other_agent_acted_requires_new_look(tmp_path):
    c = Clock()
    a, b = make(tmp_path, c, "Claude", 1), make(tmp_path, c, "Agy", 2)
    act(a)
    a.note_observation()
    a.release()
    act(b)  # Agy changes the screen
    b.release()
    with pytest.raises(ControlChanged, match="take a new screenshot"):
        act(a)
    assert a.status()["you_hold_control"]  # kept control, only the step was skipped
    act(a)  # told once; next action goes through


def test_no_new_look_needed_if_observed_after_last_action(tmp_path):
    c = Clock()
    a, b = make(tmp_path, c, "Claude", 1), make(tmp_path, c, "Agy", 2)
    act(a)
    a.release()
    act(b)
    b.release()
    a.note_observation()  # Claude looked after Agy finished
    act(a)


def test_own_actions_never_trigger_new_look(tmp_path):
    c = Clock()
    a = make(tmp_path, c, "Claude", 1)
    a.note_observation()
    for _ in range(3):
        act(a)
        a.release()
        c.t += 1


def test_higher_priority_waiter_goes_first(tmp_path):
    from pinlet.control.lease import LeaseStore

    c = Clock()
    a = make(tmp_path, c, "Claude", 1)
    b = make(tmp_path, c, "Agy", 2, priority=0)
    hi = make(tmp_path, c, "Phone", 3, priority=5)
    act(a)
    with LeaseStore(tmp_path).transaction() as st:  # both queued at the same moment
        for w in (b, hi):
            st["waiters"][w.agent.id] = {
                "id": w.agent.id,
                "name": w.agent.name,
                "pid": w.agent.pid,
                "priority": w.agent.priority,
                "since": c.t,
                "seen": c.t,
            }
    a.release()
    with pytest.raises(ControlBusy):
        act(b)
    act(hi)


def test_aging_prevents_starvation():
    st = empty_state()
    lo, hi = Agent("lo", "Lo", 1, 0), Agent("hi", "Hi", 2, 1)
    st["waiters"] = {
        "lo": {"id": "lo", "pid": 1, "priority": 0, "since": 0, "seen": 100},
        "hi": {"id": "hi", "pid": 2, "priority": 1, "since": 99, "seen": 100},
    }
    assert decide(st, hi, 100, SET, lambda p: True) == (False, "queued")
    assert decide(st, lo, 100, SET, lambda p: True)[0] is True


def test_hold_suspends_idle_and_slice(tmp_path):
    c = Clock()
    a, b = make(tmp_path, c, "Claude", 1), make(tmp_path, c, "Agy", 2)
    act(a)
    assert a.hold(30)["held_for"] == 30
    c.t += 20
    with pytest.raises(ControlBusy):
        act(b)
    c.t += 11
    act(b)


def test_hold_is_capped(tmp_path):
    c = Clock()
    a = make(tmp_path, c, "Claude", 1)
    assert a.hold(10_000)["held_for"] == 120


def test_preempt_by_priority_at_step_boundary(tmp_path):
    c = Clock()
    a = make(tmp_path, c, "Claude", 1, preempt=True)
    boss = make(tmp_path, c, "Mayur-phone", 2, priority=9, preempt=True)
    act(a)
    c.t += 1
    act(boss)


def test_busy_busy_timeout_treats_hung_step_as_finished(tmp_path):
    c = Clock()
    a, b = make(tmp_path, c, "Claude", 1), make(tmp_path, c, "Agy", 2)
    with a.tool_scope():
        act(a)
        c.t += 301
        act(b)


def test_status_lists_waiters_and_rules(tmp_path):
    c = Clock()
    a, b = make(tmp_path, c, "Claude", 1), make(tmp_path, c, "Agy", 2)
    act(a)
    from pinlet.control.lease import LeaseStore

    with LeaseStore(tmp_path).transaction() as st:
        st["waiters"]["x"] = {
            "id": "x",
            "name": "Agy",
            "pid": 2,
            "priority": 0,
            "since": c.t,
            "seen": c.t,
        }
    s = b.status()
    assert s["holder"]["name"] == "Claude" and not s["you_hold_control"]
    assert s["waiting"][0]["name"] == "Agy"
    assert s["rules"]["idle_grace_s"] == 10


# --------------------------------------------------------------- HAL glue
class FakeHAL:
    def __init__(self):
        self.calls = []

    def mouse_click(self, x, y, button="left", click_count=1):
        self.calls.append(("click", x, y))

    def keyboard_type(self, text, delay_ms=12.0):
        self.calls.append(("type", text))

    def capture_screen(self, region=None):
        self.calls.append(("capture",))
        return "img"

    def get_screen_size(self):
        return (100, 100)


def test_control_hal_wraps_actions_and_marks_observations(tmp_path):
    c = Clock()
    a, b = make(tmp_path, c, "Claude", 1), make(tmp_path, c, "Agy", 2)
    hal = ControlHAL(FakeHAL(), a)
    hal.mouse_click(1, 2)
    assert a.status()["you_hold_control"]
    assert hal.capture_screen() == "img"
    assert hal.get_screen_size() == (100, 100)
    assert not hasattr(hal, "get_accessibility_tree")  # passthrough keeps hasattr honest
    c.t += 1
    with pytest.raises(ControlBusy):
        ControlHAL(FakeHAL(), b).keyboard_type("hi")


def test_wrap_hal_noop_without_controller(monkeypatch):
    monkeypatch.setenv("PINLET_CONTROL", "0")
    from pinlet import control

    control.set_controller(None)
    h = FakeHAL()
    assert wrap_hal(h) is h


def test_control_off_under_pytest_by_default():
    assert control_enabled({"PYTEST_CURRENT_TEST": "x"}) is False
    assert control_enabled({}) is True
    assert control_enabled({"PINLET_CONTROL": "1", "PYTEST_CURRENT_TEST": "x"}) is True


def test_settings_from_env():
    s = settings_from_env(
        {
            "PINLET_CONTROL_IDLE_GRACE": "4",
            "PINLET_CONTROL_SLICE": "x",
            "PINLET_CONTROL_PREEMPT": "1",
        }
    )
    assert s.idle_grace == 4 and s.slice == 60 and s.preempt


def test_overlay_found_through_control_hal(tmp_path):
    from pinlet.hal.session import DesktopSession
    from pinlet.overlay import NullOverlay, OverlayHAL, find_overlay_hal
    from pinlet.types import DisplayMetrics, DisplayTopology

    class Shown(NullOverlay):
        def __init__(self):
            self.texts = []

        def intent(self, text, ms=None):
            self.texts.append(text)
            return True

    c = Clock()
    ov = OverlayHAL(FakeHAL(), Shown())
    ctl = make(tmp_path, c, "Agy", 2)
    wrapped = wrap_hal(ov, controller=ctl)
    topo = DisplayTopology(
        displays=[DisplayMetrics(display_id="p", origin=(0, 0), size=(1, 1), scale=1.0)],
        primary_id="p",
    )
    session = DesktopSession(backend=wrapped, topology=topo, quiescence=None)
    assert find_overlay_hal(session) is ov
    other = make(tmp_path, c, "Claude", 1)
    act(other)
    with pytest.raises(ControlBusy):
        session.mouse_click(1, 1)
    assert ov.overlay.texts[:2] == ["Waiting for Claude", ""]


def test_overlay_label_defaults_to_agent_name():
    from pinlet.overlay import overlay_config

    assert overlay_config({"PINLET_AGENT_NAME": "Agy"})["label"] == "Agy"
    assert overlay_config({"PINLET_AGENT_NAME": "Agy", "PINLET_OVERLAY_LABEL": "X"})["label"] == "X"


# ---------------------------------------------------------------- registry
def test_registry_and_pid_cache(tmp_path):
    ctl = Controller(tmp_path, name="Claude", pid=os.getpid())
    try:
        names = [a["name"] for a in list_agents(tmp_path)]
        assert names == ["Claude"]
        ctl.set_name("claude-code")
        assert list_agents(tmp_path)[0]["name"] == "claude-code"
        assert os.getpid() in AgentPidCache(tmp_path, ttl=0)
    finally:
        ctl.close()
    assert list_agents(tmp_path) == []


def test_dead_agent_file_is_pruned(tmp_path):
    d = tmp_path / "agents"
    d.mkdir()
    (d / "999999.json").write_text(json.dumps({"pid": 999999, "name": "ghost"}))
    assert list_agents(tmp_path) == []
    assert not (d / "999999.json").exists()


def test_agent_whose_file_vanished_registers_again(tmp_path):
    ctl = Controller(tmp_path, name="Claude", pid=os.getpid())
    try:
        (tmp_path / "agents" / f"{os.getpid()}.json").unlink()
        assert list_agents(tmp_path) == []
        ctl.tool_scope()  # next tool call
        assert [a["name"] for a in list_agents(tmp_path)] == ["Claude"]
    finally:
        ctl.close()


def test_dead_pid_not_listed(tmp_path):
    d = tmp_path / "agents"
    d.mkdir()
    (d / "999999.json").write_text(json.dumps({"pid": 999999, "name": "ghost"}))
    assert list_agents(tmp_path) == []


# ------------------------------------------------------------ server tools
def test_server_tools_report_off_and_on(tmp_path, monkeypatch):
    server = pytest.importorskip("pinlet.server")
    from pinlet import control

    control.set_controller(None)
    monkeypatch.setenv("PINLET_CONTROL", "0")
    assert server.control_status()["shared_control"] is False
    c = Clock()
    ctl = make(tmp_path, c, "Claude", os.getpid())
    control.set_controller(ctl)
    try:
        assert server.request_control(max_wait_s=0)["you_hold_control"] is True
        assert server.hold_control(5)["held_for"] == 5
        assert server.control_status()["holder"]["name"] == "Claude"
        assert server.release_control()["released"] is True
    finally:
        control.set_controller(None)


# ------------------------------------------------------- two real processes
def _worker(root: str, name: str, log: str, n: int) -> None:
    settings = ControlSettings(idle_grace=0.02, slice=0.15, max_wait=20, poll=0.005)
    ctl = Controller(Path(root), name=name, settings=settings)
    done = 0
    while done < n:
        try:
            _one_tool(ctl, name, log)
        except ControlChanged:
            ctl.note_observation()  # "take a new screenshot", then retry
            continue
        done += 1
        time.sleep(0.03)  # think time > idle grace, so turns alternate
    ctl.close()


def _one_tool(ctl, name, log):
    with ctl.tool_scope():
        for _ in range(2):  # two-step tool, must stay together
            with ctl.action():
                t0 = time.monotonic()
                time.sleep(0.004)
                t1 = time.monotonic()
                with open(log, "a") as fh:
                    fh.write(f"{name} {t0} {t1}\n")


def test_two_processes_never_overlap(tmp_path):
    log = tmp_path / "log.txt"
    ctx = mp.get_context("spawn")
    procs = [ctx.Process(target=_worker, args=(str(tmp_path), n, str(log), 8)) for n in ("A", "B")]
    for p in procs:
        p.start()
    for p in procs:
        p.join(60)
        assert p.exitcode == 0
    rows = [ln.split() for ln in log.read_text().splitlines()]
    rows = sorted((float(t0), float(t1), who) for who, t0, t1 in rows)
    assert len(rows) == 32
    for (_s0, e0, w0), (s1, _e1, w1) in itertools.pairwise(rows):
        assert s1 >= e0, f"{w0} and {w1} acted at the same time"
    # each two-step tool stayed in one turn: steps come in same-agent pairs
    whos = [w for _, _, w in rows]
    assert all(whos[i] == whos[i + 1] for i in range(0, len(whos), 2))
    assert {"A", "B"} <= set(whos)


# --------------------------------------------------- Pinlet picker commands
def test_pause_from_picker_waits_then_resumes(tmp_path):
    from pinlet.control import commands

    c = Clock()
    a = make(tmp_path, c, "Claude", 1)
    seen = []
    a.on_paused = seen.append
    commands.set_paused(tmp_path, 1, True)
    real_sleep = c.sleep

    def sleep(s):
        real_sleep(s)
        if c.t > 1005:
            commands.set_paused(tmp_path, 1, False)

    a._sleep = sleep
    act(a)
    assert seen == [True, False]
    assert c.t > 1005


def test_stop_from_picker_raises_once(tmp_path):
    from pinlet.control import ControlStopped, commands

    c = Clock()
    a = make(tmp_path, c, "Claude", 1)
    commands.request_stop(tmp_path, 1)
    with pytest.raises(ControlStopped, match="Pinlet menu"):
        act(a)
    act(a)  # a new task after the stop runs


def test_paused_mid_tool_lets_other_pinlet_work(tmp_path):
    from pinlet.control import commands

    c = Clock()
    a, b = make(tmp_path, c, "Claude", 1), make(tmp_path, c, "Agy", 2)
    turns = []

    def sleep(s):
        c.t += s
        if not turns and c.t > 1011:
            act(b)  # Agy works while Claude is paused (Claude is idle, not busy)
            b.release()
            turns.append("agy")
            commands.set_paused(tmp_path, 1, False)

    a._sleep = sleep
    with a.tool_scope():
        act(a)
        commands.set_paused(tmp_path, 1, True)
        with pytest.raises(ControlChanged):
            act(a)  # back from pause: Agy acted meanwhile, so look again
    assert turns == ["agy"]


def test_overview_and_picker_groups(tmp_path):
    from pinlet.control import commands
    from pinlet.control.registry import register
    from pinlet.overlay.menu import picker_groups

    me, other = os.getpid(), os.getppid()
    register(tmp_path, "a", "Claude", me)
    assert picker_groups(commands.overview(tmp_path), me) == []  # one Pinlet: no picker
    register(tmp_path, "b", "Agy", other)
    commands.set_paused(tmp_path, other, True)
    c = Clock()
    ctl = make(tmp_path, c, "Claude", me)
    act(ctl)
    groups = picker_groups(commands.overview(tmp_path), me)
    titles = [g[0] for g in groups]
    assert titles == ["Agy  · paused", "Claude  · this bar, in control", "All Pinlets"]
    assert groups[0][1] == other and groups[0][2][0] == ("Resume", "menuAgentPauseToggle:")
    assert groups[1][2] == [("Pause", "menuAgentPauseToggle:"), ("Stop", "menuAgentStop:")]
    assert groups[2][1] == 0
    commands.request_stop(tmp_path, other)
    assert commands.read(tmp_path, other) == {"paused": False, "stop": 1}


# ------------------------------------------------- fairness and feedback
def test_busy_message_names_holder_place_and_eta(tmp_path):
    c = Clock()
    a, b = make(tmp_path, c, "Claude", 1), make(tmp_path, c, "Agy", 2)
    act(a)
    c.t += 3
    with pytest.raises(ControlBusy) as err:
        act(b)
    text = str(err.value)
    assert "Claude, is using the screen" in text
    assert "next in line" in text and "within about 7 s" in text  # idle_grace 10 - 3


def test_waiter_that_gave_up_keeps_its_place(tmp_path):
    c = Clock()
    a, b, d = (make(tmp_path, c, n, p) for n, p in (("Claude", 1), ("Agy", 2), ("Codex", 3)))
    act(a)
    c.t += 1
    with pytest.raises(ControlBusy, match="next in line"):
        act(b)  # Agy queues first, then gives up (max_wait 0) and goes off to think
    c.t += 6  # longer than waiter_ttl: Agy is not polling, but keeps its place
    act(a)  # Claude keeps working
    with pytest.raises(ControlBusy, match=r"1 agent\(s\) are ahead of you"):
        act(d)
    with pytest.raises(ControlBusy, match="next in line"):
        act(b)  # Agy is back and still first


def test_absent_reserved_waiter_never_blocks_a_present_one(tmp_path):
    c = Clock()
    a = make(tmp_path, c, "Claude", 1)
    b = make(tmp_path, c, "Agy", 2)
    d = make(tmp_path, c, "Codex", 3)
    act(a)
    with pytest.raises(ControlBusy):
        act(b)  # Agy queues, gives up, and goes away
    c.t += 25  # Claude idle past the grace; Agy is not polling
    act(d)  # Codex is here now: it must not wait behind an absent Agy
    assert d.status()["you_hold_control"]


def test_reserved_place_expires(tmp_path):
    c = Clock()
    a = make(tmp_path, c, "Claude", 1)
    b = make(tmp_path, c, "Agy", 2)
    act(a)
    with pytest.raises(ControlBusy):
        act(b)
    assert a.status()["waiting"]
    c.t += a.settings.reserve + 1
    assert a.status()["waiting"] == []


def test_holder_asking_the_person_lets_others_work(tmp_path):
    c = Clock()
    a, b = make(tmp_path, c, "Claude", 1), make(tmp_path, c, "Agy", 2)
    with pytest.raises(ControlChanged), a.action(), a.waiting_for_person():
        act(b)  # Claude's card is up: Agy gets the desktop at once, no idle wait
        assert b.status()["you_hold_control"]
        b.release()


def test_asking_holder_must_look_again_after_another_agent_acted(tmp_path):
    c = Clock()
    a, b = make(tmp_path, c, "Claude", 1), make(tmp_path, c, "Agy", 2)
    with pytest.raises(ControlChanged):
        with a.action():
            a.note_observation()
            with a.waiting_for_person():
                act(b)
                b.release()


def test_asking_holder_keeps_control_when_nobody_cut_in(tmp_path):
    c = Clock()
    a = make(tmp_path, c, "Claude", 1)
    with a.action():
        with a.waiting_for_person():
            c.t += 40  # a slow answer: nobody else wanted the desktop
    assert a.status()["you_hold_control"]


def test_denied_question_does_not_take_the_lease_back(tmp_path):
    c = Clock()
    a, b = make(tmp_path, c, "Claude", 1), make(tmp_path, c, "Agy", 2)
    with a.action(), a.waiting_for_person() as turn:
        act(b)  # Agy works while the card is up; then the person says Deny
        turn.reacquire = False  # no ControlChanged: the Deny is the answer
    assert not a.status()["you_hold_control"]
