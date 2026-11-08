---
name: pinlet-skill-crafting
description: Turn a desktop task that will repeat into something Pinlet replays without a model - a declarative trajectory for any app, or a crystallized Tcl/CLI skill for journaled apps like Vivado and Verdi. Use after a task has worked, or when the user asks to automate or save a workflow. Read the pinlet skill first for the general rules.
---

# Crafting reusable desktop skills

Two ways to make a task replayable. Pick by app:

| App has a command journal (Vivado `vivado.jou`, Verdi `novas.cmd`) | Use a **crystallized skill**: recorded GUI steps are correlated with the journal and compiled to a Tcl/CLI template. Replays with 0 visual tokens. |
| --- | --- |
| Any other app | Use a **trajectory**: a JSON list of steps that `execute_trajectory` runs locally, with variables and per-step assertions. |

Crystallize only after the same path has worked twice. A skill compiled from a lucky run replays the luck.

## Trajectory (any app)

1. Do the task once with normal tools and note each step that mattered.
2. Write the spec (shape in `references/trajectory-spec.md`): `trajectory_name`, `variables` for anything that changes, `steps` with `step_id`, `action`, `parameters`, and `assertions`.
3. Prefer label-based actions (`find_and_click`, `click_and_type` with `query`) over coordinates, so the trajectory survives layout changes.
4. Run it with `execute_trajectory(trajectory_spec)`. It stops at the first failed step and reports which.
5. Keep the spec in the repo or the project's notes, so other agents can run the same file.

## Crystallized skill (journaled apps)

1. `start_skill_recording(journal_path=...)` (omit the path to use the app's default journal).
2. Do the task through the GUI.
3. `stop_skill_recording()`: read the captured steps.
4. `crystallize_skill(skill_name, task_prompt, app_target, extracted_entities, verification_cmd, description, cassette_path)`:
   - `extracted_entities`: the literals to lift into parameters, e.g. `{"runtime": "10us", "radix": "hex"}`.
   - `verification_cmd`: a Tcl check that proves success, e.g. a `get_value` or `report_*` that must match.
   - `cassette_path`: optional. When given, the compiled macro is replayed against that cassette before it is registered, and a mismatch refuses registration. Record one during step 2 with `start_cassette_recording(name)` / `stop_cassette_recording()` (the `eval` skill documents the cassette format and replay runner).
5. Later: `list_crystallized_skills`, then `execute_crystallized_skill(skill_name, arguments)`.

## Naming and hygiene

- Names: `<app>_<verb>_<object>`, e.g. `vivado_run_sim_fifo`, `chrome_export_gemini_answer`.
- One skill does one job; chain them in a trajectory with the `execute_crystallized_skill` step.
- Remove broken ones with `delete_crystallized_skill(skill_name)` rather than leaving them to fail later.

## References

- `references/trajectory-spec.md`: the full spec schema, actions, templating and assertions, with an example.
