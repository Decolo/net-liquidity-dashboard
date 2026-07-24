"""H.8 commercial bank credit — A-layer transmission (bank lending).

Why: WALCL shows how much water the Fed put in; H.8 shows whether banks
are actually passing it through. Loan contraction (e.g. post-SVB 2023)
means net-liquidity alone overstates how friendly conditions are.

Series (FRED, weekly Wednesday release, ~1 week lag):
  - TLAACBW027SBOG: Total loans and leases, all commercial banks (SA, $B)
  - BUSLOANS:       Commercial & industrial loans (SA, $B, monthly)

Same pattern as update_bc.py: FRED CSV, no API key.
"""

from __future__ import annotations

import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd
import requests

FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"
START = "2020-01-01"
OUT_DIR = Path(__file__).parent / "charts"
OUT_DIR.mkdir(exist_ok=True)
ROOT = Path(__file__).parent

BG = "#0d1117"
FG = "#e6edf3"
GRID = "#21262d"

SERIES = {
    "TLAACBW027SBOG": {"name": "Total loans & leases, all commercial banks", "unit": "$B", "color": "#3fb950"},
    "BUSLOANS": {"name": "C&I loans (monthly)", "unit": "$B", "color": "#58a6ff"},
}


def fetch_fred(series: str) -> pd.Series:
    r = requests.get(FRED_CSV.format(series=series), timeout=30)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text), parse_dates=["observation_date"], na_values=".")
    df = df.rename(columns={"observation_date": "date", series: "value"})
    df = df.dropna().set_index("date").sort_index()
    return df["value"].astype(float)


def styled_ax(ax: plt.Axes) -> None:
    ax.set_facecolor(BG)
    ax.tick_params(colors=FG, which="both")
    for spine in ax.spines.values():
        spine.set_color(GRID)
    ax.grid(True, color=GRID, linestyle="-", linewidth=0.5, alpha=0.7)
    ax.xaxis.set_major_locator(mdates.YearLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))


def plot_h8(data: dict[str, pd.Series]) -> Path:
    fig, axes = plt.subplots(2, 1, figsize=(12, 7), sharex=True)
    fig.patch.set_facecolor(BG)

    for ax, (code, meta) in zip(axes, SERIES.items()):
        s = data[code]
        s = s[s.index >= pd.Timestamp(START)].dropna()
        ax.plot(s.index, s.values / 1000, color=meta["color"], linewidth=1.8)
        ax.fill_between(s.index, s.values / 1000, color=meta["color"], alpha=0.12)
        styled_ax(ax)
        ax.set_title(f"{meta['name']}  ({code})  —  $T", color=FG, fontsize=11, loc="left", pad=8)
        if not s.empty:
            ax.text(
                0.99, 0.9,
                f"latest: ${s.iloc[-1]/1000:.2f}T  ·  {s.index[-1].strftime('%Y-%m-%d')}",
                transform=ax.transAxes, color=FG, fontsize=9, ha="right", alpha=0.9,
            )

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    fig.text(
        0.99, 0.005,
        f"A-layer bank credit transmission  ·  Updated {stamp}  ·  FRED H.8  ·  not a trading signal",
        ha="right", va="bottom", color=FG, fontsize=8, alpha=0.7,
    )
    fig.suptitle(
        "H.8 bank credit — is the water reaching the pipes?",
        color=FG, fontsize=13, fontweight="bold", y=0.995,
    )
    plt.tight_layout(rect=[0, 0.02, 1, 0.98])
    out = OUT_DIR / "h8_panel.png"
    fig.savefig(out, dpi=160, facecolor=BG)
    plt.close(fig)
    return out


def _chg(s: pd.Series, n: int) -> float | None:
    """Change over the last n observations (weekly series → n=4 ≈ 1 month)."""
    s = s.dropna()
    if len(s) <= n:
        return None
    return round(float(s.iloc[-1] - s.iloc[-(n + 1)]), 2)


def write_status(data: dict[str, pd.Series]) -> dict:
    loans = data["TLAACBW027SBOG"].dropna()
    ci = data["BUSLOANS"].dropna()

    # weekly series: 4 obs ≈ 1 month, 13 obs ≈ 1 quarter
    chg_4w = _chg(loans, 4)
    chg_13w = _chg(loans, 13)

    flags = []
    if chg_4w is not None and chg_4w < 0:
        flags.append("h8_loans_contracting_4w")
    if chg_13w is not None and chg_13w < 0:
        flags.append("h8_loans_contracting_13w")

    payload = {
        "updated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "source": "FRED (Fed H.8 release)",
        "layer": "A-bank credit transmission",
        "frequency": "weekly (Wednesday release, ~1w lag); BUSLOANS monthly",
        "unit": "USD billions",
        "series": {
            "TLAACBW027SBOG": {
                "name": SERIES["TLAACBW027SBOG"]["name"],
                "as_of": loans.index[-1].strftime("%Y-%m-%d") if not loans.empty else None,
                "value_B": round(float(loans.iloc[-1]), 1) if not loans.empty else None,
                "value_T": round(float(loans.iloc[-1]) / 1000, 3) if not loans.empty else None,
                "chg_4w_B": chg_4w,
                "chg_13w_B": chg_13w,
            },
            "BUSLOANS": {
                "name": SERIES["BUSLOANS"]["name"],
                "as_of": ci.index[-1].strftime("%Y-%m-%d") if not ci.empty else None,
                "value_B": round(float(ci.iloc[-1]), 1) if not ci.empty else None,
                "chg_3m_B": _chg(ci, 3),
            },
        },
        "heuristic_flags": flags,
        "read_hint": (
            "Loan growth = banks passing liquidity through (last-mile transmission). "
            "Contraction while net liquidity is flat/up = A-layer overstates easiness."
        ),
        "disclaimer": "H.8 is a lagging weekly aggregate. Not a trading signal.",
    }

    path = ROOT / "latest_h8.json"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    print("Fetching H.8 bank credit series...")
    data: dict[str, pd.Series] = {}
    for code in SERIES:
        s = fetch_fred(code)
        data[code] = s
        print(f"  {code:16s} {len(s):>5} pts  latest {s.index[-1].date()}  ${s.iloc[-1]/1000:.2f}T")

    print("Rendering h8_panel.png...")
    out = plot_h8(data)
    print(f"  wrote {out}")

    payload = write_status(data)
    print("Wrote latest_h8.json")
    print("Heuristic flags:", payload.get("heuristic_flags") or "(none)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
