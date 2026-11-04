"""expect_ax_change on a platform without an accessibility diff is checked on pixels."""

from PIL import Image

from dobby.agent_loop.bicameral import BicameralStepController, VerificationTier


class NoAXHAL:
    """No accessibility diff (as on macOS today); the screen flips to a new frame on click."""

    def __init__(self, changes: bool) -> None:
        self.changes = changes
        self.clicked = False
        self.clicks = []

    def capture_screen(self, region=None):
        if self.clicked and self.changes:
            img = Image.new("RGB", (200, 100), (30, 30, 30))
            img.paste((240, 240, 240), (100, 0, 200, 100))  # a preview pane opened on the right
            return img
        return Image.new("RGB", (200, 100), (30, 30, 30))

    def mouse_click(self, x, y, button="left", click_count=1):
        self.clicked = True
        self.clicks.append((x, y))

    def get_accessibility_tree(self, diff_only=False, **_):
        return {"available": False, "changed": False, "nodes": []}


class AskedGemma:
    def __init__(self) -> None:
        self.asked = False

    def evaluate_step(self, action, args, expected, ax_diff):
        self.asked = True
        return {"verified": False, "confidence": 0.0, "reason": "image does not show any mouse click action"}


def _controller(hal, gemma=None):
    return BicameralStepController(hal=hal, local_gemma_client=gemma, settle_timeout_s=0.05, settle_poll_s=0.01)


def test_visible_change_passes_at_tier0_without_asking_gemma():
    gemma = AskedGemma()
    verdict = _controller(NoAXHAL(changes=True), gemma).execute_step(
        "mouse_click", {"x": 10, "y": 10}, {"expect_ax_change": True}
    )
    assert verdict.success is True
    assert verdict.tier_used == VerificationTier.TIER0_DETERMINISTIC
    assert "screen changed" in verdict.reason
    assert gemma.asked is False


def test_no_change_says_so_instead_of_a_vague_model_answer():
    gemma = AskedGemma()
    verdict = _controller(NoAXHAL(changes=False), gemma).execute_step(
        "mouse_click", {"x": 10, "y": 10}, {"expect_ax_change": True}
    )
    assert verdict.success is False
    assert "did not change" in verdict.reason
    assert "accessibility diff" in verdict.reason
    assert gemma.asked is False


def test_a_visual_question_still_goes_to_the_local_model():
    gemma = AskedGemma()
    _controller(NoAXHAL(changes=False), gemma).execute_step(
        "mouse_click", {"x": 10, "y": 10}, {"expect_ax_change": True, "visual_question": "Is the preview open?"}
    )
    assert gemma.asked is True
