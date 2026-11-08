"""``dobby setup`` registers Dobby with each AI tool and links the skills, idempotently."""

import json
from pathlib import Path

from dobby import agent_setup


def _home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    for d in (".cursor", ".codex", ".gemini/config", ".copilot", ".config/opencode"):
        (home / d).mkdir(parents=True)
    (home / ".cursor" / "mcp.json").write_text('{"mcpServers": {"other": {"command": "x"}}}')
    (home / ".codex" / "config.toml").write_text('model = "gpt-5"\n')
    return home


def _run(home: Path, *extra: str) -> int:
    return agent_setup.main(["--home", str(home), "--command", "/opt/dobby", *extra])


def test_setup_registers_every_found_tool_and_keeps_other_servers(tmp_path, capsys):
    home = _home(tmp_path)
    assert _run(home) == 0
    cursor = json.loads((home / ".cursor" / "mcp.json").read_text())
    assert cursor["mcpServers"]["other"] == {"command": "x"}
    assert cursor["mcpServers"]["Dobby"]["command"] == "/opt/dobby"
    assert (home / ".cursor" / "mcp.json.dobby-backup").exists()
    codex = (home / ".codex" / "config.toml").read_text()
    assert codex.startswith('model = "gpt-5"\n')
    assert '[mcp_servers.Dobby]\ncommand = "/opt/dobby"' in codex
    anti = json.loads((home / ".gemini" / "config" / "mcp_config.json").read_text())
    assert anti["mcpServers"]["Dobby"]["command"] == "/opt/dobby"
    copilot = json.loads((home / ".copilot" / "mcp-config.json").read_text())
    assert copilot["mcpServers"]["Dobby"]["tools"] == ["*"]
    opencode = json.loads((home / ".config" / "opencode" / "opencode.json").read_text())
    assert opencode["mcp"]["Dobby"]["command"] == ["/opt/dobby"]
    # Tools that are not installed are left alone.
    assert not (home / ".gemini" / "settings.json").exists()
    assert not (home / ".claude").exists()


def test_skills_are_linked_into_the_shared_folder(tmp_path):
    home = _home(tmp_path)
    _run(home, "--no-mcp")
    linked = home / ".agents" / "skills"
    names = agent_setup.skill_names()
    assert names[0] == "dobby" and len(names) >= 9
    for name in names:
        assert (linked / name / "SKILL.md").is_file()
        assert (linked / name).resolve() == (agent_setup.skills_dir() / name).resolve()


def test_second_run_changes_nothing(tmp_path):
    home = _home(tmp_path)
    _run(home)
    before = (home / ".codex" / "config.toml").read_text()
    changes = agent_setup.plan(home, command=["/opt/dobby"], env={})
    assert changes and all(c.apply is None for c in changes), [
        (c.tool, c.status) for c in changes if c.apply
    ]
    _run(home)
    assert (home / ".codex" / "config.toml").read_text() == before


def test_an_existing_dobby_entry_is_kept_unless_forced(tmp_path):
    home = _home(tmp_path)
    (home / ".cursor" / "mcp.json").write_text('{"mcpServers": {"dobby": {"command": "/old"}}}')
    _run(home, "--only", "cursor")
    assert json.loads((home / ".cursor" / "mcp.json").read_text())["mcpServers"] == {
        "dobby": {"command": "/old"}
    }
    _run(home, "--only", "cursor", "--force")
    assert json.loads((home / ".cursor" / "mcp.json").read_text())["mcpServers"] == {
        "Dobby": {"command": "/opt/dobby"}
    }


def test_dry_run_and_list_write_nothing(tmp_path, capsys):
    home = _home(tmp_path)
    before = (home / ".cursor" / "mcp.json").read_text()
    assert _run(home, "--dry-run") == 0
    assert _run(home, "--list") == 0
    out = capsys.readouterr().out
    assert "would add" in out and "not found" in out
    assert (home / ".cursor" / "mcp.json").read_text() == before
    assert not (home / ".agents").exists()


def test_a_foreign_skill_folder_is_never_replaced(tmp_path):
    home = _home(tmp_path)
    mine = home / ".agents" / "skills" / "dobby"
    mine.mkdir(parents=True)
    (mine / "SKILL.md").write_text("mine")
    _run(home, "--no-mcp", "--force")
    assert (mine / "SKILL.md").read_text() == "mine"


def test_unknown_tool_is_an_error(tmp_path):
    assert _run(_home(tmp_path), "--only", "nope") == 2


def test_skills_are_readable_without_a_skills_client():
    index = agent_setup.skills_index_markdown()
    assert "mcp://dobby/skills/dobby" in index
    assert agent_setup.read_skill("dobby").startswith("---\nname: dobby\n")
    for bad in ("../dobby", "dobby/SKILL.md", "", "nope"):
        try:
            agent_setup.read_skill(bad)
        except ValueError:
            continue
        raise AssertionError(f"read_skill accepted {bad!r}")


def test_server_serves_the_skills_as_resources_and_a_prompt():
    import asyncio

    from dobby.server import mcp_server

    async def go():
        templates = await mcp_server.list_resource_templates()
        assert any("mcp://dobby/skills/{name}" in str(t.uri_template) for t in templates)
        body = await mcp_server.read_resource("mcp://dobby/skills/dobby")
        prompts = await mcp_server.list_prompts()
        return body, prompts

    body, prompts = asyncio.run(go())
    text = "".join(getattr(part, "content", "") or "" for part in body)
    assert "name: dobby" in text
    assert "dobby" in [p.name for p in prompts]
    assert "mcp://dobby/skills/dobby" in (mcp_server.instructions or "")
