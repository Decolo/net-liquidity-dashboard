#!/usr/bin/env python3
"""Lightweight local web server for liquidity snapshots.

  cd ~/net-liquidity-dashboard && source .venv/bin/activate
  python server.py                 # http://127.0.0.1:8765
  python server.py --port 8765
  python server.py --refresh daily # run job bucket then serve

Endpoints:
  GET /                 dashboard HTML
  GET /api/health
  GET /api/snapshot     merged A/B/C + slow layers (agent-friendly)
  GET /api/regime       heuristic regime labels (QT/QE, rates, funding)
  GET /api/a | /bc | /h8 | /buybacks | /breadth | /finra | /ici | /bond | /holdings
  GET /api/report       plain-text analysis skeleton
  POST /api/refresh?bucket=daily|weekly|monthly|session|all
  GET /charts/<file>    PNG charts
  GET /static/...       dashboard assets if any
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

import regime as regime_mod

ROOT = Path(__file__).resolve().parent
HOST = "127.0.0.1"
PORT = 8765

JSON_FILES = {
    "a": ROOT / "latest.json",
    "bc": ROOT / "latest_bc.json",
    "h8": ROOT / "latest_h8.json",
    "buybacks": ROOT / "latest_buybacks.json",
    "breadth": ROOT / "latest_breadth.json",
    "finra": ROOT / "latest_finra_margin.json",
    "ici": ROOT / "latest_ici_flows.json",
    "bond": ROOT / "latest_bond_issuance.json",
    "holdings": ROOT / "latest_holdings_liq.json",
}


def load_json(path: Path) -> Optional[Any]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        return {"error": str(e), "path": str(path)}


def build_snapshot() -> Dict[str, Any]:
    layers = {k: load_json(p) for k, p in JSON_FILES.items()}
    a = layers.get("a") or {}
    bc = layers.get("bc") or {}
    series = (bc.get("series") or {}) if isinstance(bc, dict) else {}

    def s(key: str, field: str = "value"):
        block = series.get(key) or {}
        return block.get(field)

    summary = {
        "net_liquidity_T": a.get("net_liquidity_T"),
        "delta_30d_T": a.get("delta_30d_T"),
        "wresbal_T": a.get("wresbal_T"),
        "rrp_T": a.get("rrp_T"),
        "sofr_minus_iorb_bp": a.get("sofr_minus_iorb_bp"),
        "hy_oas": s("BAMLH0A0HYM2"),
        "vix": s("VIXCLS"),
        "usdjpy": s("DEXJPUS"),
        "t10y2y": s("T10Y2Y"),
        "margin_debt_T": (layers.get("finra") or {}).get("margin_debt_t") if isinstance(layers.get("finra"), dict) else None,
        "h8_loans_T": (
            (((layers.get("h8") or {}).get("series") or {}).get("TLAACBW027SBOG") or {}).get("value_T")
            if isinstance(layers.get("h8"), dict)
            else None
        ),
        "h8_loans_chg_4w_B": (
            (((layers.get("h8") or {}).get("series") or {}).get("TLAACBW027SBOG") or {}).get("chg_4w_B")
            if isinstance(layers.get("h8"), dict)
            else None
        ),
        "ici_combined_equity_weekly_mn": (
            ((layers.get("ici") or {}).get("latest_week") or {}).get("combined_mf_and_etf") or {}
        ).get("equity_mn")
        if isinstance(layers.get("ici"), dict)
        else None,
        "bond_hits_180d": (layers.get("bond") or {}).get("count_in_window") if isinstance(layers.get("bond"), dict) else None,
        "buyback_ratio_chg_60d_pct": (
            ((layers.get("buybacks") or {}).get("daily_proxy") or {}).get("chg_60d_pct")
            if isinstance(layers.get("buybacks"), dict)
            else None
        ),
        "buyback_above_200d_ma": (
            ((layers.get("buybacks") or {}).get("daily_proxy") or {}).get("above_200d_ma")
            if isinstance(layers.get("buybacks"), dict)
            else None
        ),
        "breadth_pct_above_50d": (
            ((layers.get("breadth") or {}).get("sectors") or {}).get("pct_above_50d_ma")
            if isinstance(layers.get("breadth"), dict)
            else None
        ),
        "fin_cp_spread_bp": (
            (((layers.get("bc") or {}).get("funding") or {}).get("fin_cp_bill_bp") or {}).get("value_bp")
            if isinstance(layers.get("bc"), dict)
            else None
        ),
    }

    # crude regime labels for UI only
    flags = []
    try:
        if a.get("sofr_minus_iorb_bp") is not None and a["sofr_minus_iorb_bp"] >= 15:
            flags.append("A_price_stress_sofr_iorb")
        if a.get("sofr_minus_iorb_bp") is not None and a["sofr_minus_iorb_bp"] <= 0:
            flags.append("A_price_easy_sofr_below_iorb")
        if a.get("rrp_T") is not None and a["rrp_T"] < 0.05:
            flags.append("A_rrp_near_empty")
        if a.get("delta_30d_T") is not None and a["delta_30d_T"] > 0:
            flags.append("A_net_liq_rising_30d")
        elif a.get("delta_30d_T") is not None and a["delta_30d_T"] < 0:
            flags.append("A_net_liq_falling_30d")
        hy = s("BAMLH0A0HYM2")
        if hy is not None and hy >= 4.5:
            flags.append("B_hy_wide")
        vix = s("VIXCLS")
        if vix is not None and vix >= 25:
            flags.append("C_vix_elevated")
    except Exception:
        pass

    # Merge per-layer heuristic flags (each update_*.py already applies its thresholds)
    for key in ("h8", "buybacks", "breadth", "bc"):
        layer = layers.get(key)
        if isinstance(layer, dict):
            for f in layer.get("heuristic_flags") or []:
                if f not in flags:
                    flags.append(f)

    return {
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "project": str(ROOT),
        "summary": summary,
        "flags": flags,
        "regime": regime_mod.classify(
            a=layers.get("a") if isinstance(layers.get("a"), dict) else None,
            bc=layers.get("bc") if isinstance(layers.get("bc"), dict) else None,
            h8=layers.get("h8") if isinstance(layers.get("h8"), dict) else None,
        ),
        "layers": layers,
        "discipline": {
            "A": "Fed plumbing quantity + overnight price — not equity forecast",
            "B": "Risk appetite pipes (credit, carry, flows, margin, issuance proxy)",
            "C": "Mechanical vol / holdings microstructure",
            "never": [
                "CDS widen ≠ Fed empty",
                "tech bonds ≠ Fed easing",
                "RRP↓ ≠ auto risk-on",
                "DXY ≠ bank reserves",
                "Longbridge capital ≠ exchange tape",
            ],
        },
    }


def build_report() -> str:
    snap = build_snapshot()
    s = snap["summary"]
    lines = [
        f"US liquidity snapshot  {snap['generated_utc']}",
        "=" * 56,
        "",
        "[A] Dollar system",
        f"  Net Liq     {s.get('net_liquidity_T')} T   (30d Δ {s.get('delta_30d_T')} T)",
        f"  WRESBAL     {s.get('wresbal_T')} T",
        f"  RRP         {s.get('rrp_T')} T",
        f"  SOFR-IORB   {s.get('sofr_minus_iorb_bp')} bp",
        "",
        "[B/C] Daily FRED slice",
        f"  HY OAS      {s.get('hy_oas')}",
        f"  VIX         {s.get('vix')}",
        f"  USDJPY      {s.get('usdjpy')}",
        f"  10Y-2Y      {s.get('t10y2y')}",
        f"  FinCP-bill  {s.get('fin_cp_spread_bp')} bp",
        "",
        "[A] Bank credit (H.8)",
        f"  Loans total  {s.get('h8_loans_T')} T   (4w Δ {s.get('h8_loans_chg_4w_B')} B)",
        "",
        "[B] Equity demand proxies",
        f"  Buyback PKW/SPY 60d   {s.get('buyback_ratio_chg_60d_pct')} %   (above 200d MA: {s.get('buyback_above_200d_ma')})",
        f"  Breadth sectors>50dMA {s.get('breadth_pct_above_50d')} %",
        "",
        "[B slow]",
        f"  FINRA margin debt  {s.get('margin_debt_T')} T",
        f"  ICI comb. equity weekly (mn)  {s.get('ici_combined_equity_weekly_mn')}",
        f"  SEC debt-form hits (window)   {s.get('bond_hits_180d')}",
        "",
        f"Regime: {' · '.join(f'{k}={v}' for k, v in (snap.get('regime') or {}).get('states', {}).items()) or '(unknown)'}",
        f"Flags: {', '.join(snap['flags']) or '(none)'}",
        "",
        "Read order: quantity(A) → price(A SOFR-IORB) → credit(HY) → vol(VIX) → carry(USDJPY) → slow B.",
        "Do not treat as trade signal. Layers must not be collapsed.",
        "",
    ]
    a = snap["layers"].get("a") or {}
    if isinstance(a, dict):
        lines.append(f"A as_of / updated: {a.get('as_of')} / {a.get('updated_utc')}")
    h = snap["layers"].get("holdings")
    if isinstance(h, dict) and h.get("holdings"):
        lines.append("")
        lines.append("[C] Holdings micro (Longbridge)")
        for row in h["holdings"]:
            q = row.get("quote") or {}
            last = q.get("extended_last") or q.get("last_regular")
            pc = (row.get("option_volume") or {}).get("put_call_ratio")
            net = ((row.get("capital") or {}).get("net") or {}).get("total")
            lines.append(
                f"  {row.get('symbol')}: last={last}  capNet={net}  P/C={pc}  flags={','.join(row.get('flags') or [])}"
            )
    return "\n".join(lines) + "\n"


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _json(self, code: int, obj: Any) -> None:
        body = json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _text(self, code: int, text: str) -> None:
        body = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _html_file(self, path: Path) -> None:
        if not path.exists():
            self.send_error(404, "dashboard missing")
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        if path == "/":
            self._html_file(ROOT / "web" / "index.html")
            return
        if path == "/api/health":
            self._json(200, {"ok": True, "root": str(ROOT), "utc": datetime.now(timezone.utc).isoformat()})
            return
        if path == "/api/snapshot":
            self._json(200, build_snapshot())
            return
        if path == "/api/regime":
            self._json(200, regime_mod.classify())
            return
        if path == "/api/report":
            self._text(200, build_report())
            return
        if path.startswith("/api/"):
            key = path[len("/api/") :]
            if key in JSON_FILES:
                data = load_json(JSON_FILES[key])
                if data is None:
                    self._json(404, {"error": "missing", "file": str(JSON_FILES[key])})
                else:
                    self._json(200, data)
                return
            self._json(404, {"error": "unknown endpoint", "path": path})
            return

        # charts and other static from ROOT
        if path.startswith("/charts/") or path.startswith("/data/") or path.startswith("/web/"):
            return super().do_GET()

        self._json(404, {"error": "not found", "path": path})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.rstrip("/") != "/api/refresh":
            self._json(404, {"error": "not found"})
            return
        qs = parse_qs(parsed.query)
        bucket = (qs.get("bucket") or ["daily"])[0]
        if bucket not in ("daily", "weekly", "monthly", "session", "event", "all"):
            self._json(400, {"error": "bad bucket", "bucket": bucket})
            return

        def job():
            subprocess.run(
                [sys.executable, str(ROOT / "run_jobs.py"), bucket],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
            )

        # sync refresh so agent gets fresh data (may take ~1–3 min for daily)
        p = subprocess.run(
            [sys.executable, str(ROOT / "run_jobs.py"), bucket],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=600,
        )
        self._json(
            200 if p.returncode == 0 else 500,
            {
                "bucket": bucket,
                "exit_code": p.returncode,
                "stdout_tail": (p.stdout or "")[-2000:],
                "stderr_tail": (p.stderr or "")[-1000:],
                "snapshot": build_snapshot() if p.returncode == 0 else None,
            },
        )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=HOST)
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--refresh", choices=["daily", "weekly", "monthly", "session", "all"], default=None)
    args = ap.parse_args()

    if args.refresh:
        print(f"Pre-refresh bucket={args.refresh} ...")
        subprocess.run([sys.executable, str(ROOT / "run_jobs.py"), args.refresh], cwd=str(ROOT))

    # ensure dashboard exists
    dash = ROOT / "web" / "index.html"
    if not dash.exists():
        print(f"ERROR: missing {dash}", file=sys.stderr)
        return 1

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Liquidity dashboard  http://{args.host}:{args.port}/")
    print(f"  API snapshot       http://{args.host}:{args.port}/api/snapshot")
    print(f"  Text report        http://{args.host}:{args.port}/api/report")
    print(f"  Root               {ROOT}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    return 0


if __name__ == "__main__":
    sys.exit(main())
