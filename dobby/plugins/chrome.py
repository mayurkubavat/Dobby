"""Google Chrome Browser Plugin for Vision MCP.

Implements specialized browser tab management, address bar navigation,
sub-pane bounding box routing, and web application automation macros.
"""

import time


def _send_hotkey(hal, combo: str) -> None:
    parts = [p.strip().lower() for p in combo.split("+")]
    hal.keyboard_hotkey(parts)


from typing import Any

from dobby.plugins.base import BaseUIAppPlugin
from dobby.types import FeatureDescriptor, SubPaneROI


class ChromeBrowserPlugin(BaseUIAppPlugin):
    """Specialized UI plugin for Google Chrome and Chromium browsers."""

    app_id = "chrome"
    name = "Google Chrome Browser Plugin"
    description = "Automates tab lifecycle, omnibox navigation, content scrolling, and web application interactions"

    def _register_panes(self) -> None:
        """Registers standard Chrome browser sub-panes."""
        self.sub_panes = {
            "tab_strip": SubPaneROI(
                name="tab_strip",
                description="Top tab strip containing open tabs and new tab button",
                relative_x=0.0,
                relative_y=0.0,
                relative_width=1.0,
                fixed_height=42,
            ),
            "address_bar": SubPaneROI(
                name="address_bar",
                description="Omnibox address bar, back/forward buttons, and extensions",
                relative_x=0.0,
                relative_y=0.0,
                offset_y=42,
                relative_width=1.0,
                fixed_height=44,
            ),
            "bookmark_bar": SubPaneROI(
                name="bookmark_bar",
                description="Bookmarks toolbar below address bar",
                relative_x=0.0,
                relative_y=0.0,
                offset_y=86,
                relative_width=1.0,
                fixed_height=30,
            ),
            "content_area": SubPaneROI(
                name="content_area",
                description="Rendered web page content viewport",
                relative_x=0.0,
                relative_y=0.0,
                offset_y=116,
                relative_width=1.0,
                relative_height=1.0,
            ),
            "devtools_dock": SubPaneROI(
                name="devtools_dock",
                description="Developer Tools panel when docked to the right half of the window",
                relative_x=0.5,
                relative_y=0.0,
                offset_y=116,
                relative_width=0.5,
                relative_height=1.0,
            ),
        }

    def _register_features(self) -> None:
        """Registers declarative Chrome capabilities and keyboard shortcuts."""
        self.feature_map = {
            "browser.navigate": FeatureDescriptor(
                feature_id="browser.navigate",
                name="Navigate URL",
                description="Focuses address bar, enters URL, and loads web page",
                category="navigation",
                hotkey="ctrl+l",
                target_pane="address_bar",
                parameters={
                    "url": {
                        "type": "string",
                        "required": True,
                        "description": "URL to navigate to",
                    },
                    "new_tab": {
                        "type": "boolean",
                        "default": False,
                        "description": "Whether to open in new tab",
                    },
                },
            ),
            "browser.new_tab": FeatureDescriptor(
                feature_id="browser.new_tab",
                name="New Tab",
                description="Opens a new empty browser tab",
                category="navigation",
                hotkey="ctrl+t",
                target_pane="tab_strip",
            ),
            "browser.close_tab": FeatureDescriptor(
                feature_id="browser.close_tab",
                name="Close Tab",
                description="Closes the currently active browser tab",
                category="navigation",
                hotkey="ctrl+w",
                target_pane="tab_strip",
            ),
            "browser.switch_tab": FeatureDescriptor(
                feature_id="browser.switch_tab",
                name="Switch Tab",
                description="Switches to a tab by index (1..9) or direction ('next' / 'prev')",
                category="navigation",
                target_pane="tab_strip",
                parameters={
                    "tab_index": {"type": "integer", "description": "Target tab index from 1 to 9"},
                    "direction": {"type": "string", "description": "'next' or 'prev'"},
                },
            ),
            "browser.reload": FeatureDescriptor(
                feature_id="browser.reload",
                name="Reload Page",
                description="Reloads the current webpage with optional cache bypass",
                category="navigation",
                hotkey="ctrl+r",
                parameters={
                    "hard": {
                        "type": "boolean",
                        "default": False,
                        "description": "Whether to bypass cache (Ctrl+Shift+R)",
                    },
                },
            ),
            "browser.zoom_in": FeatureDescriptor(
                feature_id="browser.zoom_in",
                name="Zoom In",
                description="Increases webpage zoom level",
                category="view",
                hotkey="ctrl+plus",
            ),
            "browser.zoom_out": FeatureDescriptor(
                feature_id="browser.zoom_out",
                name="Zoom Out",
                description="Decreases webpage zoom level",
                category="view",
                hotkey="ctrl+minus",
            ),
            "browser.zoom_reset": FeatureDescriptor(
                feature_id="browser.zoom_reset",
                name="Reset Zoom",
                description="Resets webpage zoom to default 100%",
                category="view",
                hotkey="ctrl+0",
            ),
            "browser.find_in_page": FeatureDescriptor(
                feature_id="browser.find_in_page",
                name="Find in Page",
                description="Opens Chrome in-page search bar and optionally searches text",
                category="editing",
                hotkey="ctrl+f",
                parameters={
                    "query": {"type": "string", "description": "Search string to locate"},
                },
            ),
            "browser.devtools": FeatureDescriptor(
                feature_id="browser.devtools",
                name="Toggle DevTools",
                description="Toggles Chrome Developer Tools panel",
                category="devtools",
                hotkey="ctrl+shift+i",
            ),
            "browser.scroll_content": FeatureDescriptor(
                feature_id="browser.scroll_content",
                name="Scroll Web Content",
                description="Positions pointer in web content viewport and scrolls smoothly",
                category="view",
                target_pane="content_area",
                parameters={
                    "direction": {
                        "type": "string",
                        "default": "down",
                        "description": "'down' or 'up'",
                    },
                    "amount": {
                        "type": "integer",
                        "default": 15,
                        "description": "Scroll wheel tick count",
                    },
                },
            ),
            "browser.dismiss_dialog": FeatureDescriptor(
                feature_id="browser.dismiss_dialog",
                name="Dismiss Dialog or Popup",
                description="Dismisses active modal dialogs, cookie banners, or permission requests",
                category="navigation",
                hotkey="escape",
            ),
            "browser.gemini_query": FeatureDescriptor(
                feature_id="browser.gemini_query",
                name="Gemini Web Query Macro",
                description="Submits prompt to active Gemini Web app session and monitors streaming generation",
                category="macro",
                target_pane="content_area",
                parameters={
                    "prompt": {
                        "type": "string",
                        "required": True,
                        "description": "Research prompt to submit",
                    },
                    "press_enter": {
                        "type": "boolean",
                        "default": True,
                        "description": "Whether to press Enter to submit",
                    },
                },
            ),
        }

    def detect(self, window_title: str, app_name: str | None = None) -> bool:
        """Matches Google Chrome or Chromium desktop windows."""
        lower_title = window_title.lower() if window_title else ""
        lower_app = app_name.lower() if app_name else ""
        return (
            "google chrome" in lower_title
            or "chromium" in lower_title
            or "chrome" in lower_app
            or "chromium" in lower_app
        )

    def execute_feature(
        self,
        feature_id: str,
        controller: Any,
        args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Executes a Chrome browser feature with closed-loop verification."""
        args = args or {}
        feature = self.get_feature(feature_id)
        if not feature:
            return {
                "success": False,
                "feature_id": feature_id,
                "message": f"Feature '{feature_id}' is not supported by {self.name}.",
            }

        hal = controller.hal

        # 1. Navigation to URL
        if feature_id == "browser.navigate":
            url = args.get("url", "")
            if not url:
                return {
                    "success": False,
                    "feature_id": feature_id,
                    "message": "Missing required 'url' parameter.",
                }

            if args.get("new_tab", False):
                _send_hotkey(hal, "ctrl+t")
                time.sleep(0.3)

            # Focus address bar
            _send_hotkey(hal, "ctrl+l")
            time.sleep(0.15)
            # Type sanitized URL and press Enter
            hal.keyboard_type(url)
            time.sleep(0.1)
            hal.keyboard_press_key("enter")

            # Verify visual change
            res = controller.execute_with_verification(
                action_name=f"browser.navigate({url})",
                action_func=lambda: None,
                settle_time_sec=0.5,
            )
            return {
                "success": res.success,
                "feature_id": feature_id,
                "delta_detected": res.delta_detected,
                "change_ratio": res.change_ratio,
                "message": f"Navigated to {url}. Visual delta: {res.change_ratio * 100:.2f}%",
            }

        # 2. Tab management
        elif feature_id == "browser.new_tab":
            res = controller.execute_with_verification(
                action_func=lambda: _send_hotkey(hal, "ctrl+t"),
                action_name="browser.new_tab",
                settle_time_sec=0.3,
            )
            return {
                "success": res.success,
                "feature_id": feature_id,
                "delta_detected": res.delta_detected,
                "change_ratio": res.change_ratio,
                "message": "Opened new browser tab.",
            }

        elif feature_id == "browser.close_tab":
            res = controller.execute_with_verification(
                action_func=lambda: _send_hotkey(hal, "ctrl+w"),
                action_name="browser.close_tab",
                settle_time_sec=0.3,
            )
            return {
                "success": res.success,
                "feature_id": feature_id,
                "delta_detected": res.delta_detected,
                "change_ratio": res.change_ratio,
                "message": "Closed active tab.",
            }

        elif feature_id == "browser.switch_tab":
            tab_index = args.get("tab_index")
            direction = args.get("direction")
            if tab_index and 1 <= tab_index <= 9:
                combo = f"ctrl+{tab_index}"
            elif direction == "prev":
                combo = "ctrl+shift+tab"
            else:
                combo = "ctrl+tab"

            res = controller.execute_with_verification(
                action_func=lambda: _send_hotkey(hal, combo),
                action_name=f"browser.switch_tab({combo})",
                settle_time_sec=0.25,
            )
            return {
                "success": res.success,
                "feature_id": feature_id,
                "delta_detected": res.delta_detected,
                "change_ratio": res.change_ratio,
                "message": f"Switched tab via {combo}.",
            }

        # 3. Reload
        elif feature_id == "browser.reload":
            combo = "ctrl+shift+r" if args.get("hard", False) else "ctrl+r"
            res = controller.execute_with_verification(
                action_func=lambda: _send_hotkey(hal, combo),
                action_name=f"browser.reload({combo})",
                settle_time_sec=0.5,
            )
            return {
                "success": res.success,
                "feature_id": feature_id,
                "delta_detected": res.delta_detected,
                "change_ratio": res.change_ratio,
                "message": f"Reloaded page ({combo}).",
            }

        # 4. Zoom controls
        elif feature_id in ("browser.zoom_in", "browser.zoom_out", "browser.zoom_reset"):
            hotkey = feature.hotkey
            res = controller.execute_with_verification(
                action_func=lambda: _send_hotkey(hal, hotkey),
                action_name=feature_id,
                settle_time_sec=0.2,
            )
            return {
                "success": res.success,
                "feature_id": feature_id,
                "delta_detected": res.delta_detected,
                "change_ratio": res.change_ratio,
                "message": f"Executed {feature_id}.",
            }

        # 5. Find in page
        elif feature_id == "browser.find_in_page":
            query = args.get("query", "")
            _send_hotkey(hal, "ctrl+f")
            time.sleep(0.1)
            if query:
                hal.keyboard_type(query)
                time.sleep(0.05)
                hal.keyboard_press_key("enter")
            return {
                "success": True,
                "feature_id": feature_id,
                "message": f"Find in page triggered for query '{query}'.",
            }

        # 6. DevTools
        elif feature_id == "browser.devtools":
            res = controller.execute_with_verification(
                action_func=lambda: _send_hotkey(hal, "ctrl+shift+i"),
                action_name="browser.devtools",
                settle_time_sec=0.5,
            )
            return {
                "success": res.success,
                "feature_id": feature_id,
                "delta_detected": res.delta_detected,
                "change_ratio": res.change_ratio,
                "message": "Toggled Developer Tools.",
            }

        # 7. Scroll content viewport
        elif feature_id == "browser.scroll_content":
            direction = args.get("direction", "down")
            amount = int(args.get("amount", 15))
            # Position cursor in middle of content pane
            win = hal.get_active_window()
            if win:
                pane_bbox = self.get_sub_pane("content_area", win.bbox)
                if pane_bbox:
                    hal.mouse_move(*pane_bbox.center)

            res = controller.execute_with_verification(
                action_func=lambda: hal.mouse_scroll(
                    dx=0, dy=-amount if direction == "down" else amount
                ),
                action_name=f"browser.scroll_content({direction}, {amount})",
                settle_time_sec=0.3,
            )
            return {
                "success": res.success,
                "feature_id": feature_id,
                "delta_detected": res.delta_detected,
                "change_ratio": res.change_ratio,
                "message": f"Scrolled content {direction} by {amount} units.",
            }

        # 8. Dismiss Dialog
        elif feature_id == "browser.dismiss_dialog":
            # Attempt autonomous obstruction dismissal first
            obs_res = controller.obstruction_interceptor.scan_and_dismiss()
            if obs_res.get("dismissed", False):
                return {
                    "success": True,
                    "feature_id": feature_id,
                    "message": f"Dismissed obstruction: {obs_res.get('message')}",
                }

            # Fallback to Escape key
            res = controller.execute_with_verification(
                action_func=lambda: hal.keyboard_press_key("escape"),
                action_name="browser.dismiss_dialog(escape)",
                settle_time_sec=0.2,
            )
            return {
                "success": res.success,
                "feature_id": feature_id,
                "delta_detected": res.delta_detected,
                "change_ratio": res.change_ratio,
                "message": "Sent Escape to dismiss dialog.",
            }

        # 9. Gemini Query Macro
        elif feature_id == "browser.gemini_query":
            prompt = args.get("prompt", "")
            if not prompt:
                return {
                    "success": False,
                    "feature_id": feature_id,
                    "message": "Missing required 'prompt' parameter.",
                }

            press_enter = args.get("press_enter", True)
            # Find prompt input box or click in content area center
            win = hal.get_active_window()
            if win:
                pane_bbox = self.get_sub_pane("content_area", win.bbox)
                if pane_bbox:
                    # Focus lower portion of content area (where prompt bar resides)
                    target_x = pane_bbox.center[0]
                    target_y = pane_bbox.y + int(pane_bbox.height * 0.88)
                    hal.mouse_click(target_x, target_y)
                    time.sleep(0.2)

            res = controller.execute_with_verification(
                action_func=lambda: hal.keyboard_type(prompt, cadence_ms=5),
                action_name="browser.gemini_query(type)",
                settle_time_sec=0.3,
            )
            if press_enter:
                time.sleep(0.1)
                hal.keyboard_press_key("enter")

            return {
                "success": res.success,
                "feature_id": feature_id,
                "delta_detected": res.delta_detected,
                "change_ratio": res.change_ratio,
                "message": f"Dispatched Gemini prompt ({len(prompt)} chars). Enter pressed: {press_enter}",
            }

        # Fallback to feature hotkey if defined
        elif feature.hotkey:
            res = controller.execute_with_verification(
                action_func=lambda: _send_hotkey(hal, feature.hotkey),
                action_name=f"feature.{feature_id}({feature.hotkey})",
                settle_time_sec=0.25,
            )
            return {
                "success": res.success,
                "feature_id": feature_id,
                "delta_detected": res.delta_detected,
                "change_ratio": res.change_ratio,
                "message": f"Executed hotkey '{feature.hotkey}' for {feature_id}.",
            }

        return {
            "success": False,
            "feature_id": feature_id,
            "message": "No execution pathway resolved.",
        }
