# Changelog

All notable changes to Pinlet are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). Pinlet is pre-1.0,
so a minor version may still change behaviour.

## [Unreleased]

The first public release will be 0.1.0. It contains:

### Added

- Rime can stand in for the floating bar (Settings > Companion > "Show progress with: Rime", the default once the companion is on). Rime shows the AI's step in a thought cloud, with the AI's optional `why` from action tools such as `click(why="to open the draft")`; a speech bubble points at the Allow / Deny card, a burst shows trouble and ice shows a pause. The first click on Rime while an AI works freezes it, with frost at the screen edges, and opens a ring: Resume, Let one step run, Cancel task (hold for a moment), Tell, Show what it sees (brackets on what the AI saw last) and Quieter cloud. Nothing on Rime approves anything. Rime steps aside when the AI's pointer heads for it, an ice thread joins it to the AI's pointer, small orbs stand for other AIs, its face hardens on a risky step, and it keeps a mission log of the last task's steps. Control-Option-P pauses every AI from anywhere. Optional: shake the pointer to call Rime over, tuck it behind a screen edge, and say "Rime, wait" to freeze (on-device speech, off by default). If the app stops answering, the bar comes back within two seconds.
- Linux (X11): Rime with `PINLET_COMPANION=1`: thought cloud, click to freeze, a menu with Resume, Let one step run and Cancel task, and it steps out of the AI's way. The bar stays on Wayland.
- "Let one step run" for a paused AI: the next action goes ahead and the one after it waits.

- macOS: keys are no longer refused as "Pinlet's own window" in other apps. The key guard read the frontmost app from a value that never updates in the engine, so after a restart that left Pinlet in front every key press and shortcut was refused.
- The bar stops saying Pinlet is working the moment the AI lets go of control. After Pinlet restarts in the middle of a task, the bar's timer and step count carry on. The glow now also appears around the window when a task only uses the keyboard.
- The bar can sit at the bottom of the screen, above the Dock, so it does not cover a window's title bar: Settings > Appearance > "Keep the bar at the bottom of the screen", or `PINLET_OVERLAY_PILL_POS=bottom`.
- When Pinlet quits in good order, a call it had already answered may be sent again after the restart. Only calls cut off by the restart, or answered in its last two seconds, are still held back once.
- An AI can now use Pinlet's Settings window: look at it in screenshots and
  change the Appearance, Companion, Notifications and Menu tabs. The General,
  Permissions, AI Tools and Diagnostics tabs, the Allow/Deny card, the bar
  and the menu stay the person's alone
  (`PINLET_SETTINGS_BY_AI=0` closes Settings again). Opening Pinlet again
  while it runs (Spotlight, `open -a Pinlet`) shows Settings.
- The goal an AI gives stays on the bar while it thinks between steps (it went away with the pointer after about half a minute, and on Linux after eight seconds), and one agent letting go of control no longer clears another agent's goal. It leaves after three minutes without a step or when the AI releases control. It also comes back when Pinlet restarts in the middle of a task (an update, for one).
- `pinlet://settings/permissions` (or `/appearance`, `/ai-tools`…) opens
  Settings on that tab, and Settings > Appearance can let screenshots and
  recordings show the bar.
- A companion, off by default (Settings > Companion > Show the companion, or
  `PINLET_COMPANION=1`): Rime, a small animated character in a corner of the
  screen that shows what Pinlet is doing (asleep, awake, thinking, working,
  asking, paused, done, in trouble, locked). Click it or press ⌃⌥Space to
  tell your AI something; drag it anywhere. It only mirrors state: Allow and
  Deny stay on the card, clicks pass through it while an AI works, and it is
  left out of screenshots and screen sharing unless Settings > Appearance says
  to show the bar and companion there. `Pinlet --companion-sheet out.png` draws every
  mood.
- Settings > Appearance: light or dark look, bar glass, one colour for every AI
  from macOS's palette, minimal text, and the click trail.
- Return in a chat, reply or comment box asks like the Send button does.
- The bar while an AI works has one button, the word **Pause**; paused, it
  offers **Resume** or **Cancel task**. Stop stays on ⌃⌥⎋ and in the menu.
- The bar leads with the AI's goal ("Pip: Reply to Sam · Click ‘Send’ · step 7
  · 0:42"), which the AI gives with `request_control(goal=…)`; hovering shows
  the goal and step in full. Pinlet's pointer now also appears, with a badge,
  for typing, key presses and scrolling.
- Pinlet no longer clicks payment buttons (Pay, Buy, Place order, Checkout,
  Subscribe and the like) in any mode, turbo included. The AI is told to hand
  the click to the person. `PINLET_BLOCK_PAYMENTS=0` lifts the rule.
- `pinlet browser`: starts a separate Chrome (or another Chromium browser) with
  its own profile in the background, so an AI in background mode has a browser
  that is not the person's.
- Background mode with apps that have several windows: the accessibility tree
  covers only the agent's window, a click that the app would give to another of
  its windows is refused, and picking a window asks Chromium and Electron apps
  for their page elements. Windows are told apart by the window server's id, so
  two windows of one app at the same place are not mixed, and Cmd+A works in
  web and Electron text fields. With "borrow the pointer when idle", a
  right-click keeps its menu open for up to 15 seconds so the AI can pick from
  it, then closes it and gives the front back.
- "Don't ask again" on the Allow card for a risky step. The tick covers one
  kind of step (send, delete, install, account, quit, close) in one app for
  one AI, for this task, an hour, today, or always (a month). "Allow, and run
  this task without asking" covers every kind until that task ends. Standing
  choices take Touch ID or the password. Rules live in `~/.pinlet/rules.json`,
  signed with a key the app keeps in the Keychain, so an edited file is
  ignored; each rule works at most 50 times a day (`PINLET_RULE_CEILING`).
  The card shows a picture of what will be clicked. Settings > Permissions
  lists every rule and always-allowed app, each with Remove, and "Ask About
  Everything Again". Passwords, payments, security prompts and Pinlet's own
  windows are never covered.
- Background mode on macOS (`PINLET_BACKGROUND=1`): the AI works in one window
  without taking the screen. `focus_window` picks the window without bringing
  it forward, screenshots show that window alone (even when it is covered or
  on another Desktop), clicks press, focus or select through accessibility,
  and typing goes straight to the window's app. The person's pointer, keyboard
  and front app are left alone, and their input is no longer a take-over.
  Steps that need the real pointer are refused, or with
  `PINLET_BACKGROUND_POINTER=idle` done while the person is idle.
- `scripts/install.sh`: installs Pinlet into a folder it owns
  (`~/Library/Application Support/Pinlet` or `~/.local/share/pinlet`) with uv,
  on Apple silicon Macs and Linux, and runs `pinlet setup`. Running it again
  updates Pinlet.
- `~/.pinlet/bin/pinlet`, one launcher every AI tool runs. It tries the Python
  it was written for, then Pinlet's own runtime, and otherwise says how to fix
  it, so a moved checkout no longer breaks AI tool configs.
- `pinlet doctor` (`--json`): checks Python, packages, the launcher, the model,
  the skills, the shared Pinlet and each AI tool, with a fix for each problem.
- `pinlet setup --repair`, `--remove` and `--json` (and `--list --json`).
- A missed Allow/Deny no longer turns into Deny after 30 s while Pinlet.app
  runs: the card hands it to the menu, where it waits with Allow and Deny for
  up to `PINLET_PROMPT_HOLD` seconds (default 300). "Let Pinlet use …?" cards
  and menu rows offer "Always allow in …", saved in `~/.pinlet/apps.json`.
- "Always allow in …" now takes Touch ID (or your password) in Pinlet.app, and
  the saved rule lapses after 30 days (`PINLET_ALWAYS_DAYS`; 0 keeps it), after
  which Pinlet asks about that app again.
- Off limits at every level, turbo included (`pinlet/overlay/offlimits.py`): an
  AI cannot click or type into Pinlet's own card, bar, menu, Activity window or
  the General, Permissions, AI Tools and Diagnostics tabs of Settings, or a
  macOS login, Touch ID or privacy prompt; Pinlet never types into a password
  field; and it stops at a payment button ("Your turn") instead of pressing it.
- Pinlet does nothing while the Mac's screen is locked.
- Human pace is the default (`PINLET_PACE=human`, Settings > General > Pace):
  at least 0.7 s between steps, the pointer rests on its target before a click
  and text is typed at a readable speed, so a person can follow and press
  Stop. `PINLET_PACE=fast` is the old behaviour.
- Only Pinlet.app can Allow, Resume or "tell" on the control socket: the engine
  checks the connecting process against the app's own process id. The
  `pinlet://resume`, `resume-all` and `toggle` links are gone (Pause and Stop
  links stay).
- The Allow/Deny card no longer takes the keyboard when it appears (click it
  first), holds Allow back for 0.6 s, and drops input injected through Pinlet.
- Pinlet.app: the menu bar icon breathes while a Pinlet works (not with Reduce
  Motion); optional finish sound and trackpad tap; VoiceOver hears when a
  Pinlet starts, needs you, pauses or is done.
- Pinlet.app installs its own engine: build.sh bundles the installer and the
  source, the Welcome window installs it on first launch and connects the AI
  tools found, and a newer bundled source updates the engine on launch. The
  app never starts a Python that is gone. Settings gains **AI Tools** and
  **Diagnostics** tabs. A main menu makes Cmd+, and copy/paste work, and
  Settings and Welcome open in front of the current app.
- `pinlet triage`: writes a debugging report (doctor, permission levels,
  policy, recent activity, redacted log tails, a playbook) to
  `~/.pinlet/logs/` and starts Claude Code, Codex, Gemini CLI or Cursor's
  `agent` on it. `--print` only prints it.
- Pinlet.app notifies when an AI finishes and waits in `wait_for_wake`
  ("Tibble is waiting for you", with the reason it gave), and when three tool
  calls in a row fail ("Tibble is having trouble"; Settings can turn it off).
- A shared task board for agents: `post_task`, `list_tasks`, `claim_task` and
  `finish_task` (with failure codes), `pinlet tasks`, and a Tasks section in
  the activity panel. The poster hears when a task is finished.

### Changed

- The default level is now "Ask for risky" (careful) instead of turbo: Send,
  Delete, Publish and risky shortcuts ask Allow/Deny, and Pinlet pauses when
  the person takes over. `--turbo`, `PINLET_MODE=turbo`, a per-client level, or
  Settings > Permissions > Ask before > Nothing (Touch ID) asks nothing again;
  `PINLET_DEFAULT_MODE` sets the default for every client.
- New look, "Frost": the app icon is a white frosted tile with a pearl pointer
  and a cyan star, and the Linux tray tile matches it. Buttons, the screen glow,
  the glass rim and the pointer move from violet to cool cyan (fill `#0E7490`,
  glow `#0EA5C6` / `#67E8F9` on dark). The menu bar icon keeps its shape.
- `pinlet setup` registers the launcher instead of a venv's own `pinlet` script.
- Several AI clients on one shared Pinlet work together smoothly:
  - The connector survives a restart of the shared Pinlet (Pinlet.app updated,
    relaunched or quit): it reconnects, replays the client's `initialize`, sends
    `tools/list_changed`, and answers a call that was running with "Pinlet
    restarted, look again" instead of hanging. A Pinlet that is quitting no
    longer pushes a client into a private Pinlet of its own.
  - Screenshot marks (`click(mark_id=...)`), `get_screen_state` element ids,
    tool profiles (`manage_toolsets`) and trajectory history are per session,
    so one agent's screenshot or profile change never answers another's call.
    `tools/list_changed` goes only to the client whose tools changed.
  - A click on the sleeping bar wakes one waiting agent (the latest), not all.
  - An unanswered Allow/Deny card no longer freezes the other agents for up to
    5 minutes: while it waits, they may use the desktop.
  - `control_busy` says who is using the screen, your place in line and about
    when they must hand over, and you keep your place for 120 s.
  - Idle grace before a waiting agent may take over: 20 s (was 10 s, shorter
    than AI think time between calls).
  - The menu shows each client's own name from the start, and the server log
    tags every call with its session and logs handoffs.

### Added

- `pinlet setup`: registers Pinlet with every AI tool it finds (Claude Code,
  Claude Desktop, Codex, Cursor, Gemini CLI, Antigravity, GitHub Copilot,
  OpenCode, VS Code) and links the agent skills into `~/.agents/skills` and
  `~/.claude/skills`. `--dry-run`, `--list`, `--only`, backups, idempotent.
- Agent guidance that works in any AI tool: skills renamed `pinlet` and
  `pinlet-*` (portable Agent Skills names), trigger-focused descriptions,
  fuller MCP server instructions, skills served as `mcp://pinlet/skills/<name>`
  resources, and a `pinlet` MCP prompt.
- MCP server for desktop automation on macOS and Linux (X11 / XWayland):
  accessibility tree (AX, AT-SPI), local OCR, an ONNX icon detector, numbered
  screen states, and click, type, scroll, hotkey and window tools with
  post-action verification.
- Local models through Ollama for yes/no checks and picks, with per-role model
  settings (`get_model_roles`, `set_model_role`) and a benchmark script.
- On-screen overlay: a second pointer, a glow around the working window, and a
  control bar with Pause and Stop; Liquid Glass on macOS 26 and later.
- Secure mode (`pinlet --secure`): per-app grants, Allow/Deny before Send,
  Delete and Pay, no typing into password fields. Turbo is the default.
- Input lock and background clicks, so the person's clicks stay out of the
  window Pinlet works in.
- One shared Pinlet per computer with a control lease, so several AI clients
  take turns; a native Pinlet.app menu bar shell on macOS and a tray icon on
  Linux.
- Admin policy file (`/Library/Application Support/Pinlet/policy.json`,
  `/etc/pinlet/policy.json`) that can force secure mode, turn tools off, allow
  only listed shell commands and set per-app tiers. See `docs/admin-policy.md`.
- App plugins and skills for Chrome, AMD Vivado and Synopsys Verdi, and
  recordable, replayable sessions (cassettes) for testing.
- Continuous integration on macOS and Ubuntu: tests, a dependency audit, and
  ruff lint and format checks.
- `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, `NOTICE`, issue and
  pull request templates, and docstrings on every public module, class and
  function.

### Changed (for people who used the pre-release)

- `run_terminal_command` is off by default. Set `PINLET_ALLOW_SHELL=1`, or allow
  it in the admin policy, to turn it on.
- `start_skill_recording` no longer looks for a journal in a built-in folder;
  pass `journal_path` or set `PINLET_JOURNAL_PATH`.
- The examples and the Vivado trajectory script save their output to
  `PINLET_ARTIFACT_DIR` (default `./artifacts`).
- `~/.pinlet` is created readable by you only, and the bundled ONNX detector is
  checked against a pinned hash before it loads.

[Unreleased]: https://github.com/mayurkubavat/Pinlet/commits/master
