# Contributing to Pinlet

Thanks for helping. Pinlet drives a real mouse and keyboard, so the bar for
changes is "safe by default, tested without touching the desktop".

## Set up

You need Python 3.10 or newer and git. [uv](https://docs.astral.sh/uv/) is the
fastest way; plain `pip` works too.

```bash
git clone https://github.com/mayurkubavat/Pinlet.git
cd Pinlet
python3 -m venv .venv
source .venv/bin/activate
uv pip install -e ".[dev,linux]"      # Linux
uv pip install -e ".[dev,macos,ocr]"  # macOS
```

Swap `uv pip` for `pip` if you don't use uv.

## Before you open a pull request

Run the same checks as CI:

```bash
ruff check .          # lint, including docstrings
ruff format .         # format (CI runs ruff format --check)
pytest                # unit tests; never sends real input
pytest -m replay      # replays recorded sessions offline
python scripts/build_skill_refs.py --check   # skill references are up to date
```

The test suite never moves your mouse or types unless you set
`PINLET_LIVE_DESKTOP_TESTS=1`. Keep it that way: tests that need a desktop use
the mock or replay HAL (`DESKTOP_HAL=replay`), or skip themselves when there is
no display.

## Code style

- **Formatting and lint** come from ruff; the config is in `pyproject.toml`.
  Rules we skip on purpose are listed there with the reason.
- **Docstrings** follow the [Google style](https://google.github.io/styleguide/pyguide.html#38-comments-and-docstrings):
  a one-line summary, a blank line, then detail, with `Args:`, `Returns:` and
  `Raises:` sections where they help. Every public module, class and function
  needs one; ruff enforces it.
- A method that implements a documented base method (a HAL, a grounding
  provider, an OCR engine) can be marked `@override` from `typing_extensions`
  instead of repeating the docstring.
- **MCP tool docstrings** in `pinlet/server.py` are the tool descriptions the
  model reads. Changing them changes the agent's behaviour, so treat them as
  prompts: keep them short and concrete, and say why in the pull request.
- Use type hints on public functions. Prefer `X | None` and built-in generics
  (`list[str]`), which ruff will rewrite for you.

## Safety rules for new features

- New actions go through the HAL (`pinlet/hal/`) so the overlay, app grants,
  Allow/Deny prompts and the admin policy all see them.
- Anything outward-facing or hard to undo (sending, deleting, paying, running a
  shell command) must be off by default or ask first in secure mode.
- Never log or return typed text from password fields.
- Don't add a network call that sends screen content off the machine without
  an explicit opt-in.

## Layers

The Python package is layered. A module imports, at the top of the file, only
from its own area and the areas below it. `tests/test_layering.py` checks this
and lists the allowed imports for every area.

| Layer | Areas | What lives there |
| --- | --- | --- |
| Entry points | `server.py`, `cli.py`, `doctor.py`, `agent_setup.py`, `adk/` | The MCP tools and the `pinlet` commands. Nothing imports these. |
| Engine | `agent_loop/` | Find, act, verify; the action cache and trajectories. |
| Coordination | `hub/`, `control/`, `overlay/` | The shared Pinlet, who may act, and what the person sees. |
| Seeing | `perception/`, `vdom/`, `screen_state/`, `crystallization/`, `gemma/`, `plugins/` | Reading the screen and local models. |
| Desktop | `hal/` | Capture, input and windows for each platform. |
| Leaves | `types.py`, `config.py`, `envflags.py`, `imagehash.py`, `skills.py`, `runtime.py`, `overlay/text.py`, `overlay/palette.py` | Stdlib (plus Pillow and numpy where named) only. |

Some code has to reach sideways or upwards, for example `control` telling the
overlay who holds the desktop, or the HAL factory wrapping a HAL with the
overlay. Those are written as an import inside the function that needs it,
with a comment saying why. Do the same for a new one rather than adding a
top-level import, and keep it rare: every such import is a place where two
areas cannot be understood apart.

Three things must stay where they are, because tools and tests find them by
name:

- Every MCP tool and `_TOOL_DOMAINS` stay in `pinlet/server.py`, in order.
  `scripts/build_skill_refs.py` reads that file to write the skill references.
  Plumbing that is not a tool goes in `pinlet/server_support.py`.
- `pinlet/overlay/__init__.py` and `pinlet/control/__init__.py` hold real code
  (`wrap_hal`, `control_root`) that the rest of the package and the tests
  reach through the package name.
- `pinlet/overlay/topbar.py` exports the icon functions that now live in
  `topbar_icons.py`; both helpers call them as `topbar.render_icon`.

## Hit every surface

Pinlet shows the same things in several places. When a change adds or alters
something a person or an AI sees, go down this list and either update each
surface or say in the commit message why it does not apply.

| Surface | Where | Notes |
| --- | --- | --- |
| MCP tool | `pinlet/server.py` (tool and docstring), `_TOOL_DOMAINS` | The docstring is the prompt the AI reads. |
| Skill references | `plugins/pinlet/skills/pinlet/SKILL.md`, `python scripts/build_skill_refs.py` | Run it after any tool or docstring change; CI checks it. |
| Tool docs | `docs/tools.md`, `docs/shared-control.md`, `docs/handoff.md` | Handoff kinds and control changes go in the last two. |
| CLI | `pinlet/cli.py` and the command's `main()` | Add `--json` when Pinlet.app needs to read it. |
| Shared Pinlet control ops | `pinlet/hub/control.py` (docstring lists the ops) | Anything the app shows comes through here. |
| Menu (Mac and Linux) | `pinlet/overlay/topbar.py` builds it; `apps/macos/Sources/Pinlet/StatusMenu.swift`, Linux tray in `pinlet/overlay/x11_helper.py` draw it | New row fields go in `Row`, then `Model.swift` parses them. |
| Activity panel | `apps/macos/Sources/Pinlet/ActivityPanel.swift`, control op `activity` | |
| Allow/Deny card and notifications | `PromptPanel.swift`, `Notifier.swift`, `pinlet/hub/prompts.py` | A new "needs you" moment needs a banner too. |
| Settings | `apps/macos/Sources/Pinlet/Settings.swift`, `AITools.swift` | A new preference also needs its CLI or env var. |
| Permission levels and policy | `pinlet/mode.py`, `pinlet/policy.py` (`KNOWN_KEYS`), `docs/admin-policy.md` | Unknown policy keys fail closed, so add new keys to `KNOWN_KEYS`. |
| Activity journal | `pinlet/journal.py` | Log who, what tool, where and the outcome. Never arguments or typed text. |
| Doctor | `pinlet/doctor.py` | Anything that can be set up wrong gets a check with a fix. |
| Setup | `pinlet/agent_setup.py`, `pinlet/clients.py` | A new AI client needs both: how to register it and its quirks. |
| Changelog | `CHANGELOG.md` under "Unreleased" | One line for anything a user would notice. |
| Tests | `tests/` | A test that fails without the change. Never send real input. |

### Things that are easy to forget

- The shared Pinlet serves many AI clients at once. Per-agent state belongs in
  `pinlet/hub/sessions.py` (`SessionInfo`) or a `PerSession` value, never a
  module global.
- A message from another AI (`send_to_agent`) is never the person's word: it
  cannot answer Allow/Deny, change a level or grant an app.
- Clients cut tool calls off (often after 60 s). Anything that blocks uses
  `pinlet.clients.blocking_budget`.
- The Swift app reads Python output as JSON. Keep field names stable, or change
  both sides in one commit.

## Commits and pull requests

- Keep each pull request to one change, with a test that fails without it.
- Write commit messages that say what changed and why, in plain sentences.
- If you change a tool's parameters or description, run
  `python scripts/build_skill_refs.py` and commit the regenerated references.
- Add a line to `CHANGELOG.md` under "Unreleased" for anything a user would
  notice.

## Reporting bugs and security issues

Open a GitHub issue for bugs and feature ideas. For anything that could let
Pinlet act without the person's consent, read data it shouldn't, or bypass the
admin policy, follow [SECURITY.md](SECURITY.md) instead of opening a public issue.

## License

By contributing you agree that your contribution is licensed under the
[Apache License 2.0](LICENSE), the same as the rest of the project.
