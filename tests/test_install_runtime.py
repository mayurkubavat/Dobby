"""Pinlet's own runtime: the stable launcher, setup's repair/remove/JSON, and ``pinlet doctor``."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from pinlet import agent_setup, doctor, runtime

pytestmark = pytest.mark.skipif(sys.platform.startswith("win"), reason="sh launcher")


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    for d in (".cursor", ".codex", ".gemini/config", ".copilot", ".config/opencode"):
        (home / d).mkdir(parents=True)
    monkeypatch.setenv("PINLET_LAUNCHER", str(home / ".pinlet" / "bin" / "pinlet"))
    monkeypatch.setenv("PINLET_DATA_DIR", str(tmp_path / "data"))
    return home


def _setup(home: Path, *extra: str) -> int:
    return agent_setup.main(["--home", str(home), *extra])


def test_data_dir_per_platform():
    env = {"XDG_DATA_HOME": "/x"}
    assert runtime.data_dir(env, "linux") == Path("/x/pinlet")
    assert runtime.data_dir({}, "darwin").parts[-3:] == ("Library", "Application Support", "Pinlet")
    assert runtime.data_dir({"PINLET_DATA_DIR": "/d"}, "darwin") == Path("/d")


def test_launcher_runs_the_first_python_that_exists(tmp_path):
    path = tmp_path / "pinlet"
    runtime.write_launcher("/gone/python", path)
    assert runtime.launcher_targets(path)[0] == "/gone/python"
    text = runtime.launcher_text("/gone/python", sys.executable)
    path.write_text(text)
    path.chmod(0o755)
    out = subprocess.run([str(path), "help"], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0 and "pinlet setup" in out.stdout
    assert runtime.launcher_works(path)


def test_launcher_explains_when_every_python_is_gone(tmp_path):
    path = tmp_path / "pinlet"
    path.write_text(runtime.launcher_text("/gone/a", "/gone/b"))
    path.chmod(0o755)
    out = subprocess.run([str(path)], capture_output=True, text=True, timeout=10)
    assert out.returncode == 127
    assert "Open Pinlet.app" in out.stderr
    assert not runtime.launcher_works(path)


def test_a_launcher_someone_else_wrote_is_left_alone(tmp_path):
    path = tmp_path / "pinlet"
    path.write_text("#!/bin/sh\necho mine\n")
    runtime.write_launcher(sys.executable, path)
    assert path.read_text() == "#!/bin/sh\necho mine\n"


def test_setup_points_tools_at_the_launcher_and_writes_it(home):
    assert _setup(home) == 0
    launcher = runtime.launcher_path()
    assert runtime.launcher_works(launcher)
    cursor = json.loads((home / ".cursor" / "mcp.json").read_text())
    assert cursor["mcpServers"]["Pinlet"]["command"] == str(launcher)
    assert f'command = "{launcher}"' in (home / ".codex" / "config.toml").read_text()


def test_repair_replaces_entries_whose_command_is_gone(home):
    (home / ".cursor" / "mcp.json").write_text(
        '{"mcpServers": {"Pinlet": {"command": "/old/clone/.venv/bin/pinlet"}, "x": {"command": "y"}}}'
    )
    (home / ".codex" / "config.toml").write_text(
        'model = "m"\n\n[mcp_servers.Pinlet]\ncommand = "/old/pinlet"\n\n[other]\na = 1\n'
    )
    states = {e["key"]: e["state"] for e in agent_setup.tool_status(home)}
    assert states["cursor"] == "broken" and states["codex"] == "broken"
    _setup(home)  # a plain run keeps them
    assert "/old/clone" in (home / ".cursor" / "mcp.json").read_text()
    assert _setup(home, "--repair") == 0
    launcher = str(runtime.launcher_path())
    servers = json.loads((home / ".cursor" / "mcp.json").read_text())["mcpServers"]
    assert servers["Pinlet"]["command"] == launcher and servers["x"] == {"command": "y"}
    codex = (home / ".codex" / "config.toml").read_text()
    assert "/old/pinlet" not in codex and f'command = "{launcher}"' in codex
    assert 'model = "m"' in codex and "[other]\na = 1" in codex
    assert {e["key"]: e["state"] for e in agent_setup.tool_status(home)}["cursor"] == "connected"


def test_remove_takes_entries_and_skill_links_out(home):
    _setup(home)
    assert (home / ".agents" / "skills" / "pinlet").is_symlink()
    assert _setup(home, "--remove") == 0
    assert "Pinlet" not in json.loads((home / ".cursor" / "mcp.json").read_text())["mcpServers"]
    assert "mcp_servers" not in (home / ".codex" / "config.toml").read_text()
    assert not (home / ".agents" / "skills" / "pinlet").exists()
    states = {e["key"]: e["state"] for e in agent_setup.tool_status(home)}
    assert states["cursor"] == "not set up"


def test_list_json_reports_each_tool(home, capsys):
    assert _setup(home, "--list", "--json") == 0
    tools = {t["key"]: t for t in json.loads(capsys.readouterr().out)["tools"]}
    assert tools["cursor"]["installed"] and tools["cursor"]["state"] == "not set up"
    assert tools["vscode"]["state"] == "not installed"


def test_setup_json_lists_changes_and_restarts(home, capsys):
    assert _setup(home, "--only", "cursor", "--json") == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] and "cursor" in out["restart"]
    assert any(c["tool"] == "cursor" and c["status"] == "added" for c in out["changes"])


def test_doctor_flags_a_missing_launcher_and_broken_tools(home, capsys):
    (home / ".cursor" / "mcp.json").write_text('{"mcpServers": {"Pinlet": {"command": "/gone"}}}')
    assert doctor.main(["--home", str(home), "--json"]) == 1
    checks = {c["name"]: c for c in json.loads(capsys.readouterr().out)["checks"]}
    assert checks["Launcher"]["status"] == "fail"
    assert checks["Cursor"]["status"] == "fail" and "--repair" in checks["Cursor"]["fix"]
    assert checks["Python"]["status"] == "ok"
    _setup(home, "--repair")
    capsys.readouterr()
    doctor.main(["--home", str(home), "--json"])
    checks = {c["name"]: c for c in json.loads(capsys.readouterr().out)["checks"]}
    assert checks["Launcher"]["status"] == "ok" and checks["Cursor"]["status"] == "ok"


def test_install_script_is_valid_sh():
    script = Path(__file__).resolve().parents[1] / "scripts" / "install.sh"
    assert os.access(script, os.X_OK)
    subprocess.run(["sh", "-n", str(script)], check=True)
