#!/usr/bin/env python3
"""
Measure the Gemma yes/no prompt on labelled crops before changing it.

Each case is an image, a question, and the acceptable answers (yes, no, unsure).
The report counts correct answers, wrong ones (a yes or no that is not
acceptable: the expensive mistake), unsure ones, OCR-unverified evidence, and
latency. Compare two runs to decide whether a prompt or model change helps.

    python scripts/eval_prompts.py --synthetic                 # built-in cases
    python scripts/eval_prompts.py --cases my_cases.jsonl      # your own crops
    python scripts/eval_prompts.py --synthetic --ocr-context   # also show Gemma the OCR text
    python scripts/eval_prompts.py --synthetic --prompt-dir ~/try-prompts   # yes_no.txt override
    python scripts/eval_prompts.py --synthetic --logprobs      # p_yes from token logprobs

A cases file has one JSON object per line; image paths are relative to the file:

    {"image": "crops/synth_done.png", "question": "Did synthesis complete?", "expect": ["yes"]}

Needs Ollama running with the configured vision model. Read-only: never touches the desktop.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw, ImageFont


def _font(size: int):
    for path in (
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/SFNS.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _dialog(title: str, body: str, buttons: list[str]) -> Image.Image:
    img = Image.new("RGB", (520, 220), (236, 236, 236))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 519, 34], fill=(210, 210, 214))
    d.text((14, 8), title, fill=(20, 20, 20), font=_font(18))
    d.text((24, 70), body, fill=(10, 10, 10), font=_font(20))
    x = 500
    for label in reversed(buttons):
        w = 24 + 11 * len(label)
        x -= w + 12
        d.rounded_rectangle(
            [x, 160, x + w, 194], radius=6, fill=(255, 255, 255), outline=(150, 150, 150)
        )
        d.text((x + 12, 167), label, fill=(0, 0, 0), font=_font(17))
    return img


def _status(text: str, dot=None) -> Image.Image:
    img = Image.new("RGB", (600, 60), (248, 248, 248))
    d = ImageDraw.Draw(img)
    if dot:
        d.ellipse([16, 18, 40, 42], fill=dot)
    d.text((54 if dot else 16, 18), text, fill=(30, 30, 30), font=_font(20))
    return img


def synthetic_cases(out_dir: Path) -> list[dict[str, Any]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    specs = [
        (
            "synth_done",
            _dialog("Vivado", "Synthesis Completed successfully.", ["OK"]),
            [("Did synthesis complete?", ["yes"]), ("Is there an error message?", ["no"])],
        ),
        (
            "license_err",
            _dialog("Error", "License checkout failed for Synthesis.", ["Retry", "Cancel"]),
            [("Did the license checkout succeed?", ["no"]), ("Is an error shown?", ["yes"])],
        ),
        (
            "running",
            _status("Running synth_design ..."),
            [("Has synthesis finished?", ["no", "unsure"]), ("Is synthesis running?", ["yes"])],
        ),
        (
            "green",
            _status("Status", dot=(30, 170, 60)),
            [("Is the status indicator green?", ["yes"])],
        ),
        ("red", _status("Status", dot=(210, 40, 40)), [("Is the status indicator green?", ["no"])]),
        (
            "blank",
            Image.new("RGB", (400, 200), (128, 128, 128)),
            [
                ("Did the file save succeed?", ["unsure"]),
                ("Is the Save dialog closed?", ["unsure", "yes"]),
            ],
        ),
        (
            "save_prompt",
            _dialog(
                "Save Changes",
                "Save changes to project.xpr before closing?",
                ["Save", "Discard", "Cancel"],
            ),
            [
                ("Is the Save dialog closed?", ["no"]),
                ("Does the dialog offer to discard changes?", ["yes"]),
            ],
        ),
    ]
    cases = []
    for name, img, questions in specs:
        path = out_dir / f"{name}.png"
        img.save(path)
        for q, expect in questions:
            cases.append({"image": str(path), "question": q, "expect": expect})
    return cases


def load_cases(path: Path) -> list[dict[str, Any]]:
    cases = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            c = json.loads(line)
            img = Path(c["image"])
            c["image"] = str(img if img.is_absolute() else (path.parent / img))
            c["expect"] = [
                e.lower() for e in (c["expect"] if isinstance(c["expect"], list) else [c["expect"]])
            ]
            cases.append(c)
    return cases


def _label(answer) -> str:
    return "unsure" if answer is None else ("yes" if answer else "no")


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--cases", type=Path, help="JSONL cases file")
    ap.add_argument("--synthetic", action="store_true", help="use the built-in synthetic cases")
    ap.add_argument("--synthetic-dir", type=Path, default=Path("/tmp/dc_prompt_eval"))
    ap.add_argument("--no-ocr", action="store_true", help="no OCR at all: evidence is not checked")
    ap.add_argument(
        "--ocr-context",
        action="store_true",
        help="also show Gemma the OCR text (CONFIG.gemma3_ocr_context)",
    )
    ap.add_argument("--prompt-dir", help="folder with <prompt_id>.txt overrides to evaluate")
    ap.add_argument(
        "--logprobs",
        action="store_true",
        help="answer from Gemma's yes/no token probabilities (CONFIG.gemma3_yes_no_logprobs)",
    )
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--json", action="store_true", help="print the full report as JSON")
    args = ap.parse_args()

    from pinlet.config import CONFIG
    from pinlet.gemma.prompts import get_prompt
    from pinlet.perception.ocr import get_ocr_engine
    from pinlet.vdom.gemma3_sidecar import Gemma3Grounder

    if args.prompt_dir is not None:
        CONFIG.gemma3_prompt_dir = args.prompt_dir
    if args.logprobs:
        CONFIG.gemma3_yes_no_logprobs = True
    cases = []
    if args.synthetic:
        cases += synthetic_cases(args.synthetic_dir)
    if args.cases:
        cases += load_cases(args.cases)
    if not cases:
        ap.error("give --synthetic and/or --cases")

    gemma = Gemma3Grounder()
    if not gemma.client.has_model(gemma.vision_model):
        print(
            f"Gemma model {gemma.vision_model} is not available at {gemma.endpoint}",
            file=sys.stderr,
        )
        return 2
    ocr = None if args.no_ocr else get_ocr_engine()
    if ocr is not None and not ocr.is_available:
        ocr = None
    gemma.client.warmup(gemma.vision_model)

    rows, latencies = [], []
    for c in cases:
        img = Image.open(c["image"]).convert("RGB")
        lines = [ln.text for ln in ocr.recognize(img)] if ocr else None
        for _ in range(args.repeats):
            t = time.perf_counter()
            res = gemma.ask_yes_no(
                img,
                c["question"],
                ocr_lines=lines if args.ocr_context else None,
                verify_lines=lines,
            )
            ms = (time.perf_counter() - t) * 1000.0
            latencies.append(ms)
            got = _label(res["answer"])
            if res["source"] == "fallback":
                outcome = "error"
            elif got in c["expect"]:
                outcome = "correct"
            elif got == "unsure":
                outcome = "unsure"
            else:
                outcome = "wrong"
            rows.append(
                {
                    "image": Path(c["image"]).name,
                    "question": c["question"],
                    "expect": c["expect"],
                    "got": got,
                    "confidence": round(res["confidence"], 2),
                    "outcome": outcome,
                    "evidence": res["evidence"],
                    "evidence_type": res.get("evidence_type"),
                    "evidence_verified": res.get("evidence_verified"),
                    "ms": round(ms),
                    "p_yes": res.get("p_yes"),
                    "source": res["source"],
                }
            )

    n = len(rows)
    count = lambda k: sum(1 for r in rows if r["outcome"] == k)
    report = {
        "prompt": get_prompt("yes_no_logprob" if args.logprobs else "yes_no").tag,
        "logprob_answers": sum(1 for r in rows if r["source"] == "gemma_logprobs"),
        "model": gemma.vision_model,
        "ocr_verify": ocr is not None,
        "ocr_context": ocr is not None and args.ocr_context,
        "cases": n,
        "correct": count("correct"),
        "wrong": count("wrong"),
        "unsure": count("unsure"),
        "errors": count("error"),
        "unverified_text_evidence": sum(1 for r in rows if r["evidence_verified"] is False),
        "latency_ms_p50": round(statistics.median(latencies)) if latencies else None,
        "latency_ms_max": round(max(latencies)) if latencies else None,
        "rows": rows,
    }
    if args.json:
        print(json.dumps(report, indent=1))
    else:
        for r in rows:
            mark = {"correct": "ok ", "wrong": "XX ", "unsure": "?? ", "error": "!! "}[r["outcome"]]
            ver = {True: " [ocr ok]", False: " [ocr MISSING]", None: ""}[r["evidence_verified"]]
            print(
                f"{mark}{r['image']:<16} {r['question'][:44]:<44} want {'/'.join(r['expect']):<10} "
                f"got {r['got']:<6} {r['confidence']:.2f} {r['ms']:>5} ms  {r['evidence'][:40]}{ver}"
            )
        print(
            f"\n{report['prompt']} on {report['model']} (OCR verify {'on' if report['ocr_verify'] else 'off'}, "
            f"context {'on' if report['ocr_context'] else 'off'}): "
            f"{report['correct']}/{n} correct, {report['wrong']} wrong, {report['unsure']} unsure, "
            f"{report['errors']} errors; p50 {report['latency_ms_p50']} ms"
        )
    return 1 if report["wrong"] or report["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
