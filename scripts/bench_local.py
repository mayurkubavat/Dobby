"""
Read-only benchmark of the local perception tiers on a real desktop.

For the active window it measures, over N repeats: capture, accessibility walk,
OCR (cold and cached), Gemma yes/no (cold vs warm), and Gemma Set-of-Marks
labeling (cold vs cached). It also estimates the image tokens a screenshot of the
same window would cost a cloud model versus the text tokens each local answer
costs, so you can see what each tier saves. Never clicks or types.

    python scripts/bench_local.py --repeats 3
    python scripts/bench_local.py --no-gemma
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time


def ms(fn):
    t = time.perf_counter()
    out = fn()
    return out, (time.perf_counter() - t) * 1000.0


def claude_image_tokens(w: int, h: int, long_edge: int = 1568, max_px: int = 1_150_000) -> int:
    """Approximate vision tokens (w*h/750) after the API's downscale limits."""
    scale = min(1.0, long_edge / max(w, h), (max_px / float(w * h)) ** 0.5)
    return int((w * scale) * (h * scale) / 750)


def text_tokens(obj) -> int:
    return max(1, len(json.dumps(obj, default=str)) // 4)


def med(xs):
    return round(statistics.median(xs), 1) if xs else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--no-gemma", action="store_true")
    args = ap.parse_args()

    from dobby.hal.factory import get_desktop_hal
    from dobby.perception.ax import get_accessibility_inspector
    from dobby.perception.ocr import get_ocr_engine

    hal = get_desktop_hal()
    win = hal.get_active_window()
    region = (win.bbox.x, win.bbox.y, win.bbox.width, win.bbox.height) if win else None
    report = {"platform": sys.platform, "window": win.title if win else None, "region": region}

    caps = []
    img = None
    for _ in range(args.repeats):
        img, t = ms(lambda: hal.capture_screen(region=region))
        caps.append(t)
    report["capture_ms"] = med(caps)
    shot_tokens = claude_image_tokens(*img.size)
    report["screenshot_tokens_if_sent"] = shot_tokens

    insp = get_accessibility_inspector()
    if insp.is_available:
        runs = [ms(insp.inspect_desktop) for _ in range(args.repeats)]
        els = runs[-1][0]
        report["accessibility"] = {
            "ms": med([t for _, t in runs]),
            "elements": len(els),
            "text_tokens": text_tokens([[e.role, e.name, e.bbox.x, e.bbox.y] for e in els]),
        }
    else:
        els = []
        report["accessibility"] = {"available": False, "error": getattr(insp, "error", None)}

    ocr = get_ocr_engine()
    if ocr.is_available:
        lines, cold = ms(lambda: ocr.inner.recognize(img))
        _, warm = ms(lambda: ocr.recognize(img))
        _, cached = ms(lambda: ocr.recognize(img))
        report["ocr"] = {
            "engine": ocr.name,
            "first_ms": round(cold, 1),
            "uncached_ms": round(warm, 1),
            "cached_ms": round(cached, 1),
            "lines": len(lines),
            "text_tokens": text_tokens([ln.as_dict() for ln in lines]),
        }

    if not args.no_gemma:
        from dobby.vdom.gemma3_sidecar import Gemma3Grounder

        g = Gemma3Grounder()
        if g.is_available():
            q = "Is there any visible text in this window?"
            first, t_first = ms(lambda: g.ask_yes_no(img, q))
            warm_runs = [
                ms(lambda: g.ask_yes_no(img, q))[1] for _ in range(max(1, args.repeats - 1))
            ]
            report["gemma_yes_no"] = {
                "first_ms": round(t_first, 1),
                "warm_ms": med(warm_runs),
                "answer": first,
                "text_tokens": text_tokens(first),
            }
            boxes = []
            if region and els:
                boxes = [
                    (e.bbox.x - region[0], e.bbox.y - region[1], e.bbox.width, e.bbox.height)
                    for e in els[:10]
                ]
            if boxes:
                labels, t_lab = ms(lambda: g.marks.label_marks(img, boxes, app_key="bench"))
                _, t_cached = ms(lambda: g.marks.label_marks(img, boxes, app_key="bench"))
                report["gemma_label_marks"] = {
                    "boxes": len(boxes),
                    "named": len(labels),
                    "ms": round(t_lab, 1),
                    "cached_ms": round(t_cached, 1),
                }
            report["gemma_stats"] = g.diagnostics()
        else:
            report["gemma"] = {"available": False, "installed": sorted(g.client.installed_models())}

    # What each local answer saves versus sending the screenshot instead
    savings = {}
    for key in ("accessibility", "ocr", "gemma_yes_no"):
        tt = (report.get(key) or {}).get("text_tokens")
        if tt:
            savings[key] = {"text_tokens": tt, "saved_vs_screenshot": shot_tokens - tt}
    report["token_savings_per_check"] = savings

    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
