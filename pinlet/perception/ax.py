"""macOS Accessibility (AXUIElement) inspector.

Has the same interface as ``ATSPIInspector``: ``is_available``, ``inspect_desktop``
and ``inspect``.

Roles are mapped to the AT-SPI role names the rest of the code already
understands ("push button", "entry", "dialog", "label", ...), so modal triage,
vDOM fusion and Set-of-Marks work unchanged.

Pruning follows the token-efficiency research: skip nodes under 4 points,
skip nodes entirely off-screen, and cap each walk by node count and time.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from pinlet.config import CONFIG
from pinlet.hal import target
from pinlet.hal.background import ax_window_id
from pinlet.perception.atspi import INTERACTABLE_ROLES
from pinlet.types import BoundingBox, UIElement

logger = logging.getLogger(__name__)

AX_ROLE_MAP = {
    "AXButton": "push button",
    "AXMenuButton": "push button",
    "AXCheckBox": "check box",
    "AXRadioButton": "radio button",
    "AXDisclosureTriangle": "toggle button",
    "AXMenuItem": "menu item",
    "AXMenuBarItem": "menu item",
    "AXTextField": "entry",
    "AXSearchField": "entry",
    "AXSecureTextField": "password text",
    "AXTextArea": "text",
    "AXComboBox": "combo box",
    "AXPopUpButton": "combo box",
    "AXLink": "link",
    "AXSlider": "slider",
    "AXIncrementor": "spin button",
    "AXRow": "list item",
    "AXCell": "table cell",
    "AXOutline": "tree",
    "AXTable": "tree table",
    "AXStaticText": "label",
    "AXImage": "image",
    "AXWindow": "frame",
    "AXSheet": "dialog",
    "AXGroup": "panel",
    "AXSplitGroup": "panel",
    "AXScrollArea": "scroll pane",
    "AXToolbar": "tool bar",
    "AXTabGroup": "page tab list",
    "AXList": "list",
}

PASSTHROUGH_ROLES = {"AXGroup", "AXWebArea", "AXScrollArea", "AXSplitGroup", "AXLayoutArea"}

DIALOG_SUBROLES = {"AXDialog", "AXSystemDialog", "AXFloatingWindow"}

# Fewer window elements than this (menu bar aside) right after enabling web content
# means Chromium has not built its tree yet.
_NEAR_EMPTY = 5


class AXInspector:
    """Walks the AX tree of the frontmost app (or apps matching a filter)."""

    def __init__(
        self,
        ax: Any = None,
        appkit: Any = None,
        max_nodes: int = 4000,
        max_seconds: float = 0.6,
        screen_bounds: tuple[int, int, int, int] | None = None,
        front_pid_fn: Callable[[], int | None] | None = None,
    ) -> None:
        self.screen_bounds = screen_bounds
        # NSWorkspace's frontmost app goes stale in a server (no run loop turns),
        # so the real thing asks lsappinfo. Injected fakes (tests) answer themselves.
        if front_pid_fn is None and ax is None and appkit is None:
            from pinlet.hal.macos import _lsappinfo_front_pid

            front_pid_fn = _lsappinfo_front_pid
        self._front_pid_fn = front_pid_fn
        self.max_nodes = max_nodes
        self.max_seconds = max_seconds
        self._ax = ax
        self._appkit = appkit
        self.error: str | None = None
        # pid -> True if AXManualAccessibility was accepted, False if the app refused it
        self.web_content_enabled: dict = {}
        if self._ax is None:
            try:
                import ApplicationServices  # type: ignore

                self._ax = ApplicationServices
            except Exception as e:
                self.error = (
                    f"{type(e).__name__}: {e} (pip install pyobjc-framework-ApplicationServices)"
                )
        if self._appkit is None and self._ax is not None:
            try:
                import AppKit  # type: ignore

                self._appkit = AppKit
            except Exception as e:
                self.error = f"{type(e).__name__}: {e}"

    @property
    def is_available(self) -> bool:
        """True when the AX API is loaded and this process is trusted for Accessibility."""
        if self._ax is None or self._appkit is None:
            return False
        try:
            trusted = bool(self._ax.AXIsProcessTrusted())
        except Exception:
            trusted = False
        if not trusted:
            self.error = "Accessibility permission not granted to this process (System Settings > Privacy & Security > Accessibility)"
        return trusted

    # -------------------------------------------------------------- AX access

    def _attr(self, el, name: str):
        try:
            err, value = self._ax.AXUIElementCopyAttributeValue(el, name, None)
        except Exception:
            return None
        return value if err == 0 else None

    def _frame(self, el) -> tuple[int, int, int, int] | None:
        pos, size = self._attr(el, "AXPosition"), self._attr(el, "AXSize")
        if pos is None or size is None:
            return None
        try:
            ok1, p = self._ax.AXValueGetValue(pos, self._ax.kAXValueCGPointType, None)
            ok2, s = self._ax.AXValueGetValue(size, self._ax.kAXValueCGSizeType, None)
        except Exception:
            return None
        if not (ok1 and ok2):
            return None
        return int(p.x), int(p.y), int(s.width), int(s.height)

    def _enable_web_content(self, root, pid: int) -> bool:
        """Turns on the web-content AX tree for Electron and Chromium apps.

        They build it only after a client sets AXManualAccessibility on the app element.

        Native apps reject the attribute (kAXErrorAttributeUnsupported), which is harmless.
        Tried once per pid. AXEnhancedUserInterface is deliberately not used: it breaks window
        animations and positioning in some apps.

        Returns True the first time it succeeds, so the caller can let the tree settle.
        """
        if not CONFIG.ax_enable_web_content or pid in self.web_content_enabled:
            return False
        setter = getattr(self._ax, "AXUIElementSetAttributeValue", None)
        if setter is None:
            self.web_content_enabled[pid] = False
            return False
        try:
            err = setter(root, "AXManualAccessibility", True)
        except Exception as e:
            logger.debug(f"AXManualAccessibility on pid {pid} failed: {e}")
            err = -1
        ok = err == 0
        self.web_content_enabled[pid] = ok
        return ok

    def _apps(self, app_name_filter: str | None):
        ws = self._appkit.NSWorkspace.sharedWorkspace()
        if not app_name_filter:
            front = None
            # Background mode: the app the agent works in, not the one in front.
            pid_fn = target.current_pid if target.current_pid() else self._front_pid_fn
            if pid_fn is not None:
                try:
                    pid = pid_fn()
                    if pid:
                        front = self._appkit.NSRunningApplication.runningApplicationWithProcessIdentifier_(
                            pid
                        )
                except Exception:  # noqa: BLE001 - fall back to NSWorkspace
                    front = None
            if front is None:
                front = ws.frontmostApplication()
            return [front] if front is not None else []
        needle = app_name_filter.lower()
        return [
            a
            for a in ws.runningApplications()
            if a.activationPolicy() == 0 and needle in str(a.localizedName() or "").lower()
        ]

    # ------------------------------------------------------------------ public

    def inspect_desktop(
        self,
        app_name_filter: str | None = None,
        max_depth: int = 16,
        filter_interactable: bool = True,
    ) -> list[UIElement]:
        """Flattened on-screen elements of the active app, or of apps matching ``app_name_filter``.

        Bounding boxes are in global screen coordinates.
        """
        if not self.is_available:
            return []
        screen = self.screen_bounds
        if screen is None:
            screen = self._main_screen()

        apps = []
        settle = False
        for app in self._apps(app_name_filter):
            pid = int(app.processIdentifier())
            root = self._ax.AXUIElementCreateApplication(pid)
            settle = self._enable_web_content(root, pid) or settle
            apps.append(root)
        if settle and CONFIG.ax_web_content_settle_seconds > 0:
            time.sleep(CONFIG.ax_web_content_settle_seconds)

        elements = self._collect(apps, max_depth, filter_interactable, screen)
        # Chromium builds the web tree lazily after the first enable; a read that
        # finds only the menu bar and window buttons is retried while it fills in.
        retry_until = time.monotonic() + CONFIG.ax_web_content_retry_seconds
        while settle and self._near_empty(elements) and time.monotonic() < retry_until:
            time.sleep(0.25)
            elements = self._collect(apps, max_depth, filter_interactable, screen)
        return elements

    @staticmethod
    def _near_empty(elements: list[UIElement]) -> bool:
        return (
            sum(1 for e in elements if e.role != "menu item" and e.role != "menubar") < _NEAR_EMPTY
        )

    def _collect(self, apps, max_depth, filter_interactable, screen) -> list[UIElement]:
        elements: list[UIElement] = []
        budget = [self.max_nodes]
        deadline = time.monotonic() + self.max_seconds
        # Background mode: the agent's own window only (an app's other windows may be
        # the person's), and no menu bar, which belongs to the app in front.
        chosen = target.get()
        own = target.current_frame()
        if CONFIG.ax_include_menu_bar and apps and target.get() is None:
            # Top-level menu bar items of the first (frontmost) app only: depth 2 stops
            # before the closed menus, which have no on-screen frame anyway.
            bar = self._attr(apps[0], "AXMenuBar")
            if bar is not None:
                self._walk(
                    bar,
                    1,
                    2,
                    filter_interactable,
                    elements,
                    deadline,
                    budget,
                    screen,
                    parent_role="",
                )
        for root in apps:
            windows = list(self._attr(root, "AXWindows") or [])
            if chosen is not None:
                # By the window's own id; by its frame when the id cannot be read.
                mine = [w for w in windows if ax_window_id(w) == chosen.window_id]
                if not mine and own is not None:
                    mine = [w for w in windows if target.same_frame(self._frame(w), own)]
                windows = mine or windows
            for win in windows:
                self._walk(
                    win,
                    1,
                    max_depth,
                    filter_interactable,
                    elements,
                    deadline,
                    budget,
                    screen,
                    parent_role="",
                )
                if len(elements) >= CONFIG.som_max_elements or time.monotonic() > deadline:
                    break
        return elements[: CONFIG.som_max_elements]

    @staticmethod
    def _main_screen() -> tuple[int, int, int, int] | None:
        try:
            import Quartz  # type: ignore

            b = Quartz.CGDisplayBounds(Quartz.CGMainDisplayID())
            return (int(b.origin.x), int(b.origin.y), int(b.size.width), int(b.size.height))
        except Exception:
            return None

    def hit_test(self, x: int, y: int) -> dict | None:
        """The deepest element at screen point (x, y) as ``{"role", "name", "bbox"}``, or None."""
        if not self.is_available:
            return None
        try:
            # Background mode asks the agent's own app: its window may be covered.
            pid = target.current_pid()
            if pid:
                scope = self._ax.AXUIElementCreateApplication(pid)
            else:
                scope = self._ax.AXUIElementCreateSystemWide()
            err, el = self._ax.AXUIElementCopyElementAtPosition(scope, float(x), float(y), None)
        except Exception:
            return None
        if err != 0 or el is None:
            return None
        ax_role = str(self._attr(el, "AXRole") or "")
        subrole = str(self._attr(el, "AXSubrole") or "")
        role = AX_ROLE_MAP.get(ax_role, ax_role.replace("AX", "").lower() or "unknown")
        if ax_role == "AXWindow" and subrole in DIALOG_SUBROLES:
            role = "dialog"
        name = ""
        for attr in ("AXTitle", "AXDescription", "AXValue"):
            v = self._attr(el, attr)
            if isinstance(v, str) and v.strip():
                name = v.strip()[:120]
                break
        secure = ax_role == "AXSecureTextField" or subrole == "AXSecureTextField"
        if secure:
            name = str(self._attr(el, "AXTitle") or self._attr(el, "AXDescription") or "").strip()[
                :120
            ]
        frame = self._frame(el)
        bbox = (
            {"x": frame[0], "y": frame[1], "width": frame[2], "height": frame[3]} if frame else None
        )
        owner = None
        try:  # whose element this is: Pinlet's own are off limits (overlay.offlimits)
            err, owner_pid = self._ax.AXUIElementGetPid(el, None)
            if err == 0 and owner_pid:
                owner = int(owner_pid)
        except Exception:
            owner = None
        return {"role": role, "name": name, "bbox": bbox, "secure": secure, "pid": owner}

    def inspect(self) -> list[UIElement]:
        """All elements (not just interactable) of the frontmost app."""
        return self.inspect_desktop(filter_interactable=False)

    def _walk(
        self, el, depth, max_depth, filter_interactable, out, deadline, budget, screen, parent_role
    ) -> None:
        if (
            depth > max_depth
            or budget[0] <= 0
            or time.monotonic() > deadline
            or len(out) >= CONFIG.som_max_elements
        ):
            return
        budget[0] -= 1
        ax_role = str(self._attr(el, "AXRole") or "")
        subrole = str(self._attr(el, "AXSubrole") or "")
        role = AX_ROLE_MAP.get(ax_role, ax_role.replace("AX", "").lower() or "unknown")
        if ax_role == "AXWindow" and subrole in DIALOG_SUBROLES:
            role = "dialog"
        if ax_role == "AXRadioButton" and parent_role == "AXTabGroup":
            role = "page tab"

        frame = self._frame(el)
        visible = True
        if frame is not None:
            x, y, w, h = frame
            if screen is not None:
                sx, sy, sw, sh = screen
                visible = x + w > sx and y + h > sy and x < sx + sw and y < sy + sh
            if w >= 4 and h >= 4 and visible:
                interactable = role in INTERACTABLE_ROLES
                if not filter_interactable or interactable or role == "dialog":
                    name = ""
                    for attr in (
                        "AXTitle",
                        "AXDescription",
                        "AXValue",
                        "AXPlaceholderValue",
                        "AXHelp",
                    ):
                        v = self._attr(el, attr)
                        if isinstance(v, str) and v.strip():
                            name = v.strip()[:200]
                            break
                    states = []
                    if self._attr(el, "AXFocused"):
                        states.append("focused")
                    enabled = self._attr(el, "AXEnabled")
                    states.append("disabled" if enabled is False else "enabled")
                    if self._attr(el, "AXSelected"):
                        states.append("selected")
                    out.append(
                        UIElement(
                            id=len(out) + 1,
                            role=role,
                            name=name,
                            bbox=BoundingBox(x=x, y=y, width=w, height=h),
                            interactable=interactable,
                            states=states,
                        )
                    )
        if not visible:
            return  # off-screen subtree
        # Chromium nests web content in many unnamed AXGroup wrappers (one per <div>).
        # They do not count toward max_depth; the node budget and deadline still bound the walk.
        next_depth = depth if ax_role in PASSTHROUGH_ROLES and not subrole else depth + 1
        for child in self._attr(el, "AXChildren") or []:
            self._walk(
                child,
                next_depth,
                max_depth,
                filter_interactable,
                out,
                deadline,
                budget,
                screen,
                ax_role,
            )


def get_accessibility_inspector():
    """AXInspector on macOS, ATSPIInspector elsewhere."""
    import sys

    if sys.platform == "darwin":
        return AXInspector()
    from pinlet.perception.atspi import ATSPIInspector

    return ATSPIInspector()
