"""Fetch free FRED series for equity-liquidity B/C layers (partial).

B (auto slice):
  - Credit: HY OAS (BAMLH0A0HYM2)
  - Carry proxy: USDJPY (DEXJPUS)
  - Dollar: broad index (DTWEXBGS)
  - Curve: 10Y-2Y (T10Y2Y)
  - Funding stress: CP−bill spreads (DCPF3M/DCPN3M vs DTB3),
    foreign official RRP pool (WLRRAFOIAL).
    Note: TEDRATE discontinued 2022-01 (LIBOR cessation) —
    financial CP − bill spread is the modern successor.

C (auto slice):
  - VIX (VIXCLS)

D (inflation slice):
  - Core CPI (CPILFESL, monthly; YoY in series.yoy_pct)
  - 10Y breakeven inflation (T10YIE, daily) — market inflation expectations

NOT included (need other sources / manual):
  options gamma, CTA positioning, order-book depth,
  true cross-currency basis (paid data only).
  Buybacks/breadth/H.8 now tracked by their own scripts.

Same pattern as update.py: FRED CSV, no API key.
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
    "BAMLH0A0HYM2": {"name": "HY OAS (ICE BofA)", "unit": "pct", "layer": "B-credit"},
    "BAMLC0A0CM": {"name": "IG OAS (ICE BofA)", "unit": "pct", "layer": "B-credit"},
    "VIXCLS": {"name": "VIX", "unit": "index", "layer": "C-vol"},
    "DEXJPUS": {"name": "USD/JPY", "unit": "fx", "layer": "B-carry"},
    "DTWEXBGS": {"name": "Broad USD (DTWEXBGS)", "unit": "index", "layer": "B-dollar"},
    "T10Y2Y": {"name": "10Y-2Y curve", "unit": "pct", "layer": "B-curve"},
    "T10YIE": {"name": "10Y breakeven inflation", "unit": "pct", "layer": "D-inflation"},
    "CPILFESL": {"name": "Core CPI (ex food & energy)", "unit": "index", "layer": "D-inflation"},
}

# Offshore/short-term dollar funding stress (B-funding slice).
# TEDRATE is discontinued (2022-01, LIBOR cessation) — financial CP−bill
# spread is its modern successor: same bank unsecured funding premium.
FUNDING = {
    "DCPF3M": {"name": "3M AA financial CP", "unit": "pct", "layer": "B-funding"},
    "DCPN3M": {"name": "3M AA nonfinancial CP", "unit": "pct", "layer": "B-funding"},
    "DTB3": {"name": "3M T-bill (secondary)", "unit": "pct", "layer": "B-funding"},
    "WLRRAFOIAL": {"name": "Foreign official reverse repo pool", "unit": "musd", "layer": "B-offshore"},
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


def plot_bc_panel(data: dict[str, pd.Series]) -> Path:
    order = ["BAMLH0A0HYM2", "VIXCLS", "DEXJPUS", "DTWEXBGS", "T10Y2Y"]
    colors = ["#f85149", "#a371f7", "#58a6ff", "#3fb950", "#f0b429"]
    fig, axes = plt.subplots(len(order), 1, figsize=(12, 11), sharex=True)
    fig.patch.set_facecolor(BG)

    for ax, code, color in zip(axes, order, colors):
        s = data[code]
        s = s[s.index >= pd.Timestamp(START)].dropna()
        meta = SERIES[code]
        ax.plot(s.index, s.values, color=color, linewidth=1.5)
        ax.fill_between(s.index, s.values, color=color, alpha=0.12)
        styled_ax(ax)
        ax.set_title(f"{meta['layer']}  ·  {meta['name']}  ({code})", color=FG, fontsize=10, loc="left", pad=6)
        if not s.empty:
            ax.text(
                0.99, 0.88,
                f"latest: {s.iloc[-1]:.2f}  ·  {s.index[-1].strftime('%Y-%m-%d')}",
                transform=ax.transAxes, color=FG, fontsize=9, ha="right", alpha=0.9,
            )

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    fig.text(
        0.99, 0.005,
        f"B/C auto slice  ·  Updated {stamp}  ·  FRED  ·  not a trading signal",
        ha="right", va="bottom", color=FG, fontsize=8, alpha=0.7,
    )
    fig.suptitle(
        "Equity liquidity B/C — free FRED slice (credit · vol · yen · dollar · curve)",
        color=FG, fontsize=13, fontweight="bold", y=0.995,
    )
    plt.tight_layout(rect=[0, 0.02, 1, 0.98])
    out = OUT_DIR / "bc_panel.png"
    fig.savefig(out, dpi=160, facecolor=BG)
    plt.close(fig)
    return out


def compute_cp_spreads(funding: dict[str, pd.Series]) -> dict[str, pd.Series]:
    """CP − 3M T-bill spreads in bp, aligned on common dates."""
    bill = funding["DTB3"].dropna()
    out: dict[str, pd.Series] = {}
    for code, label in (("DCPF3M", "fin_cp_bill_bp"), ("DCPN3M", "nonfin_cp_bill_bp")):
        cp = funding[code].dropna()
        both = cp.index.intersection(bill.index)
        if len(both) == 0:
            out[label] = pd.Series(dtype=float)
            continue
        out[label] = ((cp.loc[both] - bill.loc[both]) * 100.0).sort_index()
    return out


def plot_funding_stress(funding: dict[str, pd.Series], spreads: dict[str, pd.Series]) -> Path:
    """B-funding slice: CP−bill spreads (TED successor) + foreign official RRP."""
    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(12, 7), sharex=True, gridspec_kw={"height_ratios": [2, 1.4]}
    )
    fig.patch.set_facecolor(BG)

    for label, color, name in (
        ("fin_cp_bill_bp", "#f85149", "Financial CP − bill (TED successor)"),
        ("nonfin_cp_bill_bp", "#f0b429", "Nonfinancial CP − bill"),
    ):
        s = spreads.get(label)
        if s is None or s.empty:
            continue
        s = s[s.index >= pd.Timestamp(START)]
        ax1.plot(s.index, s.values, color=color, linewidth=1.4, label=name)
    styled_ax(ax1)
    ax1.axhline(0, color=GRID, linewidth=0.8)
    ax1.set_ylabel("spread (bp)", color=FG)
    ax1.set_title(
        "Short-term dollar funding stress  ·  CP − 3M bill spreads  (B-funding)",
        color=FG, fontsize=13, fontweight="bold", pad=12,
    )
    leg = ax1.legend(loc="upper right", facecolor=BG, edgecolor=GRID, labelcolor=FG)
    for txt in leg.get_texts():
        txt.set_color(FG)

    fro = funding["WLRRAFOIAL"].dropna() / 1000  # $M → $B
    fro = fro[fro.index >= pd.Timestamp(START)]
    ax2.plot(fro.index, fro.values, color="#58a6ff", linewidth=1.6)
    ax2.fill_between(fro.index, fro.values, color="#58a6ff", alpha=0.12)
    styled_ax(ax2)
    ax2.set_ylabel("$B", color=FG)
    ax2.set_title(
        "Foreign official reverse repo pool (WLRRAFOIAL) — foreign CB dollars parked at Fed",
        color=FG, fontsize=10, pad=6, loc="left",
    )
    if not fro.empty:
        ax2.text(
            0.99, 0.88,
            f"latest: ${fro.iloc[-1]:.0f}B  ·  {fro.index[-1].strftime('%Y-%m-%d')}",
            transform=ax2.transAxes, color=FG, fontsize=9, ha="right", alpha=0.9,
        )

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    fig.text(
        0.99, 0.01,
        f"Updated {stamp}  ·  FRED  ·  TEDRATE discontinued 2022 → CP−bill is the successor  ·  not a trading signal",
        ha="right", va="bottom", color=FG, fontsize=8, alpha=0.7,
    )
    plt.tight_layout()
    out = OUT_DIR / "funding_stress.png"
    fig.savefig(out, dpi=160, facecolor=BG)
    plt.close(fig)
    return out


def _chg(s: pd.Series, n: int) -> float | None:
    s = s.dropna()
    if len(s) <= n:
        return None
    return round(float(s.iloc[-1] - s.iloc[-(n + 1)]), 4)


def write_status(
    data: dict[str, pd.Series],
    funding: dict[str, pd.Series],
    spreads: dict[str, pd.Series],
) -> dict:
    payload = {
        "updated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "layer": "B/C auto slice (partial)",
        "coverage": {
            "included": list(SERIES.keys()) + list(FUNDING.keys()),
            "excluded": [
                "options_gamma",
                "cta_positioning",
                "order_book_depth",
                "xccy_basis_true",  # real cross-currency basis needs paid data
            ],
        },
        "series": {},
        "read_hints": {
            "BAMLH0A0HYM2": "HY spread wider = credit tighter (B leverage pipe)",
            "BAMLC0A0CM": "IG spread wider = investment-grade stress (corporate bond pipe)",
            "VIXCLS": "Higher vol = C-layer mechanical de-risk risk up",
            "DEXJPUS": "JPY strength (USDJPY down) = yen-carry stress proxy",
            "DTWEXBGS": "Broad dollar; NOT bank reserves (see A-layer)",
            "T10Y2Y": "Curve; context for risk appetite / growth pricing",
            "T10YIE": "10Y breakeven inflation = market inflation expectations; rising = long-end pressure",
            "CPILFESL": "Core CPI (monthly); use yoy_pct — the 20-50bps memory/software contribution shows up here",
            "fin_cp_bill_bp": "Financial CP − bill = TED successor; widening = bank funding premium up",
            "nonfin_cp_bill_bp": "Corporate short-term funding premium",
            "WLRRAFOIAL": "Foreign official RRP; jump = foreign CBs hoarding dollars at Fed",
        },
    }

    for code, s in data.items():
        s = s.dropna()
        if s.empty:
            continue
        block = {
            "name": SERIES[code]["name"],
            "layer": SERIES[code]["layer"],
            "as_of": s.index[-1].strftime("%Y-%m-%d"),
            "value": round(float(s.iloc[-1]), 4),
            "chg_5d": _chg(s, 5),
            "chg_20d": _chg(s, 20),
        }
        if code == "CPILFESL":
            # monthly frequency: chg_5d/20d are observation-based (~months);
            # the standard read is YoY % change
            block["frequency"] = "monthly"
            if len(s) >= 13:
                block["yoy_pct"] = round(
                    (float(s.iloc[-1]) / float(s.iloc[-13]) - 1) * 100, 2)
        payload["series"][code] = block

    # Funding block: CP−bill spreads + foreign official repo pool
    fund_block: dict = {}
    for label in ("fin_cp_bill_bp", "nonfin_cp_bill_bp"):
        s = spreads.get(label)
        if s is None or s.dropna().empty:
            continue
        s = s.dropna()
        fund_block[label] = {
            "as_of": s.index[-1].strftime("%Y-%m-%d"),
            "value_bp": round(float(s.iloc[-1]), 1),
            "chg_5d_bp": _chg(s, 5),
            "chg_20d_bp": _chg(s, 20),
        }
    fro = funding["WLRRAFOIAL"].dropna()
    if not fro.empty:
        fro_chg = _chg(fro, 4)
        fund_block["foreign_official_rrp"] = {
            "as_of": fro.index[-1].strftime("%Y-%m-%d"),
            "value_B": round(float(fro.iloc[-1]) / 1000, 1),
            "chg_4w_B": round(fro_chg / 1000, 1) if fro_chg is not None else None,
        }
    payload["funding"] = fund_block

    # Simple joint flags (heuristic only)
    hy = data["BAMLH0A0HYM2"].dropna()
    vix = data["VIXCLS"].dropna()
    usdjpy = data["DEXJPUS"].dropna()
    flags = []
    if not hy.empty and float(hy.iloc[-1]) >= 4.5:
        flags.append("HY_OAS_elevated_ge_4.5")
    if not vix.empty and float(vix.iloc[-1]) >= 25:
        flags.append("VIX_ge_25")
    if len(usdjpy) > 5:
        d5 = float(usdjpy.iloc[-1] - usdjpy.iloc[-6])
        if d5 <= -2.0:
            flags.append("USDJPY_down_ge_2_in_5d_yen_strength")
    fin_sp = spreads.get("fin_cp_bill_bp")
    if fin_sp is not None and not fin_sp.dropna().empty and float(fin_sp.dropna().iloc[-1]) >= 30:
        flags.append("fin_CP_spread_ge_30bp_funding_stress")
    payload["heuristic_flags"] = flags
    payload["disclaimer"] = (
        "Heuristic flags are teaching aids only. "
        "B buybacks/flows tracked separately (see latest_buybacks.json); "
        "true XCCY basis and C microstructure are NOT in this feed."
    )

    path = ROOT / "latest_bc.json"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    print("Fetching B/C FRED slice...")
    data: dict[str, pd.Series] = {}
    for code, meta in SERIES.items():
        s = fetch_fred(code)
        data[code] = s
        print(
            f"  {code:14s} {len(s):>5} pts  "
            f"latest {s.index[-1].date()}  {s.iloc[-1]:.4g}  ({meta['layer']})"
        )

    print("Fetching funding stress slice (CP, bills, foreign RRP)...")
    funding: dict[str, pd.Series] = {}
    for code, meta in FUNDING.items():
        s = fetch_fred(code)
        funding[code] = s
        print(
            f"  {code:14s} {len(s):>5} pts  "
            f"latest {s.index[-1].date()}  {s.iloc[-1]:.4g}  ({meta['layer']})"
        )
    spreads = compute_cp_spreads(funding)
    for label, s in spreads.items():
        s = s.dropna()
        if not s.empty:
            print(f"  {label:20s} latest {s.index[-1].date()}  {s.iloc[-1]:+.1f} bp")

    print("Rendering bc_panel.png...")
    out = plot_bc_panel(data)
    print(f"  wrote {out}")
    print("Rendering funding_stress.png...")
    out2 = plot_funding_stress(funding, spreads)
    print(f"  wrote {out2}")

    payload = write_status(data, funding, spreads)
    print("Wrote latest_bc.json")
    print("Heuristic flags:", payload.get("heuristic_flags") or "(none)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
