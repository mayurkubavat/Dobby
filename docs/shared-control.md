# Shared control: two AI agents, one desktop

Every AI client (Claude Code, Claude Desktop, Cursor, Codex, Gemini CLI,
Antigravity, Copilot) connects to one shared Pinlet (`pinlet serve`, owned by
Pinlet.app on macOS). Each connection is its own session. Shared control makes
the sessions take turns on the mouse and keyboard.

## How it works

- Every acting HAL call (click, move, drag, scroll, type, key, hotkey, focus)
  takes a lease stored in `~/.pinlet/control/lease.json`, changed only under an
  OS file lock. Reading the screen never needs the lease.
- A whole tool call is one turn: the first action takes the lease and it stays
  busy until the tool returns, so another agent never cuts in between a click
  and the typing that follows.
- With nobody waiting, the holder keeps control. When another agent waits, the
  holder hands over between steps after `release_control`, after 20 s idle, or
  after a 60 s slice. Higher `PINLET_AGENT_PRIORITY` wins the queue; waiting time
  ages priority up so nobody starves. `PINLET_CONTROL_PREEMPT=1` lets higher
  priority take over at the next step boundary.
- A holder whose process died loses the lease at once.
- A holder waiting for the person (an Allow/Deny card) yields at once, so an
  unanswered card never freezes the other agents. After Allow it takes control
  back; if another agent acted meanwhile, the step is skipped with "look again".
- A tool that waited `PINLET_CONTROL_MAX_WAIT` without getting control returns
  `control_busy` with who is using the screen, its place in line and roughly
  when the holder must hand over. The agent keeps its place in line for 120 s,
  so calling again does not send it to the back. Only agents waiting right now
  can be handed control: one that went off to think never blocks the others.
- After a handoff, if the other agent acted since this agent last read the
  screen, the first action is skipped with "look again" (like a take-over).
- The overlay ignores synthetic input from every registered Pinlet process
  (`~/.pinlet/control/agents/<pid>.json`), so two agents no longer pause each
  other. While queued, the overlay shows "Waiting for <agent>".
- The person still outranks everyone: take-over, Pause and Stop work as before.
  Turbo never skips the lease.

## What each session keeps to itself

In the shared Pinlet the engine is one process, so state that an agent relies
on between calls is kept per session: the Set-of-Marks ids of its last
screenshot (`click(mark_id=...)`), the element ids of its last
`get_screen_state` (`act_on_element`), its tool profile (`manage_toolsets`,
with `tools/list_changed` sent only to that client), its trajectory history,
and its Pause/Stop/inbox. The server log tags each tool call with
`[s<id> <name>]` and logs every handoff (`... handed off: control_busy`).

## Waking from the bar

Several agents can wait in `wait_for_wake` at once. A click on the sleeping bar
wakes one of them, the one that started waiting last; a Stop reaches all of
them. The bar stays asleep until the last one stops waiting. Wake an older one
from its row in the Pinlet menu.

## Pinlet picker (control bar ▾)

When more than one Pinlet runs, the ▾ menu on the control bar lists them under
"Pinlets", each with its state (this bar, in control, waiting, paused). Each
entry's submenu pauses or resumes, or stops, just that Pinlet; "All Pinlets"
pauses or resumes every one. Commands go through
`~/.pinlet/control/commands/<pid>.json`, which each Pinlet checks before every
action. A paused Pinlet lets go of a turn it held between steps, so the others
keep working, and looks again after Resume if they acted meanwhile. It gives up
after 10 minutes paused.

## Tools

`control_status`, `request_control(max_wait_s)`, `release_control`,
`hold_control(seconds)` (capped at 120 s). All are in the core profile.

## Working together

- `list_agents` shows who is connected. `send_to_agent(to, text)` sends a note
  that arrives with that agent's next tool call (or wakes it from
  `wait_for_wake`), marked as coming from an AI. `hand_control_to(to, note)`
  releases control and keeps it for that agent for 30 s.
- The shared task board: `post_task(title, detail, for_agent)`, `list_tasks`,
  `claim_task(id)` and `finish_task(id, result, failure)`. Failure codes are
  `needs_person`, `blocked`, `not_found` and `error`; `gave_up` puts the task
  back as open. The poster hears how it went with its next tool call. A task
  whose owner's process is gone opens again. The board is
  `~/.pinlet/control/tasks.json`; `pinlet tasks` prints it, and the activity
  panel shows open and claimed tasks.
- Messages and tasks come from another AI, never the person: they cannot answer
  Allow/Deny, change a permission level or grant an app.

## Configuration (per client, in its MCP server `env`)

| Variable | Default | Meaning |
| --- | --- | --- |
| `PINLET_AGENT_NAME` | MCP client name, else `Pinlet-<pid>` | Name shown to the other agent and as the overlay label |
| `PINLET_AGENT_PRIORITY` | 0 | Higher goes first in the queue |
| `PINLET_CONTROL` | on (off under pytest and replay) | `0` disables the lease |
| `PINLET_CONTROL_IDLE_GRACE` | 20 | Seconds idle before a waiter may take over (AI agents often think 10-20 s between calls) |
| `PINLET_CONTROL_SLICE` | 60 | Longest hold while someone waits |
| `PINLET_CONTROL_MAX_WAIT` | 45 | How long a tool waits before reporting who has control |
| `PINLET_CONTROL_HOLD_CAP` | 120 | Upper bound for `hold_control` |
| `PINLET_CONTROL_PREEMPT` | off | Priority preemption at step boundaries |
| `PINLET_CONTROL_DIR` | `~/.pinlet/control` | Lease and registry location |
