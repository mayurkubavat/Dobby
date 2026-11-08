# Dobby on Linux (X11)

Dobby runs on Linux under an **X11 or XWayland** session. Native Wayland
(no `DISPLAY`) is not supported yet: the server starts with a fail-closed
driver that explains what is missing instead of acting.

| Piece | Linux implementation |
| --- | --- |
| Capture | mss (XShm) |
| Mouse | pynput (XTest) |
| Keyboard | XTest via python-xlib; characters missing from the layout (é, →, 日) are bound to a spare keycode per keystroke |
| Windows | EWMH: `_NET_CLIENT_LIST_STACKING` (front to back), `_NET_ACTIVE_WINDOW`, `_NET_WM_PID`, `_NET_FRAME_EXTENTS` |
| Accessibility tree | AT-SPI2 via PyGObject (`perception/atspi.py`) |
| Settle signal | XDamage + XFixes cursor |
| OCR | RapidOCR (ONNX): `rapidocr_onnxruntime` up to Python 3.12, `rapidocr` 3.x on 3.13+ (downloads its models once) |
| Overlay | GTK 3 helper (`overlay/x11_helper.py`), same protocol as the macOS helper |

## Install (Ubuntu / Debian)

```bash
sudo apt install python3-gi python3-gi-cairo gir1.2-atspi-2.0 gir1.2-gtk-3.0 \
    at-spi2-core x11-utils
python3 -m venv --system-site-packages .venv   # PyGObject comes from the system
source .venv/bin/activate
pip install -e ".[linux,ocr]"
```

`--system-site-packages` is the easy way to get PyGObject; `pip install
PyGObject` also works but needs `libgirepository1.0-dev` and a compiler.

Turn on the accessibility bus for apps (GNOME does this when a screen reader
is on; elsewhere set it once):

```bash
gsettings set org.gnome.desktop.interface toolkit-accessibility true
```

Chromium and Electron apps (Chrome, VS Code, Slack) build their accessibility
tree only when assistive technology is announced. Dobby sets
`org.a11y.Status.IsEnabled` on the bus when it starts inspecting; apps that
were already running before that need a restart, or start them with
`--force-renderer-accessibility`. Java apps (including the Vivado GUI) need
the Java ATK bridge (`libatk-wrapper-java`), otherwise Dobby falls back to OCR
and vision for them.

## XWayland sessions

On a GNOME or KDE Wayland desktop, `DISPLAY` is still set for XWayland and
Dobby uses it. Only X11 apps are visible and controllable there; native
Wayland apps (most GNOME apps, Firefox by default) are not. Log in to an
"on Xorg" session, or run apps with `GDK_BACKEND=x11` / `QT_QPA_PLATFORM=xcb`.

## Coordinates and scaling

On X11, input, capture and window geometry share physical pixels, so the
display topology reports scale 1.0 at any UI scale. `hal.ui_scale` (from
`Xft.dpi`) is reported for diagnostics only.

## Overlay

On by default when `DISPLAY` is set (`DOBBY_OVERLAY=0` turns it off). Panels
are click-through override-redirect windows; with a compositor they are
translucent and clicks ripple, without one they are cut to shape. The overlay
is unmapped for every capture, so screenshots, OCR and Gemma never see it.
The control bar sits at the top centre while Dobby acts ("working", with Pause
and Stop), is paused or stopped; while Dobby rests it stays out of sight and
the tray icon has Wake. `DOBBY_OVERLAY_PILL=always` also keeps a "Dobby is
sleeping" bar (click it to wake Dobby or put it back to sleep), `off` hides
the bar entirely.
Ctrl+Alt+Esc stops Dobby, Ctrl+Alt+P pauses and resumes. GNOME and Metacity
use Ctrl+Alt+Esc for cycle-panels, so there Stop moves to Ctrl+Alt+End; the
Stop button's tooltip shows the key that is live. Not yet on Linux:
the numbered step trail, the scan sweep, the tag menu and user take-over
detection.

## Headless test rig

The live tests in `tests/test_linux_x11.py` run against Xvfb:

```bash
sudo apt install xvfb openbox dbus-x11 at-spi2-core zenity gedit \
    python3-gi gir1.2-atspi-2.0 gir1.2-gtk-3.0 python3-gi-cairo
export DISPLAY=:99
Xvfb :99 -screen 0 1600x1000x24 &
eval "$(dbus-launch --sh-syntax)"
openbox &
/usr/libexec/at-spi-bus-launcher --launch-immediately &
gsettings set org.gnome.desktop.interface toolkit-accessibility true
python -m pytest tests/test_linux_x11.py
```

They skip everywhere else (macOS, no `DISPLAY`).

## Known gaps

- Native Wayland (portal capture, libei input, compositor window lists) is
  the next phase.
- `tests/test_enhanced_vision.py::test_mcp_new_server_tools` expects an
  active window, so it fails on an empty headless display.
