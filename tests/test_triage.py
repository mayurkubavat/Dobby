"""pinlet triage writes a report with a playbook and starts the person's own AI tool."""

from __future__ import annotations

from pinlet import triage


def test_redact_hides_typed_text():
    line = 'type_text text=\'hunter2 secret\' x=1 "note": "call mom" password=abc'
    out = triage.redact(line)
    assert "hunter2" not in out and "call mom" not in out and "abc" not in out
    assert "x=1" in out and "type_text" in out


def test_report_has_every_section(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PINLET_MODES_FILE", str(tmp_path / "modes.json"))
    logs = tmp_path / ".pinlet" / "logs"
    logs.mkdir(parents=True)
    (logs / "daemon.log").write_text("boot\nerror: text='do not show'\n")
    text = triage.report("clicks land in the wrong place", log_lines=5, home=tmp_path)
    for heading in (
        "What the person saw",
        "pinlet doctor",
        "Permission levels",
        "Admin policy",
        "What the agents did lately",
        "Shared Pinlet log",
        "Playbook",
    ):
        assert f"## {heading}" in text
    assert "clicks land in the wrong place" in text
    assert "boot" in text and "do not show" not in text


def test_pick_agent_prefers_the_asked_one():
    found = {"codex": "/bin/codex", "agent": "/bin/agent"}
    assert triage.pick_agent("", found.get) == "codex"
    assert triage.pick_agent("cursor", found.get) == "cursor"
    assert triage.pick_agent("claude", found.get) is None
    assert triage.pick_agent("", {}.get) is None


def test_main_writes_the_report_and_starts_the_tool(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PINLET_MODES_FILE", str(tmp_path / "modes.json"))
    monkeypatch.setattr(
        triage.shutil, "which", lambda name: "/bin/" + name if name == "codex" else None
    )
    ran = []
    assert triage.main(["menu", "is", "empty"], run=lambda cmd: ran.append(cmd) or 0) == 0
    (report,) = (tmp_path / ".pinlet" / "logs").glob("triage-*.md")
    assert oct(report.stat().st_mode & 0o777) == "0o600"
    assert "menu is empty" in report.read_text()
    assert ran[0][0] == "codex" and str(report) in ran[0][1]
    monkeypatch.setattr(triage.shutil, "which", lambda name: None)
    assert triage.main([], run=lambda cmd: 0) == 1
    assert "No Claude Code" in capsys.readouterr().out
    assert triage.main(["--print"]) == 0
    assert "## Playbook" in capsys.readouterr().out


def test_policy_section_is_filled(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    text = triage.report(home=tmp_path)
    assert "could not read" not in text and "doctor failed" not in text
