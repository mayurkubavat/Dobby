# Handing control between Dobby and you

How control moves when an AI (Claude Code, Claude Desktop, Antigravity...)
drives Dobby and you step in, and the tests that keep it working.

## The flow

1. **Dobby works.** Each action passes a checkpoint first (`OverlayClient.checkpoint`).
2. **You step in**, one of four ways:
   - **Stop** (bar, menu, Ctrl+Opt+Esc): the step is not done.
   - **Deny** (or no answer) on an Allow/Deny prompt: the risky step is not done.
   - **Don't Allow** on "Let Dobby use …?", or an app that `~/.dobby/apps.json` limits:
     the step is not done and the AI gets `blocked`. Dobby does not ask again this session.
   - **Pause** (bar, menu, Ctrl+Opt+P): Dobby waits up to 2 minutes at its next step.
   - **Take over**: you click, drag, scroll or type while Dobby works. Dobby waits
     until you stop (auto) or press Resume (manual), then skips the step so the AI looks again.
     Moving the mouse alone does not count, and nothing counts while an Allow/Deny prompt is up.
3. **The AI is told what happened.** Every tool returns:

   ```json
   {"ok": false, "handoff": "stopped|denied|blocked|interrupted|paused|control_changed|control_busy",
    "look_again": true, "retry_ok": false, "note": "what to do next", "detail": "..."}
   ```

   `dobby/handoff.py` builds it; `DobbyServer.add_tool` wraps every tool so no tool can
   swallow it, and the catch-all blocks in the agent loop and trajectories re-raise it.
4. **Hand back.** The AI calls `wait_for_wake`; the bar says "click to wake". Clicking it,
   or pressing Resume when Dobby was paused, returns the call and clears any pause or take-over,
   so the AI's next step runs at once.
5. **Task over.** About `active_secs + linger_secs` (8 + 20 s) after the last step the
   "thinking" glow stops and the agent pointer goes away.

## Rules that were broken before

| Symptom | Cause | Fix |
| --- | --- | --- |
| Glow keeps sweeping and the pointer stays after the AI is done | The last screen read set "thinking"; only a later pointer move cleared it, and nothing ever hid the pointer | `menu.idle_cleanup`, called from both helpers' ticks |
| Bar's timer and "working" restart after each step | Clearing the tag (`intent ""`) counted as a new action | Only a non-empty intent marks Dobby active |
| Reaching for Pause presses "Resume now" | Moving the mouse turned the bar into take-over mode under the pointer | `menu.takes_over`: pointer moves never take over |
| After Allow, the AI's next step is skipped as "interrupted" | Moving to the Allow button counted as a take-over | No take-over while a prompt is up |
| AI retries after Stop, or works around a Deny | All of these became "Action failed during execution: ..." | Structured `handoff` result |
| A Stop pressed after the task kills the next task | The Stop stayed pending until the next action | Stops older than 120 s are dropped (`stop_ttl`) |
| A long pause looked like Stop | Same exception | `OverlayPaused` → `handoff: paused`, and Resume wakes a waiting AI |
| No Stop, Allow/Deny or wake on Linux | The HAL factory wrapped the overlay on macOS only | Wrapped on Linux X11 too |

## Tests

- `tests/test_handoff.py` runs everywhere: a scripted helper process plays the person,
  and tools are called through `DobbyServer.call_tool`, so each test checks exactly what
  the AI receives and what input reached the desktop. It includes a full
  Dobby → person → Dobby round trip.
- `tests/test_overlay_macos_ui.py` (macOS only) checks the helper itself: reaching for the
  bar, answering Allow/Deny, clearing the tag, and putting the pointer away.

Add a test here whenever a new way to step in, or a new tool that acts, is added.
`test_every_registered_tool_is_guarded` fails if a tool skips the guard.
