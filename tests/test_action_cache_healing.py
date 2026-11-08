"""
Unit and Integration Tests for Dynamic Self-Healing Action Cache (Option 1).
"""

import os
import tempfile

from PIL import Image, ImageDraw

from pinlet.agent_loop.cache import (
    ActionCache,
    base64_to_image,
    compute_dhash,
    hamming_distance,
    image_to_base64_thumbnail,
)
from pinlet.agent_loop.controller import FeedbackLoopController
from pinlet.types import BoundingBox, WindowInfo


class MockHAL:
    def __init__(self, screen_image: Image.Image, active_window: WindowInfo | None = None):
        self.screen_image = screen_image
        self.active_window = active_window
        self.clicks = []
        self.typed_text = []

    def get_screen_size(self):
        return self.screen_image.size

    def capture_screen(self, region=None):
        if region:
            rx, ry, rw, rh = region
            return self.screen_image.crop((rx, ry, rx + rw, ry + rh))
        return self.screen_image.copy()

    def get_active_window(self):
        return self.active_window

    def list_windows(self):
        return [self.active_window] if self.active_window else []

    def mouse_click(self, x, y, button="left", click_count=1):
        self.clicks.append((x, y, button, click_count))

    def mouse_move(self, x, y):
        pass

    def mouse_down(self, button="left"):
        pass

    def mouse_up(self, button="left"):
        pass

    def mouse_scroll(self, dx, dy):
        pass

    def keyboard_type(self, text, delay_ms=10.0):
        self.typed_text.append(text)

    def keyboard_press_key(self, key):
        pass

    def keyboard_hotkey(self, keys):
        pass

    def get_cursor_position(self):
        return (100, 100)


def create_synthetic_ui(button_box=(200, 200, 100, 36), text="Run"):
    """Creates a clean synthetic desktop image with a drawn UI button."""
    img = Image.new("RGB", (1280, 800), (240, 240, 240))
    draw = ImageDraw.Draw(img)
    bx, by, bw, bh = button_box
    draw.rectangle([bx, by, bx + bw, by + bh], fill=(0, 120, 215), outline=(0, 90, 180), width=2)
    draw.text((bx + 20, by + 10), text, fill=(255, 255, 255))
    return img


def test_dhash_and_hamming_distance():
    """Validates 64-bit dHash computation and Hamming distance properties."""
    img1 = Image.new("RGB", (100, 40), (255, 255, 255))
    draw = ImageDraw.Draw(img1)
    draw.rectangle([10, 10, 90, 30], fill=(0, 0, 0))

    img2 = img1.copy()
    h1 = compute_dhash(img1)
    h2 = compute_dhash(img2)
    assert h1 == h2
    assert hamming_distance(h1, h2) == 0

    # Slight perturbation (single pixel line)
    img3 = img1.copy()
    draw3 = ImageDraw.Draw(img3)
    draw3.line([0, 0, 10, 10], fill=(120, 120, 120))
    h3 = compute_dhash(img3)
    dist = hamming_distance(h1, h3)
    assert dist <= 6  # Close Hamming distance

    # Completely different image (vertical stripes)
    img_diff = Image.new("RGB", (100, 40), (0, 0, 0))
    draw_diff = ImageDraw.Draw(img_diff)
    for x in range(0, 100, 10):
        draw_diff.line([x, 0, x, 40], fill=(255, 255, 255))
    h_diff = compute_dhash(img_diff)
    assert hamming_distance(h1, h_diff) > 10


def test_thumbnail_encoding_and_decoding():
    """Validates thumbnail encoding to compact base64 and round-trip decoding."""
    crop = Image.new("RGB", (120, 40), (30, 144, 255))
    # 1. Unscaled (default max_dim=256)
    b64_unscaled = image_to_base64_thumbnail(crop)
    decoded_unscaled = base64_to_image(b64_unscaled)
    assert decoded_unscaled is not None
    assert decoded_unscaled.size == (120, 40)

    # 2. Downscaled when max_dim < size
    b64_scaled = image_to_base64_thumbnail(crop, max_dim=48)
    decoded_scaled = base64_to_image(b64_scaled)
    assert decoded_scaled is not None
    assert decoded_scaled.size[0] <= 48


def test_window_movement_reprojection():
    """Validates dynamic re-projection when the host window is moved across the desktop."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        cache_path = tmp.name

    try:
        cache = ActionCache(cache_file_path=cache_path)

        orig_win = WindowInfo(
            window_id="0x100",
            title="Vivado 2025.1",
            bbox=BoundingBox(x=100, y=100, width=1000, height=700),
            is_active=True,
        )
        btn_box = BoundingBox(x=250, y=180, width=100, height=36)

        # 1. Record button inside orig_win (window offset is (150, 80))
        cache.record(
            app_key="vivado",
            query="Run Simulation",
            bbox=btn_box,
            center_coords=btn_box.center,
            role="push_button",
            window_info=orig_win,
        )

        entry = cache.get("vivado", "Run Simulation")
        assert entry.window_offset == (150, 80)

        # 2. Window moved by +200 X and +150 Y to (300, 250)
        moved_win = WindowInfo(
            window_id="0x100",
            title="Vivado 2025.1",
            bbox=BoundingBox(x=300, y=250, width=1000, height=700),
            is_active=True,
        )

        status, resolved = cache.resolve_locator(
            app_key="vivado",
            query="Run Simulation",
            current_window=moved_win,
        )

        assert status == "WINDOW_REPROJECTED"
        assert resolved is not None
        # Expected new X = 300 + 150 = 450, new Y = 250 + 80 = 330
        assert resolved.bbox.x == 450
        assert resolved.bbox.y == 330
        assert resolved.center_coords == (450 + 50, 330 + 18)

    finally:
        if os.path.exists(cache_path):
            os.remove(cache_path)


def test_preflight_visual_verification():
    """Validates pre-flight dHash verification against live screen."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        cache_path = tmp.name

    try:
        cache = ActionCache(cache_file_path=cache_path)
        screen = create_synthetic_ui(button_box=(200, 200, 100, 36))
        btn_crop = screen.crop((200, 200, 300, 236))
        btn_box = BoundingBox(x=200, y=200, width=100, height=36)

        cache.record(
            app_key="test_app",
            query="Run",
            bbox=btn_box,
            center_coords=btn_box.center,
            visual_crop=btn_crop,
        )

        # 1. Matching live screen -> VERIFIED
        status, resolved = cache.resolve_locator(
            app_key="test_app",
            query="Run",
            live_screen=screen,
            dhash_threshold=6,
        )
        assert status == "VERIFIED"
        assert resolved.center_coords == (250, 218)

        # 2. Blanked / changed screen -> STALE (when template neighborhood match fails)
        blank_screen = Image.new("RGB", (1280, 800), (255, 255, 255))
        status_stale, _ = cache.resolve_locator(
            app_key="test_app",
            query="Run",
            live_screen=blank_screen,
            dhash_threshold=6,
        )
        assert status_stale == "STALE"

    finally:
        if os.path.exists(cache_path):
            os.remove(cache_path)


def test_localized_neighborhood_drift_auto_reanchoring():
    """Validates drift recovery: button moved by dx=15, dy=-8 is auto-reanchored."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        cache_path = tmp.name

    try:
        cache = ActionCache(cache_file_path=cache_path)

        orig_screen = create_synthetic_ui(button_box=(200, 200, 100, 36))
        btn_crop = orig_screen.crop((200, 200, 300, 236))
        btn_box = BoundingBox(x=200, y=200, width=100, height=36)

        cache.record(
            app_key="eda_tool",
            query="Run",
            bbox=btn_box,
            center_coords=btn_box.center,
            visual_crop=btn_crop,
        )

        # Create drifted screen: button moved by dx=15, dy=-8 to (215, 192)
        drifted_screen = create_synthetic_ui(button_box=(215, 192, 100, 36))

        status, healed = cache.resolve_locator(
            app_key="eda_tool",
            query="Run",
            live_screen=drifted_screen,
            search_radius=60,
            min_match_score=0.78,
            auto_heal=True,
        )

        assert status == "HEALED"
        assert healed is not None
        assert healed.heal_count == 1
        assert healed.bbox.x == 215
        assert healed.bbox.y == 192
        assert healed.center_coords == (215 + 50, 192 + 18)
        assert healed.last_drift == (15, -8)

        # Verify cache persisted healed coordinates
        stats = cache.get_stats()
        assert stats["total_heals"] == 1
        assert stats["healed_ratio"] > 0.0

    finally:
        if os.path.exists(cache_path):
            os.remove(cache_path)


def test_proactive_verify_all():
    """Validates proactive verification and health reporting across multiple entries."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        cache_path = tmp.name

    try:
        cache = ActionCache(cache_file_path=cache_path)

        # Entry 1: Button at (100, 100)
        screen = create_synthetic_ui(button_box=(100, 100, 80, 30), text="OK")
        crop1 = screen.crop((100, 100, 180, 130))
        cache.record(
            "my_app",
            "OK",
            BoundingBox(x=100, y=100, width=80, height=30),
            (140, 115),
            visual_crop=crop1,
        )

        # Entry 2: Button at (300, 100)
        draw = ImageDraw.Draw(screen)
        draw.rectangle([300, 100, 380, 130], fill=(180, 40, 40))
        draw.text((310, 110), "Cancel", fill=(255, 255, 255))
        crop2 = screen.crop((300, 100, 380, 130))
        cache.record(
            "my_app",
            "Cancel",
            BoundingBox(x=300, y=100, width=80, height=30),
            (340, 115),
            visual_crop=crop2,
        )

        # Now test proactive_verify_all on live screen
        report = cache.proactive_verify_all(app_key="my_app", live_screen=screen)
        assert report["total_checked"] == 2
        assert report["verified_count"] == 2
        assert "ok" in report["verified"]
        assert "cancel" in report["verified"]

    finally:
        if os.path.exists(cache_path):
            os.remove(cache_path)


def test_controller_find_and_click_healed():
    """Validates end-to-end FeedbackLoopController find_and_click with auto-reanchoring."""
    drifted_screen = create_synthetic_ui(button_box=(220, 210, 100, 36), text="Compile")
    win = WindowInfo(
        window_id="0x99",
        title="IDE",
        bbox=BoundingBox(x=0, y=0, width=1280, height=800),
        is_active=True,
    )
    mock_hal = MockHAL(drifted_screen, win)
    controller = FeedbackLoopController(mock_hal)

    # Pre-seed cache with original un-drifted position (200, 200) and template crop
    orig_screen = create_synthetic_ui(button_box=(200, 200, 100, 36), text="Compile")
    orig_crop = orig_screen.crop((200, 200, 300, 236))
    controller.action_cache.record(
        app_key=controller._get_active_app_key(),
        query="Compile",
        bbox=BoundingBox(x=200, y=200, width=100, height=36),
        center_coords=(250, 218),
        visual_crop=orig_crop,
        window_info=win,
    )

    # Call find_and_click: should detect drift of (+20, +10), auto-reanchor, and click (270, 228)
    res = controller.find_and_click(query="Compile", verify_delta=False)
    assert res.success is True
    assert "healed" in res.action_type.lower()
    assert (270, 228, "left", 1) in mock_hal.clicks


def test_manage_cache_verify_and_heal(live_screen):
    """Validates manage_cache tool with verify, heal, and stats actions."""
    from pinlet.server import manage_cache

    # 1. Stats action
    stats = manage_cache(action="stats")
    assert "entries_count" in stats
    assert "total_hits" in stats
    assert "total_heals" in stats

    # 2. Verify action on active app
    ver_res = manage_cache(action="verify")
    assert "total_checked" in ver_res
    assert "verified_count" in ver_res
    assert "healed_count" in ver_res

    # 3. Heal action
    heal_res = manage_cache(action="heal")
    assert "total_checked" in heal_res
