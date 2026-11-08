---
name: pinlet-app-vivado
description: Automate AMD/Xilinx Vivado - simulation, waveforms and the Tcl console - with pinlet's Vivado plugin, Tcl first and the GUI second. Use when a task involves Vivado projects, xsim simulation or waveform inspection. Read the pinlet skill first for the general rules.
---

# Vivado

Tcl is the most reliable interface. Use the GUI only for what Tcl cannot show, such as reading a waveform.

## Order of preference

1. **A crystallized skill** for the task, if one exists (`list_crystallized_skills`).
2. **Tcl** through `invoke_plugin_feature("vivado.tcl_exec", plugin_id="vivado", args={"command": "..."})`, for run, restart, add_wave, set radix, and reports.
3. **Plugin features** for waveform view work: `vivado.zoom_fit`, `vivado.zoom_in`, `vivado.zoom_out`, `vivado.run_all`, `vivado.run_for`, `vivado.restart`, `vivado.add_wave`, `vivado.set_radix`. Full list in `references/features.md`.
4. **GUI perception** on one pane at a time: `capture_pane(pane_name="waveform_canvas")` or `"tcl_console"` instead of the whole window.

## Reading results

- Prefer values over pixels: read signal values from the Tcl console (`get_value`) or the waveform's value column with `read_screen_text(region=...)`.
- Check the simulation log text in the Tcl console pane for `$finish`, errors and assertion failures.
- After `run_for` or `run_all`, `wait_for_ui_change` before reading.

## Making it repeatable

Record with `start_skill_recording` (Vivado writes `vivado.jou`), then `crystallize_skill` with a `verification_cmd` that checks a value or report. See the `pinlet-skill-crafting` skill.

## References

- `references/features.md`: Vivado plugin features and named panes (generated from the plugin code).
- `references/waveform-automation.md`: a verified FIFO simulation walkthrough and pane-level perception.
- `references/layout-for-dv.md`: arranging the Vivado layout for verification work.
- `references/plugin.md`: the plugin's design.
- `references/tool-features-research.md`: broader Vivado feature research (not all verified against the plugin).
