"""The goal an agent stated outlives a restart of the shared Pinlet."""

from pinlet.hub import goals


def test_goal_is_kept_for_a_reconnecting_agent(tmp_path):
    goals.save(tmp_path, "abc123", "Reply  to Sam\nabout the invoice", now=1000.0)
    assert goals.load(tmp_path, "abc123", now=1030.0) == "Reply to Sam about the invoice"
    assert goals.load(tmp_path, "other", now=1030.0) == ""
    assert goals.load(tmp_path, "abc123", now=1000.0 + goals.MAX_AGE_S + 1) == ""


def test_an_empty_goal_forgets_it_and_bad_files_are_ignored(tmp_path):
    goals.save(tmp_path, "abc123", "Tidy the Desktop", now=1000.0)
    goals.save(tmp_path, "abc123", "", now=1001.0)
    assert goals.load(tmp_path, "abc123", now=1002.0) == ""
    goals.save(tmp_path, "", "no key, nothing saved")
    assert not list((tmp_path / "goals").glob("*.json"))
    (tmp_path / "goals" / "abc123.json").write_text("not json")
    assert goals.load(tmp_path, "abc123") == ""
    goals.save(tmp_path, "../../etc/x", "Stay inside", now=5.0)
    assert [p.parent.name for p in tmp_path.rglob("*.json") if p.name != "abc123.json"] == ["goals"]


def test_timer_and_step_count_carry_over_a_restart(tmp_path):
    goals.save(tmp_path, "abc123", "Reply to Sam", now=1000.0)
    goals.step(tmp_path, "abc123", now=1010.0)
    goals.step(tmp_path, "abc123", now=1500.0)  # a long task stays fresh while it moves
    task = goals.task(tmp_path, "abc123", now=1520.0)
    assert task == {"text": "Reply to Sam", "elapsed": 520.0, "steps": 2}
    goals.step(tmp_path, "nobody", now=1520.0)  # no goal: nothing is made up
    assert goals.task(tmp_path, "nobody", now=1520.0) == {}
    assert goals.task(tmp_path, "abc123", now=1500.0 + goals.MAX_AGE_S + 1) == {}
