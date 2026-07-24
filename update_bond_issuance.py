#!/usr/bin/env python3
"""Hyperscaler / major tech debt-related SEC filing feed (event-layer B).

Free approach: SEC data.sec.gov submissions JSON per CIK.
Tracks debt-ish forms: 424B2, 424B3, 424B5, FWP (free writing prospectus).

This is NOT a complete IG bond calendar (no size/coupon parse guaranteed).
It flags that a name has been active in capital markets docs recently.

Schedule suggestion: daily or every 2–3 days.

Usage:
  python update_bond_issuance.py
  python update_bond_issuance.py --days 120
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

import requests

ROOT = Path(__file__).resolve().parent
OUT_JSON = ROOT / "latest_bond_issuance.json"
OUT_CSV = ROOT / "data" / "bond_filings_recent.csv"
HEADERS = {
    # SEC requires descriptive User-Agent
    "User-Agent": "net-liquidity-dashboard/1.0 personal-research contact@localhost",
    "Accept-Encoding": "gzip, deflate",
}

# CIK zero-padded 10 digits
ISSUERS = [
    {"ticker": "AMZN", "name": "Amazon", "cik": "0001018724"},
    {"ticker": "GOOGL", "name": "Alphabet", "cik": "0001652044"},
    {"ticker": "META", "name": "Meta", "cik": "0001326801"},
    {"ticker": "MSFT", "name": "Microsoft", "cik": "0000789019"},
    {"ticker": "ORCL", "name": "Oracle", "cik": "0001341439"},
    {"ticker": "AAPL", "name": "Apple", "cik": "0000320193"},
    {"ticker": "NVDA", "name": "NVIDIA", "cik": "0001045810"},
]

DEBT_FORMS = {"424B2", "424B3", "424B5", "FWP", "S-3ASR", "S-3"}


def fetch_submissions(cik: str) -> dict:
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    r = requests.get(url, headers=HEADERS, timeout=60)
    r.raise_for_status()
    return r.json()


def filings_for(issuer: dict, since: datetime) -> List[Dict[str, Any]]:
    data = fetch_submissions(issuer["cik"])
    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accs = recent.get("accessionNumber", [])
    docs = recent.get("primaryDocument", [])
    descs = recent.get("primaryDocDescription", [])

    out = []
    for i, form in enumerate(forms):
        if form not in DEBT_FORMS:
            continue
        try:
            fd = datetime.strptime(dates[i], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except Exception:
            continue
        if fd < since:
            continue
        acc = accs[i].replace("-", "")
        doc = docs[i] if i < len(docs) else ""
        # https://www.sec.gov/Archives/edgar/data/{cik_nozero}/{acc}/{doc}
        cik_nz = str(int(issuer["cik"]))
        href = f"https://www.sec.gov/Archives/edgar/data/{cik_nz}/{acc}/{doc}" if doc else None
        out.append(
            {
                "ticker": issuer["ticker"],
                "name": issuer["name"],
                "cik": issuer["cik"],
                "form": form,
                "filing_date": dates[i],
                "accession": accs[i],
                "description": descs[i] if i < len(descs) else "",
                "url": href,
            }
        )
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=180, help="Lookback window in days")
    args = ap.parse_args()

    since = datetime.now(timezone.utc) - timedelta(days=args.days)
    all_rows: List[Dict[str, Any]] = []
    by_ticker: Dict[str, List[dict]] = {}

    for iss in ISSUERS:
        try:
            rows = filings_for(iss, since)
            time.sleep(0.25)  # be polite to SEC
        except Exception as e:
            by_ticker[iss["ticker"]] = [{"error": str(e)}]
            continue
        by_ticker[iss["ticker"]] = rows
        all_rows.extend(rows)

    all_rows.sort(key=lambda x: x.get("filing_date", ""), reverse=True)

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    # simple CSV
    import csv

    with OUT_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=["filing_date", "ticker", "name", "form", "description", "accession", "url", "cik"],
        )
        w.writeheader()
        for r in all_rows:
            w.writerow(r)

    # per-ticker latest debt-ish form
    latest = {}
    for t, rows in by_ticker.items():
        good = [r for r in rows if "error" not in r]
        latest[t] = good[0] if good else None

    active = [t for t, v in latest.items() if v is not None]

    payload = {
        "updated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "source": "SEC EDGAR data.sec.gov/submissions",
        "layer": "B-tech debt issuance (event proxy)",
        "frequency": "event / poll daily",
        "lookback_days": args.days,
        "forms_tracked": sorted(DEBT_FORMS),
        "issuers": [{"ticker": x["ticker"], "name": x["name"], "cik": x["cik"]} for x in ISSUERS],
        "count_in_window": len(all_rows),
        "tickers_with_hits": active,
        "latest_by_ticker": latest,
        "recent": all_rows[:40],
        "history_csv": str(OUT_CSV),
        "disclaimer": (
            "Presence of 424B2/FWP etc. indicates capital-markets documentation activity; "
            "not every filing is a large new IG bond deal, and sizes are not auto-extracted. "
            "Not a substitute for a professional issuance calendar."
        ),
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    print(f"Bond/SEC debt forms  window={args.days}d  hits={len(all_rows)}  names={active}")
    for r in all_rows[:8]:
        print(f"  {r['filing_date']}  {r['ticker']:6}  {r['form']:8}  {r.get('url','')[:70]}")
    print(f"Wrote {OUT_JSON}")
    print(f"Wrote {OUT_CSV}")


if __name__ == "__main__":
    main()
