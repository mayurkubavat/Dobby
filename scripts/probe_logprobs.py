#!/usr/bin/env python3
"""
Check that the local Ollama returns token logprobs for the Gemma model, and time
a logit-scored choice against a generated JSON answer on the same question.

    python scripts/probe_logprobs.py            # uses CONFIG.gemma3_vision_model
    python scripts/probe_logprobs.py --model gemma3:4b --repeats 5

Exit code 0 when logprobs work, 3 when this Ollama returns none (upgrade Ollama
to use pinlet.gemma.decide; it falls back to uncalibrated answers until then).
Read-only: never touches the desktop.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> int:
    from pinlet.config import CONFIG
    from pinlet.gemma.client import GemmaClient
    from pinlet.gemma.decide import GemmaDecider
    from pinlet.gemma.gate import gate

    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--model", default=CONFIG.gemma3_vision_model)
    ap.add_argument("--repeats", type=int, default=3)
    args = ap.parse_args()

    client = GemmaClient(CONFIG.gemma3_endpoint, keep_alive=CONFIG.gemma3_keep_alive)
    if not client.has_model(args.model):
        print(f"{args.model} is not installed at {client.base_url}", file=sys.stderr)
        return 2
    try:
        version = client._transport("GET", f"{client.base_url}/api/version", None, 2.0).get(
            "version"
        )
    except Exception:
        version = "unknown"
    print(f"Ollama {version}, model {args.model}")
    client.warmup(args.model)

    decider = GemmaDecider(client, model=args.model)
    state = (
        "Goal: save the open document.\nWindow: TextEdit, 'Untitled' with unsaved changes.\n"
        "A dialog asks: Do you want to save changes?"
    )
    options = ["Don't Save", "Cancel", "Save"]

    choice_ms, json_ms = [], []
    d = None
    for _ in range(max(1, args.repeats)):
        t = time.perf_counter()
        d = decider.choose(state, "Which button saves the document?", options)
        choice_ms.append((time.perf_counter() - t) * 1000)
        t = time.perf_counter()
        client.chat_json(
            args.model,
            "Pick the button that saves the document.",
            state + "\nButtons: " + ", ".join(options),
            {
                "type": "object",
                "properties": {"button": {"type": "string", "enum": options}},
                "required": ["button"],
            },
            max_tokens=40,
        )
        json_ms.append((time.perf_counter() - t) * 1000)

    print("decision:", d.as_dict())
    print("gate(click):", gate(d, "click").as_dict())
    print(
        f"logprob choice p50 {statistics.median(choice_ms):.0f} ms, generated JSON p50 {statistics.median(json_ms):.0f} ms"
    )
    if not d.calibrated:
        print(
            "No logprobs in the reply: this Ollama is too old for logit-scored decisions.",
            file=sys.stderr,
        )
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
