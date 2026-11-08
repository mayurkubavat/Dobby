"""Regression tests for the review fixes on the session facade, bicameral Tier 0 and toolsets."""

from pinlet.agent_loop.bicameral import BicameralStepController
from pinlet.hal.session import DesktopSession
from pinlet.types import DisplayMetrics, DisplayTopology


class _Backend:
    display_server = "Quartz"

    def __init__(self):
        self.clicks = []

    def mouse_click(self, x, y, button="left", click_count=1):
        self.clicks.append((x, y))

    def get_screen_size(self):
        return (100, 100)


def _topology():
    return DisplayTopology(
        displays=[DisplayMetrics(display_id="p", origin=(0, 0), size=(100, 100), scale=1.0)],
        primary_id="p",
    )


def test_session_passes_backend_attributes_through():
    session = DesktopSession(backend=_Backend(), topology=_topology(), quiescence=None)
    assert session.display_server == "Quartz"


def test_tier0_uses_text_reader_on_live_hal():
    calls = []

    def reader(contains=None):
        calls.append(contains)
        return {"available": True, "count": 1, "lines": [{"text": contains}]}

    ctl = BicameralStepController(_Backend(), text_reader=reader)
    verdict = ctl.execute_step("mouse_click", {"x": 1, "y": 2}, {"assert_screen_text": "Saved"})
    assert verdict.success and verdict.tokens_consumed == 0
    assert calls == ["Saved"]


def test_unavailable_ocr_never_proves_text_absent():
    ctl = BicameralStepController(
        _Backend(), text_reader=lambda contains=None: {"available": False, "lines": []}
    )
    assert ctl._read_text("Error") is None


def test_cheap_perception_tools_are_core():
    from pinlet.server import _TOOL_DOMAINS

    for name in (
        "get_accessibility_tree",
        "find_element",
        "find_and_click",
        "read_screen_text",
        "ask_screen",
    ):
        assert _TOOL_DOMAINS[name][0] == "core", name
    assert _TOOL_DOMAINS["run_terminal_command"][0] != "core"


def test_controller_without_session_has_no_x11_monitor(mock_hal):
    from pinlet.agent_loop.controller import FeedbackLoopController

    assert FeedbackLoopController(mock_hal).quiescence_monitor is None
