---
name: orchestrator
description: Entry point for any task that drives the Mac or Linux desktop through the Dobby MCP server. Covers the cheapest-first perception ladder, verified actions, recovery, safety, and which Dobby skill to load next.
---

# Desktop-control orchestrator

You plan; Dobby and local Gemma perceive and verify. Gemma never plans and never gives click coordinates. Coordinates come from the accessibility tree, OCR boxes or numbered marks.

## Load the right companion skill

| Situation | Load |
| --- | --- |
| Unsure which perception tool fits, or need a tool's parameters | `dobby:perception` |
| Working in the Claude app, Chrome, Vivado or Verdi | `dobby:app-claude`, `app-chrome`, `app-vivado`, `app-verdi` |
| The task will repeat | `dobby:skill-crafting` |
| Changing prompts, models or server code | `dobby:eval` |
| A tool is missing, permission errors, empty AX tree, Gemma unavailable | `dobby:troubleshoot` |

## 0. Before the first action

- If a tool below is missing, call `manage_toolsets(action="list_domains")`, then `manage_toolsets(action="enable", domains=[...])`. `core` holds the AX, OCR and ask tools.
- `list_windows`, then `focus_window(window_id)` on the target app. Never act on a window you have not focused.
- `list_crystallized_skills`: if a skill already does the task, use `execute_crystallized_skill(skill_name, arguments)` and stop.
- On macOS, shortcuts use `cmd`, not `ctrl`.

## 1. Perception ladder (cheapest first; stop at the first rung that answers)

1. `get_accessibility_tree(app_name_filter="<App>")`: names, roles, boxes, zero image tokens. After the first full read use `diff_only=true`.
2. `find_element(query, role)` or `find_and_click(query)` when you know the label.
3. `read_screen_text(contains="<text>")`: local OCR with click points.
4. `ask_screen(question, expected_text=...)`: local yes/no. Pass `expected_text` when the answer is a visible word, so OCR settles it without Gemma.
5. `find_visual_element(description)`: unnamed icons, picked by local Gemma from detector boxes.
6. `take_screenshot(mode="annotated_som", clip_to_active_window=true)`: marks plus a resource URI, no pixels. Click by `mark_id`.
7. `take_screenshot(..., include_image=true)`: last resort. At most one image per five actions.

## 2. Acting

- Every `click` carries a contract: `expected_outcome={"assert_screen_text": "Saved"}`, `{"assert_screen_text_not_present": "Unsaved"}` or `{"expect_ax_change": true}`. The server verifies locally and returns a verdict with the tier used. Read the verdict; do not screenshot to check.
- Prefer `click_and_type(query, text, press_enter)` to separate click and type.
- For 3 or more known steps, send one `execute_trajectory(trajectory_spec)`; it stops at the first failed step.
- Use `scroll_until_visible(query)`, not scroll-and-look loops.
- After builds, simulations or page loads, use `wait_for_ui_change` instead of polling with screenshots.
- For a longer known sequence that local Gemma should check step by step without you in the loop, write a plan JSON and run it with the ADK pipeline (`python -m dobby.adk plan.json`). You read only its report: every step's tier and verdict, or where it stopped and what the screen showed. See `references/adk-pipeline.md`.

## 3. Recovery (in order, then stop)

1. A verdict fails: retry once with `find_visual_element` for the same target.
2. An unexpected dialog: `dismiss_obstructions`, or `resolve_modal_dialog(button_label=...)` only for a safe label (OK, Close, Got it, Dismiss, No thanks, Not now, Later, Skip). Anything that deletes, discards, overwrites, sends or pays goes to the user.
3. A second failure: one `take_screenshot(include_image=true)`, then replan or ask the user. Never loop more than twice on one step.

## 4. Safety

- Never call `run_terminal_command` without the user's go-ahead for that command.
- Never type passwords, keys or payment details; hand those to the user.
- Stay inside the app the user named; say so before touching another.
- Stop and ask before anything irreversible: delete, send, publish, purchase, overwrite.

## 5. Sharing the desktop with another agent

Another AI may drive the same desktop through its own Dobby. Only one agent clicks or types at a time; reading the screen is always allowed.

- "<name> has control of the desktop": wait or do non-desktop work, then retry. `control_status` shows who holds control and for how long.
- "Another agent used the desktop since your last look": the step was skipped and you now hold control. Look again (AX tree or screenshot) before acting.
- "Stopped by the person from the Dobby menu": stop the task and report; do not retry unless asked.
- When a task is done, or before long planning, call `release_control` so the other agent can start at once.
- For a flow that must not be interrupted (a wizard, a drag across windows), call `hold_control(seconds)` first.

## 6. Report

End with what was done, the verdict and tier of each critical step, anything left for the user, and how many screenshot images were used.

## 7. Staying available

If the user wants Dobby to stay available after a task, do not end the turn: report, then call `wait_for_wake(timeout_s, reason)`. The bar reads "Dobby is sleeping · click to wake" and does not hold desktop control. When it returns `woken`, ask for or continue with the user's next instruction and look at the screen again before acting; on `stopped`, stop; on `timed_out`, call it again or end the turn; on `unavailable` (overlay off), end the turn.

When the person steps in, a tool returns `handoff` instead of a result. Follow its `note`:
- `stopped`: end the task now and say where you stopped. Do not retry or continue unless they ask again.
- `denied`: do not do that step again or reach the same result another way; explain and ask.
- `interrupted` / `control_changed`: the step was skipped. Look at the screen again, then decide; never replay old coordinates.
- `paused`: call `wait_for_wake` (Resume or a click on the bar hands back), or end the turn and ask.
