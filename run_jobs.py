#!/usr/bin/env python3
"""Run liquidity data jobs by cadence bucket (config-driven via jobs.json).

Examples:
  python run_jobs.py daily
  python run_jobs.py weekly
  python run_jobs.py monthly
  python run_jobs.py event
  python run_jobs.py all
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
JOBS_FILE = ROOT / "jobs.json"

# "all" runs each unique script once, in this bucket order
ALL_ORDER = ("daily", "session", "weekly", "monthly")


def load_jobs() -> dict[str, list[str]]:
    if not JOBS_FILE.exists():
        print(f"ERROR: missing {JOBS_FILE}", file=sys.stderr)
        sys.exit(2)
    try:
        jobs = json.loads(JOBS_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"ERROR: invalid jobs.json: {e}", file=sys.stderr)
        sys.exit(2)
    if not isinstance(jobs, dict) or not all(
        isinstance(v, list) and all(isinstance(s, str) for s in v) for v in jobs.values()
    ):
        print("ERROR: jobs.json must map bucket -> [script, ...]", file=sys.stderr)
        sys.exit(2)
    return jobs


def run_script(name: str) -> int:
    path = ROOT / name
    if not path.exists():
        print(f"SKIP missing {name}", file=sys.stderr)
        return 0
    print(f"\n=== {name} ===", flush=True)
    p = subprocess.run([sys.executable, str(path)], cwd=str(ROOT))
    return p.returncode


def main() -> int:
    jobs = load_jobs()
    buckets = sorted(jobs.keys())

    ap = argparse.ArgumentParser()
    ap.add_argument(
        "bucket",
        choices=[*buckets, "all"],
        help="Which schedule bucket to run",
    )
    args = ap.parse_args()

    if args.bucket == "all":
        scripts = []
        for b in ALL_ORDER:
            for s in jobs.get(b, []):
                if s not in scripts:
                    scripts.append(s)
    else:
        scripts = jobs[args.bucket]

    rc = 0
    for s in scripts:
        code = run_script(s)
        if code != 0:
            rc = code
            print(f"FAILED {s} exit={code}", file=sys.stderr)
    return rc


if __name__ == "__main__":
    sys.exit(main())
