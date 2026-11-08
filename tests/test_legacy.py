"""Settings carried over from Pinlet's old name, Dobby (pinlet._legacy, policy paths, setup)."""

from pathlib import Path

from pinlet import _legacy, agent_setup, policy


def test_old_env_vars_fill_unset_new_ones():
    env = {"DOBBY_SECURE": "1", "DOBBY_MODE": "x", "PINLET_MODE": "y"}
    assert _legacy.copy_env(env) == ["PINLET_SECURE"]
    assert env["PINLET_SECURE"] == "1" and env["PINLET_MODE"] == "y"


def test_home_folder_moves_once_and_leaves_a_link(tmp_path):
    (tmp_path / ".dobby").mkdir()
    (tmp_path / ".dobby" / "apps.json").write_text("{}")
    assert _legacy.move_home(tmp_path)
    assert (tmp_path / ".pinlet" / "apps.json").read_text() == "{}"
    assert (tmp_path / ".dobby").is_symlink()
    assert (tmp_path / ".dobby" / "apps.json").exists()
    assert not _legacy.move_home(tmp_path)


def test_home_folder_left_alone_when_new_one_exists(tmp_path):
    (tmp_path / ".dobby").mkdir()
    (tmp_path / ".pinlet").mkdir()
    assert not _legacy.move_home(tmp_path)
    assert not (tmp_path / ".dobby").is_symlink()


def test_policy_reads_old_location_only_while_new_is_missing(tmp_path):
    new = tmp_path / "pinlet" / "policy.json"
    old = tmp_path / "dobby" / "policy.json"
    assert policy._legacy_policy(new) == new
    old.parent.mkdir()
    old.write_text("{}")
    assert policy._legacy_policy(new) == old
    new.parent.mkdir()
    new.write_text("{}")
    assert policy._legacy_policy(new) == new


def test_setup_replaces_an_old_dobby_entry(tmp_path):
    path = tmp_path / "mcp.json"
    path.write_text('{"mcpServers": {"Dobby": {"command": "dobby"}}}')
    change = agent_setup._json_server(
        "cursor", path, "mcpServers", {"command": "pinlet"}, force=False
    )
    assert change.status == "updated" and "Dobby" in change.detail
    change.apply()
    assert '"Pinlet"' in path.read_text() and '"Dobby"' not in path.read_text()


def test_setup_removes_old_dobby_skill_links(tmp_path):
    repo = tmp_path / "Dobby" / "plugins" / "dobby" / "skills" / "dobby-eval"
    repo.mkdir(parents=True)
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "dobby-eval").symlink_to(repo)
    (skills / "dobby-mine").mkdir()  # a real folder: not ours to remove
    changes = agent_setup._unlink_legacy_skills("x", skills)
    assert [Path(c.target).name for c in changes] == ["dobby-eval"]
    changes[0].apply()
    assert not (skills / "dobby-eval").exists() and (skills / "dobby-mine").is_dir()
