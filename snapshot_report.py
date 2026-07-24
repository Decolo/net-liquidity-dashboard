#!/usr/bin/env python3
"""CLI text report for agents (no server required).

  python snapshot_report.py
  python snapshot_report.py --json   # full merged snapshot
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# reuse server builders
sys.path.insert(0, str(Path(__file__).resolve().parent))
from server import build_report, build_snapshot  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    if args.json:
        print(json.dumps(build_snapshot(), ensure_ascii=False, indent=2))
    else:
        print(build_report(), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
