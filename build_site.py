#!/usr/bin/env python3
"""Static site builder for the GitHub Pages dashboard (美元水位计).

Reads the latest*.json layers via server.build_snapshot(), strips private
and machine-local bits, accumulates data/brief_history.jsonl for sparklines,
and writes a deployable static site into site/:

  site/index.html       verbatim copy of web/index.html (dual-mode page)
  site/snapshot.json    sanitized build_snapshot() output
  site/api/brief.json   schema v1 — the contract consumed by us-liquidity-monitor
  site/report.txt       server.build_report() text
  site/charts/*.png     CHART_FILES allowlist

Usage: python build_site.py
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from server import build_report, build_snapshot  # verified import-safe
import voice

ROOT = Path(__file__).resolve().parent
SITE = ROOT / "site"
HISTORY_PATH = ROOT / "data" / "brief_history.jsonl"

# Explicit allowlist — orphan cds_*.png charts (not CI-maintained) stay out.
CHART_FILES = [
    "net_liquidity.png",
    "components.png",
    "funding_sofr_iorb.png",
    "bc_panel.png",
    "funding_stress.png",
    "h8_panel.png",
    "buybacks.png",
    "breadth.png",
]


# ── Sanitize (privacy / local-path strip) ────────────────────────────────

def sanitize(obj: Any, root: Path) -> Any:
    """Recursively replace any string containing '/Users/' or the repo root
    with its basename. Value-based: catches unknown future keys and stale
    foreign-root paths like /Users/decolo/net-liquidity-dashboard/..."""
    if isinstance(obj, dict):
        return {k: sanitize(v, root) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize(v, root) for v in obj]
    if isinstance(obj, str) and ("/Users/" in obj or str(root) in obj):
        return obj.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    return obj


def site_snapshot() -> dict:
    """build_snapshot() minus the holdings layer and the local project path,
    with all remaining values sanitized."""
    snap = build_snapshot()
    snap.pop("project", None)
    layers = snap.get("layers")
    if isinstance(layers, dict):
        layers.pop("holdings", None)
    return sanitize(snap, ROOT)


# ── History (brief sparkline accumulation) ───────────────────────────────

def load_history(path: Optional[Path] = None) -> list:
    """JSONL read; [] on any read/parse failure. Skips corrupt lines."""
    path = path or HISTORY_PATH
    try:
        recs = []
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(rec, dict):
                    recs.append(rec)
        return recs
    except OSError:
        return []


def upsert_history(recs: list, record: dict) -> list:
    """Append record, replacing a same-date tail (idempotent re-runs)."""
    if recs and recs[-1].get("date") == record.get("date"):
        recs[-1] = record
    else:
        recs.append(record)
    return recs


def append_history(record: dict, path: Optional[Path] = None,
                   max_lines: int = 400, keep: int = 250) -> None:
    """Upsert one record into the JSONL archive. Trims to `keep` lines once
    past `max_lines`. Best-effort: history must never break the build."""
    path = path or HISTORY_PATH
    try:
        recs = upsert_history(load_history(path), record)
        if len(recs) > max_lines:
            recs = recs[-keep:]
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            for r in recs:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    except OSError:
        pass


def history_record(snap: dict, rating: str) -> dict:
    """One dated record for the sparkline archive."""
    s = snap.get("summary") or {}
    now = datetime.now(timezone.utc)
    a = (snap.get("layers") or {}).get("a") or {}
    return {
        "date": a.get("as_of") or now.strftime("%Y-%m-%d"),
        "ts_utc": now.strftime("%Y-%m-%d %H:%M UTC"),
        "net_liquidity_T": s.get("net_liquidity_T"),
        "delta_30d_T": s.get("delta_30d_T"),
        "vix": s.get("vix"),
        "hy_oas": s.get("hy_oas"),
        "ig_oas": s.get("ig_oas"),
        "sofr_iorb_bp": s.get("sofr_minus_iorb_bp"),
        "rating": rating,
    }


def spark(history: list, key: str, n: int = 7) -> list:
    """Last n non-null numeric values for key, oldest-first. Length 0..n."""
    vals = [r.get(key) for r in history
            if isinstance(r.get(key), (int, float)) and not isinstance(r.get(key), bool)]
    return vals[-n:]


# ── brief.json (schema v1 — cross-repo contract) ─────────────────────────

def build_brief(snap: dict, history: list) -> dict:
    """The machine-readable 白话 brief consumed by the Pages hero and by
    us-liquidity-monitor's --mode part1. Deterministic: no LLM involved."""
    summary = snap.get("summary") or {}
    regime = snap.get("regime") or {}
    states = regime.get("states") or {}
    flags = regime.get("flags") or []
    rating = voice.rate(states, flags)
    a = (snap.get("layers") or {}).get("a") or {}
    return {
        "schema_version": 1,
        "as_of": a.get("as_of"),
        "generated_utc": snap.get("generated_utc"),
        "rating": rating,
        "rating_drivers": voice.rating_drivers(regime, summary),
        "headline": voice.headline(rating, summary),
        "history_days": len(history),
        "net_liquidity": {
            "value_T": summary.get("net_liquidity_T"),
            "delta_30d_T": summary.get("delta_30d_T"),
            "spark_7d": spark(history, "net_liquidity_T"),
        },
        "regime": {
            "states": states,
            "flags": flags,
            "labels_zh": voice.regime_labels_zh(states, flags),
        },
        "vitals": {
            "vix": summary.get("vix"),
            "hy_oas_pct": summary.get("hy_oas"),
            "ig_oas_pct": summary.get("ig_oas"),
            "sofr_iorb_bp": summary.get("sofr_minus_iorb_bp"),
            "t10y2y": summary.get("t10y2y"),
            "breadth_pct_above_50d": summary.get("breadth_pct_above_50d"),
            "lev_etf_aum_B": summary.get("lev_etf_total_aum_B"),
            "cpi_core_yoy_pct": summary.get("cpi_core_yoy_pct"),
            "t10yie_pct": summary.get("t10yie_pct"),
            "usdkrw": summary.get("usdkrw"),
            "payrolls_mom_chg_k": summary.get("payrolls_mom_chg_k"),
            "unrate": summary.get("unrate"),
        },
    }


# ── Site writer ──────────────────────────────────────────────────────────

def write_site(site_dir: Path = SITE) -> dict:
    """Assemble the full static site. Returns the brief for inspection."""
    snap = site_snapshot()
    rating = voice.rate((snap.get("regime") or {}).get("states") or {},
                        (snap.get("regime") or {}).get("flags") or [])
    record = history_record(snap, rating)
    # Same-day rebuilds upsert (not duplicate) today's point, so the brief's
    # sparkline and history_days count distinct dates only.
    history = upsert_history(load_history(), record)
    brief = build_brief(snap, history)
    append_history(record)

    site_dir.mkdir(parents=True, exist_ok=True)
    (site_dir / "api").mkdir(exist_ok=True)
    (site_dir / "charts").mkdir(exist_ok=True)

    shutil.copy2(ROOT / "web" / "index.html", site_dir / "index.html")
    (site_dir / "snapshot.json").write_text(
        json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")
    (site_dir / "api" / "brief.json").write_text(
        json.dumps(brief, ensure_ascii=False, indent=1), encoding="utf-8")
    (site_dir / "report.txt").write_text(build_report(), encoding="utf-8")
    for name in CHART_FILES:
        src = ROOT / "charts" / name
        if src.exists():
            shutil.copy2(src, site_dir / "charts" / name)
    return brief


def main() -> int:
    brief = write_site()
    print(f"site/ written — rating {brief['rating']} · {brief['headline']}")
    print(f"history_days={brief['history_days']}  as_of={brief['as_of']}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
