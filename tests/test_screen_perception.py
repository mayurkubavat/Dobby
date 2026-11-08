"""Settle waiting, icon-detector fusion, occlusion checks and visual (Set-of-Marks) choice."""

import math
from types import SimpleNamespace

from PIL import Image, ImageDraw

from dobby.gemma.client import GemmaClient
from dobby.gemma.decide import GemmaDecider
from dobby.screen_state import StateTracker, build_state
from dobby.screen_state.observe import act_on_element, decide_next_element, observe
from dobby.screen_state.occlusion import check_occlusion
from dobby.screen_state.settle import wait_until_settled
from dobby.screen_state.visual import decide_visual_element
from dobby.types import BoundingBox, WindowInfo

WIN = WindowInfo(
    window_id="7",
    title="Doc",
    bbox=BoundingBox(x=0, y=0, width=400, height=300),
    is_active=True,
    app_name="Editor",
)


def node(role, name, x, y, w=80, h=24, states=("enabled",)):
    return {
        "role": role,
        "name": name,
        "bbox": {"x": x, "y": y, "width": w, "height": h},
        "states": list(states),
        "interactable": True,
    }


def frame(shade=255, box=None):
    img = Image.new("RGB", (400, 300), (shade, shade, shade))
    if box:
        ImageDraw.Draw(img).rectangle(box, fill=(0, 0, 0))
    return img


# ------------------------------------------------------------------ settle


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def test_settle_returns_after_two_still_samples():
    clock = FakeClock()
    res = wait_until_settled(lambda: frame(), sleep=clock.sleep, clock=clock)
    assert res.settled and res.samples == 2 and res.last_delta == 0.0


def test_settle_waits_for_loading_page_then_succeeds():
    clock = FakeClock()
    frames = iter([frame(0), frame(80), frame(160), frame(255), frame(255)])
    res = wait_until_settled(lambda: next(frames), sleep=clock.sleep, clock=clock)
    assert res.settled and res.samples == 5


def test_settle_gives_up_on_endless_animation_and_tolerates_capture_errors():
    clock = FakeClock()
    n = [0]

    def spinning():
        n[0] += 1
        return frame(0 if n[0] % 2 else 255)

    res = wait_until_settled(spinning, timeout_s=1.0, sleep=clock.sleep, clock=clock)
    assert not res.settled and res.waited_ms <= 1000

    def broken():
        raise OSError("no display")

    assert not wait_until_settled(broken, sleep=clock.sleep, clock=clock).settled


def test_small_caret_blink_still_counts_as_settled():
    clock = FakeClock()
    frames = iter([frame(box=(100, 100, 101, 112)), frame(), frame(box=(100, 100, 101, 112))])
    assert wait_until_settled(lambda: next(frames), sleep=clock.sleep, clock=clock).settled


# --------------------------------------------------------- detector fusion


def test_detector_boxes_become_unnamed_icons_only_where_nothing_is_known():
    s = build_state(
        [node("push button", "Save", 300, 250)],
        detector_boxes=[
            (305, 252, 40, 20),  # inside Save -> dropped
            (10, 10, 24, 24),  # new icon
            (0, 0, 400, 300),  # panel containing Save -> dropped
            (12, 12, 20, 20),  # inside the first icon -> dropped
            (900, 900, 20, 20),
        ],  # outside the window -> dropped
        window=WIN,
    )
    icons = [e for e in s.elements if e.source == "detector"]
    assert len(icons) == 1 and icons[0].role == "icon" and icons[0].label == ""
    assert "seen, not named" in icons[0].describe()


# --------------------------------------------------------------- occlusion


class Win(SimpleNamespace):
    pass


def test_occlusion_by_window_in_front_and_clear_when_target_is_front():
    front = Win(
        window_id="9",
        title="Alert",
        app_name="Other",
        bbox=BoundingBox(x=50, y=50, width=200, height=100),
    )
    target = Win(window_id="7", title="Doc", app_name="Editor", bbox=WIN.bbox)
    hal = SimpleNamespace(list_windows=lambda: [front, target])
    occ = check_occlusion(hal, (100, 80, 40, 20), (120, 90), target_window_id="7")
    assert occ.covered and "Alert" in occ.reason
    hal2 = SimpleNamespace(list_windows=lambda: [target, front])
    assert (
        check_occlusion(hal2, (100, 80, 40, 20), (120, 90), target_window_id="7").covered is False
    )


def test_occlusion_by_hit_test_and_unknown_without_checks():
    hal = SimpleNamespace(list_windows=list)
    sheet = lambda x, y: {
        "role": "dialog",
        "name": "Save changes?",
        "bbox": {"x": 0, "y": 0, "width": 400, "height": 200},
    }
    assert check_occlusion(
        hal, (100, 80, 40, 20), (120, 90), "7", "push button", hit_test=sheet
    ).covered
    itself = lambda x, y: {
        "role": "push button",
        "name": "OK",
        "bbox": {"x": 100, "y": 80, "width": 40, "height": 20},
    }
    assert (
        check_occlusion(
            hal, (100, 80, 40, 20), (120, 90), "7", "push button", hit_test=itself
        ).covered
        is False
    )
    assert check_occlusion(hal, (100, 80, 40, 20), (120, 90), "7").covered is None


# ------------------------------------------------------ observe/act wiring


class FakeHAL:
    def __init__(self, windows=None, shots=None):
        self.clicks = []
        self.windows = windows
        self.shots = shots or [frame()]

    def get_active_window(self):
        return WIN

    def list_windows(self):
        if self.windows is None:
            raise NotImplementedError
        return self.windows

    def capture_screen(self, region=None):
        return self.shots.pop(0) if len(self.shots) > 1 else self.shots[0]

    def mouse_click(self, x, y, button="left", click_count=1):
        self.clicks.append((x, y))

    def keyboard_type(self, text, delay_ms=12.0):
        pass

    def keyboard_press_key(self, key):
        pass


class FakeGrounder:
    def __init__(self, boxes):
        self.boxes = boxes

    def generate_proposals(self, image, offset=(0, 0)):
        return [
            {"bbox": {"x": x + offset[0], "y": y + offset[1], "width": w, "height": h}}
            for x, y, w, h in self.boxes
        ]


class FakeController:
    def __init__(self, tree, hal=None, icons=()):
        self.tree = tree
        self.hal = hal or FakeHAL()
        self.neural_grounder = FakeGrounder(icons)

    def get_accessibility_tree(self, app_name_filter=None, filter_interactable=True):
        return self.tree

    def read_screen_lines(self, region=None):
        return []

    def execute_with_verification(self, action_name, action_func, verify_delta=True, **kw):
        action_func()
        return {"success": True}


def test_observe_settles_and_reports_it():
    ctl = FakeController([node("push button", "OK", 10, 10)])
    s = observe(ctl, StateTracker(), settle=True)
    assert s.settle["settled"] and s.window_id == "7"


def test_act_refuses_covered_target():
    front = Win(
        window_id="9",
        title="Popup",
        app_name="X",
        bbox=BoundingBox(x=0, y=0, width=200, height=200),
    )
    target = Win(window_id="7", title="Doc", app_name="Editor", bbox=WIN.bbox)
    ctl = FakeController([node("push button", "OK", 10, 10)], hal=FakeHAL(windows=[front, target]))
    t = StateTracker()
    s = observe(ctl, t)
    res = act_on_element(ctl, t, "e1", s.generation)
    assert not res["ok"] and "Popup" in res["reason"] and ctl.hal.clicks == []


def test_act_on_detector_icon_rebinds_with_detector():
    ctl = FakeController([node("push button", "OK", 300, 250)], icons=[(10, 10, 24, 24)])
    t = StateTracker()
    s = observe(ctl, t, use_detector=True)
    icon = next(e for e in s.elements if e.source == "detector")
    res = act_on_element(ctl, t, icon.eid, s.generation)
    assert res["ok"] and ctl.hal.clicks == [(22, 22)] and res["settle"]["settled"]


# ------------------------------------------------------------ visual choice


class FakeOllama:
    """Letter answers for choices, yes/no for statements; records whether images were sent."""

    def __init__(self, letter="A", yes=0.95):
        self.letter, self.yes, self.bodies = letter, yes, []

    def __call__(self, method, url, body, timeout):
        if url.endswith("/api/tags"):
            return {"models": [{"name": "gemma3:4b"}]}
        self.bodies.append(body)
        if "Statement:" in body["messages"][1]["content"]:
            top = [("yes", math.log(self.yes)), ("no", math.log(1 - self.yes))]
        else:
            top = [(self.letter, math.log(0.9)), ("Z", math.log(0.1))]
        return {
            "message": {"content": top[0][0]},
            "logprobs": [
                {
                    "token": top[0][0],
                    "logprob": top[0][1],
                    "top_logprobs": [{"token": t, "logprob": lp} for t, lp in top],
                }
            ],
        }


def test_visual_choice_marks_icons_and_maps_letter_to_eid():
    ctl = FakeController(
        [node("push button", "Save", 300, 250)], icons=[(110, 60, 24, 24), (150, 60, 24, 24)]
    )
    fake = FakeOllama(letter="B")
    out = decide_visual_element(
        ctl, StateTracker(), GemmaDecider(GemmaClient(transport=fake)), "zoom to fit"
    )
    assert (
        out["marks"] == 3
        and out["gate"]["verdict"] == "escalate"
        and "suggestion" in out["gate"]["reason"]
    )
    assert out["element"]["source"] == "detector" and out["element"]["bbox"]["x"] == 150
    assert (
        all("images" in b["messages"][1] for b in fake.bodies) and len(fake.bodies) == 2
    )  # choice + close-up


def test_visual_choice_close_up_no_escalates():
    ctl = FakeController([], icons=[(110, 60, 24, 24)])
    out = decide_visual_element(
        ctl, StateTracker(), GemmaDecider(GemmaClient(transport=FakeOllama(yes=0.1))), "zoom"
    )
    assert out["gate"]["verdict"] == "escalate" and "close-up" in out["gate"]["reason"]


def test_text_lane_escalation_falls_back_to_visual():
    ctl = FakeController([node("push button", "Cancel", 300, 250)], icons=[(10, 10, 24, 24)])
    fake = FakeOllama(letter="A", yes=0.1)  # text pick gets a "no" -> escalate -> visual also "no"
    out = decide_next_element(
        ctl,
        StateTracker(),
        GemmaDecider(GemmaClient(transport=fake)),
        "zoom to fit",
        visual_fallback=True,
    )
    assert out["source"] == "visual" and out["text_lane"]["gate"]["verdict"] == "escalate"


# ------------------------------------------------- fixes from the Mac smoke test


def test_visual_choice_skips_menu_bar_and_off_window_elements():
    win = WindowInfo(
        window_id="7",
        title="Doc",
        bbox=BoundingBox(x=100, y=100, width=400, height=300),
        is_active=True,
        app_name="Editor",
    )
    hal = FakeHAL()
    hal.get_active_window = lambda: win
    ctl = FakeController(
        [node("menu item", "View", 60, 0, 40, 22), node("push button", "", 120, 120, 30, 30)],
        hal=hal,
    )
    fake = FakeOllama(letter="A")
    out = decide_visual_element(
        ctl, StateTracker(), GemmaDecider(GemmaClient(transport=fake)), "open search"
    )
    assert (
        out["marks"] == 1
        and out["element"]["bbox"]["x"] == 120
        and out["gate"]["verdict"] == "escalate"
    )


def test_degenerate_detector_falls_back_to_contours_and_scales_retina():
    from dobby.screen_state.observe import detect_icon_boxes, looks_degenerate

    class Grounder:
        def generate_proposals(self, image, offset=(0, 0)):
            return [{"bbox": {"x": 10 * i, "y": 0, "width": 257, "height": 113}} for i in range(8)]

        def _generate_cv_proposals(self, image, offset=(0, 0)):
            return [
                {"bbox": {"x": 40, "y": 20, "width": 48, "height": 48}},  # 24x24 pt icon
                {"bbox": {"x": 0, "y": 0, "width": 700, "height": 40}},
            ]  # toolbar strip, too big

    hal = FakeHAL(shots=[Image.new("RGB", (800, 600))])  # 2x capture of a 400x300 window
    ctl = SimpleNamespace(hal=hal, neural_grounder=Grounder())
    assert looks_degenerate([(0, 0, 257, 113)] * 8)
    assert detect_icon_boxes(ctl, (100, 50, 400, 300)) == [(120, 60, 24, 24)]


def test_settle_auto_only_right_after_an_action():
    ctl = FakeController([node("push button", "OK", 10, 10)])
    t = StateTracker()
    assert observe(ctl, t, settle="auto").settle is None
    t.mark_action()
    assert observe(ctl, t, settle="auto").settle["settled"]


def test_visual_lane_never_offers_window_controls_or_frame_corners():
    from dobby.screen_state.observe import detect_icon_boxes

    hal = FakeHAL()
    ctl = FakeController(
        [
            node("push button", "", 8, 6, 14, 14),  # close button (title bar, left)
            node("push button", "", 200, 150, 30, 30),
        ],
        hal=hal,
    )
    out = decide_visual_element(
        ctl, StateTracker(), GemmaDecider(GemmaClient(transport=FakeOllama())), "do it"
    )
    assert out["marks"] == 1 and out["element"]["bbox"]["x"] == 200

    class Grounder:
        def generate_proposals(self, image, offset=(0, 0)):
            return [
                {"bbox": {"x": 0, "y": 0, "width": 22, "height": 19}},  # window corner
                {"bbox": {"x": 376, "y": 278, "width": 24, "height": 22}},  # opposite corner
                {"bbox": {"x": 100, "y": 100, "width": 24, "height": 24}},
                {"bbox": {"x": 150, "y": 100, "width": 24, "height": 24}},
                {"bbox": {"x": 200, "y": 100, "width": 24, "height": 24}},
            ]

    boxes = detect_icon_boxes(
        SimpleNamespace(hal=FakeHAL(), neural_grounder=Grounder()), (0, 0, 400, 300)
    )
    assert sorted(b[0] for b in boxes) == [100, 150, 200]
