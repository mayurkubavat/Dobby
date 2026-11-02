"""
Read-only smoke test of the local perception stack on a real desktop (macOS or Linux).

It never clicks or types. It checks: HAL (screen size, windows, capture),
accessibility tree, OCR, and the local Gemma sidecar (one yes/no question and one
Set-of-Marks labeling pass), and prints timings as JSON.

    python scripts/smoke_local.py            # all checks
    python scripts/smoke_local.py --no-gemma # skip Gemma calls
"""

from __future__ import annotations

import argparse
import json
import sys
import time


def timed(fn):
    start = time.perf_counter()
    try:
        return fn(), round((time.perf_counter() - start) * 1000.0, 1), None
    except Exception as e:  # report and continue
        return None, round((time.perf_counter() - start) * 1000.0, 1), f"{type(e).__name__}: {e}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-gemma", action="store_true")
    args = ap.parse_args()

    from dobby.hal.factory import get_desktop_hal
    from dobby.perception.ax import get_accessibility_inspector
    from dobby.perception.ocr import get_ocr_engine

    report = {"platform": sys.platform}
    hal = get_desktop_hal()
    report["hal"] = type(hal).__name__

    size, ms, err = timed(hal.get_screen_size)
    report["screen_size"] = {"value": size, "ms": ms, "error": err}

    wins, ms, err = timed(hal.list_windows)
    report["windows"] = {"count": len(wins or []), "ms": ms, "error": err,
                         "first": [w.title for w in (wins or [])[:5]]}
    active, _, _ = timed(hal.get_active_window)
    region = None
    if active:
        region = (active.bbox.x, active.bbox.y, active.bbox.width, active.bbox.height)
        report["active_window"] = {"title": active.title, "bbox": list(region)}

    img, ms, err = timed(lambda: hal.capture_screen(region=region))
    report["capture"] = {"size": list(img.size) if img else None, "ms": ms, "error": err,
                         "non_blank": bool(img and img.getextrema() != ((0, 0), (0, 0), (0, 0)))}

    insp = get_accessibility_inspector()
    els, ms, err = timed(insp.inspect_desktop)
    report["accessibility"] = {"inspector": type(insp).__name__, "available": insp.is_available,
                               "elements": len(els or []), "ms": ms, "error": err or getattr(insp, "error", None),
                               "sample": [f"{e.role}: {e.name}" for e in (els or [])[:8]]}

    ocr = get_ocr_engine()
    if img is not None:
        lines, ms, err = timed(lambda: ocr.recognize(img))
        lines2, ms2, _ = timed(lambda: ocr.recognize(img))
        report["ocr"] = {"engine": ocr.name, "available": ocr.is_available, "lines": len(lines or []),
                         "ms": ms, "cached_ms": ms2, "error": err or ocr.diagnostics().get("error"),
                         "sample": [ln.text for ln in (lines or [])[:8]]}

    if not args.no_gemma and img is not None:
        from dobby.vdom.gemma3_sidecar import Gemma3Grounder

        g = Gemma3Grounder()
        report["gemma_available"] = g.is_available()
        if report["gemma_available"]:
            ans, ms, err = timed(lambda: g.ask_yes_no(img, "Is there any visible text in this window?"))
            report["gemma_yes_no"] = {"result": ans, "ms": ms, "error": err}
            boxes = [(e.bbox.x - region[0], e.bbox.y - region[1], e.bbox.width, e.bbox.height)
                     for e in (els or [])[:8]] if region else []
            if boxes:
                labels, ms, err = timed(lambda: g.marks.label_marks(img, boxes, app_key="smoke"))
                report["gemma_label_marks"] = {"boxes": len(boxes), "named": len(labels or {}), "ms": ms,
                                               "error": err, "labels": labels}
                _, ms_cached, _ = timed(lambda: g.marks.label_marks(img, boxes, app_key="smoke"))
                report["gemma_label_marks"]["cached_ms"] = ms_cached
        report["gemma_stats"] = g.diagnostics()

    print(json.dumps(report, indent=2, default=str))
    from dobby.personality import startup_line

    greeting = startup_line()
    if greeting:
        print(greeting, file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
