#!/usr/bin/env python3
"""Market breadth — C-layer market-level microstructure.

Fills the gap where C-layer was holdings-only: distinguishes "my names
are down" from "the whole market is deteriorating underneath the index".

Free proxy set (yfinance, no per-constituent scan):
  - 11 SPDR sector ETFs: % advancing today, % above own 50d MA
  - Style/size: SPY QQQ IWM DIA relative 20d performance
  - SPY vs its own 50d / 200d MA

Usage:
  python update_breadth.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parent
OUT_JSON = ROOT / "latest_breadth.json"
OUT_DIR = ROOT / "charts"
OUT_DIR.mkdir(exist_ok=True)
START = "2023-01-01"  # breadth ratios only need ~1y of context

BG = "#0d1117"
FG = "#e6edf3"
GRID = "#21262d"

SECTORS = {
    "XLK": "Tech",
    "XLF": "Financials",
    "XLV": "Health Care",
    "XLY": "Cons Discretionary",
    "XLP": "Cons Staples",
    "XLE": "Energy",
    "XLI": "Industrials",
    "XLB": "Materials",
    "XLU": "Utilities",
    "XLRE": "Real Estate",
    "XLC": "Comm Services",
}
INDEXES = ["SPY", "QQQ", "IWM", "DIA"]


def fetch_closes(tickers: list[str]) -> pd.DataFrame:
    raw = yf.download(tickers, start=START, progress=False, auto_adjust=True)
    if raw is None or raw.empty:
        return pd.DataFrame()
    close = raw["Close"]
    if isinstance(close, pd.Series):
        close = close.to_frame(tickers[0])
    return close.dropna(how="all")


def styled_ax(ax: plt.Axes) -> None:
    ax.set_facecolor(BG)
    ax.tick_params(colors=FG, which="both")
    for spine in ax.spines.values():
        spine.set_color(GRID)
    ax.grid(True, color=GRID, linestyle="-", linewidth=0.5, alpha=0.7)
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))


def plot_breadth(pct_above_50d: pd.Series, spy: pd.Series, spy50: pd.Series, spy200: pd.Series) -> Path:
    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(12, 7), sharex=True, gridspec_kw={"height_ratios": [1.4, 2]}
    )
    fig.patch.set_facecolor(BG)

    ax1.plot(pct_above_50d.index, pct_above_50d.values, color="#3fb950", linewidth=1.5)
    ax1.fill_between(pct_above_50d.index, pct_above_50d.values, color="#3fb950", alpha=0.12)
    ax1.axhline(50, color=GRID, linewidth=0.9)
    styled_ax(ax1)
    ax1.set_ylim(0, 100)
    ax1.set_ylabel("% sectors > 50d MA", color=FG)
    ax1.set_title(
        "Market breadth  ·  sector participation  (C-layer, market-level)",
        color=FG, fontsize=13, fontweight="bold", pad=12,
    )
    if not pct_above_50d.empty:
        ax1.text(
            0.99, 0.85,
            f"latest: {pct_above_50d.iloc[-1]:.0f}%  ·  {pct_above_50d.index[-1].strftime('%Y-%m-%d')}",
            transform=ax1.transAxes, color=FG, fontsize=9, ha="right", alpha=0.9,
        )

    ax2.plot(spy.index, spy.values, color="#e6edf3", linewidth=1.4, label="SPY")
    ax2.plot(spy50.index, spy50.values, color="#f0b429", linewidth=1.0, label="50d MA")
    ax2.plot(spy200.index, spy200.values, color="#f85149", linewidth=1.0, label="200d MA")
    styled_ax(ax2)
    ax2.set_ylabel("SPY", color=FG)
    leg = ax2.legend(loc="upper left", facecolor=BG, edgecolor=GRID, labelcolor=FG)
    for txt in leg.get_texts():
        txt.set_color(FG)

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    fig.text(
        0.99, 0.01,
        f"Updated {stamp}  ·  yfinance sector ETFs  ·  not a trading signal",
        ha="right", va="bottom", color=FG, fontsize=8, alpha=0.7,
    )
    plt.tight_layout()
    out = OUT_DIR / "breadth.png"
    fig.savefig(out, dpi=160, facecolor=BG)
    plt.close(fig)
    return out


def main() -> int:
    tickers = list(SECTORS.keys()) + INDEXES
    print(f"Fetching {len(tickers)} tickers (yfinance)...")
    close = fetch_closes(tickers)
    if close.empty:
        print("ERROR: yfinance returned no data", file=sys.stderr)
        return 1
    have = [t for t in tickers if t in close.columns]
    missing = sorted(set(tickers) - set(have))
    if missing:
        print(f"  WARNING missing tickers: {missing}", file=sys.stderr)
    sector_cols = [t for t in SECTORS if t in close.columns]
    print(f"  {len(close)} rows, latest {close.index[-1].date()}, sectors={len(sector_cols)}")

    sectors = close[sector_cols].dropna(how="all")
    daily_ret = sectors.pct_change()
    last_ret = daily_ret.iloc[-1]
    advancing = [t for t in sector_cols if pd.notna(last_ret[t]) and last_ret[t] > 0]
    declining = [t for t in sector_cols if pd.notna(last_ret[t]) and last_ret[t] < 0]

    ma50 = sectors.rolling(50).mean()
    above_50 = (sectors > ma50).sum(axis=1) / len(sector_cols) * 100.0
    above_50 = above_50[ma50.dropna(how="all").index.min():]
    now_above_50 = [t for t in sector_cols if sectors[t].iloc[-1] > ma50[t].iloc[-1]]

    spy = close["SPY"].dropna()
    spy50 = spy.rolling(50).mean().dropna()
    spy200 = spy.rolling(200).mean().dropna()
    spy_above_50 = bool(spy.iloc[-1] > spy50.iloc[-1]) if not spy50.empty else None
    spy_above_200 = bool(spy.iloc[-1] > spy200.iloc[-1]) if not spy200.empty else None

    def rel_perf(t: str, n: int = 20) -> float | None:
        if t not in close.columns:
            return None
        s = close[t].dropna()
        if len(s) <= n:
            return None
        return round((float(s.iloc[-1]) / float(s.iloc[-(n + 1)]) - 1) * 100, 2)

    size_style = {t: rel_perf(t) for t in INDEXES}

    pct_above = round(float(above_50.iloc[-1]), 1) if not above_50.empty else None
    flags = []
    if pct_above is not None and pct_above <= 30:
        flags.append("breadth_weak_le_30pct")
    if pct_above is not None and pct_above >= 80:
        flags.append("breadth_strong_ge_80pct")
    if spy_above_200 is False:
        flags.append("spy_below_200d")
    # Narrow leadership: index above MAs while most sectors below theirs
    if spy_above_50 and pct_above is not None and pct_above < 50:
        flags.append("narrow_leadership")
    iwm_20d = size_style.get("IWM")
    spy_20d = size_style.get("SPY")
    if iwm_20d is not None and spy_20d is not None and (iwm_20d - spy_20d) <= -3.0:
        flags.append("small_caps_lagging_ge_3pct_20d")

    payload = {
        "updated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "source": "yfinance (SPDR sector ETFs + SPY/QQQ/IWM/DIA)",
        "layer": "C-market breadth (proxy)",
        "as_of": close.index[-1].strftime("%Y-%m-%d"),
        "sectors": {
            "count": len(sector_cols),
            "advancing_today": len(advancing),
            "declining_today": len(declining),
            "advancing_names": advancing,
            "pct_above_50d_ma": pct_above,
            "above_50d_names": now_above_50,
        },
        "spy": {
            "above_50d_ma": spy_above_50,
            "above_200d_ma": spy_above_200,
        },
        "size_style_20d_pct": size_style,
        "heuristic_flags": flags,
        "read_hint": (
            "Sector participation proxies A/D breadth without scanning constituents. "
            "narrow_leadership = index held up by few sectors — fragile tape."
        ),
        "disclaimer": (
            "Sector-ETF breadth is a coarse proxy for constituent-level A/D. "
            "Not a trading signal."
        ),
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    print("Rendering breadth.png...")
    out = plot_breadth(above_50, spy, spy50, spy200)
    print(f"  wrote {out}")
    print(f"Wrote {OUT_JSON}")
    print(
        f"Breadth: {len(advancing)}/{len(sector_cols)} sectors up today, "
        f"{pct_above}% above 50d MA"
    )
    print("Heuristic flags:", flags or "(none)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
