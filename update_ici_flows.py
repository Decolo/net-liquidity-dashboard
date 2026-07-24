#!/usr/bin/env python3
"""ICI mutual-fund + combined ETF/LT fund flows (weekly B-layer buyer proxy).

Primary files (year in filename — script tries current/previous year):
  Mutual fund only:   https://www.ici.org/flows_data_YYYY.xls
  Combined MF+ETF:    https://www.ici.org/combined_flows_data_YYYY.xls

Units: USD millions. Weekly figures are estimates.
Schedule suggestion: weekly (Thu/Fri after ICI release).

Usage:
  python update_ici_flows.py
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Optional, Tuple

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent
OUT_JSON = ROOT / "latest_ici_flows.json"
OUT_DIR = ROOT / "data"
HEADERS = {"User-Agent": "net-liquidity-dashboard/1.0 (personal research)"}


def try_download(url: str) -> Optional[bytes]:
    try:
        r = requests.get(url, headers=HEADERS, timeout=60)
        if r.status_code != 200:
            return None
        ctype = (r.headers.get("content-type") or "").lower()
        if "html" in ctype and "excel" not in ctype and "sheet" not in ctype:
            return None
        if len(r.content) < 1000:
            return None
        return r.content
    except requests.RequestException:
        return None


def resolve_xls(kind: str) -> Tuple[str, bytes]:
    """kind: 'flows' | 'combined'"""
    year = datetime.now().year
    names = {
        "flows": [f"https://www.ici.org/flows_data_{y}.xls" for y in (year, year - 1)],
        "combined": [f"https://www.ici.org/combined_flows_data_{y}.xls" for y in (year, year - 1)],
    }
    for url in names[kind]:
        blob = try_download(url)
        if blob:
            return url, blob
    raise RuntimeError(f"Could not download ICI {kind} xls for {year}/{year-1}")


def parse_date_cell(v) -> Optional[pd.Timestamp]:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, datetime):
        return pd.Timestamp(v)
    s = str(v).strip()
    if not s or s.lower() == "nan":
        return None
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%Y-%m"):
        try:
            return pd.to_datetime(s, format=fmt)
        except (ValueError, TypeError):
            pass
    try:
        return pd.to_datetime(s)
    except Exception:
        return None


def extract_series(df: pd.DataFrame, section_keyword: str) -> pd.DataFrame:
    """After a row whose col0 contains section_keyword, read date rows until blank/note."""
    start = None
    for i, row in df.iterrows():
        c0 = str(row.iloc[0]) if pd.notna(row.iloc[0]) else ""
        if section_keyword.lower() in c0.lower():
            start = i + 1
            break
    if start is None:
        return pd.DataFrame()

    rows = []
    for i in range(start, len(df)):
        row = df.iloc[i]
        c0 = row.iloc[0]
        if pd.isna(c0):
            # skip single blank; stop on note-like long text later
            continue
        s0 = str(c0)
        if s0.lower().startswith("note") or len(s0) > 80:
            break
        dt = parse_date_cell(c0)
        if dt is None:
            continue
        # columns (combined & flows share sparse layout):
        # 1 total, 3 equity total, 5 equity domestic, 11 bond total (flows has different bond col)
        def num(col):
            if col >= len(row):
                return None
            v = pd.to_numeric(row.iloc[col], errors="coerce")
            return float(v) if pd.notna(v) else None

        rows.append(
            {
                "date": dt.strftime("%Y-%m-%d"),
                "total_mn": num(1),
                "equity_mn": num(3),
                "equity_domestic_mn": num(5),
                "bond_mn": num(11),  # works for combined (Bond Total); MF-only bond also often col 25 — filled later
            }
        )
    return pd.DataFrame(rows)


def extract_mf_bond_col(df: pd.DataFrame, section_keyword: str) -> dict:
    """For flows_data only: bond total is around col 25."""
    start = None
    for i, row in df.iterrows():
        c0 = str(row.iloc[0]) if pd.notna(row.iloc[0]) else ""
        if section_keyword.lower() in c0.lower():
            start = i + 1
            break
    out = {}
    if start is None:
        return out
    for i in range(start, len(df)):
        row = df.iloc[i]
        dt = parse_date_cell(row.iloc[0])
        if dt is None:
            s0 = str(row.iloc[0]) if pd.notna(row.iloc[0]) else ""
            if s0.lower().startswith("note") or (s0 and len(s0) > 80):
                break
            continue
        v = pd.to_numeric(row.iloc[25], errors="coerce") if len(row) > 25 else None
        if pd.notna(v):
            out[dt.strftime("%Y-%m-%d")] = float(v)
    return out


def latest_row(df: pd.DataFrame) -> Optional[dict]:
    if df is None or df.empty:
        return None
    d = df.copy()
    d["_dt"] = pd.to_datetime(d["date"])
    d = d.sort_values("_dt")
    r = d.iloc[-1]
    return {k: (None if pd.isna(v) else v) for k, v in r.drop(labels=["_dt"]).items()}


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    flows_url, flows_blob = resolve_xls("flows")
    comb_url, comb_blob = resolve_xls("combined")

    flows_df = pd.read_excel(BytesIO(flows_blob), header=None)
    comb_df = pd.read_excel(BytesIO(comb_blob), header=None)

    mf_weekly = extract_series(flows_df, "Estimated Weekly Net New Cash Flow")
    mf_monthly = extract_series(flows_df, "Monthly Net New Cash Flow")
    # bond col fix for MF file
    bond_w = extract_mf_bond_col(flows_df, "Estimated Weekly Net New Cash Flow")
    bond_m = extract_mf_bond_col(flows_df, "Monthly Net New Cash Flow")
    if not mf_weekly.empty and bond_w:
        mf_weekly["bond_mn"] = mf_weekly["date"].map(bond_w)
    if not mf_monthly.empty and bond_m:
        mf_monthly["bond_mn"] = mf_monthly["date"].map(bond_m)

    comb_weekly = extract_series(comb_df, "Estimated weekly fund flows")
    comb_monthly = extract_series(comb_df, "Monthly fund flows")

    mf_weekly.to_csv(OUT_DIR / "ici_mf_weekly.csv", index=False)
    comb_weekly.to_csv(OUT_DIR / "ici_combined_weekly.csv", index=False)
    if not mf_monthly.empty:
        mf_monthly.to_csv(OUT_DIR / "ici_mf_monthly.csv", index=False)
    if not comb_monthly.empty:
        comb_monthly.to_csv(OUT_DIR / "ici_combined_monthly.csv", index=False)

    lw_mf = latest_row(mf_weekly)
    lw_comb = latest_row(comb_weekly)
    lm_mf = latest_row(mf_monthly)
    lm_comb = latest_row(comb_monthly)

    payload = {
        "updated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "source": "Investment Company Institute (ICI)",
        "layer": "B-fund/ETF flows",
        "frequency": "weekly estimates + monthly actuals",
        "unit": "USD millions",
        "urls": {"mutual_fund_flows": flows_url, "combined_mf_etf": comb_url},
        "latest_week": {
            "mutual_fund_only": lw_mf,
            "combined_mf_and_etf": lw_comb,
        },
        "latest_month": {
            "mutual_fund_only": lm_mf,
            "combined_mf_and_etf": lm_comb,
        },
        "read_hint": (
            "combined equity_mn includes MF+ETF — better 'who is buying stocks' proxy than MF-only "
            "(MF-only equity has long been structural outflows into ETFs)."
        ),
        "disclaimer": (
            "Weekly = estimates (~98% coverage). Monthly MF = actual net new cash flow; "
            "ETF side in combined = net issuance. Not a trading signal."
        ),
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def fmt(block, label):
        if not block:
            return f"{label}: n/a"
        return (
            f"{label} asof={block.get('date')} total={block.get('total_mn')} "
            f"equity={block.get('equity_mn')} bond={block.get('bond_mn')}"
        )

    print(fmt(lw_comb, "ICI combined weekly"))
    print(fmt(lw_mf, "ICI MF-only weekly"))
    print(f"Wrote {OUT_JSON}")


if __name__ == "__main__":
    main()
