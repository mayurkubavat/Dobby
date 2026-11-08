---
name: pinlet-eval
description: Check that a change to Pinlet (prompts, local models, perception or server code) helps rather than hurts - unit and replay tests, cassettes, and the local benchmark. Use before committing such a change, or when comparing two models or prompts.
---

# Evaluating Pinlet changes

A change lands only with evidence that it helped. Run these in order; stop at the first failure.

1. **Unit and replay tests** (fast, no desktop needed):
   `.venv/bin/python -m pytest -q`
   On macOS, 5 skips are expected (X11-only tests, a missing font, RapidOCR is the Linux engine). Any failure blocks the change.
2. **Generated references are current:**
   `.venv/bin/python scripts/build_skill_refs.py --check`
3. **Replay the behavior you changed** against a cassette (below), so the result does not depend on what is on screen today. `pytest -m replay` runs only the cassettes in `tests/cassettes/`.
4. **Benchmark on the real desktop** (read-only, never clicks):
   `.venv/bin/python scripts/bench_local.py --repeats 3`
   Compare capture, OCR and Gemma latency and the token-savings block with the previous run.
5. **For a Gemma prompt or model change, score the prompt** (needs Ollama, never touches the desktop):
   `.venv/bin/python scripts/eval_prompts.py --synthetic` (add `--cases my.jsonl` for real crops, `--prompt-dir <dir>` to try an override, `--ocr-context` to see what showing Gemma the OCR text does).
   The number that must not rise is **wrong** (a confident yes or no that is incorrect). More unsure is acceptable; it only costs a screenshot.

## What to compare

| Change | Must not get worse | Should improve |
| --- | --- | --- |
| Gemma prompt or model | `eval_prompts.py` wrong count, dialog triage tests | Correct count, latency (warm p50) |
| OCR settings | Lines found on the bench window | OCR ms, text tokens |
| Perception or ladder logic | Tests, replay verdicts | Screenshots per task |
| Server tools or toolsets | Tests, tool reference drift check | Tool count in `core` only if each earns its place |

Write the before and after numbers into the commit message.

## Cassettes

A cassette is a recorded desktop: frames (AX tree, OCR boxes, windows, screen size, an optional image) and the transitions an action causes. Replaying one runs the real controller against a fake desktop and raises `CassetteMismatch` when an action does not match the recording. Format and how to make one: `references/cassettes.md`.

To record one from a live session, enable the `skills` toolset, call `start_cassette_recording(name)`, do the task with the normal tools, then `stop_cassette_recording()`. Each input action is followed by a settle wait and a frame of AX nodes, OCR lines, windows and cursor (add `capture_images=true` for JPEG frames). The cassette lands in `~/.pinlet/cassettes/<name>/`. Typed text is stored verbatim, so never record while typing a secret.

To keep it as a regression test, copy the folder into `tests/cassettes/`, add an `expect.json` for the final frame, and run `.venv/bin/python -m pytest -q -m replay` (or `scripts/replay_cassette.py <folder>` for one).

## References

- `references/cassettes.md`: cassette JSON format, recording, `expect.json`, the replay runner, and a minimal example.
