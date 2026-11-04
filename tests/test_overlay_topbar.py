"""The top-bar item's model: per-Dobby status files, the dropdown view, icons."""

from __future__ import annotations

import json
import os
import time

from dobby.overlay import topbar


def _agents():
    return [
        {"pid": 11, "name": "Mail Dobby", "paused": False, "holding": True, "waiting": False, "color": "#14B8A6"},
        {"pid": 22, "name": "Xcode Dobby", "paused": True, "holding": False, "waiting": False},
        {"pid": 33, "name": "Notes Dobby", "paused": False, "holding": False, "waiting": True},
    ]


def test_palette_is_elf_violet_tuned():
    p = topbar.PALETTE
    assert (p["accent"], p["accent_dark"], p["fill"]) == ("#7C5CFF", "#9B82FF", "#5B3FE0")
    assert (p["caution"], p["danger"], p["ok"], p["idle"]) == ("#F5A524", "#F04438", "#22A365", "#9A98A6")


def test_publish_and_read_round_trip(tmp_path):
    d = str(tmp_path)
    topbar.publish(os.getpid(), mode="working", step="Click ‘Send’", steps=3, elapsed=42, app="Mail", directory=d)
    states = topbar.read_states(d)
    rec = states[os.getpid()]
    assert rec["mode"] == "working" and rec["steps"] == 3 and rec["app"] == "Mail"
    topbar.clear(os.getpid(), d)
    assert topbar.read_states(d) == {}


def test_read_drops_files_of_exited_helpers(tmp_path):
    path = tmp_path / "77.json"
    path.write_text(json.dumps({"pid": 77, "helper": 2 ** 22 + 12345, "mode": "working", "at": time.time()}))
    assert topbar.read_states(str(tmp_path)) == {}
    assert not path.exists()


def test_stale_working_reads_as_sleeping(tmp_path):
    (tmp_path / "77.json").write_text(json.dumps({"pid": 77, "helper": os.getpid(), "mode": "working",
                                                  "at": time.time() - 600}))
    assert topbar.read_states(str(tmp_path))[77]["mode"] == "ready"


def test_publish_without_server_pid_is_a_no_op(tmp_path):
    topbar.publish(0, mode="working", directory=str(tmp_path))
    assert os.listdir(tmp_path) == []


def test_view_counts_and_orders_rows():
    states = {11: {"mode": "working", "step": "Click ‘Send’", "steps": 7, "elapsed": 42, "app": "Mail"}}
    v = topbar.topbar_view(_agents(), states, own_pid=11)
    assert v.header == "Dobby"
    assert v.subtitle == "3 running · 1 working · 1 paused"
    assert [r.name for r in v.rows] == ["Xcode Dobby", "Mail Dobby", "Notes Dobby"]  # paused first
    xcode, mail, notes = v.rows
    assert mail.detail == "Working · Click ‘Send’ · in Mail · step 7 · 0:42"
    assert mail.shape == "spark" and mail.color == "#14B8A6" and mail.own
    assert xcode.detail == "Paused" and xcode.color == topbar.PALETTE["caution"]
    assert xcode.actions[0] == ("Resume", "pause_toggle")
    assert notes.detail == "Waiting for its turn" and notes.color == topbar.PALETTE["idle"]
    assert [a for _t, a in v.all_actions] == ["pause_all", "resume_all", "stop_all"]
    assert topbar.icon_state(v) == "attention" and topbar.icon_title(v) == "3"


def test_view_single_sleeping_dobby():
    v = topbar.topbar_view(_agents()[:1], {}, own_pid=11)
    assert v.subtitle == "1 running · sleeping"
    assert v.rows[0].detail == "Sleeping" and v.rows[0].shape == "moon"
    assert v.all_actions == [] and topbar.icon_title(v) == ""
    assert topbar.icon_state(v) == "idle"


def test_view_needs_you_and_none_running():
    v = topbar.topbar_view(_agents()[:1], {11: {"mode": "confirm"}})
    assert v.rows[0].state == "confirm" and "Allow or Deny" in v.rows[0].detail
    assert v.subtitle == "1 running · 1 needs you"
    assert topbar.topbar_view([], {}).subtitle == "No Dobby is running"


def test_turbo_and_long_timer():
    v = topbar.topbar_view(_agents()[:1], {11: {"mode": "working", "turbo": True, "elapsed": 3725}})
    assert v.rows[0].detail == "Working ⚡ · 1h 02m"
    assert topbar.icon_state(v) == "working"


def test_signature_changes_with_content():
    a = topbar.topbar_view(_agents()[:1], {})
    b = topbar.topbar_view(_agents()[:1], {11: {"mode": "working"}})
    assert a.signature() != b.signature()
    assert a.signature() == topbar.topbar_view(_agents()[:1], {}).signature()


def test_dobby_mode_priority():
    m = topbar.dobby_mode
    assert m(paused=True, takeover=False, confirming=True, stopped=False, working=True, awake=False) == "confirm"
    assert m(paused=True, takeover=False, confirming=False, stopped=False, working=True, awake=False) == "paused"
    assert m(paused=False, takeover=False, confirming=False, stopped=False, working=False, awake=True) == "awake"
    assert m(paused=False, takeover=False, confirming=False, stopped=False, working=False, awake=False) == "ready"


def test_icons_render_and_cache(tmp_path):
    img = topbar.render_icon("working", 36)
    assert img.size == (36, 36) and img.mode == "RGBA"
    assert img.getpixel((10, 14))[3] > 0  # the pointer is drawn
    assert img.getpixel((28, 8))[3] > 0  # and the spark
    row = topbar.render_row_icon("#7C5CFF", "spark", 52)
    assert row.size == (52, 52) and row.getpixel((1, 1))[3] == 0 and row.getpixel((26, 2))[3] > 200  # rounded square
    for shape in ("spark", "moon", "pause", "stop", "alert", "hand", "clock"):
        topbar.render_row_icon("#9A98A6", shape, 26)
    p1 = topbar.bar_icon_path("idle", 36, directory=str(tmp_path))
    p2 = topbar.bar_icon_path("idle", 36, directory=str(tmp_path))
    assert p1 == p2 and os.path.isfile(p1)
    assert topbar.bar_icon_path("idle", 36, count=3, directory=str(tmp_path)) != p1
    assert os.path.isfile(topbar.row_icon_path("#7C5CFF", "moon", directory=str(tmp_path)))


def test_view_exports_plain_json_for_a_native_shell():
    v = topbar.topbar_view(_agents(), {11: {"mode": "working"}}, own_pid=11)
    d = json.loads(json.dumps(v.to_dict()))
    assert d["subtitle"] == v.subtitle and d["icon_state"] == "attention" and d["icon_title"] == "3"
    assert d["rows"][1]["actions"] == [["Pause", "pause_toggle"], ["Stop", "stop"]]
    assert d["palette"]["accent"] == "#7C5CFF"


def test_tray_tile_icon(tmp_path):
    img = topbar.render_tile_icon("working", 44)
    assert img.size == (44, 44)
    assert img.getpixel((22, 3))[3] > 200  # the tile reaches the edge: as big as the icon allows
    assert img.getpixel((0, 0))[3] == 0  # rounded corners
    p = topbar.tray_icon_path("idle", 44, count=2, directory=str(tmp_path))
    assert os.path.isfile(p) and p != topbar.tray_icon_path("idle", 44, directory=str(tmp_path))
