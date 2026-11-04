# Dobby

Dobby lets an AI assistant use your desktop the way you do: it looks at the
screen, finds buttons and fields, clicks, types and checks that each step
worked. It runs as an [MCP](https://modelcontextprotocol.io) server, so any
MCP client (Claude Code, Claude Desktop, Antigravity, Cursor and others) can
drive it.

Dobby tries hard not to send screenshots to the cloud model. It reads the
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
  action Dobby waits for the screen to settle and can check the result with
  OCR or a local yes/no question (`ask_screen`) instead of a screenshot.
- **Local models, your choice.** Every model job has a role you can point at
  any Ollama model.
- **An on-screen overlay.** A second cursor, a glow around the window Dobby
  is working in, and a control bar with Pause and Stop, so you always see
  what it is doing and can take over.
- **Shared control.** Two AI clients can share one desktop and take turns.
- **App know-how.** Plugins and skills for Chrome, AMD Vivado and Synopsys
  Verdi; recordable, replayable sessions ("cassettes") for testing.

---

## Install

You need Python 3.10 or newer and git.

```bash
git clone https://github.com/mayurkubavat/Dobby.git
cd Dobby
python3 -m venv .venv
source .venv/bin/activate
```

### macOS

```bash
pip install -e ".[macos,ocr]"
```

Then open **System Settings > Privacy & Security** and give the app that
starts Dobby (Terminal, your IDE, Claude Desktop, Antigravity...) both
**Accessibility** and **Screen Recording**. Without them Dobby can neither
read the screen nor click.

### Linux (X11 or XWayland)

```bash
sudo apt install python3-gi python3-gi-cairo gir1.2-atspi-2.0 gir1.2-gtk-3.0 \
    gir1.2-ayatanaappindicator3-0.1 at-spi2-core x11-utils   # ayatana: Dobby's tray icon
python3 -m venv --system-site-packages .venv   # recreate the venv: PyGObject comes from the system
source .venv/bin/activate
pip install -e ".[linux,ocr]"
gsettings set org.gnome.desktop.interface toolkit-accessibility true
```

Native Wayland apps are not visible to Dobby yet. On a GNOME or KDE Wayland
desktop, log in to an "on Xorg" session or run apps with `GDK_BACKEND=x11`.
Chrome, VS Code and other Electron apps started before Dobby need a restart
to expose their accessibility tree. Details: [docs/linux.md](docs/linux.md).

### Check it

```bash
python scripts/smoke_local.py
```

This only reads: it reports screen capture, accessibility, OCR and the local
model, with timings. It never clicks or types.

When a tool fails in your AI client, look in `~/.dobby/logs/server.log`: one
line per tool call with its duration, plus the full traceback of any error.

---

## Local models (Ollama)

Dobby's checks run on a local model, so they cost no cloud tokens.

1. Install [Ollama](https://ollama.com) and start it.
2. Pull the default model:

   ```bash
   ollama pull gemma3:4b
   ```

That is all you need. Dobby talks to Ollama at `http://127.0.0.1:11434`.

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
   `DOBBY_MODEL_VISION=qwen2.5vl:7b`
2. At runtime, ask the AI to call `set_model_role("visual_pick", "qwen2.5vl:7b")`
3. A file at `~/.dobby/models.json`, re-read when it changes:

   ```json
   { "vision": "qwen2.5vl:7b", "visual_pick": "qwen3-vl:8b" }
   ```

Dobby never downloads models; `set_model_role` refuses a model you have not
pulled. To compare candidates on the same test cases:

```bash
python scripts/bench_models.py --models gemma3:4b,qwen2.5vl:7b
```

It reports accuracy, confident mistakes and whether the model admits "none"
when the target is not there.

---

## Connect your AI client

Every client starts its own Dobby. In each example, replace
`/path/to/Dobby` with where you cloned it.

### Claude Code

```bash
claude mcp add --scope user Dobby -- /path/to/Dobby/.venv/bin/dobby
```

Optional: install the Dobby skills, which teach the agent to read the screen
cheaply and verify each step:

```
/plugin marketplace add /path/to/Dobby
/plugin install dobby@dobby
```

### Claude Desktop

Edit the config file and restart Claude Desktop:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Linux: `~/.config/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "Dobby": {
      "command": "/path/to/Dobby/.venv/bin/dobby"
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
    "Dobby": {
      "command": "/path/to/Dobby/.venv/bin/dobby"
    }
  }
}
```

### Any other MCP client

Run `/path/to/Dobby/.venv/bin/dobby` as a stdio server. On Linux, add
`"env": { "DISPLAY": ":0" }` if the client does not pass your display.

### Fewer tools, smaller prompts

Dobby has 56 tools, grouped into toolsets. It starts with only the core
tools (`minimal_computer_use`) to keep the AI's prompt small. The AI can
switch with `manage_toolsets`, for example to `full_developer` (window
management, plugins, recording, advanced vision) or `eda_automation` (adds
Vivado and Verdi).

---

## The overlay

When Dobby acts you see it:

- a violet **agent cursor** that glides to each target and ripples on click,
  with a tag saying what it is doing ("Dobby › Click 'Run Synthesis'") until
  its next action, and numbered marks where it clicked that fade out;
- a soft **glow** around the window it is working in;
- a **control bar** at the top of the screen: "Dobby is sleeping" when idle,
  "Dobby is working · Click 'Send' · step 7 · 0:42" while working, with
  **Pause** and **Stop**. When two or more Dobbys are busy at once it also
  shows one coloured dot per Dobby ("2 Dobbys"); click them to pause or stop one.

**Turbo or secure.** By default Dobby runs in **turbo**: no permission
prompts, and it keeps going if you touch the mouse (Stop and Pause still
work). Start it with `dobby --secure` (or `DOBBY_MODE=secure` in the client's
`env`) to turn on everything below.

**In secure mode Dobby asks before it uses an app.** The first time it clicks or types in an
app it shows "Let Dobby use Vivado?" once; your answer lasts until Dobby's
server exits. Password managers start read-only, and Dobby never types into
a password field: the bar says "Your turn" instead. To skip the question for
apps you trust, or to limit an app, write `~/.dobby/apps.json`:

```json
{ "apps": { "Vivado": "full", "Terminal": "click", "Slack": "read" }, "default": "ask" }
```

`full` lets Dobby click and type, `click` allows only clicks and scrolling,
`read` only looking. `"default": "full"` never asks.

In secure mode, risky steps (quit, close, delete, log out, and clicking buttons such as
**Send**, **Delete**, **Pay** or **Publish**) turn red and ask you to
**Allow** or **Deny** first, and if you click, scroll or type while Dobby works,
it pauses and looks again before its next step (just moving the mouse, for
example to reach the bar, does not count).

The AI is told exactly what you did: each tool returns `handoff` =
`stopped`, `denied`, `blocked` (not allowed in that app), `interrupted` or
`paused` with what to do next, so it
ends the task on Stop, does not work around a Deny, and looks again after you
took over. A Stop ends the task it was pressed for; one pressed more than two
minutes before Dobby's next step is ignored. About half a minute after the
last step the agent pointer and the "thinking" glow go away.

| Shortcut | macOS | Linux |
| --- | --- | --- |
| Stop | Ctrl+Opt+Esc | Ctrl+Alt+Esc (Ctrl+Alt+End on GNOME, which owns Esc) |
| Pause / resume | Ctrl+Opt+P | Ctrl+Alt+P |
| Hide / show the bar | Ctrl+Opt+H | |

**Keep Dobby on call.** Ask the AI to call `wait_for_wake` when it is done.
The bar then says "click to wake"; clicking it hands control back to the AI.
If you paused Dobby and the AI is waiting, Resume hands back too.

The overlay is hidden from Dobby's own screenshots, so the model never sees
it. Settings go in the client's `env`:

| Variable | Effect |
| --- | --- |
| `DOBBY_OVERLAY=0` | Turn the overlay off |
| `DOBBY_OVERLAY_STYLE=classic` | Use the older, larger look |
| `DOBBY_OVERLAY_GLASS=regular` | On macOS 26 and later, use regular (frostier) Liquid Glass instead of clear; `0` keeps the older frosted bar |
| `DOBBY_OVERLAY_NARRATE=0` | Quieter modern look: no "Dobby is working" text, the tag shows each action only briefly, unnumbered trail dots, a dot for every running Dobby |
| `DOBBY_MODE=secure` | Same as `dobby --secure`: app grants, Allow/Deny prompts, no typing into password fields, pause on take-over. Default `turbo` |
| `DOBBY_OVERLAY_TURBO=0` | Keep turbo's no-prompt default but still pause when you take over |
| `DOBBY_APP_GRANTS=off` | Secure mode without the per-app question |
| `DOBBY_CONFIRM_CLICKS=0` | Secure mode without the Send/Delete/Pay prompt |
| `DOBBY_INPUT_LOCK=window` | Keep your clicks out of the window Dobby works in; buttons are then pressed through accessibility so your pointer stays put (macOS and Linux). `DOBBY_BACKGROUND_CLICKS=0` uses the real mouse instead |
| `DOBBY_OVERLAY_COLOR=#7c5cff` | Fix the colour (otherwise each Dobby gets its own) |
| `DOBBY_OVERLAY_CAPTURE_VISIBLE=0` | Hide the overlay from your screen recordings too |
| `DOBBY_PERSONALITY=elf` | Dobby's house-elf voice on the bar: rotating lines ("Dobby is on it!", "Dobby is thinking hard…"), a cheer when a task ends, rare surprises. `subtle` (default) uses a few fixed words; `off` keeps the plain text |
| `DOBBY_USER_NAME=Sam` | Dobby calls you by name instead of "sir" |
| `DOBBY_PERSONALITY_LINES=~/dobby-lines.json` | Your own lines: `{"working": ["Dobby is on it!"], "done": [...]}` replaces that moment's lines |

Dobby's lines only reach the bar and the startup log. What the AI reads back
from tools, the pointer tag, Allow/Deny prompts and "You're in control" stay
plain, and a new line shows at most every 20 seconds.

On Linux the trail of recent clicks, the scan sweep and take-over detection
are not drawn yet.

---

## Two AIs, one desktop

### One shared Dobby

Every AI client runs the same `dobby` command, but there is only one Dobby
per computer. The first client starts `dobby serve` in the background (it
loads the models once); later clients connect to it over a private socket
in a few milliseconds. Each client keeps its own mode (turbo or secure),
its own name and its own turn at the mouse. The shared Dobby exits 10
minutes after the last client leaves.

After updating from an older checkout, run `pip install -e .` again so the
`dobby` command points at the new connector.

| Variable | Effect |
| --- | --- |
| `DOBBY_SHARED=0` | Run a private Dobby inside this client (the old behaviour) |
| `DOBBY_IDLE_EXIT=600` | Seconds the shared Dobby waits with no clients before exiting (`0` = never) |
| `DOBBY_SOCKET=/path/dobby.sock` | Socket location. Default `~/Library/Application Support/Dobby/dobby.sock` on macOS, `$XDG_RUNTIME_DIR/dobby/dobby.sock` on Linux |

`dobby serve` starts the shared Dobby by hand (its log is
`~/.dobby/logs/daemon.log`), and `dobby stdio` runs a private one.
`control_status` lists every connected client.

### Dobby.app (macOS)

`apps/macos` is a small native Swift app that owns the shared Dobby. Build
and install it with:

```bash
apps/macos/build.sh --install   # needs the Xcode command line tools
```

Once it is installed, `dobby` opens Dobby.app instead of starting the engine
itself. The app starts `dobby serve` as its own child, so macOS asks for
Screen Recording and Accessibility for **Dobby** once, whichever AI client
or terminal you use. Its menu bar icon lists each connected AI with Pause,
Resume and Stop, and has Pause / Stop all, permission shortcuts, Open at
Login, Show Log, Restart and Quit. Links such as `dobby://pause-all`,
`dobby://stop-all` or `dobby://stop?pid=4242` do the same from Shortcuts or a
script. `DOBBY_APP=0` skips the app; `DOBBY_APP=/path/Dobby.app` picks one.
Run `apps/macos/build.sh --setup-signing` once before your first build: it
makes a local "Dobby Local Signing" certificate, so Screen Recording and
Accessibility stay granted across rebuilds (an ad-hoc build loses them each
time). `DOBBY_SIGN_IDENTITY` picks another identity, such as a Developer ID.

### Taking turns

If you run Dobby from two clients at once (say Claude Code and Antigravity),
they take turns instead of fighting over the mouse. A whole tool call is one
turn, so nobody cuts in between a click and the typing after it. Control
passes to the waiting agent when the holder releases it, after 10 seconds
idle, or after a 60 second slice.

When more than one Dobby runs, the **▾** menu on the control bar lists them,
so you can pause or stop each one. You always outrank both: Pause, Stop and
taking over the mouse still work.

There is one bar per busy Dobby. If two are working (or one is asking
Allow/Deny), their bars stack one row apart at the top of the screen, each in
its own colour, and the "sleeping" bar steps aside until they are done.

Useful settings, per client:

| Variable | Effect |
| --- | --- |
| `DOBBY_AGENT_NAME=Antigravity` | Name shown on the overlay and to the other agent |
| `DOBBY_NAMES=Ada,Grace` | First names handed out to each running Dobby (default: made-up elf names, shown as "Tibble · Claude Code"; also `~/.dobby/names.txt`, one per line; `off` = `Dobby-<pid>`) |
| `DOBBY_AGENT_PRIORITY=1` | Higher goes first when both are waiting |
| `DOBBY_CONTROL=0` | Turn shared control off |

More: [docs/shared-control.md](docs/shared-control.md).

---

## Learn more

- [docs/tools.md](docs/tools.md): every tool with its parameters
- [docs/architecture.md](docs/architecture.md): how the pieces fit
- [docs/linux.md](docs/linux.md): Linux details, XWayland, a headless test rig
- [docs/shared-control.md](docs/shared-control.md): the control lease in depth
- [plugins/dobby/README.md](plugins/dobby/README.md): the agent skills

## Development

```bash
pip install -e ".[dev]"
pytest tests/
pytest -m replay          # replay recorded sessions offline
```

The test suite never sends real mouse or keyboard input unless you set
`DOBBY_LIVE_DESKTOP_TESTS=1`.

## License

Apache 2.0. See [LICENSE](LICENSE).
