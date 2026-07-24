#!/usr/bin/env python3
"""FINRA customer margin statistics (monthly B-layer leverage).

Source (official; no API/feed):
  https://www.finra.org/rules-guidance/key-topics/margin-accounts/margin-statistics
  Excel: /sites/default/files/2021-03/margin-statistics.xlsx

Units: $ millions. Typical publish lag: ~3rd week of following month.
Schedule suggestion: monthly (e.g. 25th) or weekly check for new month.

Usage:
  python update_finra_margin.py
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent
OUT_JSON = ROOT / "latest_finra_margin.json"
OUT_CSV = ROOT / "data" / "finra_margin_history.csv"
URL = "https://www.finra.org/sites/default/files/2021-03/margin-statistics.xlsx"
HEADERS = {"User-Agent": "net-liquidity-dashboard/1.0 (personal research)"}


def fetch() -> pd.DataFrame:
    r = requests.get(URL, headers=HEADERS, timeout=60)
    r.raise_for_status()
    df = pd.read_excel(BytesIO(r.content))
    # Expected columns:
    # Year-Month | Debit Balances... | Free Credit Cash | Free Credit Margin
    df.columns = ["year_month", "margin_debt_mn", "free_credit_cash_mn", "free_credit_margin_mn"]
    df["year_month"] = df["year_month"].astype(str).str.strip()
    for c in ("margin_debt_mn", "free_credit_cash_mn", "free_credit_margin_mn"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["year_month", "margin_debt_mn"]).copy()
    # sort newest first as published; normalize ascending for CSV
    df["_ord"] = pd.to_datetime(df["year_month"], format="%Y-%m", errors="coerce")
    df = df.dropna(subset=["_ord"]).sort_values("_ord")
    return df.drop(columns=["_ord"])


def main() -> None:
    df = fetch()
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT_CSV, index=False)

    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else None
    yoy = df.iloc[-13] if len(df) > 12 else None

    debt = float(last["margin_debt_mn"])
    debt_t = debt / 1_000_000  # millions → trillions
    mom = None
    yoy_chg = None
    if prev is not None:
        mom = debt - float(prev["margin_debt_mn"])
    if yoy is not None:
        yoy_chg = debt - float(yoy["margin_debt_mn"])

    payload = {
        "updated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "source": "FINRA Customer Margin Balances",
        "source_url": URL,
        "layer": "B-leverage (market-wide)",
        "frequency": "monthly",
        "unit": "USD millions (except margin_debt_t in trillions)",
        "as_of_month": last["year_month"],
        "margin_debt_mn": debt,
        "margin_debt_t": round(debt_t, 3),
        "free_credit_cash_mn": float(last["free_credit_cash_mn"]) if pd.notna(last["free_credit_cash_mn"]) else None,
        "free_credit_margin_mn": float(last["free_credit_margin_mn"]) if pd.notna(last["free_credit_margin_mn"]) else None,
        "mom_change_mn": round(mom, 1) if mom is not None else None,
        "yoy_change_mn": round(yoy_chg, 1) if yoy_chg is not None else None,
        "history_rows": int(len(df)),
        "history_csv": str(OUT_CSV),
        "disclaimer": (
            "FINRA publishes aggregate member-firm balances; no official real-time feed. "
            "Lag typically ~2–3 weeks after month-end. Not a trading signal."
        ),
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(
        f"FINRA margin  as_of={payload['as_of_month']}  "
        f"debt=${payload['margin_debt_t']:.3f}T  "
        f"MoM={payload['mom_change_mn']} mn  "
        f"YoY={payload['yoy_change_mn']} mn"
    )
    print(f"Wrote {OUT_JSON}")
    print(f"Wrote {OUT_CSV}")


if __name__ == "__main__":
    main()
