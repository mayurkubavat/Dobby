# ADK pipeline: plans checked by local Gemma

`dobby/adk` runs a plan you wrote, step by step, on Google ADK. Code calls the desktop tools; local Gemma only judges, through ADK `LlmAgent`s with an `output_schema` (which turns tools off for them). Gemma never plans and never picks coordinates. When a step cannot be verified, the run stops and returns a report for you to replan from.

Install once: `.venv/bin/pip install -e '.[adk]'` (google-adk and litellm; no new model).

## Plan format

```json
{
 "name": "vivado_synth",
 "goal": "Run synthesis and confirm it finished",
 "steps": [
  {"tool": "find_and_click", "args": {"query": "Run Synthesis"},
   "expect": {"text": "Running synth_design"}},
  {"tool": "press_key", "args": {"key": "Return"},
   "expect": {"question": "Did synthesis complete successfully?", "allow_dialog": true},
   "settle_ms": 2000, "retries": 5}
 ]
}
```

- `tool`: one of `click`, `find_and_click`, `type_text`, `press_key`, `hotkey`, `scroll`, `scroll_until_visible`, `focus_window`, `invoke_plugin_feature`, `execute_crystallized_skill`. Anything else is refused when the plan loads. The runner enables the toolsets the plan needs.
- `expect.text` / `expect.text_absent`: OCR text that must (not) be on screen. Checked first, with no model.
- `expect.question`: a yes/no question for Gemma when text alone cannot tell.
- `allow_dialog`: a dialog appearing after this step is expected. Otherwise a new dialog is triaged and anything but a benign one stops the run.
- `settle_ms` (400) before observing; `retries` (2) extra observe-and-check rounds for slow UIs. The tool itself is never re-run.

## Run it

```bash
.venv/bin/python -m dobby.adk plan.json            # starts Dobby over MCP and drives the live desktop
.venv/bin/python -m dobby.adk plan.json --json     # full report
DESKTOP_HAL=replay DESKTOP_CASSETTE=tests/cassettes/example_click \
  .venv/bin/python -m dobby.adk examples/adk/example_click_plan.json   # rehearse on a cassette
```

Exit code 0 means every step passed.

## How each step is checked (cheapest first)

| Tier | What | Cost |
| --- | --- | --- |
| `ocr` | `expect.text` found / `text_absent` not found | ~0.2 s, no model |
| `gemma_text` | Verifier agent judges OCR text + accessible names; a "pass" must quote evidence that is really in the observation | ~1-2 s warm |
| `gemma_vision` | `ask_screen(question)`: Gemma looks at the pixels | ~1.5 s warm |
| `dialog` | Triage agent classifies a new dialog; not benign stops the run | ~1-2 s |

A model error or bad JSON counts as unsure, never as a pass. On a cassette without recorded images, `gemma_vision` always answers `blank_crop` (unsure), so rehearsals are decided by `ocr` and `gemma_text`.

## Reading the report

`status` is `done` or `escalated`. When escalated, `stopped_at`, `reason` and `observation` (the text the screen showed) tell you what to do next: fix the plan, take one screenshot, or ask the user. Do not re-run a plan that stopped on a destructive dialog without the user.

## Where the prompts live

The verifier and triage system prompts are `adk_verifier` and `adk_triage` in `dobby/gemma/prompts.py`, overridable like the others (`~/.dobby/prompts/<id>.txt`).
