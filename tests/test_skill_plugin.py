"""The skills plugin stays loadable and in sync with the code it documents."""

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "pinlet"
SKILLS = PLUGIN / "skills"


def _frontmatter(text: str) -> dict:
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    assert m, "SKILL.md must start with YAML frontmatter"
    out = {}
    for line in m.group(1).splitlines():
        key, _, value = line.partition(":")
        out[key.strip()] = value.strip()
    return out


def test_manifests_are_valid():
    plugin = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text())
    assert plugin["name"] == "pinlet"
    market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
    entry = next(p for p in market["plugins"] if p["name"] == "pinlet")
    assert (ROOT / entry["source"]).resolve() == PLUGIN.resolve()


def test_every_skill_has_frontmatter_and_existing_references():
    skills = sorted(p for p in SKILLS.iterdir() if p.is_dir())
    assert {p.name for p in skills} >= {
        "pinlet",
        "pinlet-perception",
        "pinlet-skill-crafting",
        "pinlet-eval",
        "pinlet-troubleshoot",
        "pinlet-app-claude",
        "pinlet-app-chrome",
        "pinlet-app-gemini",
        "pinlet-app-vivado",
        "pinlet-app-verdi",
    }
    for skill in skills:
        text = (skill / "SKILL.md").read_text(encoding="utf-8")
        meta = _frontmatter(text)
        assert meta.get("name") == skill.name
        assert 20 < len(meta.get("description", "")) <= 1024
        for ref in re.findall(r"`references/([\w.-]+\.md)`", text):
            assert (skill / "references" / ref).is_file(), (
                f"{skill.name} cites missing references/{ref}"
            )


def test_skills_only_name_tools_the_server_has():
    tools_md = (SKILLS / "pinlet-perception" / "references" / "tools.md").read_text(
        encoding="utf-8"
    )
    known = set(re.findall(r"^\| `(\w+)` \|", tools_md, re.M))
    assert len(known) > 30
    tool_like = re.compile(r"`([a-z]+(?:_[a-z]+)+)\(")
    for skill_md in SKILLS.glob("*/SKILL.md"):
        for name in tool_like.findall(skill_md.read_text(encoding="utf-8")):
            assert name in known, f"{skill_md.parent.name} calls unknown tool {name}"


def test_generated_references_are_current():
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "build_skill_refs.py"), "--check"],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_skill_names_are_unique_outside_the_plugin():
    """Outside Claude Code skills share one folder, so every name carries the pinlet prefix."""
    for skill in SKILLS.iterdir():
        if skill.is_dir():
            assert skill.name == "pinlet" or skill.name.startswith("pinlet-"), skill.name


def test_entry_skill_says_when_to_use_pinlet():
    meta = _frontmatter((SKILLS / "pinlet" / "SKILL.md").read_text(encoding="utf-8"))
    desc = meta["description"].lower()
    for word in ("desktop", "click", "screen", "use whenever"):
        assert word in desc


def test_repo_agents_skills_link_points_at_the_plugin():
    link = ROOT / ".agents" / "skills"
    assert link.resolve() == SKILLS.resolve()
