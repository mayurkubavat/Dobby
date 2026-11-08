## What this changes

<!-- One or two sentences: what and why. Link the issue if there is one. -->

## How it was tested

<!-- Tests added or changed, and anything you tried by hand (platform, app). -->

## Checklist

- [ ] `ruff check .` and `ruff format --check .` pass
- [ ] `pytest` passes, and no test sends real input without `PINLET_LIVE_DESKTOP_TESTS=1`
- [ ] New public functions and classes have docstrings
- [ ] Tool parameters or descriptions changed: ran `python scripts/build_skill_refs.py`
- [ ] Anything that sends, deletes, pays or runs commands is off by default or asks first
- [ ] `CHANGELOG.md` updated under "Unreleased" if users will notice
