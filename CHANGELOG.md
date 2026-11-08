# Changelog

All notable changes to Dobby are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). Dobby is pre-1.0,
so a minor version may still change behaviour.

## [Unreleased]

The first public release will be 0.1.0. It contains:

### Added

- MCP server for desktop automation on macOS and Linux (X11 / XWayland):
  accessibility tree (AX, AT-SPI), local OCR, an ONNX icon detector, numbered
  screen states, and click, type, scroll, hotkey and window tools with
  post-action verification.
- Local models through Ollama for yes/no checks and picks, with per-role model
  settings (`get_model_roles`, `set_model_role`) and a benchmark script.
- On-screen overlay: a second pointer, a glow around the working window, and a
  control bar with Pause and Stop; Liquid Glass on macOS 26 and later.
- Secure mode (`dobby --secure`): per-app grants, Allow/Deny before Send,
  Delete and Pay, no typing into password fields. Turbo is the default.
- Input lock and background clicks, so the person's clicks stay out of the
  window Dobby works in.
- One shared Dobby per computer with a control lease, so several AI clients
  take turns; a native Dobby.app menu bar shell on macOS and a tray icon on
  Linux.
- Admin policy file (`/Library/Application Support/Dobby/policy.json`,
  `/etc/dobby/policy.json`) that can force secure mode, turn tools off, allow
  only listed shell commands and set per-app tiers. See `docs/admin-policy.md`.
- App plugins and skills for Chrome, AMD Vivado and Synopsys Verdi, and
  recordable, replayable sessions (cassettes) for testing.
- Continuous integration on macOS and Ubuntu: tests, a dependency audit, and
  ruff lint and format checks.
- `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, `NOTICE`, issue and
  pull request templates, and docstrings on every public module, class and
  function.

### Changed (for people who used the pre-release)

- `run_terminal_command` is off by default. Set `DOBBY_ALLOW_SHELL=1`, or allow
  it in the admin policy, to turn it on.
- `start_skill_recording` no longer looks for a journal in a built-in folder;
  pass `journal_path` or set `DOBBY_JOURNAL_PATH`.
- The examples and the Vivado trajectory script save their output to
  `DOBBY_ARTIFACT_DIR` (default `./artifacts`).
- `~/.dobby` is created readable by you only, and the bundled ONNX detector is
  checked against a pinned hash before it loads.

[Unreleased]: https://github.com/mayurkubavat/Dobby/commits/master
