"""The Agent Skills shipped with Pinlet: where they are and how to read them.

Used by ``pinlet setup`` (which links them into each AI tool), by the MCP
server (which serves them as resources) and by ``pinlet doctor``.

Stdlib only, so it runs before any desktop backend is importable.
"""

from __future__ import annotations

import re
from pathlib import Path

_SKILL_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def skills_dir() -> Path | None:
    """Returns the folder holding the skills (``plugins/pinlet/skills`` in a clone), if present."""
    here = Path(__file__).resolve().parent.parent / "plugins" / "pinlet" / "skills"
    return here if here.is_dir() else None


def skill_names(root: Path | None = None) -> list[str]:
    """Names of the skills shipped with Pinlet, entry skill first."""
    root = root or skills_dir()
    if root is None:
        return []
    names = sorted(p.name for p in root.iterdir() if (p / "SKILL.md").is_file())
    return sorted(names, key=lambda n: n != "pinlet")


def _frontmatter(text: str) -> dict[str, str]:
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    out: dict[str, str] = {}
    for line in (m.group(1) if m else "").splitlines():
        key, _, value = line.partition(":")
        out[key.strip()] = value.strip()
    return out


def read_skill(name: str, root: Path | None = None) -> str:
    """Returns one skill's SKILL.md text. Raises ValueError for an unknown or unsafe name."""
    root = root or skills_dir()
    if root is None or not _SKILL_NAME.match(name or ""):
        raise ValueError(f"Unknown Pinlet skill {name!r}")
    path = root / name / "SKILL.md"
    if not path.is_file():
        raise ValueError(f"Unknown Pinlet skill {name!r}; known: {', '.join(skill_names(root))}")
    return path.read_text(encoding="utf-8")


def skills_index_markdown(root: Path | None = None) -> str:
    """A Markdown list of the skills with their descriptions and resource URIs."""
    root = root or skills_dir()
    names = skill_names(root)
    if not names:
        return (
            "# Pinlet skills\n\nThe skills folder is not installed with this copy of Pinlet. "
            "Install Pinlet from a clone (`pip install -e .`) to get them.\n"
        )
    lines = [
        "# Pinlet skills",
        "",
        "Read `mcp://pinlet/skills/<name>` for one. Start with `pinlet`.",
        "",
    ]
    for name in names:
        desc = _frontmatter(read_skill(name, root)).get("description", "")
        lines.append(f"- `{name}` (mcp://pinlet/skills/{name}): {desc}")
    return "\n".join(lines) + "\n"
