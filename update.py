"""Fetch FRED series, compute US Net Liquidity, and render dashboard charts.

Net Liquidity = Fed Balance Sheet (WALCL) - Treasury General Account (WTREGEN)
                - Overnight Reverse Repo (RRPONTSYD)

Designed to run under GitHub Actions on a daily cron. No API keys required.
"""

from __future__ import annotations

import io
import sys
from datetime import datetime, timezone
from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd
import requests
import yfinance as yf

FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"
SERIES = {
    "WALCL": "Fed Balance Sheet",
    "WTREGEN": "Treasury General Account",
    "RRPONTSYD": "Overnight Reverse Repo",
    "SOFR": "Secured Overnight Financing Rate",
    "IORB": "Interest on Reserve Balances",
    "WRESBAL": "Reserve Balances",
}
START = "2020-01-01"
OUT_DIR = Path(__file__).parent / "charts"
OUT_DIR.mkdir(exist_ok=True)

BG = "#0d1117"
FG = "#e6edf3"
GRID = "#21262d"
ACCENT = "#a371f7"
BTC = "#f7931a"
COLORS = {
    "WALCL": "#3fb950",
    "WTREGEN": "#f85149",
    "RRPONTSYD": "#58a6ff",
    "SOFR": "#58a6ff",
    "IORB": "#3fb950",
    "SPREAD": "#f0b429",
}


def fetch_fred(series: str) -> pd.Series:
    r = requests.get(FRED_CSV.format(series=series), timeout=30)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text), parse_dates=["observation_date"], na_values=".")
    df = df.rename(columns={"observation_date": "date", series: "value"})
    df = df.dropna().set_index("date").sort_index()
    return df["value"].astype(float)


def fetch_btc() -> pd.Series:
    raw = yf.download("BTC-USD", start=START, progress=False, auto_adjust=True)
    if raw is None or raw.empty:
        return pd.Series(dtype=float)
    close = raw["Close"]
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]
    return close.dropna()


def styled_ax(ax: plt.Axes) -> None:
    ax.set_facecolor(BG)
    ax.tick_params(colors=FG, which="both")
    for spine in ax.spines.values():
        spine.set_color(GRID)
    ax.grid(True, color=GRID, linestyle="-", linewidth=0.5, alpha=0.7)
    ax.xaxis.set_major_locator(mdates.YearLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))


def plot_net_liquidity(walcl: pd.Series, tga: pd.Series, rrp: pd.Series, btc: pd.Series) -> Path:
    # FRED units: WALCL = Millions of $; WTREGEN = Millions of $; RRPONTSYD = Billions of $.
    idx = walcl.index.union(tga.index).union(rrp.index)
    idx = idx[idx >= pd.Timestamp(START)]
    walcl_t = walcl.reindex(idx).ffill() / 1_000_000   # millions -> trillions
    tga_t = tga.reindex(idx).ffill() / 1_000_000       # millions -> trillions
    rrp_t = rrp.reindex(idx).ffill() / 1_000           # billions  -> trillions
    net_t = (walcl_t - tga_t - rrp_t).dropna()

    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(12, 8), sharex=True, gridspec_kw={"height_ratios": [3, 1]}
    )
    fig.patch.set_facecolor(BG)

    ax1b = ax1.twinx()
    line_nl = ax1.plot(net_t.index, net_t.values, color=ACCENT, linewidth=2.2, label="Net Liquidity")
    line_btc = []
    if not btc.empty:
        btc_clip = btc[btc.index >= net_t.index.min()]
        line_btc = ax1b.plot(btc_clip.index, btc_clip.values, color=BTC, linewidth=1.2, alpha=0.8, label="BTC/USD")
        ax1b.set_yscale("log")
        ax1b.tick_params(colors=BTC, which="both")
        ax1b.set_ylabel("BTC/USD (log)", color=BTC)
        for spine in ax1b.spines.values():
            spine.set_color(GRID)

    styled_ax(ax1)
    ax1.set_ylabel("Net Liquidity ($T)", color=ACCENT)
    ax1.set_title(
        "US Net Liquidity   =   Fed Balance Sheet  −  TGA  −  RRP",
        color=FG, fontsize=14, fontweight="bold", pad=14,
    )

    lines = line_nl + line_btc
    labels = [l.get_label() for l in lines]
    leg = ax1.legend(lines, labels, loc="upper left", facecolor=BG, edgecolor=GRID, labelcolor=FG)
    for txt in leg.get_texts():
        txt.set_color(FG)

    # Lower panel: net liquidity 30d delta
    delta = net_t.diff(30)
    ax2.fill_between(
        delta.index, 0, delta.values,
        where=delta.values >= 0, color="#3fb950", alpha=0.65, interpolate=True,
    )
    ax2.fill_between(
        delta.index, 0, delta.values,
        where=delta.values < 0, color="#f85149", alpha=0.65, interpolate=True,
    )
    ax2.axhline(0, color=GRID, linewidth=0.8)
    styled_ax(ax2)
    ax2.set_ylabel("30-day Δ ($T)", color=FG)

    latest = net_t.iloc[-1]
    latest_date = net_t.index[-1].strftime("%Y-%m-%d")
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    fig.text(
        0.99, 0.01,
        f"Latest: ${latest:.2f}T  ·  {latest_date}  ·  Updated {stamp}  ·  Source: FRED",
        ha="right", va="bottom", color=FG, fontsize=8, alpha=0.7,
    )

    plt.tight_layout()
    out = OUT_DIR / "net_liquidity.png"
    fig.savefig(out, dpi=180, facecolor=BG)
    plt.close(fig)
    return out


def plot_components(walcl: pd.Series, tga: pd.Series, rrp: pd.Series) -> Path:
    fig, axes = plt.subplots(3, 1, figsize=(12, 9), sharex=True)
    fig.patch.set_facecolor(BG)

    series_map = [
        ("WALCL", walcl / 1_000_000, "Fed Balance Sheet (WALCL)  —  $T"),
        ("WTREGEN", tga / 1_000_000, "Treasury General Account (WTREGEN)  —  $T"),
        ("RRPONTSYD", rrp / 1_000, "Overnight Reverse Repo (RRPONTSYD)  —  $T"),
    ]

    for ax, (code, s, title) in zip(axes, series_map):
        s = s[s.index >= pd.Timestamp(START)].dropna()
        ax.plot(s.index, s.values, color=COLORS[code], linewidth=1.8)
        ax.fill_between(s.index, s.values, color=COLORS[code], alpha=0.15)
        styled_ax(ax)
        ax.set_title(title, color=FG, fontsize=11, pad=8, loc="left")
        if not s.empty:
            latest = s.iloc[-1]
            latest_date = s.index[-1].strftime("%Y-%m-%d")
            ax.text(
                0.99, 0.92,
                f"latest: ${latest:.2f}T  ·  {latest_date}",
                transform=ax.transAxes, color=FG, fontsize=9, ha="right", alpha=0.85,
            )

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    fig.text(
        0.99, 0.005,
        f"Updated {stamp}  ·  Source: FRED",
        ha="right", va="bottom", color=FG, fontsize=8, alpha=0.7,
    )

    plt.tight_layout()
    out = OUT_DIR / "components.png"
    fig.savefig(out, dpi=180, facecolor=BG)
    plt.close(fig)
    return out


def plot_funding(sofr: pd.Series, iorb: pd.Series) -> Path:
    """Price-layer: SOFR, IORB, and SOFR−IORB spread (bp)."""
    idx = sofr.index.union(iorb.index)
    idx = idx[idx >= pd.Timestamp(START)]
    sofr_a = sofr.reindex(idx).ffill().dropna()
    iorb_a = iorb.reindex(idx).ffill().dropna()
    both = sofr_a.index.intersection(iorb_a.index)
    sofr_a = sofr_a.loc[both]
    iorb_a = iorb_a.loc[both]
    spread_bp = (sofr_a - iorb_a) * 100.0  # percent → bp

    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(12, 7), sharex=True, gridspec_kw={"height_ratios": [2, 1.2]}
    )
    fig.patch.set_facecolor(BG)

    ax1.plot(sofr_a.index, sofr_a.values, color=COLORS["SOFR"], linewidth=1.6, label="SOFR")
    ax1.plot(iorb_a.index, iorb_a.values, color=COLORS["IORB"], linewidth=1.6, label="IORB")
    styled_ax(ax1)
    ax1.set_ylabel("%", color=FG)
    ax1.set_title(
        "Funding prices  ·  SOFR vs IORB  (A-layer price)",
        color=FG, fontsize=13, fontweight="bold", pad=12,
    )
    leg = ax1.legend(loc="upper left", facecolor=BG, edgecolor=GRID, labelcolor=FG)
    for txt in leg.get_texts():
        txt.set_color(FG)
    if not sofr_a.empty:
        ax1.text(
            0.99, 0.92,
            f"SOFR {sofr_a.iloc[-1]:.2f}%  ·  IORB {iorb_a.iloc[-1]:.2f}%  ·  {sofr_a.index[-1].strftime('%Y-%m-%d')}",
            transform=ax1.transAxes, color=FG, fontsize=9, ha="right", alpha=0.9,
        )

    ax2.fill_between(
        spread_bp.index, 0, spread_bp.values,
        where=spread_bp.values >= 0, color="#f85149", alpha=0.55, interpolate=True,
    )
    ax2.fill_between(
        spread_bp.index, 0, spread_bp.values,
        where=spread_bp.values < 0, color="#3fb950", alpha=0.45, interpolate=True,
    )
    ax2.plot(spread_bp.index, spread_bp.values, color=COLORS["SPREAD"], linewidth=1.2)
    ax2.axhline(0, color=GRID, linewidth=0.9)
    styled_ax(ax2)
    ax2.set_ylabel("SOFR − IORB (bp)", color=FG)
    ax2.set_title(
        "Spread: >0 means secured overnight above reserve interest (tighter funding feel)",
        color=FG, fontsize=10, pad=6, loc="left",
    )

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    fig.text(
        0.99, 0.01,
        f"Updated {stamp}  ·  Source: FRED (SOFR, IORB)  ·  not a trading signal",
        ha="right", va="bottom", color=FG, fontsize=8, alpha=0.7,
    )
    plt.tight_layout()
    out = OUT_DIR / "funding_sofr_iorb.png"
    fig.savefig(out, dpi=180, facecolor=BG)
    plt.close(fig)
    return out


def _chg_days(s: pd.Series, days: int) -> float | None:
    """Change over a calendar-day lookback using as-of alignment."""
    s = s.dropna()
    if s.empty:
        return None
    end = s.index[-1]
    start = end - pd.Timedelta(days=days)
    if start < s.index[0]:
        return None
    past = s.asof(start)
    if pd.isna(past):
        return None
    return float(s.iloc[-1] - past)


def write_status(
    walcl: pd.Series,
    tga: pd.Series,
    rrp: pd.Series,
    sofr: pd.Series | None = None,
    iorb: pd.Series | None = None,
    wresbal: pd.Series | None = None,
) -> None:
    walcl_t = walcl / 1_000_000
    tga_t = tga / 1_000_000
    rrp_t = rrp / 1_000
    idx = walcl_t.index.union(tga_t.index).union(rrp_t.index)
    net_t = (
        walcl_t.reindex(idx).ffill()
        - tga_t.reindex(idx).ffill()
        - rrp_t.reindex(idx).ffill()
    ).dropna()

    latest = {
        "as_of": net_t.index[-1].strftime("%Y-%m-%d"),
        "net_liquidity_T": round(float(net_t.iloc[-1]), 3),
        "walcl_T": round(float(walcl_t.iloc[-1]), 3),
        "tga_T": round(float(tga_t.iloc[-1]), 3),
        "rrp_T": round(float(rrp_t.iloc[-1]), 3),
        "delta_30d_T": round(float(net_t.iloc[-1] - net_t.iloc[-31]), 3) if len(net_t) > 31 else None,
        "updated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "notes": {
            "net_liquidity": "Market proxy: WALCL - TGA - RRP (not official Fed series)",
            "units": "WALCL/TGA FRED millions; RRP billions; WRESBAL millions → T as noted",
            "funding": "SOFR-IORB in bp; positive = SOFR above IORB",
        },
    }

    if wresbal is not None and not wresbal.empty:
        # WRESBAL: millions of USD (week average / similar to reserves)
        latest["wresbal_T"] = round(float(wresbal.iloc[-1]) / 1_000_000, 3)
        latest["wresbal_as_of"] = wresbal.index[-1].strftime("%Y-%m-%d")

    # Regime inputs (consumed by regime.py — keep field names stable)
    walcl_chg = _chg_days(walcl, 30)  # millions
    if walcl_chg is not None:
        latest["walcl_chg_30d_B"] = round(walcl_chg / 1000, 2)
    tga_chg = _chg_days(tga, 30)  # millions
    if tga_chg is not None:
        latest["tga_chg_30d_B"] = round(tga_chg / 1000, 2)
    if iorb is not None and not iorb.empty:
        iorb_chg = _chg_days(iorb, 60)  # percent
        if iorb_chg is not None:
            latest["iorb_chg_60d_bp"] = round(iorb_chg * 100, 1)

    if sofr is not None and iorb is not None and not sofr.empty and not iorb.empty:
        # Align last common date
        both = sofr.dropna().index.intersection(iorb.dropna().index)
        if len(both) > 0:
            d = both[-1]
            s = float(sofr.loc[d])
            i = float(iorb.loc[d])
            latest["sofr_pct"] = round(s, 4)
            latest["iorb_pct"] = round(i, 4)
            latest["sofr_minus_iorb_bp"] = round((s - i) * 100.0, 2)
            latest["funding_as_of"] = d.strftime("%Y-%m-%d")
            if len(both) > 5:
                s5 = float(sofr.loc[both[-6]])
                i5 = float(iorb.loc[both[-6]])
                latest["sofr_minus_iorb_bp_5d_ago"] = round((s5 - i5) * 100.0, 2)

    (Path(__file__).parent / "latest.json").write_text(
        __import__("json").dumps(latest, indent=2) + "\n", encoding="utf-8"
    )


def main() -> int:
    print("Fetching FRED series...")
    walcl = fetch_fred("WALCL")
    tga = fetch_fred("WTREGEN")
    rrp = fetch_fred("RRPONTSYD")
    print(f"  WALCL:     {len(walcl):>5} pts, latest {walcl.index[-1].date()}  ${walcl.iloc[-1]/1_000_000:.2f}T")
    print(f"  WTREGEN:   {len(tga):>5} pts, latest {tga.index[-1].date()}  ${tga.iloc[-1]/1_000_000:.2f}T")
    print(f"  RRPONTSYD: {len(rrp):>5} pts, latest {rrp.index[-1].date()}  ${rrp.iloc[-1]/1_000:.2f}T")

    print("Fetching funding + reserves (SOFR, IORB, WRESBAL)...")
    sofr = fetch_fred("SOFR")
    iorb = fetch_fred("IORB")
    wresbal = fetch_fred("WRESBAL")
    print(f"  SOFR:      {len(sofr):>5} pts, latest {sofr.index[-1].date()}  {sofr.iloc[-1]:.2f}%")
    print(f"  IORB:      {len(iorb):>5} pts, latest {iorb.index[-1].date()}  {iorb.iloc[-1]:.2f}%")
    both = sofr.dropna().index.intersection(iorb.dropna().index)
    if len(both):
        sp_bp = (float(sofr.loc[both[-1]]) - float(iorb.loc[both[-1]])) * 100.0
        print(f"  SOFR−IORB: {sp_bp:+.1f} bp  (as of {both[-1].date()})")
    print(f"  WRESBAL:   {len(wresbal):>5} pts, latest {wresbal.index[-1].date()}  ${wresbal.iloc[-1]/1_000_000:.2f}T")

    print("Fetching BTC/USD...")
    btc = fetch_btc()
    if btc.empty:
        print("  (BTC fetch returned empty — chart will render without overlay)")
    else:
        print(f"  BTC: {len(btc)} pts, latest {btc.index[-1].date()}  ${btc.iloc[-1]:.0f}")

    print("Rendering charts...")
    p1 = plot_net_liquidity(walcl, tga, rrp, btc)
    p2 = plot_components(walcl, tga, rrp)
    p3 = plot_funding(sofr, iorb)
    print(f"  wrote {p1}")
    print(f"  wrote {p2}")
    print(f"  wrote {p3}")

    write_status(walcl, tga, rrp, sofr=sofr, iorb=iorb, wresbal=wresbal)
    print("Wrote latest.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
