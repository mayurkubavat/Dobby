"""Unit and integration tests for Phase 1 Autonomy:
ActionCache, WindowClipper, and ObstructionInterceptor.
"""

import os
import tempfile

from dobby.agent_loop.cache import ActionCache
from dobby.agent_loop.controller import FeedbackLoopController
from dobby.agent_loop.obstruction import ObstructionInterceptor
from dobby.perception.window_clipper import WindowClipper
from dobby.server import (
    capture_window,
    dismiss_obstructions,
    manage_cache,
)
from dobby.types import BoundingBox, UIElement


def test_action_cache_lifecycle():
    """Verifies storing, retrieving, hitting, invalidating, and clearing locators."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        cache_path = tmp.name

    try:
        cache = ActionCache(cache_file_path=cache_path)
        bbox = BoundingBox(x=100, y=200, width=80, height=35)

        # 1. Record
        entry = cache.record(
            app_key="chrome",
            query="Argon Model",
            bbox=bbox,
            center_coords=(140, 217),
            role="button",
        )
        assert entry.hit_count == 1
        assert entry.query == "argon model"

        # 2. Get
        retrieved = cache.get("chrome", "Argon Model")
        assert retrieved is not None
        assert retrieved.center_coords == (140, 217)

        # 3. Hit
        cache.record_hit("chrome", "Argon Model")
        assert cache.get("chrome", "Argon Model").hit_count == 2

        # 4. Invalidate
        ok = cache.invalidate("chrome", "Argon Model")
        assert ok is True
        assert cache.get("chrome", "Argon Model") is None

        # 5. Clear
        cache.record("chrome", "Q2", bbox, (10, 10))
        assert len(cache.get_stats()["cached_keys"]) == 1
        cache.clear()
        assert len(cache.get_stats()["cached_keys"]) == 0
    finally:
        if os.path.exists(cache_path):
            os.remove(cache_path)


def test_window_clipper(mock_hal):
    """Verifies per-window bounding box clipping and coordinate integrity."""
    clipper = WindowClipper(mock_hal)

    # Resolve mock terminal window
    win = clipper.resolve_target_window(title_filter="Terminal")
    assert win is not None
    assert win.title == "Mock Terminal"
    assert win.bbox.width == 800

    # Clip region to screen boundaries
    screen_size = mock_hal.get_screen_size()
    clipped_region = clipper.clip_region_to_screen(win.bbox, screen_size)
    assert clipped_region == (50, 50, 800, 600)

    # Filter elements to window bounds
    elem_inside = UIElement(
        id=1,
        role="button",
        name="Inside Button",
        bbox=BoundingBox(x=100, y=100, width=50, height=30),
    )
    elem_outside = UIElement(
        id=2,
        role="button",
        name="Outside Button",
        bbox=BoundingBox(x=1500, y=900, width=50, height=30),
    )

    filtered = clipper.filter_and_offset_elements([elem_inside, elem_outside], win.bbox)
    assert len(filtered) == 1
    assert filtered[0].name == "Inside Button"


def test_obstruction_interceptor(mock_hal):
    """Verifies detection and dismissal of modal dialog buttons."""

    class MockATSPI:
        is_available = True

        def __init__(self, hal):
            self.hal = hal

        def inspect_desktop(self, filter_interactable=True):
            # Once clicked, the modal is dismissed
            if any(c[0] == 330 and c[1] == 215 for c in self.hal.clicks):
                return [
                    UIElement(
                        id=2,
                        role="button",
                        name="Submit Code",
                        bbox=BoundingBox(x=600, y=500, width=100, height=40),
                        interactable=True,
                    ),
                ]
            return [
                UIElement(
                    id=1,
                    role="button",
                    name="Block",
                    bbox=BoundingBox(x=300, y=200, width=60, height=30),
                    interactable=True,
                ),
                UIElement(
                    id=2,
                    role="button",
                    name="Submit Code",
                    bbox=BoundingBox(x=600, y=500, width=100, height=40),
                    interactable=True,
                ),
            ]

    interceptor = ObstructionInterceptor(mock_hal, atspi_inspector=MockATSPI(mock_hal))
    candidates = interceptor.find_obstructions()
    assert len(candidates) == 1
    assert candidates[0].name == "Block"

    res = interceptor.dismiss_if_present()
    assert res["obstruction_cleared"] is True
    assert len(res["actions_taken"]) == 1
    # Verify mock_hal received click at (330, 215)
    assert (330, 215, "left", 1) in mock_hal.clicks


def test_self_healing_find_and_click(mock_hal):
    """Verifies self-healing fallback when cached coordinate produces zero delta."""
    controller = FeedbackLoopController(mock_hal)
    # Seed cache with old/invalid coordinates
    app_key = controller._get_active_app_key()
    controller.action_cache.record(
        app_key=app_key,
        query="Run Test",
        bbox=BoundingBox(x=999, y=999, width=10, height=10),
        center_coords=(999, 999),
        role="button",
    )

    # Clicking should self-heal by invalidating stale cache and searching via GroundingEngine
    res = controller.find_and_click(query="Run Test", verify_delta=False)
    # Result succeeds even with mock HAL
    assert res.success is True


def test_phase1_mcp_server_tools(mock_hal, live_screen):
    """Verifies capture_window, dismiss_obstructions, and manage_cache MCP tools."""
    # 1. manage_cache stats
    stats = manage_cache(action="stats")
    assert "entries_count" in stats

    # 2. dismiss_obstructions
    obs_res = dismiss_obstructions()
    assert "obstruction_cleared" in obs_res

    # 3. capture_window
    win_res = capture_window(title_filter="Terminal")
    assert "image_base64" in win_res
    assert win_res["width"] > 0
