"""Wait for the screen to settle before reading it.

A page that is still loading, a sheet that is still sliding in or a list that
is still filling looks like a finished result to a single snapshot. Before an
observation, ``wait_until_settled`` samples a small grayscale thumbnail of the
window until two samples in a row match, so the reader sees the end state.

Thumbnails are 64 px wide and compared by mean absolute difference, so a
blinking caret or a small spinner barely moves the score, while a page change
moves it a lot. It gives up after ``timeout_s`` and says so, rather than
blocking forever on an animation.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from PIL import Image, ImageChops, ImageStat


@dataclass(frozen=True)
class SettleResult:
    """Whether the screen settled, how long it took and how many samples it needed."""

    settled: bool
    waited_ms: float
    samples: int
    last_delta: float | None  # mean abs pixel difference (0-255) of the last two samples

    def as_dict(self) -> dict:
        """The result as a JSON-ready dict."""
        return {
            "settled": self.settled,
            "waited_ms": round(self.waited_ms, 1),
            "samples": self.samples,
            "last_delta": None if self.last_delta is None else round(self.last_delta, 2),
        }


def thumbnail(image: Image.Image, width: int = 64) -> Image.Image:
    """Grayscale thumbnail ``width`` px wide (aspect kept)."""
    w, h = image.size
    if w <= 0 or h <= 0:
        return Image.new("L", (1, 1))
    height = max(1, int(round(h * width / float(w))))
    return image.convert("L").resize((width, height), Image.BILINEAR)


def frame_delta(a: Image.Image, b: Image.Image) -> float:
    """Mean absolute difference (0-255) between two thumbnails; 255 when sizes differ."""
    if a.size != b.size:
        return 255.0
    return float(ImageStat.Stat(ImageChops.difference(a, b)).mean[0])


def wait_until_settled(
    capture: Callable[[], Image.Image],
    timeout_s: float = 2.5,
    interval_s: float = 0.12,
    max_delta: float = 1.5,
    stable_samples: int = 2,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> SettleResult:
    """Waits for the screen to settle.

    Calls ``capture`` every ``interval_s`` until ``stable_samples`` consecutive
    thumbnails differ by at most ``max_delta``, or ``timeout_s`` passes.

    A screen that is already still returns after one interval (two samples).
    Capture errors end the wait as "not settled" instead of raising.
    """
    start = clock()
    prev: Image.Image | None = None
    calm = 0
    samples = 0
    delta: float | None = None
    while True:
        try:
            cur = thumbnail(capture())
        except Exception:
            return SettleResult(False, (clock() - start) * 1000.0, samples, delta)
        samples += 1
        if prev is not None:
            delta = frame_delta(prev, cur)
            calm = calm + 1 if delta <= max_delta else 0
            if calm >= stable_samples - 1:
                return SettleResult(True, (clock() - start) * 1000.0, samples, delta)
        prev = cur
        if clock() - start + interval_s > timeout_s:
            return SettleResult(False, (clock() - start) * 1000.0, samples, delta)
        sleep(interval_s)


def settle_window(
    controller: Any, region: tuple[int, int, int, int] | None, **kw: Any
) -> SettleResult:
    """``wait_until_settled`` on the controller's HAL capture of ``region`` (the active window)."""
    hal = controller.hal
    return wait_until_settled(lambda: hal.capture_screen(region=region), **kw)
