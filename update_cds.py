#!/usr/bin/env python3
"""Hyperscaler implied CDS via Merton (1974) structural model + DD→CDS calibration.

Single-name CDS is paid (Markit/Bloomberg). This script builds a free proxy:
  1. Merton model → Distance-to-Default (DD) per firm
  2. DD → CDS (bp) via empirically calibrated exponential mapping

The raw Merton output understates CDS by ~10x (the well-known "credit spread
puzzle"). We calibrate the DD→CDS curve using observed market reference points
(ORCL ~198bp, Big Tech ex-ORCL ~49bp, MSFT ~25bp as of Jul 2026).

Inputs (all free):
  - Stock price + shares outstanding (yfinance)
  - ATM put implied volatility (yfinance options chain)
  - Total Debt from balance sheet (yfinance)
  - Risk-free rate (FRED DTB3)

Output: latest_cds.json + charts/cds_panel.png + charts/cds_dd.png

Usage: python update_cds.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests
import yfinance as yf
from scipy.stats import norm

ROOT = Path(__file__).resolve().parent
OUT_JSON = ROOT / "latest_cds.json"
OUT_DIR = ROOT / "charts"
OUT_DIR.mkdir(exist_ok=True)
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

START = "2024-01-01"
BG = "#0d1117"
FG = "#e6edf3"
GRID = "#21262d"

HYPERS = {
    "AMZN":  {"name": "Amazon",    "color": "#f0b429"},
    "GOOGL": {"name": "Alphabet",  "color": "#58a6ff"},
    "META":  {"name": "Meta",      "color": "#3fb950"},
    "MSFT":  {"name": "Microsoft", "color": "#a371f7"},
    "ORCL":  {"name": "Oracle",    "color": "#f85149"},
    "NVDA":  {"name": "NVIDIA",    "color": "#3fb950"},
    "AAPL":  {"name": "Apple",     "color": "#e6edf3"},
}

T_MATURITY = 1.0
RATE_SERIES = "DTB3"

# ---------------------------------------------------------------------------
# DD → CDS calibration (fitted Jul 2026 from observed market levels)
#   ORCL  DD≈2.7 → ~198bp
#   BigT  DD≈8.5 → ~49bp  (ex-ORCL avg)
#   MSFT  DD≈12  → ~25bp
#   CDS = A × exp(−B × DD)
# ---------------------------------------------------------------------------
CALIB_A = 355.0
CALIB_B = 0.224


def dd_to_cds(dd: float) -> float:
    """Map distance-to-default → CDS spread (bp) using calibrated curve."""
    if dd >= 99.0:
        return 0.0
    return round(CALIB_A * np.exp(-CALIB_B * dd), 1)


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def get_rf_rate() -> float:
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={RATE_SERIES}"
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    df = pd.read_csv(pd.io.common.StringIO(r.text), parse_dates=["observation_date"], na_values=".")
    s = df.set_index("observation_date")[RATE_SERIES].dropna().astype(float)
    return float(s.iloc[-1]) / 100.0


def get_option_iv(ticker: yf.Ticker, price: float) -> Optional[float]:
    """ATM put implied volatility, ~25-75 days out. None on failure."""
    try:
        exps = ticker.options
        if not exps:
            return None
        now = datetime.now()
        candidates = []
        for e in exps:
            try:
                days = (datetime.strptime(e, "%Y-%m-%d") - now).days
                if 25 <= days <= 75:
                    candidates.append((abs(days - 45), e))
            except ValueError:
                continue
        exp = candidates[0][1] if candidates else exps[min(2, len(exps) - 1)]
        chain = ticker.option_chain(exp)
        puts = chain.puts
        if puts.empty:
            return None
        puts = puts.copy()
        puts["dist"] = abs(puts["strike"] - price)
        atm = puts.loc[puts["dist"].idxmin()]
        iv = float(atm.get("impliedVolatility", np.nan))
        if np.isnan(iv) or iv <= 0.01 or iv > 5.0:
            return None
        return iv
    except Exception:
        return None


def historical_vol(prices: pd.Series, window: int = 30) -> Optional[float]:
    if len(prices.dropna()) < window + 1:
        return None
    rets = prices.pct_change().dropna().tail(window)
    return float(rets.std() * np.sqrt(252))


# ---------------------------------------------------------------------------
# Merton solver (iterative — more stable than fsolve)
# ---------------------------------------------------------------------------

def merton_dd(
    E: float, D: float, sigma_E: float, r: float, T: float = T_MATURITY, max_iter: int = 100
) -> tuple[float, float, float]:
    """Solve Merton model iteratively → (DD, V, sigma_V).

    Returns DD=99 if debt is negligible (de facto risk-free).
    """
    if D <= 0 or E <= 0:
        return 99.0, E, sigma_E

    D_disc = D * np.exp(-r * T)

    # Initial guess
    V = E + D_disc * 0.95
    sV = sigma_E * E / (E + D_disc)

    for _ in range(max_iter):
        d1 = (np.log(V / D_disc) + (r + sV**2 / 2) * T) / (sV * np.sqrt(T))
        d2 = d1 - sV * np.sqrt(T)

        V_new = (E + D_disc * norm.cdf(d2)) / norm.cdf(d1)
        sV_new = sigma_E * E / (norm.cdf(d1) * V)

        # Relaxation for stability
        V = 0.7 * V_new + 0.3 * V
        sV = 0.7 * sV_new + 0.3 * sV

        if abs(V - V_new) / V < 1e-8 and abs(sV - sV_new) < 1e-8:
            break

    d2 = (np.log(V / D_disc) + (r - sV**2 / 2) * T) / (sV * np.sqrt(T))
    dd = float(d2)

    if dd > 99.0:
        dd = 99.0
    if dd < 0.01:
        dd = 0.01

    return dd, float(V), float(sV)


# ---------------------------------------------------------------------------
# Per-firm fetch + compute
# ---------------------------------------------------------------------------

def fetch_firm(tkr: str, rf: float) -> dict:
    tk = yf.Ticker(tkr)
    info = tk.info or {}
    price = info.get("currentPrice") or info.get("regularMarketPrice") or info.get("previousClose")
    if price is None:
        raise RuntimeError(f"{tkr}: no price")

    shares = info.get("sharesOutstanding") or info.get("impliedSharesOutstanding")
    if shares is None:
        raise RuntimeError(f"{tkr}: no shares outstanding")

    E = float(price) * float(shares)

    # Total Debt (face value)
    bs = tk.balance_sheet
    if bs is None or bs.empty:
        raise RuntimeError(f"{tkr}: no balance sheet")

    total_debt = (
        float(bs.loc["Total Debt"].iloc[0]) if "Total Debt" in bs.index else None
    )
    lt_debt = (
        float(bs.loc["Long Term Debt"].iloc[0]) if "Long Term Debt" in bs.index else None
    )
    cur_debt = (
        float(bs.loc["Current Debt"].iloc[0]) if "Current Debt" in bs.index else None
    )
    # Use Total Debt as the face value the CDS insures.
    D = total_debt if total_debt is not None else (
        (cur_debt or 0.0) + 0.5 * (lt_debt or 0.0)
    )
    if D <= 0:
        D = E * 0.01

    leverage = D / (D + E)

    # Equity volatility
    sigma_E = get_option_iv(tk, float(price))
    iv_source = "option_atm_put"
    if sigma_E is None:
        hist = yf.download(tkr, start="2025-07-01", progress=False, auto_adjust=True)
        close = hist["Close"].dropna() if not hist.empty else pd.Series(dtype=float)
        sigma_E = historical_vol(close)
        iv_source = "historical_30d" if sigma_E else "default_35pct"
    if sigma_E is None:
        sigma_E = 0.35
        iv_source = "default_35pct"

    dd, V, sV = merton_dd(E, D, sigma_E, rf)
    cds = dd_to_cds(dd)
    pd_val = float(norm.cdf(-dd))

    return {
        "ticker": tkr,
        "name": HYPERS[tkr]["name"],
        "price": round(float(price), 2),
        "shares_M": round(float(shares) / 1_000_000, 1),
        "mcap_B": round(E / 1_000_000_000, 1),
        "total_debt_B": round(D / 1_000_000_000, 1),
        "leverage_pct": round(leverage * 100, 2),
        "sigma_E_pct": round(sigma_E * 100, 1),
        "iv_source": iv_source,
        "distance_to_default": round(dd, 2),
        "cds_bp": cds,
        "pd_risk_neutral_pct": round(pd_val * 100, 4),
        "rf_pct": round(rf * 100, 2),
        "asset_value_B": round(V / 1_000_000_000, 1),
        "sigma_V_pct": round(sV * 100, 1),
    }


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------

def styled_ax(ax: plt.Axes) -> None:
    ax.set_facecolor(BG)
    ax.tick_params(colors=FG, which="both")
    for spine in ax.spines.values():
        spine.set_color(GRID)
    ax.grid(True, color=GRID, linestyle="-", linewidth=0.5, alpha=0.7)


def plot_cds(results: list[dict]) -> Path:
    fig, ax = plt.subplots(figsize=(10, 5))
    fig.patch.set_facecolor(BG)
    names = [r["name"] for r in results]
    values = [r["cds_bp"] for r in results]
    colors = [HYPERS[r["ticker"]]["color"] for r in results]

    bars = ax.bar(names, values, color=colors, alpha=0.85, edgecolor=BG)
    for bar, val in zip(bars, values):
        ax.text(
            bar.get_x() + bar.get_width() / 2, bar.get_height() + max(values) * 0.02,
            f"{val:.0f}bp", ha="center", va="bottom", color=FG, fontsize=9, fontweight="bold",
        )
    styled_ax(ax)
    ax.set_ylabel("CDS spread (bp)", color=FG)
    ax.set_title("Hyperscaler Implied CDS (Merton DD → calibrated)", color=FG, fontsize=13,
                 fontweight="bold", pad=14)
    for y, label, c in [(50, "~50bp IG normal", "#58a6ff"), (100, "100bp stress", "#f0b429"),
                          (200, "200bp HY boundary", "#f85149")]:
        ax.axhline(y, color=c, linewidth=0.8, alpha=0.6)
        ax.text(6.2, y + 2, label, color=c, fontsize=7, ha="right")

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    fig.text(0.99, 0.01, f"Updated {stamp}  ·  Merton DD→CDS proxy, not traded CDS",
             ha="right", va="bottom", color=FG, fontsize=8, alpha=0.7)
    plt.tight_layout()
    out = OUT_DIR / "cds_panel.png"
    fig.savefig(out, dpi=160, facecolor=BG)
    plt.close(fig)
    return out


def plot_dd(results: list[dict]) -> Path:
    fig, ax = plt.subplots(figsize=(10, 5))
    fig.patch.set_facecolor(BG)
    names = [r["name"] for r in results]
    values = [r["distance_to_default"] for r in results]
    colors = [HYPERS[r["ticker"]]["color"] for r in results]

    bars = ax.bar(names, values, color=colors, alpha=0.85, edgecolor=BG)
    for bar, val in zip(bars, values):
        ax.text(
            bar.get_x() + bar.get_width() / 2, bar.get_height() + max(max(values) * 0.02, 0.1),
            f"{val:.1f}", ha="center", va="bottom", color=FG, fontsize=9,
        )
    styled_ax(ax)
    ax.set_ylabel("Distance to Default", color=FG)
    ax.set_title("违约距离 DD（越高越安全 · DD<3=IG 边界 · DD<1.5=严重应力）",
                 color=FG, fontsize=13, fontweight="bold", pad=14)
    ax.axhline(3.0, color="#f0b429", linewidth=0.8, alpha=0.6)
    ax.text(6.2, 3.1, "DD=3 IG boundary", color="#f0b429", fontsize=7, ha="right")
    ax.axhline(1.5, color="#f85149", linewidth=0.8, alpha=0.6)
    ax.text(6.2, 1.6, "DD=1.5 stress", color="#f85149", fontsize=7, ha="right")

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    fig.text(0.99, 0.01, f"Updated {stamp}", ha="right", va="bottom", color=FG,
             fontsize=8, alpha=0.7)
    plt.tight_layout()
    out = OUT_DIR / "cds_dd.png"
    fig.savefig(out, dpi=160, facecolor=BG)
    plt.close(fig)
    return out


# ---------------------------------------------------------------------------
# Persistence & status
# ---------------------------------------------------------------------------

def load_history() -> pd.DataFrame:
    p = DATA_DIR / "cds_history.csv"
    if p.exists():
        return pd.read_csv(p, parse_dates=["date"])
    return pd.DataFrame(columns=["date"] + [f"{t}_cds" for t in HYPERS])


def append_history(results: list[dict]) -> None:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    row = {"date": today}
    for r in results:
        row[f"{r['ticker']}_cds"] = r["cds_bp"]
    hist = load_history()
    if today in hist["date"].astype(str).values:
        return
    hist = pd.concat([hist, pd.DataFrame([row])], ignore_index=True)
    hist.to_csv(DATA_DIR / "cds_history.csv", index=False)


def write_status(results: list[dict]) -> dict:
    avg_all = sum(r["cds_bp"] for r in results) / len(results)
    ex_orcl = [r for r in results if r["ticker"] != "ORCL"]
    avg_ex_orcl = sum(r["cds_bp"] for r in ex_orcl) / len(ex_orcl) if ex_orcl else None

    flags = []
    for r in results:
        if r["ticker"] == "ORCL" and r["cds_bp"] >= 200:
            flags.append("orcl_cds_ge_200bp")
        if r["distance_to_default"] < 3.0:
            flags.append(f"{r['ticker'].lower()}_dd_below_3")
        if r["distance_to_default"] < 1.5:
            flags.append(f"{r['ticker'].lower()}_dd_below_1p5")
        if r["leverage_pct"] > 25:
            flags.append(f"{r['ticker'].lower()}_leverage_gt_25pct")
    if avg_all >= 75:
        flags.append("bigtech_avg_cds_ge_75bp")

    payload = {
        "updated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "model": "Merton (1974) iterative solver + DD→CDS calibration",
        "calibration": {
            "formula": f"CDS_bp = {CALIB_A:.0f} × exp(−{CALIB_B:.3f} × DD)",
            "reference_points": "ORCL DD≈2.7→198bp, BigT ex-ORCL DD≈8.5→49bp, MSFT DD≈12→25bp",
            "fitted": "2026-07-18",
        },
        "layer": "B-CDS (hyperscaler credit, Merton proxy)",
        "rf_source": f"FRED {RATE_SERIES}",
        "results": results,
        "aggregates": {
            "big_tech_cds_bp": round(avg_all, 1),
            "big_tech_ex_orcl_cds_bp": round(avg_ex_orcl, 1) if avg_ex_orcl else None,
        },
        "heuristic_flags": flags,
        "read_hint": (
            "DD (distance-to-default) is the pure structural output — rank firms by DD. "
            "CDS (bp) is DD mapped through an empirically calibrated exponential curve. "
            "Calibration fixed Jul 2026; re-fit if market CDS levels shift structurally."
        ),
        "disclaimer": (
            "NOT real CDS. Single-name CDS requires paid Markit/Bloomberg data. "
            "Merton model ignores jump risk, liquidity premium, and complex capital "
            "structure. Direction and relative ranking are informative; absolute levels "
            "are calibrated estimates. Not a trading signal."
        ),
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    print("Fetching risk-free rate (FRED DTB3)...")
    rf = get_rf_rate()
    print(f"  DTB3 = {rf*100:.2f}%")

    results = []
    for tkr in HYPERS:
        print(f"Processing {tkr} ...")
        try:
            r = fetch_firm(tkr, rf)
            results.append(r)
            print(
                f"  {tkr:6} mcap=${r['mcap_B']:.0f}B  debt=${r['total_debt_B']:.0f}B  "
                f"lev={r['leverage_pct']:.1f}%  σE={r['sigma_E_pct']:.0f}%  "
                f"DD={r['distance_to_default']:.2f}  CDS={r['cds_bp']:.0f}bp"
            )
        except Exception as e:
            print(f"  {tkr} ERROR: {e}", file=sys.stderr)

    if not results:
        print("ERROR: no results", file=sys.stderr)
        return 1

    print("Rendering CDS charts...")
    p1 = plot_cds(results)
    print(f"  wrote {p1}")
    p2 = plot_dd(results)
    print(f"  wrote {p2}")

    append_history(results)
    payload = write_status(results)
    print(f"Wrote {OUT_JSON}")
    print(f"Heuristic flags: {payload.get('heuristic_flags') or '(none)'}")
    print()
    print(f"Big Tech CDS avg:          {payload['aggregates']['big_tech_cds_bp']:.0f}bp")
    if payload['aggregates']['big_tech_ex_orcl_cds_bp']:
        print(f"Big Tech ex-ORCL:           {payload['aggregates']['big_tech_ex_orcl_cds_bp']:.0f}bp")
    return 0


if __name__ == "__main__":
    sys.exit(main())
