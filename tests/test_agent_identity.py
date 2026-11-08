"""A client keeps its name and lease place when the shared Pinlet restarts."""

from __future__ import annotations

from pinlet.control import names, pick_first_name
from pinlet.control.controller import Controller
from pinlet.hub import sessions


def test_agent_keys_are_checked():
    assert sessions.clean_agent_key("9F2C" * 8) == "9f2c" * 8
    assert sessions.clean_agent_key("short") == ""
    assert sessions.clean_agent_key("not hex at all!") == ""
    assert sessions.clean_agent_key(None) == ""
    assert sessions.clean_agent_key("a" * 65) == ""


def test_pool_names_include_numbered_ones():
    pool = ["Tibble", "Pip"]
    assert names.is_pool_name("Tibble", pool)
    assert names.is_pool_name("Pip 3", pool)
    assert not names.is_pool_name("Pip 1", pool)
    assert not names.is_pool_name("Mallory", pool)


def test_a_returning_client_gets_its_old_name_back(monkeypatch, tmp_path):
    monkeypatch.setenv("PINLET_NAMES", "Tibble,Pip,Mopsy")
    monkeypatch.setattr("pinlet.control.control_root", lambda: tmp_path)
    assert pick_first_name(1, prefer="Pip") == "Pip"
    assert pick_first_name(1, prefer="Mallory") == "Tibble"  # not one of ours
    assert pick_first_name(1) == "Tibble"


def test_a_stable_agent_id_keeps_the_lease(tmp_path):
    def make(name, agent_id=None):
        return Controller(
            tmp_path, name=name, pid=4242, agent_id=agent_id, register=False, alive=lambda p: True
        )

    a = make("A", "4242-abc")
    a.acquire(max_wait=1)
    a._finish_without_world()
    assert a.status()["you_hold_control"]
    # the shared Pinlet restarted without releasing: the same client comes back
    again = make("A", "4242-abc")
    assert again.status()["you_hold_control"]
    assert not make("B").status()["you_hold_control"]
