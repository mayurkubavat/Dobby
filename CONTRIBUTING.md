# Contributing to Dobby

Thanks for helping. Dobby drives a real mouse and keyboard, so the bar for
changes is "safe by default, tested without touching the desktop".

## Set up

You need Python 3.10 or newer and git. [uv](https://docs.astral.sh/uv/) is the
fastest way; plain `pip` works too.

```bash
git clone https://github.com/mayurkubavat/Dobby.git
cd Dobby
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
`DOBBY_LIVE_DESKTOP_TESTS=1`. Keep it that way: tests that need a desktop use
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
- **MCP tool docstrings** in `dobby/server.py` are the tool descriptions the
  model reads. Changing them changes the agent's behaviour, so treat them as
  prompts: keep them short and concrete, and say why in the pull request.
- Use type hints on public functions. Prefer `X | None` and built-in generics
  (`list[str]`), which ruff will rewrite for you.

## Safety rules for new features

- New actions go through the HAL (`dobby/hal/`) so the overlay, app grants,
  Allow/Deny prompts and the admin policy all see them.
- Anything outward-facing or hard to undo (sending, deleting, paying, running a
  shell command) must be off by default or ask first in secure mode.
- Never log or return typed text from password fields.
- Don't add a network call that sends screen content off the machine without
  an explicit opt-in.

## Commits and pull requests

- Keep each pull request to one change, with a test that fails without it.
- Write commit messages that say what changed and why, in plain sentences.
- If you change a tool's parameters or description, run
  `python scripts/build_skill_refs.py` and commit the regenerated references.
- Add a line to `CHANGELOG.md` under "Unreleased" for anything a user would
  notice.

## Reporting bugs and security issues

Open a GitHub issue for bugs and feature ideas. For anything that could let
Dobby act without the person's consent, read data it shouldn't, or bypass the
admin policy, follow [SECURITY.md](SECURITY.md) instead of opening a public issue.

## License

By contributing you agree that your contribution is licensed under the
[Apache License 2.0](LICENSE), the same as the rest of the project.
