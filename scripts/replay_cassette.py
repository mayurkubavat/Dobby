#!/usr/bin/env python3
"""Replay one or more cassettes and print a pass/fail line for each.

python scripts/replay_cassette.py tests/cassettes/example_click
python scripts/replay_cassette.py --all ~/.pinlet/cassettes
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pinlet.hal.cassette_replay import discover_cassettes, replay_cassette


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "paths",
        nargs="+",
        help="cassette folders, .halpack/.json files, or (with --all) parent folders",
    )
    ap.add_argument("--all", action="store_true", help="treat each path as a folder of cassettes")
    ap.add_argument("--json", action="store_true", help="print full JSON reports")
    args = ap.parse_args()

    targets = []
    for p in args.paths:
        targets.extend(discover_cassettes(p) if args.all else [Path(p).expanduser()])
    if not targets:
        print("no cassettes found", file=sys.stderr)
        return 2
    failed = 0
    for target in targets:
        report = replay_cassette(target)
        if args.json:
            print(json.dumps(report, indent=1))
        else:
            status = "PASS" if report["ok"] else "FAIL"
            print(
                f"{status} {target} ({report['steps']}/{report['total_steps']} steps, final {report['final_frame']})"
            )
            for msg in report["failures"]:
                print(f"     {msg}")
        failed += not report["ok"]
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
