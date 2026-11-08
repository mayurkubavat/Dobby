"""``dobby setup``: make Dobby available to every AI tool on this computer.

Two things per tool:

* **MCP**: register the ``dobby`` command as an MCP server in the tool's own config
  (Claude Code, Claude Desktop, Codex, Cursor, Gemini CLI, Antigravity, GitHub Copilot CLI,
  OpenCode, VS Code). Existing Dobby entries are left alone unless ``--force``.
* **Skills**: link the Agent Skills in ``plugins/dobby/skills`` (``dobby``, ``dobby-*``) into the
  folders those tools read skills from. ``~/.agents/skills`` is the shared folder read by Codex,
  Cursor, Gemini CLI, Copilot, OpenCode and Antigravity; Claude Code reads ``~/.claude/skills``
  (skipped when the Dobby plugin is installed there already).

Usage::

    dobby setup                 set up every tool found on this computer
    dobby setup --dry-run       show what would change, change nothing
    dobby setup --list          show each known tool and whether it is set up
    dobby setup --only cursor,codex
    dobby setup --no-mcp | --no-skills | --force | --command /path/to/dobby

Every config file is backed up once (``<file>.dobby-backup``) before Dobby first edits it.
Stdlib only, so it runs before any desktop backend is importable.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

SERVER_NAME = "Dobby"
_SKILL_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


# ------------------------------------------------------------------ skills
def skills_dir() -> Path | None:
    """Returns the folder holding the skills (``plugins/dobby/skills`` in a clone), if present."""
    here = Path(__file__).resolve().parent.parent / "plugins" / "dobby" / "skills"
    return here if here.is_dir() else None


def skill_names(root: Path | None = None) -> list[str]:
    """Names of the skills shipped with Dobby, entry skill first."""
    root = root or skills_dir()
    if root is None:
        return []
    names = sorted(p.name for p in root.iterdir() if (p / "SKILL.md").is_file())
    return sorted(names, key=lambda n: n != "dobby")


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
        raise ValueError(f"Unknown Dobby skill {name!r}")
    path = root / name / "SKILL.md"
    if not path.is_file():
        raise ValueError(f"Unknown Dobby skill {name!r}; known: {', '.join(skill_names(root))}")
    return path.read_text(encoding="utf-8")


def skills_index_markdown(root: Path | None = None) -> str:
    """A Markdown list of the skills with their descriptions and resource URIs."""
    root = root or skills_dir()
    names = skill_names(root)
    if not names:
        return (
            "# Dobby skills\n\nThe skills folder is not installed with this copy of Dobby. "
            "Install Dobby from a clone (`pip install -e .`) to get them.\n"
        )
    lines = [
        "# Dobby skills",
        "",
        "Read `mcp://dobby/skills/<name>` for one. Start with `dobby`.",
        "",
    ]
    for name in names:
        desc = _frontmatter(read_skill(name, root)).get("description", "")
        lines.append(f"- `{name}` (mcp://dobby/skills/{name}): {desc}")
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ tools
@dataclass
class Tool:
    """One AI tool Dobby knows how to set up."""

    key: str
    label: str
    marker: Callable[[Path], bool]  # is the tool on this computer?
    mcp: Callable[..., Change] | None = None
    skills: Callable[[Path], Path] | None = None
    note: str = ""


@dataclass
class Change:
    """What setting up one part of one tool would do, or did."""

    tool: str
    part: str  # "mcp" or "skills"
    target: str
    status: str  # "added", "updated", "ok", "skipped", "would add", "error"...
    detail: str = ""
    apply: Callable[[], None] | None = field(default=None, repr=False)


def _app_support(home: Path, *parts: str) -> Path:
    if sys.platform == "darwin":
        return home.joinpath("Library", "Application Support", *parts)
    if sys.platform.startswith("win"):
        return Path(os.environ.get("APPDATA", home / "AppData" / "Roaming")).joinpath(*parts)
    return home.joinpath(".config", *parts)


def _read_json(path: Path) -> dict:
    if not path.exists() or not path.read_text(encoding="utf-8").strip():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a JSON object")
    return data


def _backup(path: Path) -> None:
    backup = path.with_name(path.name + ".dobby-backup")
    if path.exists() and not backup.exists():
        shutil.copy2(path, backup)


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _backup(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    if path.exists():
        shutil.copymode(path, tmp)
    os.replace(tmp, path)


def _existing_key(table: dict) -> str | None:
    return next((k for k in table if k.lower() == SERVER_NAME.lower()), None)


def _json_server(
    tool: str,
    path: Path,
    section: str,
    entry: dict,
    force: bool,
) -> Change:
    """Adds ``entry`` under ``section`` (``mcpServers``, ``servers``, ``mcp``) of a JSON config."""
    try:
        data = _read_json(path)
    except (OSError, ValueError) as exc:
        return Change(tool, "mcp", str(path), "error", f"cannot read: {exc}")
    table = data.get(section) or {}
    if not isinstance(table, dict):
        return Change(tool, "mcp", str(path), "error", f"'{section}' is not an object")
    key = _existing_key(table)
    if key is not None and not force:
        same = table[key] == entry
        return Change(
            tool,
            "mcp",
            str(path),
            "ok",
            "" if same else "has its own Dobby entry (--force replaces)",
        )

    def apply() -> None:
        fresh = _read_json(path)
        servers = fresh.setdefault(section, {})
        old = _existing_key(servers)
        if old is not None:
            del servers[old]
        servers[SERVER_NAME] = entry
        _write_text(path, json.dumps(fresh, indent=2) + "\n")

    return Change(tool, "mcp", str(path), "updated" if key else "added", "", apply)


def _std_entry(cmd: list[str], env: dict[str, str]) -> dict:
    entry: dict = {"command": cmd[0]}
    if cmd[1:]:
        entry["args"] = cmd[1:]
    if env:
        entry["env"] = env
    return entry


def _mcp_servers_json(path_of: Callable[[Path], Path], key: str):
    def setup(home: Path, cmd: list[str], env: dict[str, str], force: bool = False) -> Change:
        return _json_server(key, path_of(home), "mcpServers", _std_entry(cmd, env), force)

    return setup


def _copilot(home: Path, cmd: list[str], env: dict[str, str], force: bool = False) -> Change:
    entry = {"type": "local", "command": cmd[0], "args": cmd[1:], "tools": ["*"]}
    if env:
        entry["env"] = env
    return _json_server(
        "copilot", home / ".copilot" / "mcp-config.json", "mcpServers", entry, force
    )


def _opencode(home: Path, cmd: list[str], env: dict[str, str], force: bool = False) -> Change:
    entry: dict = {"type": "local", "command": cmd, "enabled": True}
    if env:
        entry["environment"] = env
    return _json_server("opencode", _opencode_path(home), "mcp", entry, force)


def _opencode_path(home: Path) -> Path:
    return home / ".config" / "opencode" / "opencode.json"


def _vscode_path(home: Path) -> Path:
    return _app_support(home, "Code", "User", "mcp.json")


def _vscode(home: Path, cmd: list[str], env: dict[str, str], force: bool = False) -> Change:
    entry = {"type": "stdio", **_std_entry(cmd, env)}
    return _json_server("vscode", _vscode_path(home), "servers", entry, force)


def _toml_str(value: str) -> str:
    return json.dumps(value)  # a TOML basic string for ordinary paths


def _codex_path(home: Path) -> Path:
    return Path(os.environ.get("CODEX_HOME", home / ".codex")) / "config.toml"


def _codex(home: Path, cmd: list[str], env: dict[str, str], force: bool = False) -> Change:
    path = _codex_path(home)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if re.search(r"^\s*\[mcp_servers\.\"?dobby\"?\]", text, re.M | re.I):
        return Change("codex", "mcp", str(path), "ok", "" if not force else "edit it by hand")
    block = [f"\n[mcp_servers.{SERVER_NAME}]", f"command = {_toml_str(cmd[0])}"]
    if cmd[1:]:
        block.append("args = [" + ", ".join(_toml_str(a) for a in cmd[1:]) + "]")
    if env:
        block.append("env = { " + ", ".join(f"{k} = {_toml_str(v)}" for k, v in env.items()) + " }")

    def apply() -> None:
        current = path.read_text(encoding="utf-8") if path.exists() else ""
        sep = "" if not current or current.endswith("\n") else "\n"
        body = "\n".join(block)
        _write_text(path, current + sep + (body if current else body.lstrip("\n")) + "\n")

    return Change("codex", "mcp", str(path), "added", "", apply)


def _claude_code(home: Path, cmd: list[str], env: dict[str, str], force: bool = False) -> Change:
    claude = shutil.which("claude") if home == Path.home() else None
    if not claude:
        return Change(
            "claude-code",
            "mcp",
            "claude mcp add",
            "skipped",
            "the claude command is not on PATH; run: claude mcp add --scope user Dobby -- "
            + " ".join(cmd),
        )
    try:
        has = subprocess.run(
            [claude, "mcp", "get", SERVER_NAME], capture_output=True, text=True, timeout=20
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return Change("claude-code", "mcp", "claude mcp", "error", str(exc))
    if has.returncode == 0 and not force:
        return Change("claude-code", "mcp", "claude mcp (user scope)", "ok")
    add = [claude, "mcp", "add", "--scope", "user"]
    for k, v in env.items():
        add += ["-e", f"{k}={v}"]
    add += [SERVER_NAME, "--", *cmd]

    def apply() -> None:
        if has.returncode == 0:
            subprocess.run([claude, "mcp", "remove", "--scope", "user", SERVER_NAME], timeout=20)
        subprocess.run(add, check=True, capture_output=True, timeout=30)

    return Change("claude-code", "mcp", "claude mcp (user scope)", "added", "", apply)


def _claude_plugin_installed(home: Path) -> bool:
    try:
        text = (home / ".claude" / "plugins" / "installed_plugins.json").read_text("utf-8")
    except OSError:
        return False
    return '"dobby@' in text


def tools() -> list[Tool]:
    """Every AI tool Dobby can set up, in the order they are reported."""
    return [
        Tool(
            "claude-code",
            "Claude Code",
            lambda h: (
                (h / ".claude").is_dir() or (h == Path.home() and bool(shutil.which("claude")))
            ),
            mcp=_claude_code,
            skills=lambda h: h / ".claude" / "skills",
        ),
        Tool(
            "claude-desktop",
            "Claude Desktop",
            lambda h: _app_support(h, "Claude").is_dir(),
            mcp=_mcp_servers_json(
                lambda h: _app_support(h, "Claude", "claude_desktop_config.json"),
                "claude-desktop",
            ),
            note="quit Claude Desktop before running setup; it rewrites its config on quit",
        ),
        Tool(
            "codex",
            "OpenAI Codex",
            lambda h: _codex_path(h).parent.is_dir() or shutil.which("codex") is not None,
            mcp=_codex,
            skills=lambda h: h / ".agents" / "skills",
        ),
        Tool(
            "cursor",
            "Cursor",
            lambda h: (h / ".cursor").is_dir(),
            mcp=_mcp_servers_json(lambda h: h / ".cursor" / "mcp.json", "cursor"),
            skills=lambda h: h / ".agents" / "skills",
        ),
        Tool(
            "gemini-cli",
            "Gemini CLI",
            lambda h: (
                (h / ".gemini" / "settings.json").exists() or shutil.which("gemini") is not None
            ),
            mcp=_mcp_servers_json(lambda h: h / ".gemini" / "settings.json", "gemini-cli"),
            skills=lambda h: h / ".agents" / "skills",
        ),
        Tool(
            "antigravity",
            "Antigravity",
            lambda h: (
                (h / ".gemini" / "antigravity").is_dir() or (h / ".gemini" / "config").is_dir()
            ),
            mcp=_mcp_servers_json(
                lambda h: h / ".gemini" / "config" / "mcp_config.json", "antigravity"
            ),
            skills=lambda h: h / ".agents" / "skills",
            note="Antigravity only reads tool schemas at start: restart it after setup",
        ),
        Tool(
            "antigravity-cli",
            "Antigravity CLI",
            lambda h: (h / ".gemini" / "antigravity-cli").is_dir(),
            mcp=_mcp_servers_json(
                lambda h: h / ".gemini" / "antigravity-cli" / "mcp_config.json", "antigravity-cli"
            ),
            skills=lambda h: h / ".gemini" / "antigravity-cli" / "skills",
        ),
        Tool(
            "copilot",
            "GitHub Copilot CLI",
            lambda h: (h / ".copilot").is_dir(),
            mcp=_copilot,
            skills=lambda h: h / ".agents" / "skills",
        ),
        Tool(
            "opencode",
            "OpenCode",
            lambda h: _opencode_path(h).parent.is_dir() or (h / ".opencode").is_dir(),
            mcp=_opencode,
            skills=lambda h: h / ".agents" / "skills",
        ),
        Tool(
            "vscode",
            "VS Code (Copilot agent mode)",
            lambda h: _vscode_path(h).parent.is_dir(),
            mcp=_vscode,
            skills=lambda h: h / ".agents" / "skills",
        ),
    ]


# ------------------------------------------------------------------ skill links
def _link_skills(tool: str, target: Path, source: Path, force: bool) -> list[Change]:
    changes: list[Change] = []
    for name in skill_names(source):
        src = source / name
        dst = target / name
        if dst.is_symlink():
            if dst.resolve() == src.resolve():
                changes.append(Change(tool, "skills", str(dst), "ok"))
                continue
            ours = dst.readlink().parts[-4:-1] == ("plugins", "dobby", "skills")
            if not (ours or force):
                changes.append(
                    Change(tool, "skills", str(dst), "skipped", f"links to {dst.readlink()}")
                )
                continue
        elif dst.exists():
            changes.append(
                Change(tool, "skills", str(dst), "skipped", "a folder by that name already exists")
            )
            continue

        def apply(src: Path = src, dst: Path = dst) -> None:
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.is_symlink():
                dst.unlink()
            try:
                dst.symlink_to(src, target_is_directory=True)
            except OSError:  # Windows without symlink rights
                shutil.copytree(src, dst)

        changes.append(
            Change(tool, "skills", str(dst), "updated" if dst.is_symlink() else "added", "", apply)
        )
    return changes


# ------------------------------------------------------------------ plan / run
def dobby_command() -> list[str]:
    """The absolute command an AI tool should run to reach Dobby."""
    for candidate in (
        Path(sys.executable).parent / "dobby",
        Path(sys.executable).parent / "dobby.exe",
    ):
        if candidate.is_file():
            return [str(candidate)]
    found = shutil.which("dobby")
    if found:
        return [str(Path(found).resolve())]
    return [sys.executable, "-m", "dobby.cli"]


def _default_env() -> dict[str, str]:
    if sys.platform.startswith("linux") and os.environ.get("DISPLAY"):
        return {"DISPLAY": os.environ["DISPLAY"]}
    return {}


def plan(
    home: Path,
    *,
    only: Sequence[str] = (),
    skip: Sequence[str] = (),
    mcp: bool = True,
    skills: bool = True,
    force: bool = False,
    command: Sequence[str] | None = None,
    env: dict[str, str] | None = None,
    include_missing: bool = False,
) -> list[Change]:
    """Works out every change setup would make, without making any."""
    cmd = list(command) if command else dobby_command()
    env = _default_env() if env is None else env
    source = skills_dir()
    changes: list[Change] = []
    linked: set[Path] = set()
    for tool in tools():
        if only and tool.key not in only:
            continue
        if tool.key in skip:
            continue
        present = tool.marker(home)
        if not present and not (only or include_missing):
            continue
        if not present and include_missing and not only:
            changes.append(Change(tool.key, "-", "", "not found"))
            continue
        if mcp and tool.mcp is not None:
            try:
                changes.append(tool.mcp(home, cmd, env, force))
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                changes.append(Change(tool.key, "mcp", "", "error", str(exc)))
        if skills and tool.skills is not None:
            target = tool.skills(home)
            if target in linked:
                continue
            linked.add(target)
            if source is None:
                changes.append(
                    Change(
                        tool.key,
                        "skills",
                        str(target),
                        "skipped",
                        "no skills folder in this install",
                    )
                )
            elif tool.key == "claude-code" and _claude_plugin_installed(home):
                changes.append(
                    Change(
                        tool.key, "skills", "dobby plugin", "ok", "the Dobby plugin provides them"
                    )
                )
            else:
                changes.extend(_link_skills(tool.key, target, source, force))
    return changes


def _labels() -> dict[str, str]:
    return {t.key: t.label for t in tools()}


def _notes() -> dict[str, str]:
    return {t.key: t.note for t in tools() if t.note}


def report(changes: Sequence[Change], dry_run: bool) -> str:
    """Formats changes as one line per tool part, collapsing per-skill links."""
    labels = _labels()
    out: list[str] = []
    grouped: dict[tuple[str, str], list[Change]] = {}
    for c in changes:
        grouped.setdefault((c.tool, c.part), []).append(c)
    for (tool, part), items in grouped.items():
        label = labels.get(tool, tool)
        if part == "skills" and len(items) > 1:
            statuses = sorted({c.status for c in items})
            folder = str(Path(items[0].target).parent)
            extra = [f"{Path(c.target).name}: {c.detail}" for c in items if c.detail]
            status = ", ".join(statuses)
            if dry_run:
                status = status.replace("added", "would add").replace("updated", "would update")
            line = f"{label:<30} skills  {status:<12} {folder} ({len(items)} skills)"
            out.append(line + ("".join(f"\n{'':<38}{e}" for e in extra)))
            continue
        for c in items:
            status = c.status
            if dry_run and c.apply is not None:
                status = "would " + {"added": "add", "updated": "update"}.get(status, status)
            detail = f"  ({c.detail})" if c.detail else ""
            out.append(f"{label:<30} {part:<7} {status:<12} {c.target}{detail}")
    return "\n".join(out)


def main(argv: Sequence[str] | None = None) -> int:
    """Runs ``dobby setup``. Returns the exit code."""
    parser = argparse.ArgumentParser(
        prog="dobby setup", description="Make Dobby available to every AI tool on this computer."
    )
    parser.add_argument("--dry-run", action="store_true", help="show the changes, make none")
    parser.add_argument("--list", action="store_true", help="show every known tool and its state")
    parser.add_argument("--only", default="", help="comma-separated tool keys")
    parser.add_argument("--skip", default="", help="comma-separated tool keys to leave alone")
    parser.add_argument("--no-mcp", action="store_true", help="do not touch MCP configs")
    parser.add_argument("--no-skills", action="store_true", help="do not link the skills")
    parser.add_argument("--force", action="store_true", help="replace existing Dobby entries")
    parser.add_argument("--command", default="", help="the dobby command to register")
    parser.add_argument("--home", default="", help=argparse.SUPPRESS)
    args = parser.parse_args(list(argv or []))

    home = Path(args.home).expanduser() if args.home else Path.home()
    split = lambda s: [x.strip() for x in s.split(",") if x.strip()]
    known = {t.key for t in tools()}
    unknown = [k for k in split(args.only) + split(args.skip) if k not in known]
    if unknown:
        print(f"Unknown tool(s): {', '.join(unknown)}. Known: {', '.join(sorted(known))}")
        return 2
    changes = plan(
        home,
        only=split(args.only),
        skip=split(args.skip),
        mcp=not args.no_mcp,
        skills=not args.no_skills,
        force=args.force,
        command=[args.command] if args.command else None,
        include_missing=args.list,
    )
    dry = args.dry_run or args.list
    if not changes:
        print("No AI tools found. Use --only <tool> to set one up anyway; --list shows them all.")
        return 0
    failed = False
    if not dry:
        for c in changes:
            if c.apply is None:
                continue
            try:
                c.apply()
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                c.status, c.detail = "error", str(exc)
                failed = True
    print(report(changes, dry))
    acted = {c.tool for c in changes if c.apply is not None and c.status != "error"}
    if not dry and acted:
        print("\nRestart those tools so they start Dobby and pick up the skills.")
        for key, note in _notes().items():
            if key in acted:
                print(f"  {_labels()[key]}: {note}")
    if any(c.status == "error" for c in changes):
        failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
