"""
AT-SPI2 accessibility inspector for Linux (X11 and XWayland).

Same interface as the macOS ``AXInspector``: ``is_available``,
``inspect_desktop``, ``inspect`` and ``hit_test``. Role names are AT-SPI's own,
which the rest of Dobby already uses.

Things this handles that a naive walk does not:

* No accessibility bus: libatspi aborts the whole process (g_error) when it
  cannot reach the bus, so the bus is probed over D-Bus first and the
  inspector reports itself unavailable instead.
* GTK 4 cannot know where its window is on screen and reports every SCREEN
  extent as (0, 0). Its WINDOW extents are right, so those widgets are placed
  with the window's client origin taken from the X server.
* Hidden widgets report a -2147483648 sentinel; they are skipped, as are
  subtrees that are not SHOWING.
* Walks are bounded by a node budget and a deadline, like the macOS walk.
* Chromium and Electron only build their accessibility tree when assistive
  technology is announced on the bus (org.a11y.Status.IsEnabled), the Linux
  counterpart of macOS AXManualAccessibility. Apps started before that flag
  was set need a restart, or ``--force-renderer-accessibility``.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from dobby.config import CONFIG
from dobby.types import BoundingBox, UIElement

logger = logging.getLogger(__name__)

INTERACTABLE_ROLES: Set[str] = {
    "push button",
    "button",
    "toggle button",
    "check box",
    "radio button",
    "menu",
    "menu item",
    "check menu item",
    "radio menu item",
    "entry",
    "text",
    "password text",
    "combo box",
    "list item",
    "tab",
    "page tab",
    "slider",
    "spin button",
    "link",
    "tree table",
    "tree",
    "icon",
    "table cell",
    "terminal",
}

# Roles that are only containers: a hit test that lands on one keeps looking deeper.
CONTAINER_ROLES: Set[str] = {
    "application", "frame", "window", "dialog", "panel", "filler", "scroll pane",
    "viewport", "layered pane", "root pane", "split pane", "section", "grouping",
    "unknown", "redundant object", "document frame", "document web", "internal frame",
}

# Background clicks: roles where the element's own action does what a click does.
PRESSABLE_ROLES: Set[str] = {
    "push button", "button", "toggle button", "check box", "radio button", "menu item",
    "check menu item", "radio menu item", "link", "page tab",
}
# Walking up from a label stops here: a click there is not a press.
PRESS_STOP_ROLES: Set[str] = CONTAINER_ROLES | {"text", "entry", "password text", "terminal", "canvas",
                                                "list", "table", "tree", "tree table", "combo box"}
PRESS_ACTIONS = ("click", "press", "activate", "toggle", "jump")
PRESS_MAX_UP = 2

_SENTINEL = -(2 ** 31)
_MAX_COORD = 1 << 15


def a11y_bus_reachable(timeout_ms: int = 500) -> Tuple[bool, str]:
    """Probes the accessibility bus without touching libatspi (which aborts when it is missing)."""
    if os.environ.get("AT_SPI_BUS_ADDRESS"):
        return True, ""
    if not os.environ.get("DBUS_SESSION_BUS_ADDRESS") and not os.environ.get("XDG_RUNTIME_DIR"):
        return False, "no session D-Bus (DBUS_SESSION_BUS_ADDRESS unset)"
    try:
        from gi.repository import Gio, GLib

        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        reply = bus.call_sync(
            "org.a11y.Bus", "/org/a11y/bus", "org.a11y.Bus", "GetAddress",
            None, GLib.VariantType.new("(s)"), Gio.DBusCallFlags.NONE, timeout_ms, None,
        )
        address = reply.unpack()[0] if reply is not None else ""
        return (bool(address), "" if address else "org.a11y.Bus returned no address")
    except Exception as e:
        return False, f"accessibility bus unreachable: {e}"


def announce_assistive_technology(timeout_ms: int = 500) -> bool:
    """Sets org.a11y.Status.IsEnabled so Chromium/Electron apps build their tree."""
    try:
        from gi.repository import Gio, GLib

        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        bus.call_sync(
            "org.a11y.Bus", "/org/a11y/bus", "org.freedesktop.DBus.Properties", "Set",
            GLib.Variant("(ssv)", ("org.a11y.Status", "IsEnabled", GLib.Variant("b", True))),
            None, Gio.DBusCallFlags.NONE, timeout_ms, None,
        )
        return True
    except Exception as e:
        logger.debug("could not set org.a11y.Status.IsEnabled: %s", e)
        return False


def _valid(x: int, y: int, w: int, h: int) -> bool:
    return (
        x != _SENTINEL and y != _SENTINEL
        and -_MAX_COORD < x < _MAX_COORD and -_MAX_COORD < y < _MAX_COORD
        and 0 < w < _MAX_COORD and 0 < h < _MAX_COORD
    )


class ATSPIInspector:
    """Walks the AT-SPI tree of the active app (or apps matching a filter)."""

    def __init__(
        self,
        atspi: Any = None,
        hal: Any = None,
        max_nodes: int = 4000,
        max_seconds: float = 0.8,
        probe_bus: bool = True,
    ) -> None:
        self.max_nodes = max_nodes
        self.max_seconds = max_seconds
        self.error: Optional[str] = None
        self.web_content_announced = False
        self._hal = hal
        self._atspi = atspi
        self._coord_type = None
        self._available = False

        if self._atspi is None:
            try:
                import gi

                gi.require_version("Atspi", "2.0")
                from gi.repository import Atspi

                self._atspi = Atspi
            except Exception as e:
                self.error = f"{type(e).__name__}: {e} (install python3-gi and gir1.2-atspi-2.0)"
                return
        if probe_bus:
            ok, why = a11y_bus_reachable()
            if not ok:
                self.error = why
                self._atspi = None
                return
        self._coord_type = self._atspi.CoordType.SCREEN
        self._available = True

    @property
    def is_available(self) -> bool:
        """True when PyGObject Atspi loaded and the accessibility bus answered."""
        return self._available

    # ------------------------------------------------------------- helpers

    def _get_hal(self) -> Any:
        if self._hal is None:
            from dobby.hal.factory import get_desktop_hal

            self._hal = get_desktop_hal()
        return self._hal

    def _client_windows(self) -> List[Dict[str, Any]]:
        try:
            fn = getattr(self._get_hal(), "client_windows", None)
            return list(fn()) if callable(fn) else []
        except Exception:
            return []

    def _active_pid(self) -> Optional[int]:
        try:
            hal = self._get_hal()
            win = hal.get_active_window()
            pid_fn = getattr(hal, "window_pid", None)
            if win is None or not callable(pid_fn):
                return None
            return pid_fn(win.window_id)
        except Exception:
            return None

    def _announce(self) -> None:
        if CONFIG.ax_enable_web_content and not self.web_content_announced:
            self.web_content_announced = announce_assistive_technology()

    def _apps(self, app_name_filter: Optional[str]) -> List[Any]:
        desktop = self._atspi.get_desktop(0)
        apps = []
        for i in range(desktop.get_child_count()):
            app = desktop.get_child_at_index(i)
            if app is not None:
                apps.append(app)
        if app_name_filter:
            needle = app_name_filter.lower()
            picked = []
            for app in apps:
                if needle in (app.get_name() or "").lower():
                    picked.append(app)
                    continue
                for w in range(app.get_child_count()):
                    child = app.get_child_at_index(w)
                    if child is not None and needle in (child.get_name() or "").lower():
                        picked.append(app)
                        break
            return picked
        pid = self._active_pid()
        if pid is not None:
            front = [a for a in apps if _safe(lambda: a.get_process_id()) == pid]
            if front:
                return front
        return apps

    def _origin_for(self, app: Any, win: Any, clients: List[Dict[str, Any]]) -> Optional[Tuple[int, int]]:
        """Client-area origin of ``win`` when its SCREEN extents are unusable (GTK 4)."""
        comp = win.get_component_iface()
        if comp is None:
            return None
        ext = comp.get_extents(self._atspi.CoordType.SCREEN)
        if (ext.x, ext.y) != (0, 0):
            return None  # toolkit knows its screen position
        pid = _safe(lambda: app.get_process_id())
        title = (win.get_name() or "").strip().lower()
        candidates = [c for c in clients if pid is not None and c.get("pid") == pid] or clients
        best = None
        for c in candidates:
            ct = (c.get("title") or "").strip().lower()
            if title and ct and (ct == title or title in ct or ct in title):
                if (c["width"], c["height"]) == (ext.width, ext.height):
                    return (c["x"], c["y"])
                best = best or (c["x"], c["y"])
        if best is not None:
            return best
        same_pid = [c for c in candidates if pid is not None and c.get("pid") == pid]
        if len(same_pid) == 1:
            return (same_pid[0]["x"], same_pid[0]["y"])
        return None

    def _extents(self, comp: Any, origin: Optional[Tuple[int, int]]) -> Optional[Tuple[int, int, int, int]]:
        try:
            if origin is None:
                r = comp.get_extents(self._coord_type)
                x, y = r.x, r.y
            else:
                r = comp.get_extents(self._atspi.CoordType.WINDOW)
                x, y = origin[0] + r.x, origin[1] + r.y
            if r.x == _SENTINEL or r.y == _SENTINEL:
                return None
            box = (int(x), int(y), int(r.width), int(r.height))
        except Exception:
            return None
        return box if _valid(*box) else None

    def _states(self, node: Any) -> List[str]:
        """Same state words as the macOS inspector: focused, enabled/disabled, selected, checked."""
        out: List[str] = []
        try:
            st = node.get_state_set()
            T = self._atspi.StateType
            if st.contains(T.FOCUSED):
                out.append("focused")
            enabled = st.contains(T.ENABLED) or st.contains(T.SENSITIVE)
            out.append("enabled" if enabled else "disabled")
            if st.contains(T.SELECTED):
                out.append("selected")
            if st.contains(T.CHECKED):
                out.append("checked")
        except Exception:
            pass
        return out

    def _showing(self, node: Any) -> bool:
        try:
            states = node.get_state_set()
            return states.contains(self._atspi.StateType.SHOWING)
        except Exception:
            return True

    def _toplevels(self, apps: List[Any]):
        clients = self._client_windows()
        for app in apps:
            for w in range(app.get_child_count()):
                win = app.get_child_at_index(w)
                if win is None or not self._showing(win):
                    continue
                yield app, win, self._origin_for(app, win, clients)

    # -------------------------------------------------------------- public

    def inspect_desktop(
        self,
        app_name_filter: Optional[str] = None,
        max_depth: int = 16,
        filter_interactable: bool = True,
    ) -> List[UIElement]:
        """
        Flattened, on-screen elements of the active app (or apps matching
        ``app_name_filter``), with true screen bounding boxes.
        """
        if not self._available:
            return []
        self._announce()
        elements: List[UIElement] = []
        budget = [self.max_nodes]
        deadline = time.monotonic() + self.max_seconds
        try:
            for _app, win, origin in self._toplevels(self._apps(app_name_filter)):
                self._walk(win, 1, max_depth, filter_interactable, elements, origin, budget, deadline)
                if len(elements) >= CONFIG.som_max_elements or budget[0] <= 0 or time.monotonic() > deadline:
                    break
        except Exception as e:
            logger.debug("AT-SPI walk failed: %s", e)
        return elements[: CONFIG.som_max_elements]

    def inspect(self) -> List[UIElement]:
        """All elements, interactable or not (used by post-condition checks)."""
        return self.inspect_desktop(filter_interactable=False)

    def hit_test(self, x: int, y: int) -> Optional[dict]:
        """The deepest element at screen point (x, y) as ``{"role", "name", "bbox"}``, or None.

        Like the macOS hit test this answers for whatever window is on top at
        the point, not only the active app: the topmost X11 window there picks
        the app, and its accessibility tree is searched.
        """
        if not self._available:
            return None
        try:
            apps = self._apps_at(x, y)
            for app, win, origin in self._toplevels(apps):
                comp = win.get_component_iface()
                box = self._extents(comp, origin) if comp is not None else None
                if box is None or not (box[0] <= x < box[0] + box[2] and box[1] <= y < box[1] + box[3]):
                    continue
                return self._deepest_at(win, x, y, origin) or self._describe(win, box)
        except Exception as e:
            logger.debug("AT-SPI hit test failed: %s", e)
        return None

    def _apps_at(self, x: int, y: int) -> List[Any]:
        for c in self._client_windows():  # front to back
            if c["x"] <= x < c["x"] + c["width"] and c["y"] <= y < c["y"] + c["height"]:
                pid = c.get("pid")
                if pid is None:
                    break
                desktop = self._atspi.get_desktop(0)
                apps = [desktop.get_child_at_index(i) for i in range(desktop.get_child_count())]
                hits = [a for a in apps if a is not None and _safe(lambda: a.get_process_id()) == pid]
                if hits:
                    return hits
                break
        return self._apps(None)

    # ------------------------------------------------------------- walking

    def _describe(self, node: Any, box: Tuple[int, int, int, int]) -> dict:
        role = node.get_role_name() or "unknown"
        return {
            "role": role,
            "name": (node.get_name() or "").strip()[:120],
            "bbox": {"x": box[0], "y": box[1], "width": box[2], "height": box[3]},
            "secure": role == "password text",
        }

    def _deepest_at(self, win: Any, x: int, y: int, origin: Optional[Tuple[int, int]]) -> Optional[dict]:
        """Smallest showing element containing the point, searched under ``win`` within the budget.

        A tree search rather than Component.get_accessible_at_point, which GTK 4
        answers only one level deep.
        """
        hit = self._deepest_node_at(win, x, y, origin)
        return hit[1] if hit else None

    def press_at(self, x: int, y: int, pid: int) -> Optional[str]:
        """Press the button of process ``pid`` at screen point (x, y) through AT-SPI.

        The Linux side of background clicks: the element's own "click" or
        "press" action runs, so the real pointer does not move and the window
        need not be in front. Only button-like, enabled elements are pressed
        (see :data:`PRESSABLE_ROLES`); anything else returns None and the
        caller clicks with the real mouse.
        """
        if not self._available or pid is None:
            return None
        try:
            desktop = self._atspi.get_desktop(0)
            apps = [desktop.get_child_at_index(i) for i in range(desktop.get_child_count())]
            apps = [a for a in apps if a is not None and _safe(lambda: a.get_process_id()) == pid]
            for _app, win, origin in self._toplevels(apps):
                comp = win.get_component_iface()
                box = self._extents(comp, origin) if comp is not None else None
                if box is None or not (box[0] <= x < box[0] + box[2] and box[1] <= y < box[1] + box[3]):
                    continue
                hit = self._deepest_node_at(win, x, y, origin)
                if hit is None:
                    return None
                return self._press(hit[0])
        except Exception as e:
            logger.debug("AT-SPI press failed: %s", e)
        return None

    def _press(self, node: Any) -> Optional[str]:
        for _ in range(PRESS_MAX_UP + 1):
            if node is None:
                return None
            role = _safe(lambda: node.get_role_name()) or ""
            if role in PRESSABLE_ROLES:
                if "disabled" in self._states(node):
                    return None
                action = _safe(lambda: node.get_action_iface())
                if action is None:
                    return None
                names = [(_safe(lambda i=i: action.get_action_name(i)) or "").lower()
                         for i in range(_safe(lambda: action.get_n_actions()) or 0)]
                for wanted in PRESS_ACTIONS:
                    if wanted in names:
                        ok = _safe(lambda: action.do_action(names.index(wanted)))
                        return role if ok else None
                return None
            if role in PRESS_STOP_ROLES:
                return None
            node = _safe(lambda: node.get_parent())
        return None

    def _deepest_node_at(self, win: Any, x: int, y: int,
                         origin: Optional[Tuple[int, int]]) -> Optional[Tuple[Any, dict]]:
        best: Optional[Tuple[int, dict]] = None
        best_leaf: Optional[Tuple[int, dict]] = None
        budget = [self.max_nodes]
        deadline = time.monotonic() + self.max_seconds
        stack = [win]
        while stack and budget[0] > 0 and time.monotonic() < deadline:
            node = stack.pop()
            budget[0] -= 1
            comp = node.get_component_iface()
            box = self._extents(comp, origin) if comp is not None else None
            if box is None or not (box[0] <= x < box[0] + box[2] and box[1] <= y < box[1] + box[3]):
                if node is not win:
                    continue
            if box is not None and node is not win:
                area = box[2] * box[3]
                desc = self._describe(node, box)
                if desc["role"] not in CONTAINER_ROLES:
                    if best_leaf is None or area < best_leaf[0]:
                        best_leaf = (area, (node, desc))
                elif best is None or area < best[0]:
                    best = (area, (node, desc))
            for c in range(node.get_child_count()):
                child = node.get_child_at_index(c)
                if child is not None and self._showing(child):
                    stack.append(child)
        pick = best_leaf or best
        return pick[1] if pick else None  # (node, desc)

    def _walk(
        self,
        node: Any,
        depth: int,
        max_depth: int,
        filter_interactable: bool,
        elements: List[UIElement],
        origin: Optional[Tuple[int, int]],
        budget: List[int],
        deadline: float,
    ) -> None:
        if depth > max_depth or len(elements) >= CONFIG.som_max_elements:
            return
        if budget[0] <= 0 or time.monotonic() > deadline:
            return
        budget[0] -= 1
        try:
            role = node.get_role_name() or "unknown"
            name = node.get_name() or ""
            is_interactable = role.lower() in INTERACTABLE_ROLES
            comp = node.get_component_iface()
            box = self._extents(comp, origin) if comp is not None else None
            if box is not None:
                x, y, w, h = box
                is_collapsed_menu = role.lower() == "menu item" and h <= 14
                if w > 8 and h > 8 and w * h >= CONFIG.som_min_element_area and not is_collapsed_menu:
                    if not filter_interactable or is_interactable or role == "dialog":
                        if not name.strip():
                            name = _safe(lambda: node.get_description()) or ""
                        elements.append(
                            UIElement(
                                id=len(elements) + 1,
                                role=role,
                                name=name.strip()[:200],
                                bbox=BoundingBox(x=x, y=y, width=w, height=h),
                                interactable=is_interactable,
                                states=self._states(node),
                            )
                        )
            for c in range(node.get_child_count()):
                child = node.get_child_at_index(c)
                if child is not None and self._showing(child):
                    self._walk(child, depth + 1, max_depth, filter_interactable, elements, origin, budget, deadline)
        except Exception:
            return


def _safe(fn):
    try:
        return fn()
    except Exception:
        return None
