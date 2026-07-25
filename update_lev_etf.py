#!/usr/bin/env python3
"""Leveraged ETF AUM monitor (B-layer speculative demand proxy).

Leveraged ETF total assets under management (AUM) proxy retail and
short-term speculative flows. Rapid AUM contraction signals the
"de-grossing" pattern described in momentum-factor unwind scenarios.

Tracking list covers semiconductor, broad-market, and tech leverage:
  - Semi:  SOXL (3x bull), SOXS (3x bear), USD (2x bull), SSG (2x bear)
  - Broad: SSO (2x SPY), UPRO (3x SPY), SPXU (3x bear SPY)
  - Tech:  QLD (2x QQQ), TQQQ (3x QQQ), SQQQ (3x bear QQQ)

Data: Yahoo Finance (yfinance). Daily frequency. Public — no API key.

Usage:
  python update_lev_etf.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import matplotlib.pyplot as plt
import yfinance as yf

ROOT = Path(__file__).resolve().parent
OUT_JSON = ROOT / "latest_lev_etf.json"
OUT_DIR = ROOT / "charts"
OUT_DIR.mkdir(exist_ok=True)

BG = "#0d1117"
FG = "#e6edf3"
GRID = "#21262d"
ACCENT = "#a371f7"

# ── Tracking universe ──────────────────────────────────────────────────────

TICKERS = [
    # Semiconductor leveraged
    "SOXL",   # Direxion Daily Semiconductor Bull 3x
    "SOXS",   # Direxion Daily Semiconductor Bear 3x
    "USD",    # ProShares Ultra Semiconductors 2x
    "SSG",    # ProShares UltraShort Semiconductors -2x
    # Broad-market leveraged
    "SSO",    # ProShares Ultra S&P500 2x
    "UPRO",   # ProShares UltraPro S&P500 3x
    "SPXU",   # ProShares UltraPro Short S&P500 -3x
    # Tech leveraged
    "QLD",    # ProShares Ultra QQQ 2x
    "TQQQ",   # ProShares UltraPro QQQ 3x
    "SQQQ",   # ProShares UltraPro Short QQQ -3x
]

# ── Data fetching ───────────────────────────────────────────────────────────

def fetch_aum(ticker: str) -> dict | None:
    """Return {ticker, name, aum_M, price, as_of} or None on failure."""
    try:
        t = yf.Ticker(ticker)
        info = t.info
        if not info or info.get("totalAssets") is None:
            # fast_info.marketCap is a fallback (market cap ≈ AUM for ETFs)
            fi = t.fast_info
            aum = getattr(fi, "marketCap", None)
            shares = getattr(fi, "shares", None)
            price = getattr(fi, "lastPrice", None)
        else:
            aum = info.get("totalAssets")
            shares = info.get("sharesOutstanding")
            price = info.get("previousClose") or info.get("currentPrice")
        if aum is None:
            return None
        name = info.get("shortName") or info.get("longName") or ticker
        return {
            "ticker": ticker,
            "name": name,
            "aum_M": round(aum / 1_000_000, 1),  # convert to millions
            "shares_M": round(shares / 1_000_000, 1) if shares else None,
            "price": round(price, 2) if price else None,
            "as_of": datetime.now().strftime("%Y-%m-%d"),
        }
    except Exception:
        return None


# ── Chart ───────────────────────────────────────────────────────────────────

def styled_ax(ax: plt.Axes) -> None:
    ax.set_facecolor(BG)
    ax.tick_params(colors=FG, which="both")
    for spine in ax.spines.values():
        spine.set_color(GRID)
    ax.grid(True, color=GRID, linestyle="-", linewidth=0.5, alpha=0.7)


def plot_lev_etf(etfs: list[dict]) -> Path | None:
    """Horizontal bar chart of per-ETF AUM in $B."""
    if not etfs:
        return None
    # Sort by AUM descending, separate bull vs bear
    bull = sorted([e for e in etfs if "bear" not in e.get("name", "").lower()
                   and "short" not in e.get("name", "").lower()
                   and e.get("ticker", "") not in ("SOXS", "SSG", "SPXU", "SQQQ")],
                  key=lambda x: x.get("aum_M", 0), reverse=True)
    bear = sorted([e for e in etfs if e.get("ticker") in ("SOXS", "SSG", "SPXU", "SQQQ")],
                  key=lambda x: x.get("aum_M", 0), reverse=True)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))
    fig.patch.set_facecolor(BG)

    for ax, data, label, color in [
        (ax1, bull, "Bull (long)", ACCENT),
        (ax2, bear, "Bear (short / inverse)", "#f87171"),
    ]:
        styled_ax(ax)
        if not data:
            ax.text(0.5, 0.5, "no data", transform=ax.transAxes,
                    ha="center", va="center", color=FG)
            ax.set_title(f"{label} — no data", color=FG)
            continue
        names = [f"{d['ticker']} ({d.get('name', '')[:20]})" for d in data]
        aums = [d["aum_M"] / 1000 for d in data]  # convert M → B
        bars = ax.barh(names, aums, color=color, height=0.6)
        ax.bar_label(bars, fmt="%.1fB", color=FG, fontsize=8, padding=2)
        ax.set_title(f"Leveraged ETF AUM — {label}", color=FG)
        ax.invert_yaxis()

    fig.tight_layout(pad=2)
    path = OUT_DIR / "lev_etf.png"
    fig.savefig(path, dpi=100, facecolor=BG, bbox_inches="tight")
    plt.close(fig)
    return path


# ── Output ──────────────────────────────────────────────────────────────────

def write_status(etfs: list[dict], total_aum_B: float) -> dict:
    """Write latest_lev_etf.json and return the payload."""
    payload = {
        "updated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "source": "Yahoo Finance (yfinance)",
        "layer": "B-leverage",
        "total_aum_B": round(total_aum_B, 2),
        "etf_count": len(etfs),
        "etfs": etfs,
        "heuristic_flags": [],
        "read_hint": (
            "Leveraged ETF AUM shrinking = retail/speculative money leaving. "
            "Rapid drawdown (>20% in 30d) signals forced deleveraging risk."
        ),
        "disclaimer": (
            "AUM data from Yahoo Finance (yfinance). May differ from issuer-reported "
            "NAV/AUM due to timing differences. Not a trading signal."
        ),
    }

    # Simple heuristic: flag if total AUM seems anomalously low
    # (thresholds TBD after observing live data for a few weeks)
    if total_aum_B < 50:
        payload["heuristic_flags"].append("lev_etf_aum_low")

    ROOT.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    return payload


# ── Main ────────────────────────────────────────────────────────────────────

def main() -> int:
    print("Fetching leveraged ETF AUM via yfinance...")
    etfs = []
    for tkr in TICKERS:
        d = fetch_aum(tkr)
        if d:
            etfs.append(d)
            print(f"  {tkr:6s}  AUM ${d['aum_M']:>10,.1f}M  ({d.get('name', '')})")
        else:
            print(f"  {tkr:6s}  SKIP — data unavailable")

    if not etfs:
        print("ERROR: no ETF data fetched.", file=sys.stderr)
        return 1

    total_aum_B = sum(e["aum_M"] for e in etfs) / 1000
    print(f"\nTotal AUM: ${total_aum_B:.1f}B across {len(etfs)} ETFs")

    chart_path = plot_lev_etf(etfs)
    if chart_path:
        print(f"  wrote {chart_path}")

    payload = write_status(etfs, total_aum_B)
    print(f"Wrote {OUT_JSON}")
    flags = payload.get("heuristic_flags") or []
    print(f"Heuristic flags: {'(none)' if not flags else ', '.join(flags)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
