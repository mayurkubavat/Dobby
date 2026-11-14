# Pinlet

Pinlet lets an AI assistant use your desktop the way you do: it looks at the
screen, finds buttons and fields, clicks, types and checks that each step
worked. It runs as an [MCP](https://modelcontextprotocol.io) server, so any
MCP client (Claude Code, Claude Desktop, Antigravity, Cursor and others) can
drive it.

Pinlet tries hard not to send screenshots to the cloud model. It reads the
operating system's accessibility tree and local OCR first, and asks a small
local model (Gemma 3 through Ollama by default) for yes/no checks and picks.
A screenshot is the last resort, not the first step.

Works on **macOS** and **Linux (X11 / XWayland)**. Windows input works;
Windows window listing is not finished yet.

---

## What you get

- **See the screen cheaply.** Accessibility tree (macOS AX, Linux AT-SPI),
  local OCR (Apple Vision or RapidOCR), a small icon detector, and a numbered
  "screen state" the AI can pick from by number.
- **Act and verify.** Click, type, scroll, hotkeys, window focus. After each
  action Pinlet waits for the screen to settle and can check the result with
  OCR or a local yes/no question (`ask_screen`) instead of a screenshot.
- **Local models, your choice.** Every model job has a role you can point at
  any Ollama model.
- **An on-screen overlay.** A second cursor, a glow around the window Pinlet
  is working in, and a control bar with Pause and Stop, so you always see
  what it is doing and can take over.
- **Shared control.** Two AI clients can share one desktop and take turns.
- **App know-how.** Plugins and skills for Chrome, AMD Vivado and Synopsys
  Verdi; recordable, replayable sessions ("cassettes") for testing.

---

## Install

Pinlet installs itself into a folder it owns, so it keeps working when you
move or delete a checkout. Apple silicon Macs and Linux (x86_64, arm64):

```bash
curl -fsSL https://raw.githubusercontent.com/mayurkubavat/Pinlet/master/scripts/install.sh | sh
```

The installer gets [uv](https://docs.astral.sh/uv/) if you do not have it, a
Python, and Pinlet's packages, then runs `pinlet setup` to connect your AI
tools. Everything goes into `~/Library/Application Support/Pinlet` (macOS) or
`~/.local/share/pinlet` (Linux), and every AI tool runs one command,
`~/.pinlet/bin/pinlet` (Linux also links `~/.local/bin/pinlet`). Run it again
to update. From a checkout, `sh scripts/install.sh` installs a copy of that
checkout; `--dev` installs the checkout itself, editable. On Linux, install
the system packages listed below first.

`pinlet doctor` checks everything Pinlet needs and prints a fix for anything
missing. `pinlet setup --repair` points AI tools whose Pinlet command moved
back at the launcher, and `pinlet setup --remove` takes Pinlet out of them.

### From a checkout (for working on Pinlet)

You need Python 3.10 or newer and git.

```bash
git clone https://github.com/mayurkubavat/Pinlet.git
cd Pinlet
python3 -m venv .venv
source .venv/bin/activate
```

#### macOS

```bash
pip install -e ".[macos,ocr]"
```

Then open **System Settings > Privacy & Security** and give the app that
starts Pinlet (Terminal, your IDE, Claude Desktop, Antigravity...) both
**Accessibility** and **Screen Recording**. Without them Pinlet can neither
read the screen nor click.

#### Linux (X11 or XWayland)

```bash
sudo apt install python3-gi python3-gi-cairo gir1.2-atspi-2.0 gir1.2-gtk-3.0 \
    gir1.2-ayatanaappindicator3-0.1 at-spi2-core x11-utils   # ayatana: Pinlet's tray icon
python3 -m venv --system-site-packages .venv   # recreate the venv: PyGObject comes from the system
source .venv/bin/activate
pip install -e ".[linux,ocr]"
gsettings set org.gnome.desktop.interface toolkit-accessibility true
```

Native Wayland apps are not visible to Pinlet yet. On a GNOME or KDE Wayland
desktop, log in to an "on Xorg" session or run apps with `GDK_BACKEND=x11`.
Chrome, VS Code and other Electron apps started before Pinlet need a restart
to expose their accessibility tree. Details: [docs/linux.md](docs/linux.md).

### Check it

```bash
pinlet doctor                    # what is installed, what is missing, how to fix it
python scripts/smoke_local.py    # from a checkout: screen capture, accessibility, OCR, timings
```

This only reads: it reports screen capture, accessibility, OCR and the local
model, with timings. It never clicks or types.

When a tool fails in your AI client, look in `~/.pinlet/logs/server.log`: one
line per tool call with its duration, plus the full traceback of any error.

---

## Local models (Ollama)

Pinlet's checks run on a local model, so they cost no cloud tokens.

1. Install [Ollama](https://ollama.com) and start it.
2. Pull the default model:

   ```bash
   ollama pull gemma3:4b
   ```

That is all you need. Pinlet talks to Ollama at `http://127.0.0.1:11434`.

### Model roles

Each kind of job has its own role, and each role can use a different model.
All four default to `gemma3:4b`.

| Role | What it does |
| --- | --- |
| `text` | Picks the right element from the numbered screen state |
| `vision` | Answers yes/no questions about a part of the screen |
| `visual_pick` | Picks a lettered box on a screenshot |
| `triage` | Reads dialog text and decides if it is safe to dismiss |

To change a role, use any of these (the first one found wins):

1. An environment variable in your MCP client config:
   `PINLET_MODEL_VISION=qwen2.5vl:7b`
2. At runtime, ask the AI to call `set_model_role("visual_pick", "qwen2.5vl:7b")`
3. A file at `~/.pinlet/models.json`, re-read when it changes:

   ```json
   { "vision": "qwen2.5vl:7b", "visual_pick": "qwen3-vl:8b" }
   ```

Pinlet never downloads models; `set_model_role` refuses a model you have not
pulled. To compare candidates on the same test cases:

```bash
python scripts/bench_models.py --models gemma3:4b,qwen2.5vl:7b
```

It reports accuracy, confident mistakes and whether the model admits "none"
when the target is not there.

---

## Connect your AI client

One command sets Pinlet up for every AI tool on this computer:

```bash
pinlet setup --dry-run   # show what it would change
pinlet setup             # do it
```

It finds Claude Code, Claude Desktop, Codex, Cursor, Gemini CLI, Antigravity,
GitHub Copilot (CLI and VS Code) and OpenCode, adds Pinlet to each one's MCP
config (other servers are kept, each file is backed up once as
`<file>.pinlet-backup`, an existing Pinlet entry is left alone unless `--force`),
points it at the stable launcher `~/.pinlet/bin/pinlet` (rewritten for the
Python that runs setup), and links the Pinlet skills into the folders those tools read skills from:
`~/.agents/skills` (shared by Codex, Cursor, Gemini CLI, Copilot, OpenCode and
Antigravity) and `~/.claude/skills`. `pinlet setup --list` shows every tool and
its state; `--only cursor,codex`, `--no-mcp` and `--no-skills` narrow it.
Restart each tool afterwards.

How agents learn to use Pinlet, whatever the tool:

- **Server instructions.** Every MCP client receives Pinlet's short guide when
  it connects: when to use the desktop, the cheapest way to look, handoffs and
  safety.
- **Agent Skills.** The `pinlet` skill (plus `pinlet-perception`,
  `pinlet-app-chrome`, `pinlet-app-vivado`...) loads automatically when a task
  needs the desktop, in any tool that supports `SKILL.md` skills.
- **MCP resources and a prompt.** Clients without skills can read the same
  guides as `mcp://pinlet/skills/<name>`, or start a task with the `pinlet` MCP
  prompt (a slash command in clients that offer prompts).

The sections below are the manual way, per client. Replace `/path/to/Pinlet`
with where you cloned it.

### Claude Code

```bash
claude mcp add --scope user Pinlet -- /path/to/Pinlet/.venv/bin/pinlet
```

Skills: `pinlet setup --only claude-code` links them into `~/.claude/skills`,
or install them as a plugin instead:

```
/plugin marketplace add /path/to/Pinlet
/plugin install pinlet@pinlet
```

### Claude Desktop

Edit the config file and restart Claude Desktop:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Linux: `~/.config/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "Pinlet": {
      "command": "/path/to/Pinlet/.venv/bin/pinlet"
    }
  }
}
```

### Antigravity

Open the MCP settings in Antigravity (or edit
`~/.gemini/antigravity/mcp_config.json`) and add:

```json
{
  "mcpServers": {
    "Pinlet": {
      "command": "/path/to/Pinlet/.venv/bin/pinlet"
    }
  }
}
```

### Codex, Cursor, Gemini CLI, Copilot, OpenCode

`pinlet setup` writes these; by hand the entries are:

- Codex, `~/.codex/config.toml`: `[mcp_servers.Pinlet]` with
  `command = "/path/to/Pinlet/.venv/bin/pinlet"`
- Cursor, `~/.cursor/mcp.json`, and Gemini CLI, `~/.gemini/settings.json`:
  the same `mcpServers` entry as Claude Desktop above
- GitHub Copilot CLI, `~/.copilot/mcp-config.json`: `mcpServers` entry with
  `"type": "local"` and `"tools": ["*"]`
- OpenCode, `~/.config/opencode/opencode.json`: under `mcp`,
  `{"type": "local", "command": ["/path/to/Pinlet/.venv/bin/pinlet"]}`

For skills, link `plugins/pinlet/skills/*` into `~/.agents/skills/`. Inside this
repo they are already visible to agents through `.agents/skills`.

### Any other MCP client

Run `/path/to/Pinlet/.venv/bin/pinlet` as a stdio server. On Linux, add
`"env": { "DISPLAY": ":0" }` if the client does not pass your display.

### Fewer tools, smaller prompts

Pinlet has 56 tools, grouped into toolsets. It starts with only the core
tools (`minimal_computer_use`) to keep the AI's prompt small. The AI can
switch with `manage_toolsets`, for example to `full_developer` (window
management, plugins, recording, advanced vision) or `eda_automation` (adds
Vivado and Verdi).

---

## The overlay

When Pinlet acts you see it:

- a cyan **agent cursor** that glides to each target and ripples on click,
  with a tag saying what it is doing ("Pinlet › Click 'Run Synthesis'") until
  its next action, and numbered marks where it clicked that fade out;
- a soft **glow** around the window it is working in;
- a **control bar** at the top of the screen while Pinlet works, asks or is
  paused (out of sight while it rests; `PINLET_OVERLAY_PILL=always` keeps a
  small "Pinlet is sleeping" bar): "Pinlet is working · Click 'Send' · step 7 · 0:42", with
  **Pause** and **Stop**. When two or more Pinlets are busy at once it also
  shows one coloured dot per Pinlet ("2 Pinlets"); click them to pause or stop one.

**How much Pinlet asks.** By default Pinlet **asks before risky steps**
(careful): Send, Delete, Publish, quit and the like show Allow/Deny, and it
pauses when you take over. `pinlet --turbo`, `PINLET_MODE=turbo`, or
Settings > Permissions > Ask before > Nothing turns the prompts off
(`PINLET_DEFAULT_MODE` sets the default for every client). Three things hold in every mode: an AI cannot press Pinlet's own
buttons or answer its questions, Pinlet never types into a password field or
answers a macOS security prompt, and it stops at a payment button and hands
that click to you. Start it with `pinlet --secure` (or `PINLET_MODE=secure` in the client's
`env`) to also ask once per app, as described next.

**In secure mode Pinlet asks before it uses an app.** The first time it clicks or types in an
app it shows "Let Pinlet use Vivado?" once; your answer lasts until Pinlet's
server exits. Password managers start read-only, and Pinlet never types into
a password field: the bar says "Your turn" instead. To skip the question for
apps you trust, or to limit an app, write `~/.pinlet/apps.json`:

```json
{ "apps": { "Vivado": "full", "Terminal": "click", "Slack": "read" }, "default": "ask" }
```

`full` lets Pinlet click and type, `click` allows only clicks and scrolling,
`read` only looking. `"default": "full"` never asks.

Ticking **Always allow in …** on the question saves the app there for you. In
Pinlet.app that takes Touch ID or your password, and the saved rule lapses
after 30 days (`PINLET_ALWAYS_DAYS`), so Pinlet asks again. Rules you write
by hand do not lapse.

When a risky step asks (see below), the card also offers **Don't ask again**
for that kind of step in that app, for that AI: for this task, an hour, today,
or always (a month, with Touch ID). Settings > Permissions lists what you have
saved and takes any of it back. Saved rules never cover passwords, payments,
macOS security prompts or Pinlet's own windows.

In secure mode, risky steps (quit, close, delete, log out, and clicking buttons such as
**Send**, **Delete**, **Pay** or **Publish**) turn red and ask you to
**Allow** or **Deny** first, and if you click, scroll or type while Pinlet works,
it pauses and looks again before its next step (just moving the mouse, for
example to reach the bar, does not count).

The AI is told exactly what you did: each tool returns `handoff` =
`stopped`, `denied`, `blocked` (not allowed in that app), `interrupted` or
`paused` with what to do next, so it
ends the task on Stop, does not work around a Deny, and looks again after you
took over. A Stop ends the task it was pressed for; one pressed more than two
minutes before Pinlet's next step is ignored. About half a minute after the
last step the agent pointer and the "thinking" glow go away.

| Shortcut | macOS | Linux |
| --- | --- | --- |
| Stop | Ctrl+Opt+Esc | Ctrl+Alt+Esc (Ctrl+Alt+End on GNOME, which owns Esc) |
| Pause / resume | Ctrl+Opt+P | Ctrl+Alt+P |
| Hide / show the bar | Ctrl+Opt+H | |

**Keep Pinlet on call.** Ask the AI to call `wait_for_wake` when it is done.
The menu bar item then lists that Pinlet as "Waiting for you" with **Wake**,
which hands control back to the AI (with `PINLET_OVERLAY_PILL=always` the bar
says "click to wake" too). If you paused Pinlet and the AI is waiting, Resume
hands back too.

**Tell Pinlet what to do while it works.** Anything you type in Pinlet.app's
activity panel (or send with the `tell` control op) reaches the AI with its
next tool result as `person_says`, and wakes a Pinlet that waits in
`wait_for_wake`.

The overlay is hidden from Pinlet's own screenshots, so the model never sees
it. Settings go in the client's `env`:

| Variable | Effect |
| --- | --- |
| `PINLET_OVERLAY=0` | Turn the overlay off |
| `PINLET_OVERLAY_PILL=always` | Keep the small "Pinlet is sleeping" bar on screen while Pinlet rests (default `auto`: the bar shows only while Pinlet works, asks or is paused; `off`: never) |
| `PINLET_OVERLAY_PILL_POS=bottom` | macOS: keep the bar at the bottom of the screen, above the Dock, so it does not cover a window's title bar (default: under the menu bar) |
| `PINLET_OVERLAY_STYLE=classic` | Use the older, larger look |
| `PINLET_OVERLAY_THEME=light` | The light bar and prompts instead of the default dark look; `auto` follows the desktop's light or dark mode |
| `PINLET_OVERLAY_GLASS=clear` | On macOS 26 and later, pin clear Liquid Glass (the default is clear while the bar sleeps and regular once it has text); `regular` pins regular; `0` keeps the older frosted bar |
| `PINLET_OVERLAY_NARRATE=0` | Quieter modern look: no "Pinlet is working" text, the tag shows each action only briefly, unnumbered trail dots, a dot for every running Pinlet |
| `PINLET_MODE=secure` | Same as `pinlet --secure`: app grants, Allow/Deny prompts, no typing into password fields, pause on take-over. Default `careful` (asks before risky steps); `turbo` asks nothing |
| `PINLET_OVERLAY_TURBO=0` | Keep turbo's no-prompt default but still pause when you take over |
| `PINLET_APP_GRANTS=off` | Secure mode without the per-app question |
| `PINLET_CONFIRM_CLICKS=0` | Secure mode without the Send/Delete/Pay prompt |
| `PINLET_BLOCK_PAYMENTS=0` | Pinlet never clicks a button that pays (Pay, Buy, Place order, Checkout, Subscribe…) in any mode; it tells the AI to hand that click to you. `0` lifts the rule |
| `PINLET_ALLOW_SHELL=1` | Let the AI run shell commands (`run_terminal_command`). Off by default |
| `PINLET_INPUT_LOCK=window` | Keep your clicks out of the window Pinlet works in; buttons are then pressed through accessibility so your pointer stays put (macOS and Linux). `PINLET_BACKGROUND_CLICKS=0` uses the real mouse instead |
| `PINLET_BACKGROUND=1` | Background mode (macOS): keep using your Mac while the AI works. The AI picks one window, which may be covered or on another Desktop, and sees, clicks and types into that window only, through accessibility. Your pointer, keyboard and front app are left alone, and your own clicks no longer pause it. Switch it on in Settings > General ("Work in the background") or with this variable. Put the apps the AI uses on a second Desktop (Dock icon > Options > Assign To) and give it a browser of its own with `pinlet browser` (a separate Chrome with its own profile, started in the background) |
| `PINLET_BACKGROUND_POINTER=idle` | In background mode, a step that needs the window in front (a drag, a right-click, a click on a canvas, most Cmd shortcuts) is refused by default. With `idle`, Pinlet waits until you have not touched the Mac for 3 seconds, does the step, then puts your app and pointer back |
| `PINLET_OVERLAY_COLOR=#0ea5c6` | Fix the colour (otherwise each Pinlet gets its own) |
| `PINLET_OVERLAY_CAPTURE_VISIBLE=0` | Hide the overlay from your screen recordings too |
| `PINLET_PERSONALITY=lively` | Pinlet's lively voice on the bar: rotating lines ("Pinlet is on it!", "Pinlet is thinking hard…"), a cheer when a task ends, rare surprises. `subtle` (default) uses a few fixed words; `off` keeps the plain text |
| `PINLET_USER_NAME=Sam` | Pinlet calls you by name instead of "sir" |
| `PINLET_PERSONALITY_LINES=~/pinlet-lines.json` | Your own lines: `{"working": ["Pinlet is on it!"], "done": [...]}` replaces that moment's lines |

Pinlet's lines only reach the bar and the startup log. What the AI reads back
from tools, the pointer tag, Allow/Deny prompts and "You're in control" stay
plain, and a new line shows at most every 20 seconds.

On Linux the trail of recent clicks, the scan sweep and take-over detection
are not drawn yet.

### For organisations: admin policy

An administrator can lock Pinlet down for everyone on a computer with one
file that people cannot override: `/Library/Application Support/Pinlet/policy.json`
(macOS) or `/etc/pinlet/policy.json` (Linux). It can force secure mode, turn
tools off, allow only listed shell commands, and set what Pinlet may do in each
app. Details and an example: [docs/admin-policy.md](docs/admin-policy.md).

---

## Two AIs, one desktop

### One shared Pinlet

Every AI client runs the same `pinlet` command, but there is only one Pinlet
per computer. The first client starts `pinlet serve` in the background (it
loads the models once); later clients connect to it over a private socket
in a few milliseconds. Each client keeps its own mode (turbo or secure),
its own name and its own turn at the mouse. The shared Pinlet exits 10
minutes after the last client leaves.

After updating from an older checkout, run `pip install -e .` again so the
`pinlet` command points at the new connector.

| Variable | Effect |
| --- | --- |
| `PINLET_SHARED=0` | Run a private Pinlet inside this client (the old behaviour) |
| `PINLET_IDLE_EXIT=600` | Seconds the shared Pinlet waits with no clients before exiting (`0` = never) |
| `PINLET_SOCKET=/path/pinlet.sock` | Socket location. Default `~/Library/Application Support/Pinlet/pinlet.sock` on macOS, `$XDG_RUNTIME_DIR/pinlet/pinlet.sock` on Linux |

`pinlet serve` starts the shared Pinlet by hand (its log is
`~/.pinlet/logs/daemon.log`), and `pinlet stdio` runs a private one.
`control_status` lists every connected client.

### Pinlet.app (macOS)

`apps/macos` is a small native Swift app that owns the shared Pinlet. Build
and install it with:

```bash
apps/macos/build.sh --install   # needs the Xcode command line tools
```

The app carries Pinlet's installer and source. On first launch its Welcome
window installs the engine into `~/Library/Application Support/Pinlet` (no
venv in the checkout needed), asks for the two permissions, and connects the
AI tools it finds. When a rebuilt app carries newer source, it updates the
engine on launch. It starts `PINLET_PYTHON` if set (for working on Pinlet from a
checkout), else its own runtime, else, before that is installed, the Python
the last AI client used (`~/.pinlet/app.json`) while it still exists, and says
"not installed" with an Install item instead of failing on a moved checkout.
An engine left running by an earlier Pinlet.app is replaced, and the engine
quits when its app goes away. Settings has an **AI Tools** tab (Connect, Remove, Repair, and a
config to paste into other tools) and **Diagnostics** (`pinlet doctor`, Copy
Report, Reinstall Engine).

Once it is installed, `pinlet` opens Pinlet.app instead of starting the engine
itself. The app starts `pinlet serve` as its own child, so macOS asks for
Screen Recording and Accessibility for **Pinlet** once, whichever AI client
or terminal you use. Its menu bar icon lists each connected AI with Pause,
Resume and Stop, and has Pause / Resume / Stop all, Clear finished, permission
shortcuts (only while one is missing), Settings, Troubleshoot (permissions,
log, relaunch) and Quit. Stopped Pinlets leave the list, and sleeping ones hide
after 15 minutes (`PINLET_MENU_HIDE_IDLE`, in seconds). Allow/Deny questions
show as a card with Return / Esc, and as a notification if you miss it.
**Activity…** (or Control-Option-Space anywhere) opens a Liquid Glass panel
with what each Pinlet is doing, its recent steps, Wake / Pause / Stop, and a
field to tell it what to do: Return sends the note, which reaches the AI at
its next step (and wakes a Pinlet waiting for you).
Spotlight and Shortcuts get Pause, Resume and Stop all Pinlets, "What is
Pinlet doing", "Tell Pinlet" and "Show What Pinlet Is Doing" (when built with XcodeGen: `brew install xcodegen`). Links such as `pinlet://pause-all`,
`pinlet://stop-all` or `pinlet://stop?pid=4242` do the same from Shortcuts or a
script. `PINLET_APP=0` skips the app; `PINLET_APP=/path/Pinlet.app` picks one.
Run `apps/macos/build.sh --setup-signing` once before your first build: it
makes a local "Pinlet Local Signing" certificate, so Screen Recording and
Accessibility stay granted across rebuilds (an ad-hoc build loses them each
time). `PINLET_SIGN_IDENTITY` picks another identity, such as a Developer ID.

### Taking turns

If you run Pinlet from two clients at once (say Claude Code and Antigravity),
they take turns instead of fighting over the mouse. A whole tool call is one
turn, so nobody cuts in between a click and the typing after it. Control
passes to the waiting agent when the holder releases it, after 10 seconds
idle, or after a 60 second slice.

When more than one Pinlet runs, the **▾** menu on the control bar lists them,
so you can pause or stop each one. You always outrank both: Pause, Stop and
taking over the mouse still work.

There is one bar per busy Pinlet. If two are working (or one is asking
Allow/Deny), their bars stack one row apart at the top of the screen, each in
its own colour, and the "sleeping" bar steps aside until they are done.

Useful settings, per client:

| Variable | Effect |
| --- | --- |
| `PINLET_AGENT_NAME=Antigravity` | Name shown on the overlay and to the other agent |
| `PINLET_NAMES=Ada,Grace` | First names handed out to each running Pinlet (default: made-up names, shown as "Tibble · Claude Code"; also `~/.pinlet/names.txt`, one per line; `off` = `Pinlet-<pid>`) |
| `PINLET_AGENT_PRIORITY=1` | Higher goes first when both are waiting |
| `PINLET_CONTROL=0` | Turn shared control off |

More: [docs/shared-control.md](docs/shared-control.md).

---

## Learn more

- [docs/tools.md](docs/tools.md): every tool with its parameters
- [docs/architecture.md](docs/architecture.md): how the pieces fit
- [docs/linux.md](docs/linux.md): Linux details, XWayland, a headless test rig
- [docs/shared-control.md](docs/shared-control.md): the control lease in depth
- [docs/admin-policy.md](docs/admin-policy.md): the organisation-wide policy file
- [plugins/pinlet/README.md](plugins/pinlet/README.md): the agent skills and how each AI tool finds them

## Development

```bash
pip install -e ".[dev]"
ruff check . && ruff format --check .
pytest tests/
pytest -m replay          # replay recorded sessions offline
```

The test suite never sends real mouse or keyboard input unless you set
`PINLET_LIVE_DESKTOP_TESTS=1`. See [CONTRIBUTING.md](CONTRIBUTING.md) for code
style and pull request rules, [SECURITY.md](SECURITY.md) to report a
vulnerability, and [CHANGELOG.md](CHANGELOG.md) for what changed.

## License

Apache 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
