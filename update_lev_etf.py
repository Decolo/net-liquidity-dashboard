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
    """Return ETF AUM snapshot or None on failure.

    AUM = Yahoo ``info.totalAssets`` (the fund's reported AUM). Known caveat:
    Yahoo refreshes this field with a lag (days), so the monitor pairs it with
    a LIVE price (fast_info.last_price) and a freshness ledger in the history
    file — when AUM stops changing while prices move, ``stale_days`` and the
    ``aum_stale_*`` flag surface it instead of pretending the data is fresh.
    """
    try:
        t = yf.Ticker(ticker)
        info = t.info or {}
        total = info.get("totalAssets")
        if not total:
            return None
        try:
            price = getattr(t.fast_info, "last_price", None) or info.get("regularMarketPrice")
        except Exception:
            price = info.get("regularMarketPrice")
        return {
            "ticker": ticker,
            "name": info.get("shortName") or info.get("longName") or ticker,
            "aum_M": round(total / 1_000_000, 1),
            "aum_source": "yahoo_totalAssets",
            "price": round(price, 2) if price else None,
            "nav_price": round(info["navPrice"], 2) if info.get("navPrice") else None,
            "as_of": datetime.now().strftime("%Y-%m-%d"),   # snapshot date
        }
    except Exception:
        return None


# ── History ledger (freshness + trajectory) ────────────────────────────────

HISTORY_FILE = ROOT / "data" / "lev_etf_history.jsonl"


def load_history() -> list[dict]:
    if not HISTORY_FILE.exists():
        return []
    return [json.loads(l) for l in
            HISTORY_FILE.read_text(encoding="utf-8").splitlines() if l.strip()]


def stale_days_for(rows: list[dict], current_total: float) -> int:
    """Days since the current total AUM value was first observed."""
    n = 0
    for r in reversed(rows):
        if abs((r.get("total_aum_B") or 0) - current_total) < 0.05:
            n += 1
        else:
            break
    return n


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

def write_status(etfs: list[dict], total_aum_B: float, stale_days: int) -> dict:
    """Write latest_lev_etf.json and return the payload."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    today = now[:10]

    semi_t = ("SOXL", "SOXS", "USD", "SSG")
    semi_B = round(sum(e["aum_M"] for e in etfs if e["ticker"] in semi_t) / 1000, 2)

    flags: list[str] = []
    if total_aum_B < 50:
        flags.append("lev_etf_aum_low")
    if stale_days >= 2:
        flags.append(f"aum_stale_{stale_days}d")

    payload = {
        "updated_utc": now,                 # when this snapshot was produced
        "data_as_of": (load_history()[-stale_days]["date"] if stale_days > 0 else today),
        "stale_days": stale_days,
        "source": "Yahoo Finance (yfinance) — info.totalAssets",
        "layer": "B-leverage",
        "total_aum_B": round(total_aum_B, 2),
        "semi_aum_B": semi_B,
        "etf_count": len(etfs),
        "etfs": etfs,
        "heuristic_flags": flags,
        "read_hint": (
            "Leveraged ETF AUM shrinking = retail/speculative money leaving. "
            "Rapid drawdown (>20% in 30d) signals forced deleveraging risk. "
            "stale_days > 0 means Yahoo's totalAssets has not refreshed — treat "
            "AUM as of data_as_of, not today. Flag 'aum_stale_Nd' fires at N>=2."
        ),
        "disclaimer": (
            "AUM from Yahoo Finance (yfinance) info.totalAssets, which refreshes "
            "with a lag of days. stale_days shows how old the figure is. "
            "Not a trading signal."
        ),
    }
    ROOT.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    return payload


def append_history(payload: dict) -> None:
    """Append one row per run date; same-day reruns replace that day's row."""
    if not payload.get("etfs"):
        return
    by = {e["ticker"]: e for e in payload["etfs"]}
    row = {
        "date": payload["updated_utc"][:10],
        "total_aum_B": payload["total_aum_B"],
        "semi_aum_B": payload.get("semi_aum_B"),
        "soxl_aum_B": round(by["SOXL"]["aum_M"] / 1000, 3) if "SOXL" in by else None,
        "tqqq_aum_B": round(by["TQQQ"]["aum_M"] / 1000, 3) if "TQQQ" in by else None,
        "stale_days": payload.get("stale_days"),
        "flags": payload.get("heuristic_flags") or [],
    }
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    lines = (HISTORY_FILE.read_text(encoding="utf-8").splitlines()
             if HISTORY_FILE.exists() else [])
    if lines and json.loads(lines[-1]).get("date") == row["date"]:
        lines.pop()  # replace today's row (idempotent reruns)
    lines.append(json.dumps(row, ensure_ascii=False))
    HISTORY_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"History: {len(lines)} day(s) in {HISTORY_FILE.relative_to(ROOT)}")


# ── Main ────────────────────────────────────────────────────────────────────

def main() -> int:
    print("Fetching leveraged ETF AUM via yfinance...")
    etfs = []
    for tkr in TICKERS:
        d = fetch_aum(tkr)
        if d:
            etfs.append(d)
            px = f"px ${d['price']:,.2f}" if d.get("price") else "px n/a"
            print(f"  {tkr:6s}  AUM ${d['aum_M']:>10,.1f}M  {px}  {d.get('name', '')}")
        else:
            print(f"  {tkr:6s}  SKIP — data unavailable")

    if not etfs:
        print("ERROR: no ETF data fetched.", file=sys.stderr)
        return 1

    total_aum_B = sum(e["aum_M"] for e in etfs) / 1000
    rows = load_history()
    stale = stale_days_for(rows, total_aum_B)
    print(f"\nTotal AUM: ${total_aum_B:.1f}B across {len(etfs)} ETFs"
          + (f"  [stale: value unchanged for {stale} day(s)]" if stale else ""))

    chart_path = plot_lev_etf(etfs)
    if chart_path:
        print(f"  wrote {chart_path}")

    payload = write_status(etfs, total_aum_B, stale)
    append_history(payload)
    print(f"Wrote {OUT_JSON}")
    flags = payload.get("heuristic_flags") or []
    print(f"Heuristic flags: {'(none)' if not flags else ', '.join(flags)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
