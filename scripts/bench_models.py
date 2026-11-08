#!/usr/bin/env python3
"""
Compare local models on Dobby's decision jobs before switching a role.

    python scripts/bench_models.py                              # the models the roles use now
    python scripts/bench_models.py --models gemma3:4b,qwen2.5vl:7b,qwen3-vl:8b
    python scripts/bench_models.py --installed                  # every known candidate already pulled
    python scripts/bench_models.py --tasks pick_visual --json out.json

Tasks: check (yes/no on crops), pick_text (pick an element or "none" from a text
screen state), pick_visual (pick a lettered box or "none" on a drawn window).
Columns that matter most: "confident wrong" (wrong answers at p >= 0.9) and
"says none when absent". Never downloads: missing models show as not installed.
Then point a role at the winner: set_model_role, ~/.dobby/models.json or
DOBBY_MODEL_<ROLE>.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dobby.config import CONFIG  # noqa: E402
from dobby.gemma.client import GemmaClient  # noqa: E402
from dobby.local_models import KNOWN_MODELS, ROLES, model_for, profile  # noqa: E402
from dobby.model_bench import bench_model, markdown  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--models", help="comma-separated Ollama tags (default: the models the roles use now)"
    )
    ap.add_argument(
        "--installed",
        action="store_true",
        help="add every known candidate that is already installed",
    )
    ap.add_argument("--tasks", default="check,pick_text,pick_visual")
    ap.add_argument("--endpoint", default=CONFIG.gemma3_endpoint)
    ap.add_argument("--timeout", type=float, default=90.0)
    ap.add_argument("--json", help="also write the full results here")
    args = ap.parse_args()

    client = GemmaClient(base_url=args.endpoint, timeout=args.timeout, keep_alive="2m")
    installed = client.installed_models(refresh=True)
    if not installed:
        print(f"Ollama at {client.base_url} is not reachable or has no models.", file=sys.stderr)
        return 2
    models = [m.strip() for m in args.models.split(",")] if args.models else []
    if not models:
        models = list(dict.fromkeys(model_for(r) for r in ROLES))
    if args.installed:
        models += [p.tag for p in KNOWN_MODELS if p.tag in installed and p.tag not in models]
    tasks = [t.strip() for t in args.tasks.split(",") if t.strip()]

    results = []
    for m in models:
        prof = profile(m)
        print(f"... {m}", file=sys.stderr, flush=True)
        results.append(
            bench_model(
                client, m, tasks, vision=prof.vision if prof else None, timeout=args.timeout
            )
        )
    print(markdown(results))
    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
