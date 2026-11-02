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
    at-spi2-core x11-utils
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

Risky steps (quit, close, delete, log out) turn amber or red and ask you to
**Allow** or **Deny** first. If you move the mouse or type, Dobby pauses and
looks again before its next step.

| Shortcut | macOS | Linux |
| --- | --- | --- |
| Stop | Ctrl+Opt+Esc | Ctrl+Alt+Esc |
| Pause / resume | Ctrl+Opt+P | Ctrl+Alt+P |
| Hide / show the bar | Ctrl+Opt+H | |

**Keep Dobby on call.** Ask the AI to call `wait_for_wake` when it is done.
The bar then says "click to wake"; clicking it hands control back to the AI.

The overlay is hidden from Dobby's own screenshots, so the model never sees
it. Settings go in the client's `env`:

| Variable | Effect |
| --- | --- |
| `DOBBY_OVERLAY=0` | Turn the overlay off |
| `DOBBY_OVERLAY_STYLE=classic` | Use the older, larger look |
| `DOBBY_OVERLAY_NARRATE=0` | Quieter modern look: no "Dobby is working" text, the tag shows each action only briefly, unnumbered trail dots, a dot for every running Dobby |
| `DOBBY_OVERLAY_TURBO=1` | No Allow/Deny prompts and no pause on take-over (Stop and Pause still work) |
| `DOBBY_OVERLAY_COLOR=#7c5cff` | Fix the colour (otherwise each Dobby gets its own) |
| `DOBBY_OVERLAY_CAPTURE_VISIBLE=0` | Hide the overlay from your screen recordings too |

On Linux the trail of recent clicks, the scan sweep and take-over detection
are not drawn yet.

---

## Two AIs, one desktop

If you run Dobby from two clients at once (say Claude Code and Antigravity),
they take turns instead of fighting over the mouse. A whole tool call is one
turn, so nobody cuts in between a click and the typing after it. Control
passes to the waiting agent when the holder releases it, after 10 seconds
idle, or after a 60 second slice.

When more than one Dobby runs, the **▾** menu on the control bar lists them,
so you can pause or stop each one. You always outrank both: Pause, Stop and
taking over the mouse still work.

Useful settings, per client:

| Variable | Effect |
| --- | --- |
| `DOBBY_AGENT_NAME=Antigravity` | Name shown on the overlay and to the other agent |
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
