#!/usr/bin/env python3
"""Regime classification — pure compute over existing latest*.json files.

No data fetching. Reads the snapshots written by update*.py and applies
heuristic thresholds to label the current macro-liquidity regime.

The point: the same net-liquidity decline means different things in
different regimes (QT-as-planned vs debt-ceiling distortion vs stress).
These labels give the raw numbers a story frame. They are heuristics,
not signals.

Usage:
  python regime.py            # print labels from files on disk
  from regime import classify # server.py injects into /api/snapshot
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent

# Thresholds (kept in one place for transparency; exported in output)
THRESHOLDS = {
    "walcl_30d_B_qt": -15.0,      # WALCL 30d change below → QT active
    "walcl_30d_B_qe": 15.0,       # above → QE active
    "rrp_near_zero_T": 0.05,      # RRP below $50B → parking lot empty
    "sofr_stress_bp": 15.0,       # SOFR-IORB at/above → funding stress
    "sofr_easy_bp": 0.0,          # at/below → funding easy
    "iorb_60d_bp_hike": 10.0,     # IORB 60d change above → hiking
    "iorb_60d_bp_cut": -10.0,     # below → cutting
    "hy_oas_tight_pct": 4.5,      # HY OAS at/above → credit tightening
    "vix_elevated": 25.0,         # VIX at/above → vol elevated
    "tga_drawdown_B": -100.0,     # TGA 30d change below → debt-ceiling watch
    "h8_contraction_B": 0.0,      # H.8 loans 4w change below → banks tightening
}


def _load(name: str) -> dict:
    p = ROOT / name
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _num(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def classify(
    a: Optional[dict] = None,
    bc: Optional[dict] = None,
    h8: Optional[dict] = None,
) -> dict:
    """Return regime states + flags. Args default to reading files from disk."""
    a = a if isinstance(a, dict) else _load("latest.json")
    bc = bc if isinstance(bc, dict) else _load("latest_bc.json")
    h8 = h8 if isinstance(h8, dict) else _load("latest_h8.json")

    t = THRESHOLDS
    series = bc.get("series") or {}

    # --- inputs ---
    walcl_30d = _num(a.get("walcl_chg_30d_B"))
    tga_30d = _num(a.get("tga_chg_30d_B"))
    iorb_60d = _num(a.get("iorb_chg_60d_bp"))
    rrp_T = _num(a.get("rrp_T"))
    sofr_iorb = _num(a.get("sofr_minus_iorb_bp"))
    hy = _num((series.get("BAMLH0A0HYM2") or {}).get("value"))
    vix = _num((series.get("VIXCLS") or {}).get("value"))
    h8_series = (h8.get("series") or {}).get("TLAACBW027SBOG") or {}
    h8_4w = _num(h8_series.get("chg_4w_B"))

    # --- mutually exclusive states ---
    if walcl_30d is None:
        balance_sheet = "unknown"
    elif walcl_30d < t["walcl_30d_B_qt"]:
        balance_sheet = "qt_active"
    elif walcl_30d > t["walcl_30d_B_qe"]:
        balance_sheet = "qe_active"
    else:
        balance_sheet = "stable"

    if iorb_60d is None:
        rates = "unknown"
    elif iorb_60d > t["iorb_60d_bp_hike"]:
        rates = "hiking"
    elif iorb_60d < t["iorb_60d_bp_cut"]:
        rates = "cutting"
    else:
        rates = "on_hold"

    if sofr_iorb is None:
        funding = "unknown"
    elif sofr_iorb >= t["sofr_stress_bp"]:
        funding = "stress"
    elif sofr_iorb <= t["sofr_easy_bp"]:
        funding = "easy"
    else:
        funding = "neutral"

    # --- boolean flags ---
    flags: list[str] = []
    if rrp_T is not None and rrp_T < t["rrp_near_zero_T"]:
        flags.append("rrp_near_zero")
    if hy is not None and hy >= t["hy_oas_tight_pct"]:
        flags.append("credit_tightening")
    if vix is not None and vix >= t["vix_elevated"]:
        flags.append("vol_elevated")
    if h8_4w is not None and h8_4w < t["h8_contraction_B"]:
        flags.append("h8_loans_contracting")
    # Debt-ceiling watch: big TGA drawdown without matching balance-sheet move.
    # Heuristic — a fast TGA drain during QT/stable WALCL smells like ceiling
    # dynamics, not ordinary spending. Manual confirmation still required.
    if (
        tga_30d is not None
        and tga_30d < t["tga_drawdown_B"]
        and balance_sheet in ("qt_active", "stable")
    ):
        flags.append("debt_ceiling_watch")

    return {
        "states": {
            "balance_sheet": balance_sheet,
            "rates": rates,
            "funding": funding,
        },
        "flags": flags,
        "inputs": {
            "walcl_chg_30d_B": walcl_30d,
            "tga_chg_30d_B": tga_30d,
            "iorb_chg_60d_bp": iorb_60d,
            "rrp_T": rrp_T,
            "sofr_minus_iorb_bp": sofr_iorb,
            "hy_oas_pct": hy,
            "vix": vix,
            "h8_loans_chg_4w_B": h8_4w,
        },
        "thresholds": t,
        "disclaimer": (
            "Heuristic regime labels for context only. Same data, different regime, "
            "different meaning — labels frame the story, they are not trade signals."
        ),
    }


def main() -> int:
    out = classify()
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
