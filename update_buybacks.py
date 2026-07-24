#!/usr/bin/env python3
"""Corporate buyback proxy (B-layer structural equity demand).

Buybacks are the single largest structural equity bid (~$800B–$1T/yr for
the S&P 500). Exact daily execution data is desk-proprietary, so this
script layers free proxies instead:

  Daily:     PKW/SPY ratio (Invesco BuyBack Achievers vs S&P 500, yfinance).
             Buyback-heavy names underperforming ≈ buyback impulse fading.
  Quarterly: S&P 500 total buybacks from data/sp500_buybacks_quarterly.csv.
             MANUAL APPEND: S&P DJI publishes each quarter's total in a
             press release ~2-3 weeks after quarter-end — add a row there.

Usage:
  python update_buybacks.py
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
OUT_JSON = ROOT / "latest_buybacks.json"
QUARTERLY_CSV = ROOT / "data" / "sp500_buybacks_quarterly.csv"
OUT_DIR = ROOT / "charts"
OUT_DIR.mkdir(exist_ok=True)
START = "2020-01-01"

BG = "#0d1117"
FG = "#e6edf3"
GRID = "#21262d"
ACCENT = "#a371f7"


def fetch_ratio() -> pd.Series:
    """PKW/SPY adjusted-close ratio, normalized to 1.0 at window start."""
    raw = yf.download(["PKW", "SPY"], start=START, progress=False, auto_adjust=True)
    if raw is None or raw.empty:
        return pd.Series(dtype=float)
    close = raw["Close"]
    if not {"PKW", "SPY"}.issubset(close.columns):
        return pd.Series(dtype=float)
    ratio = (close["PKW"] / close["SPY"]).dropna()
    if ratio.empty:
        return ratio
    return ratio / ratio.iloc[0]


def load_quarterly() -> pd.DataFrame:
    if not QUARTERLY_CSV.exists():
        return pd.DataFrame()
    df = pd.read_csv(QUARTERLY_CSV, parse_dates=["quarter_end"])
    df["buybacks_B"] = pd.to_numeric(df["buybacks_B"], errors="coerce")
    return df.dropna(subset=["quarter_end", "buybacks_B"]).sort_values("quarter_end")


def _pct_chg(s: pd.Series, n: int) -> float | None:
    s = s.dropna()
    if len(s) <= n:
        return None
    past = float(s.iloc[-(n + 1)])
    if past == 0:
        return None
    return round((float(s.iloc[-1]) / past - 1.0) * 100.0, 2)


def styled_ax(ax: plt.Axes) -> None:
    ax.set_facecolor(BG)
    ax.tick_params(colors=FG, which="both")
    for spine in ax.spines.values():
        spine.set_color(GRID)
    ax.grid(True, color=GRID, linestyle="-", linewidth=0.5, alpha=0.7)


def plot_buybacks(ratio: pd.Series, ma200: pd.Series, quarterly: pd.DataFrame) -> Path:
    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(12, 8), gridspec_kw={"height_ratios": [2, 1.3]}
    )
    fig.patch.set_facecolor(BG)

    ax1.plot(ratio.index, ratio.values, color=ACCENT, linewidth=1.6, label="PKW/SPY (norm.)")
    if not ma200.empty:
        ax1.plot(ma200.index, ma200.values, color="#f0b429", linewidth=1.1, alpha=0.9, label="200d MA")
    styled_ax(ax1)
    ax1.xaxis.set_major_locator(mdates.YearLocator())
    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax1.set_title(
        "Buyback proxy  ·  PKW/SPY relative strength  (B-layer structural bid)",
        color=FG, fontsize=13, fontweight="bold", pad=12,
    )
    leg = ax1.legend(loc="upper right", facecolor=BG, edgecolor=GRID, labelcolor=FG)
    for txt in leg.get_texts():
        txt.set_color(FG)
    if not ratio.empty:
        ax1.text(
            0.99, 0.04,
            f"latest: {ratio.iloc[-1]:.3f}  ·  {ratio.index[-1].strftime('%Y-%m-%d')}",
            transform=ax1.transAxes, color=FG, fontsize=9, ha="right", alpha=0.9,
        )

    if not quarterly.empty:
        ax2.bar(
            quarterly["quarter_end"], quarterly["buybacks_B"],
            width=55, color="#3fb950", alpha=0.75,
        )
        styled_ax(ax2)
        ax2.xaxis.set_major_locator(mdates.YearLocator())
        ax2.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
        ax2.set_ylabel("$B / quarter", color=FG)
        last = quarterly.iloc[-1]
        ax2.set_title(
            f"S&P 500 quarterly buybacks (S&P DJI, manual append)  ·  "
            f"latest {last['quarter']}: ${last['buybacks_B']:.0f}B",
            color=FG, fontsize=10, pad=6, loc="left",
        )

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    fig.text(
        0.99, 0.01,
        f"Updated {stamp}  ·  yfinance + S&P DJI  ·  proxy only, not execution data  ·  not a trading signal",
        ha="right", va="bottom", color=FG, fontsize=8, alpha=0.7,
    )
    plt.tight_layout()
    out = OUT_DIR / "buybacks.png"
    fig.savefig(out, dpi=160, facecolor=BG)
    plt.close(fig)
    return out


def main() -> int:
    print("Fetching PKW/SPY ratio (yfinance)...")
    ratio = fetch_ratio()
    if ratio.empty:
        print("ERROR: could not fetch PKW/SPY from yfinance", file=sys.stderr)
        return 1
    ma200 = ratio.rolling(200).mean().dropna()
    print(f"  ratio: {len(ratio)} pts, latest {ratio.index[-1].date()}  {ratio.iloc[-1]:.3f}")

    quarterly = load_quarterly()
    if quarterly.empty:
        print("  (no quarterly CSV — chart/json will omit S&P data)")
    else:
        last_q = quarterly.iloc[-1]
        print(f"  quarterly: {len(quarterly)} rows, latest {last_q['quarter']}  ${last_q['buybacks_B']:.0f}B")

    above_ma = (
        bool(float(ratio.iloc[-1]) >= float(ma200.iloc[-1])) if not ma200.empty else None
    )
    chg_20d = _pct_chg(ratio, 20)
    chg_60d = _pct_chg(ratio, 60)

    flags = []
    if above_ma is False and chg_60d is not None and chg_60d < -2.0:
        flags.append("buyback_proxy_weak")  # below 200d MA and fading
    if above_ma and chg_60d is not None and chg_60d > 2.0:
        flags.append("buyback_proxy_strong")

    quarterly_block = None
    if not quarterly.empty:
        last_q = quarterly.iloc[-1]
        yoy = None
        if len(quarterly) >= 5:
            yoy = round(
                (float(last_q["buybacks_B"]) / float(quarterly.iloc[-5]["buybacks_B"]) - 1) * 100, 1
            )
        ttm = round(float(quarterly["buybacks_B"].tail(4).sum()), 1) if len(quarterly) >= 4 else None
        quarterly_block = {
            "latest_quarter": str(last_q["quarter"]),
            "latest_B": float(last_q["buybacks_B"]),
            "trailing_4q_B": ttm,
            "yoy_pct": yoy,
            "rows": int(len(quarterly)),
            "csv": str(QUARTERLY_CSV),
            "append_hint": "Add new quarters from S&P DJI buyback press releases (~2-3w after quarter-end).",
        }

    payload = {
        "updated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "source": "yfinance (PKW, SPY) + S&P DJI quarterly (manual CSV)",
        "layer": "B-buybacks (proxy)",
        "daily_proxy": {
            "as_of": ratio.index[-1].strftime("%Y-%m-%d"),
            "pkw_spy_ratio_norm": round(float(ratio.iloc[-1]), 4),
            "above_200d_ma": above_ma,
            "chg_20d_pct": chg_20d,
            "chg_60d_pct": chg_60d,
        },
        "quarterly_sp500": quarterly_block,
        "heuristic_flags": flags,
        "read_hint": (
            "PKW/SPY falling = buyback-heavy names underperforming ≈ buyback impulse fading. "
            "Quarterly S&P total is the ground truth (lagged ~1 quarter)."
        ),
        "disclaimer": (
            "Proxy only — real daily buyback execution data is desk-proprietary. "
            "Not a trading signal."
        ),
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    print("Rendering buybacks.png...")
    out = plot_buybacks(ratio, ma200, quarterly)
    print(f"  wrote {out}")
    print(f"Wrote {OUT_JSON}")
    print("Heuristic flags:", flags or "(none)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
