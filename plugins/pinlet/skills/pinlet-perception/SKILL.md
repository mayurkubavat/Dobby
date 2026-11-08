---
name: pinlet-perception
description: Choose how to see the screen with Pinlet - accessibility tree, local OCR, local Gemma checks, Set-of-Marks or a screenshot - and look up any tool's exact parameters. Use when deciding how to find or verify something on screen, or when a perception call returns nothing useful. Read the pinlet skill first for the general rules.
---

# Perception with Pinlet

Pick the cheapest source that can answer the question. Each step down costs more tokens or time.

| Need | Tool | Cost | Fails when |
| --- | --- | --- | --- |
| Named controls and their boxes | `get_accessibility_tree(app_name_filter=...)` | 0 image tokens, ~0.1-0.6 s | App has no AX (canvas, games, some EDA panes), permission missing |
| A label you can name | `find_element(query, role)` / `find_and_click(query)` | 0 image tokens | Label is an icon or drawn text |
| Text and where it is | `read_screen_text(contains=...)` | ~220 ms on Apple Vision, cached repeats under 1 ms | Text is tiny, rotated or inside images |
| A yes/no about the screen | `ask_screen(question, expected_text=...)` | OCR first; Gemma ~1.2 s warm, ~8 s cold | Question needs reasoning over many items |
| Numbered elements to act on, no image | `get_screen_state()` then `act_on_element(eid, generation)` | AX + OCR, 0 image tokens; act re-binds on a fresh frame | App exposes neither AX nor readable text |
| A control with no text label (toolbar icon, canvas) | `decide_visual_element(goal)` | Icon detector + lettered boxes on a screenshot, Gemma picks a letter, close-up yes/no confirms | Text view already names the target (use `decide_next_element`) |
| Pick one of known candidates | `choose_option(goal, options, verb=...)` | One Gemma token, probabilities from logprobs | Options are not known yet (look first) |
| An unnamed icon | `find_visual_element(description)` | One Gemma call over numbered boxes | No detector box around the target |
| Layout of a window | `take_screenshot(mode="annotated_som")` | Marks + URI, no image | Very dense UIs (marks overlap) |
| Appearance itself | `take_screenshot(include_image=true)` | ~1,500 image tokens per window | Never; it is the fallback |

Rules that hold for every rung:

- Gemma names and judges; it never returns coordinates. Clicks use AX boxes, OCR boxes or mark ids.
- OCR output on a busy window can cost more tokens than the screenshot (3,714 vs 1,533 on the Claude window). Always pass `contains` or a `region`.
- `ask_screen` returns `answer` (true, false or null), `confidence`, `evidence` and `source` (ocr, gemma or fallback). A null answer means neither OCR nor Gemma could decide; escalate instead of guessing.
- `act_on_element` refuses a stale target (gone, moved, disabled) or typing into a password field; observe again rather than retrying the same eid. `decide_next_element(goal, verb)` lets local Gemma pick the eid, gated like `choose_option`; when the text view escalates it retries once on the screenshot (`visual_fallback`).
- Observations wait for the window to stop changing first (`settle`, up to ~2.5 s; reported as `settle.settled`). `act_on_element` also refuses a target covered by another window, sheet or popover, and waits for the screen to settle before reporting what changed. `get_screen_state(include_icons=true)` adds unnamed detector boxes ("seen, not named").
- `choose_option` returns a `gate.verdict`: act only on "act". "escalate" means decide yourself, "stop" means do not act (consequential verb like send or delete without `authorized=true`, or below threshold). It is in the advanced_vision toolset.
- Electron apps (Claude, VS Code, Slack) expose their full UI in the AX tree after the first inspection; if the tree is only window buttons, call again once.

## References (read when needed)

- Local models are plug and play per role (text, vision, visual_pick, triage): `get_model_roles` shows what runs where, `set_model_role` swaps one for this session, and `scripts/bench_models.py` compares candidates before you switch. See README "Local models".
- `references/tools.md`: every tool with exact parameters and defaults, grouped by toolset. Generated from the server code.
- `references/gemma-tiers.md`: what the local model does in each tool, its settings and measured latency.
- `references/zero-token-element-discovery.md`: recipes for finding elements without images.
- `references/visual-delta-verification.md`: how pixel-delta verification works and when it misleads.
- `references/architecture.md`: how HAL, perception, vDOM and the agent loop fit together.
