"""``pinlet setup``: make Pinlet available to every AI tool on this computer.

Two things per tool:

* **MCP**: register the ``pinlet`` command as an MCP server in the tool's own config
  (Claude Code, Claude Desktop, Codex, Cursor, Gemini CLI, Antigravity, GitHub Copilot CLI,
  OpenCode, VS Code). Existing Pinlet entries are left alone unless ``--force``.
* **Skills**: link the Agent Skills in ``plugins/pinlet/skills`` (``pinlet``, ``pinlet-*``) into the
  folders those tools read skills from. ``~/.agents/skills`` is the shared folder read by Codex,
  Cursor, Gemini CLI, Copilot, OpenCode and Antigravity; Claude Code reads ``~/.claude/skills``
  (skipped when the Pinlet plugin is installed there already).

Usage::

    pinlet setup                 set up every tool found on this computer
    pinlet setup --dry-run       show what would change, change nothing
    pinlet setup --list          show each known tool and whether it is set up
    pinlet setup --only cursor,codex
    pinlet setup --no-mcp | --no-skills | --force | --command /path/to/pinlet
    pinlet setup --repair        fix entries whose command moved or went away
    pinlet setup --remove        take Pinlet out of every AI tool again
    pinlet setup --list --json   each tool's state, for Pinlet.app

AI tools are pointed at ``~/.pinlet/bin/pinlet`` (see :mod:`pinlet.runtime`), which setup
writes for the Python it runs in; moving a checkout then needs no setup run at all.

Every config file is backed up once (``<file>.pinlet-backup``) before Pinlet first edits it.
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

from pinlet import runtime

# The skill readers lived here before pinlet.skills; kept importable from this module.
from pinlet.skills import (  # noqa: F401
    read_skill,
    skill_names,
    skills_dir,
    skills_index_markdown,
)

SERVER_NAME = "Pinlet"


# ------------------------------------------------------------------ tools
@dataclass
class Tool:
    """One AI tool Pinlet knows how to set up."""

    key: str
    label: str
    marker: Callable[[Path], bool]  # is the tool on this computer?
    mcp: Callable[..., Change] | None = None
    skills: Callable[[Path], Path] | None = None
    note: str = ""
    read: Callable[[Path], list[str] | None] | None = None  # the registered command, if any
    remove: Callable[[Path], Change | None] | None = None


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
    backup = path.with_name(path.name + ".pinlet-backup")
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
            "" if same else "has its own Pinlet entry (--force replaces)",
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


def _entry_command(entry: object) -> list[str] | None:
    """The command list of one MCP server entry, whatever the tool's format."""
    if not isinstance(entry, dict):
        return None
    cmd = entry.get("command")
    if isinstance(cmd, list):  # OpenCode keeps command and args in one list
        return [str(c) for c in cmd] or None
    if not isinstance(cmd, str) or not cmd:
        return None
    args = entry.get("args") or []
    return [cmd, *(str(a) for a in args if isinstance(args, list))]


def _json_reader(path_of: Callable[[Path], Path], section: str):
    def read(home: Path) -> list[str] | None:
        try:
            table = _read_json(path_of(home)).get(section) or {}
        except (OSError, ValueError):
            return None
        key = _existing_key(table) if isinstance(table, dict) else None
        return _entry_command(table[key]) if key is not None else None

    return read


def _json_remover(tool: str, path_of: Callable[[Path], Path], section: str):
    def remove(home: Path) -> Change | None:
        path = path_of(home)
        try:
            table = _read_json(path).get(section) or {}
        except (OSError, ValueError) as exc:
            return Change(tool, "mcp", str(path), "error", f"cannot read: {exc}")
        if not isinstance(table, dict) or _existing_key(table) is None:
            return None

        def apply() -> None:
            fresh = _read_json(path)
            servers = fresh.get(section) or {}
            for key in [k for k in servers if k.lower() == SERVER_NAME.lower()]:
                del servers[key]
            _write_text(path, json.dumps(fresh, indent=2) + "\n")

        return Change(tool, "mcp", str(path), "removed", "", apply)

    return remove


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
    path_of, section = _JSON_CONFIGS["copilot"]
    return _json_server("copilot", path_of(home), section, entry, force)


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


_CODEX_HEADER = re.compile(r'^\s*\[mcp_servers\.(?:"?pinlet"?)\]\s*$', re.I)


def _codex_split(text: str) -> tuple[str, str]:
    """Splits config.toml into (everything else, the Pinlet server table)."""
    keep: list[str] = []
    ours: list[str] = []
    inside = False
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("["):
            inside = bool(_CODEX_HEADER.match(line)) or (
                inside and re.match(r"^\[mcp_servers\.\"?pinlet\"?\.", stripped, re.I) is not None
            )
        (ours if inside else keep).append(line)
    rest = "".join(keep)
    return re.sub(r"\n{3,}", "\n\n", rest), "".join(ours)


def _codex_read(home: Path) -> list[str] | None:
    path = _codex_path(home)
    try:
        _, block = _codex_split(path.read_text(encoding="utf-8"))
    except OSError:
        return None
    m = re.search(r'^\s*command\s*=\s*"((?:[^"\\]|\\.)*)"', block, re.M)
    if not m:
        return None
    cmd = [json.loads(f'"{m.group(1)}"')]
    a = re.search(r"^\s*args\s*=\s*\[(.*?)\]", block, re.M | re.S)
    if a:
        cmd += [json.loads(f'"{x}"') for x in re.findall(r'"((?:[^"\\]|\\.)*)"', a.group(1))]
    return cmd


def _codex_remove(home: Path) -> Change | None:
    path = _codex_path(home)
    try:
        _, block = _codex_split(path.read_text(encoding="utf-8"))
    except OSError:
        return None
    if not block:
        return None

    def apply() -> None:
        current, _ = _codex_split(path.read_text(encoding="utf-8"))
        _write_text(path, current.rstrip("\n") + "\n" if current.strip() else "")

    return Change("codex", "mcp", str(path), "removed", "", apply)


def _codex(home: Path, cmd: list[str], env: dict[str, str], force: bool = False) -> Change:
    path = _codex_path(home)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    _, existing = _codex_split(text)
    if existing and not force:
        return Change("codex", "mcp", str(path), "ok")
    block = [f"\n[mcp_servers.{SERVER_NAME}]", f"command = {_toml_str(cmd[0])}"]
    if cmd[1:]:
        block.append("args = [" + ", ".join(_toml_str(a) for a in cmd[1:]) + "]")
    if env:
        block.append("env = { " + ", ".join(f"{k} = {_toml_str(v)}" for k, v in env.items()) + " }")

    def apply() -> None:
        current = path.read_text(encoding="utf-8") if path.exists() else ""
        current, _ = _codex_split(current)
        sep = "" if not current or current.endswith("\n") else "\n"
        body = "\n".join(block)
        _write_text(path, current + sep + (body if current.strip() else body.lstrip("\n")) + "\n")

    return Change("codex", "mcp", str(path), "updated" if existing else "added", "", apply)


def _find_claude(home: Path) -> str | None:
    """The ``claude`` command, also where installers put it when an app's PATH is minimal."""
    if home != Path.home():
        return None
    found = shutil.which("claude")
    if found:
        return found
    for candidate in (
        home / ".local" / "bin" / "claude",
        home / ".claude" / "local" / "claude",
        home / ".npm-global" / "bin" / "claude",
        Path("/opt/homebrew/bin/claude"),
        Path("/usr/local/bin/claude"),
    ):
        if os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def _claude_read(home: Path) -> list[str] | None:
    claude = _find_claude(home)
    if not claude:
        return None
    try:
        out = subprocess.run(
            [claude, "mcp", "get", SERVER_NAME], capture_output=True, text=True, timeout=20
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    command = args = ""
    for line in out.stdout.splitlines():
        key, _, value = line.strip().partition(":")
        if key.lower() == "command":
            command = value.strip()
        elif key.lower() == "args":
            args = value.strip()
    return [command, *args.split()] if command else ["(registered)"]


def _claude_remove(home: Path) -> Change | None:
    claude = _find_claude(home)
    if not claude or _claude_read(home) is None:
        return None

    def apply() -> None:
        subprocess.run(
            [claude, "mcp", "remove", "--scope", "user", SERVER_NAME],
            capture_output=True,
            timeout=20,
        )

    return Change("claude-code", "mcp", "claude mcp (user scope)", "removed", "", apply)


def _claude_code(home: Path, cmd: list[str], env: dict[str, str], force: bool = False) -> Change:
    claude = _find_claude(home)
    if not claude:
        return Change(
            "claude-code",
            "mcp",
            "claude mcp add",
            "skipped",
            "the claude command is not on PATH; run: claude mcp add --scope user Pinlet -- "
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
    return '"pinlet@' in text


def tools() -> list[Tool]:
    """Every AI tool Pinlet can set up, in the order they are reported."""
    return _with_json_hooks(
        [
            Tool(
                "claude-code",
                "Claude Code",
                lambda h: (
                    (h / ".claude").is_dir() or (h == Path.home() and bool(shutil.which("claude")))
                ),
                mcp=_claude_code,
                skills=lambda h: h / ".claude" / "skills",
                read=_claude_read,
                remove=_claude_remove,
            ),
            Tool(
                "claude-desktop",
                "Claude Desktop",
                lambda h: _app_support(h, "Claude").is_dir(),
                mcp=_mcp_servers_json(_JSON_CONFIGS["claude-desktop"][0], "claude-desktop"),
                note="quit Claude Desktop before running setup; it rewrites its config on quit",
            ),
            Tool(
                "codex",
                "OpenAI Codex",
                lambda h: _codex_path(h).parent.is_dir() or shutil.which("codex") is not None,
                mcp=_codex,
                skills=lambda h: h / ".agents" / "skills",
                read=_codex_read,
                remove=_codex_remove,
            ),
            Tool(
                "cursor",
                "Cursor",
                lambda h: (h / ".cursor").is_dir(),
                mcp=_mcp_servers_json(_JSON_CONFIGS["cursor"][0], "cursor"),
                skills=lambda h: h / ".agents" / "skills",
            ),
            Tool(
                "gemini-cli",
                "Gemini CLI",
                lambda h: (
                    (h / ".gemini" / "settings.json").exists() or shutil.which("gemini") is not None
                ),
                mcp=_mcp_servers_json(_JSON_CONFIGS["gemini-cli"][0], "gemini-cli"),
                skills=lambda h: h / ".agents" / "skills",
            ),
            Tool(
                "antigravity",
                "Antigravity",
                lambda h: (
                    (h / ".gemini" / "antigravity").is_dir() or (h / ".gemini" / "config").is_dir()
                ),
                mcp=_mcp_servers_json(_JSON_CONFIGS["antigravity"][0], "antigravity"),
                skills=lambda h: h / ".agents" / "skills",
                note="Antigravity only reads tool schemas at start: restart it after setup",
            ),
            Tool(
                "antigravity-cli",
                "Antigravity CLI",
                lambda h: (h / ".gemini" / "antigravity-cli").is_dir(),
                mcp=_mcp_servers_json(_JSON_CONFIGS["antigravity-cli"][0], "antigravity-cli"),
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
    )


# Where each JSON-configured tool keeps its MCP servers: (config file for a home, section).
# The one place these paths are written; registering, reading and removing all use it.
_JSON_CONFIGS: dict[str, tuple[Callable[[Path], Path], str]] = {
    "claude-desktop": (
        lambda h: _app_support(h, "Claude", "claude_desktop_config.json"),
        "mcpServers",
    ),
    "cursor": (lambda h: h / ".cursor" / "mcp.json", "mcpServers"),
    "gemini-cli": (lambda h: h / ".gemini" / "settings.json", "mcpServers"),
    "antigravity": (lambda h: h / ".gemini" / "config" / "mcp_config.json", "mcpServers"),
    "antigravity-cli": (
        lambda h: h / ".gemini" / "antigravity-cli" / "mcp_config.json",
        "mcpServers",
    ),
    "copilot": (lambda h: h / ".copilot" / "mcp-config.json", "mcpServers"),
    "opencode": (_opencode_path, "mcp"),
    "vscode": (_vscode_path, "servers"),
}


def _with_json_hooks(items: list[Tool]) -> list[Tool]:
    for tool in items:
        if tool.key in _JSON_CONFIGS:
            path_of, section = _JSON_CONFIGS[tool.key]
            tool.read = tool.read or _json_reader(path_of, section)
            tool.remove = tool.remove or _json_remover(tool.key, path_of, section)
    return items


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
            ours = dst.readlink().parts[-4:-1] == ("plugins", "pinlet", "skills")
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
def pinlet_command() -> list[str]:
    """The absolute command an AI tool should run to reach Pinlet.

    On macOS and Linux that is the stable launcher (``~/.pinlet/bin/pinlet``, see
    :mod:`pinlet.runtime`), so the config survives a moved checkout or a reinstall.
    """
    if not sys.platform.startswith("win"):
        return [str(runtime.launcher_path())]
    return _direct_command()


def _direct_command() -> list[str]:
    """This interpreter's own ``pinlet`` script (Windows, where there is no sh launcher)."""
    for candidate in (
        Path(sys.executable).parent / "pinlet",
        Path(sys.executable).parent / "pinlet.exe",
    ):
        if candidate.is_file():
            return [str(candidate)]
    found = shutil.which("pinlet")
    if found:
        return [str(Path(found).resolve())]
    return [sys.executable, "-m", "pinlet.cli"]


def _default_env() -> dict[str, str]:
    if sys.platform.startswith("linux") and os.environ.get("DISPLAY"):
        return {"DISPLAY": os.environ["DISPLAY"]}
    return {}


def _command_exists(cmd: Sequence[str]) -> bool:
    if not cmd:
        return False
    if cmd[0] == "(registered)":
        return True
    exe = Path(cmd[0]).expanduser()
    if not exe.is_absolute():
        return shutil.which(cmd[0]) is not None
    if exe == runtime.launcher_path():
        return runtime.launcher_works(exe)
    return os.access(exe, os.X_OK)


def tool_status(home: Path, command: Sequence[str] | None = None) -> list[dict[str, object]]:
    """Where each known AI tool stands with Pinlet, for ``--list --json`` and Pinlet.app.

    ``state`` is one of ``connected``, ``broken`` (its command is gone), ``not set up`` or
    ``not installed``; ``stable`` says the entry runs the launcher ``command``.
    """
    from pinlet.mode import client_modes, mode_key

    cmd = list(command) if command else pinlet_command()
    source = skills_dir()
    names = skill_names(source)
    modes = client_modes()
    out: list[dict[str, object]] = []
    for tool in tools():
        installed = tool.marker(home)
        registered = tool.read(home) if (installed and tool.read) else None
        if not installed:
            state = "not installed"
        elif registered is None:
            state = "not set up"
        elif _command_exists(registered):
            state = "connected"
        else:
            state = "broken"
        skills = "none"
        if tool.skills is not None and names:
            if tool.key == "claude-code" and _claude_plugin_installed(home):
                skills = "plugin"
            else:
                target = tool.skills(home)
                linked = [n for n in names if (target / n / "SKILL.md").is_file()]
                skills = (
                    "linked" if len(linked) == len(names) else ("some" if linked else "missing")
                )
        out.append(
            {
                "key": tool.key,
                "label": tool.label,
                "installed": installed,
                "state": state,
                "command": registered,
                "stable": registered is not None and registered[:1] == cmd[:1],
                "skills": skills,
                "note": tool.note,
                # How much Pinlet asks this tool (pinlet mode): "default" or a level.
                "mode_key": mode_key(tool.key),
                "mode": modes.get(mode_key(tool.key), "default"),
            }
        )
    return out


def _needs_repair(entry: dict[str, object]) -> bool:
    return entry["state"] == "broken" or (entry["state"] == "connected" and not entry["stable"])


def plan_remove(home: Path, *, only: Sequence[str] = (), skip: Sequence[str] = ()) -> list[Change]:
    """Every change that takes Pinlet out of the AI tools again (MCP entries and skill links)."""
    changes: list[Change] = []
    seen: set[Path] = set()
    for tool in tools():
        if (only and tool.key not in only) or tool.key in skip:
            continue
        if tool.remove is not None:
            try:
                change = tool.remove(home)
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                change = Change(tool.key, "mcp", "", "error", str(exc))
            if change is not None:
                changes.append(change)
        if tool.skills is None:
            continue
        target = tool.skills(home)
        if target in seen or not target.is_dir():
            continue
        seen.add(target)
        for dst in sorted(target.iterdir()):
            if not dst.is_symlink():
                continue
            if dst.readlink().parts[-4:-1] != ("plugins", "pinlet", "skills"):
                continue

            def apply(dst: Path = dst) -> None:
                dst.unlink()

            changes.append(Change(tool.key, "skills", str(dst), "removed", "", apply))
    return changes


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
    repair: bool = False,
) -> list[Change]:
    """Works out every change setup would make, without making any.

    ``repair`` also replaces Pinlet entries whose command is gone or is not ``command``.
    """
    cmd = list(command) if command else pinlet_command()
    fix: set[str] = set()
    if repair:
        fix = {str(e["key"]) for e in tool_status(home, cmd) if _needs_repair(e)}
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
                changes.append(tool.mcp(home, cmd, env, force or tool.key in fix))
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
                        tool.key, "skills", "pinlet plugin", "ok", "the Pinlet plugin provides them"
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
                status = (
                    status.replace("added", "would add")
                    .replace("updated", "would update")
                    .replace("removed", "would remove")
                )
            line = f"{label:<30} skills  {status:<12} {folder} ({len(items)} skills)"
            out.append(line + ("".join(f"\n{'':<38}{e}" for e in extra)))
            continue
        for c in items:
            status = c.status
            if dry_run and c.apply is not None:
                status = "would " + {"added": "add", "updated": "update", "removed": "remove"}.get(
                    status, status
                )
            detail = f"  ({c.detail})" if c.detail else ""
            out.append(f"{label:<30} {part:<7} {status:<12} {c.target}{detail}")
    return "\n".join(out)


def main(argv: Sequence[str] | None = None) -> int:
    """Runs ``pinlet setup``. Returns the exit code."""
    parser = argparse.ArgumentParser(
        prog="pinlet setup", description="Make Pinlet available to every AI tool on this computer."
    )
    parser.add_argument("--dry-run", action="store_true", help="show the changes, make none")
    parser.add_argument("--list", action="store_true", help="show every known tool and its state")
    parser.add_argument("--only", default="", help="comma-separated tool keys")
    parser.add_argument("--skip", default="", help="comma-separated tool keys to leave alone")
    parser.add_argument("--no-mcp", action="store_true", help="do not touch MCP configs")
    parser.add_argument("--no-skills", action="store_true", help="do not link the skills")
    parser.add_argument("--force", action="store_true", help="replace existing Pinlet entries")
    parser.add_argument("--command", default="", help="the pinlet command to register")
    parser.add_argument(
        "--repair", action="store_true", help="fix entries whose command is gone or out of date"
    )
    parser.add_argument("--remove", action="store_true", help="take Pinlet out of the AI tools")
    parser.add_argument("--json", action="store_true", help="print the result as JSON")
    parser.add_argument("--home", default="", help=argparse.SUPPRESS)
    args = parser.parse_args(list(argv or []))

    home = Path(args.home).expanduser() if args.home else Path.home()
    split = lambda s: [x.strip() for x in s.split(",") if x.strip()]
    known = {t.key for t in tools()}
    unknown = [k for k in split(args.only) + split(args.skip) if k not in known]
    if unknown:
        print(f"Unknown tool(s): {', '.join(unknown)}. Known: {', '.join(sorted(known))}")
        return 2
    command = [args.command] if args.command else None
    dry = args.dry_run or args.list
    if args.list and args.json:
        print(json.dumps({"tools": tool_status(home, command)}, indent=2))
        return 0
    if command is None and not dry and not args.remove and not sys.platform.startswith("win"):
        runtime.write_launcher()
    if args.remove:
        changes = plan_remove(home, only=split(args.only), skip=split(args.skip))
    else:
        changes = plan(
            home,
            only=split(args.only),
            skip=split(args.skip),
            mcp=not args.no_mcp,
            skills=not args.no_skills,
            force=args.force,
            command=command,
            include_missing=args.list,
            repair=args.repair,
        )
    if not changes and not args.json:
        if args.remove:
            print("Pinlet is not set up in any AI tool.")
        else:
            print(
                "No AI tools found. Use --only <tool> to set one up anyway; --list shows them all."
            )
        return 0
    if not dry:
        for c in changes:
            if c.apply is None:
                continue
            try:
                c.apply()
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                c.status, c.detail = "error", str(exc)
    failed = any(c.status == "error" for c in changes)
    acted = {c.tool for c in changes if c.apply is not None and c.status != "error"}
    if args.json:
        notes = _notes()
        print(
            json.dumps(
                {
                    "ok": not failed,
                    "dry_run": dry,
                    "changes": [
                        {
                            "tool": c.tool,
                            "part": c.part,
                            "target": c.target,
                            "status": c.status,
                            "detail": c.detail,
                            "pending": dry and c.apply is not None,
                        }
                        for c in changes
                    ],
                    "restart": sorted(acted) if not dry else [],
                    "notes": {k: notes[k] for k in acted if k in notes} if not dry else {},
                    "tools": tool_status(home, command),
                },
                indent=2,
            )
        )
        return 1 if failed else 0
    print(report(changes, dry))
    if not dry and acted:
        print(
            "\nRestart those tools so they "
            + ("stop starting Pinlet." if args.remove else "start Pinlet and pick up the skills.")
        )
        if not args.remove:
            for key, note in _notes().items():
                if key in acted:
                    print(f"  {_labels()[key]}: {note}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
