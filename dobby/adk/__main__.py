"""python -m dobby.adk plan.json [--in-process] [--model ollama_chat/gemma3:4b] [--json]"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys


def main() -> int:
    ap = argparse.ArgumentParser(prog="python -m dobby.adk", description=__doc__)
    ap.add_argument("plan", help="plan JSON file (see dobby/adk/plan.py)")
    ap.add_argument(
        "--in-process",
        action="store_true",
        help="call the tools in this process instead of starting the MCP server (for cassettes: DESKTOP_HAL=replay)",
    )
    ap.add_argument(
        "--model", help="LiteLLM model id for the judges (default ollama_chat/<gemma3_text_model>)"
    )
    ap.add_argument("--json", action="store_true", help="print the full report as JSON")
    args = ap.parse_args()

    try:
        from dobby.adk.backends import InProcessBackend, McpBackend
        from dobby.adk.judges import gemma_model
        from dobby.adk.plan import Plan
        from dobby.adk.runner import run_plan
    except ImportError as exc:
        print(f"{exc}. Install the extra: pip install -e '.[adk]'", file=sys.stderr)
        return 2

    plan = Plan.load(args.plan)
    model = gemma_model(args.model)

    async def go():
        if args.in_process:
            return await run_plan(plan, InProcessBackend(), model)
        async with McpBackend() as backend:
            return await run_plan(plan, backend, model)

    report = asyncio.run(go())
    if args.json:
        print(json.dumps(report, indent=1, default=str))
    else:
        for row in report.get("steps", []):
            print(
                f"{row['id']:<6} {row['tool']:<20} {row.get('status', '?'):<8} {row.get('tier', ''):<13} "
                f"{row.get('ms', 0):>6} ms  {row.get('detail') or (row.get('verdict') or {}).get('evidence', '')}"
            )
        print(
            f"\n{report.get('status')}"
            + (f": {report.get('reason')}" if report.get("reason") else "")
        )
    return 0 if report.get("status") == "done" else 1


if __name__ == "__main__":
    sys.exit(main())
