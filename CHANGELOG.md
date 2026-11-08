# Changelog

All notable changes to Pinlet are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). Pinlet is pre-1.0,
so a minor version may still change behaviour.

## [Unreleased]

The first public release will be 0.1.0. It contains:

### Added

- `scripts/install.sh`: installs Pinlet into a folder it owns
  (`~/Library/Application Support/Pinlet` or `~/.local/share/pinlet`) with uv,
  on Apple silicon Macs and Linux, and runs `pinlet setup`. Running it again
  updates Pinlet.
- `~/.pinlet/bin/pinlet`, one launcher every AI tool runs. It tries the Python
  it was written for, then Pinlet's own runtime, and otherwise says how to fix
  it, so a moved checkout no longer breaks AI tool configs.
- `pinlet doctor` (`--json`): checks Python, packages, the launcher, the model,
  the skills, the shared Pinlet and each AI tool, with a fix for each problem.
- `pinlet setup --repair`, `--remove` and `--json` (and `--list --json`).

### Changed

- New look, "Frost": the app icon is a white frosted tile with a pearl pointer
  and a cyan star, and the Linux tray tile matches it. Buttons, the screen glow,
  the glass rim and the pointer move from violet to cool cyan (fill `#0E7490`,
  glow `#0EA5C6` / `#67E8F9` on dark). The menu bar icon keeps its shape.
- `pinlet setup` registers the launcher instead of a venv's own `pinlet` script.
- Renamed from Dobby to Pinlet: package and command `pinlet`, module
  `pinlet`, `PINLET_*` settings, `~/.pinlet`, Pinlet.app (`dev.pinlet.app`),
  `pinlet*` skills and the `Pinlet` MCP server. For one release the old names
  still work: `DOBBY_*` variables fill unset `PINLET_*` ones, `~/.dobby` moves
  to `~/.pinlet` (a link stays behind), the old policy paths are read, a
  `dobby` command remains, and `pinlet setup` replaces old Dobby entries and
  skill links. The `elf` personality level is now `lively` (`elf` still works).

### Added

- `pinlet setup`: registers Pinlet with every AI tool it finds (Claude Code,
  Claude Desktop, Codex, Cursor, Gemini CLI, Antigravity, GitHub Copilot,
  OpenCode, VS Code) and links the agent skills into `~/.agents/skills` and
  `~/.claude/skills`. `--dry-run`, `--list`, `--only`, backups, idempotent.
- Agent guidance that works in any AI tool: skills renamed `pinlet` and
  `pinlet-*` (portable Agent Skills names), trigger-focused descriptions,
  fuller MCP server instructions, skills served as `mcp://pinlet/skills/<name>`
  resources, and a `pinlet` MCP prompt.
- MCP server for desktop automation on macOS and Linux (X11 / XWayland):
  accessibility tree (AX, AT-SPI), local OCR, an ONNX icon detector, numbered
  screen states, and click, type, scroll, hotkey and window tools with
  post-action verification.
- Local models through Ollama for yes/no checks and picks, with per-role model
  settings (`get_model_roles`, `set_model_role`) and a benchmark script.
- On-screen overlay: a second pointer, a glow around the working window, and a
  control bar with Pause and Stop; Liquid Glass on macOS 26 and later.
- Secure mode (`pinlet --secure`): per-app grants, Allow/Deny before Send,
  Delete and Pay, no typing into password fields. Turbo is the default.
- Input lock and background clicks, so the person's clicks stay out of the
  window Pinlet works in.
- One shared Pinlet per computer with a control lease, so several AI clients
  take turns; a native Pinlet.app menu bar shell on macOS and a tray icon on
  Linux.
- Admin policy file (`/Library/Application Support/Pinlet/policy.json`,
  `/etc/pinlet/policy.json`) that can force secure mode, turn tools off, allow
  only listed shell commands and set per-app tiers. See `docs/admin-policy.md`.
- App plugins and skills for Chrome, AMD Vivado and Synopsys Verdi, and
  recordable, replayable sessions (cassettes) for testing.
- Continuous integration on macOS and Ubuntu: tests, a dependency audit, and
  ruff lint and format checks.
- `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, `NOTICE`, issue and
  pull request templates, and docstrings on every public module, class and
  function.

### Changed (for people who used the pre-release)

- `run_terminal_command` is off by default. Set `PINLET_ALLOW_SHELL=1`, or allow
  it in the admin policy, to turn it on.
- `start_skill_recording` no longer looks for a journal in a built-in folder;
  pass `journal_path` or set `PINLET_JOURNAL_PATH`.
- The examples and the Vivado trajectory script save their output to
  `PINLET_ARTIFACT_DIR` (default `./artifacts`).
- `~/.pinlet` is created readable by you only, and the bundled ONNX detector is
  checked against a pinned hash before it loads.

[Unreleased]: https://github.com/mayurkubavat/Pinlet/commits/master
