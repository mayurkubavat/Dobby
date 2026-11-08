---
name: pinlet-app-verdi
description: Automate Synopsys Verdi (nWave, nTrace, nSchema) and similar dense EDA debug UIs with Pinlet - docked windows, hotkeys after pane focus, and value-column OCR. Use when a task involves Verdi waveforms, driver tracing or schematic debug. Read the pinlet skill first for the general rules.
---

# Verdi

There is no Verdi plugin yet, so these rules come from the learnings and research notes. Prefer the command journal and hotkeys over clicking tiny toolbar icons.

## Rules

1. **Dock everything first:** Window > Dock All, so `nTrace`, `nWave` and `nSchema` stop overlapping.
2. **Focus the pane, then send hotkeys:** for example `F` zoom full, `D` driver trace, `X` annotation, `Ctrl+W` waveform export. Use `hotkey` after a click inside the target pane.
3. **Read values as text:** keep View > Signal Value on in nWave, then use `read_screen_text(region=<value column>)`. Never decode values from waveform pixels.
4. **One pane at a time:** crop with a `region` or `capture_window` rather than full-screen captures.
5. **Make it repeatable:** Verdi writes `novas.cmd`. Record with `start_skill_recording(journal_path=...)` and crystallize (see the `pinlet-skill-crafting` skill).

## References

- `references/eda-ui-automation.md`: tested heuristics for Verdi and dense EDA UIs, plus focus and scrolling recipes.
- `references/tool-features-research.md`: Verdi feature research (not yet verified against a live Verdi).
