#!/usr/bin/env python3
"""Holdings liquidity snapshot via Longbridge CLI (C-layer / micro proxies).

For each equity position:
  - quote (last / extended)
  - depth (L2 ladder → spread & top depth when market open)
  - capital distribution + optional flow tail
  - option call/put volume → put/call ratio

Usage:
  python holdings_liquidity_snapshot.py
  python holdings_liquidity_snapshot.py --symbols SNDK.US,AMAT.US
  python holdings_liquidity_snapshot.py --no-flow

Requires: longbridge on PATH and logged in (longbridge auth login).
Does NOT place orders. Query only.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
OUT_JSON = ROOT / "latest_holdings_liq.json"


def run_lb(args: list[str], timeout: int = 60) -> Any:
    cmd = ["longbridge", *args, "--format", "json"]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    text = (p.stdout or "") + ("\n" + p.stderr if p.returncode else "")
    raw = p.stdout or ""
    data = parse_json_loose(raw)
    if data is None and p.returncode != 0:
        raise RuntimeError(f"longbridge {' '.join(args)} failed: {p.stderr or raw[:300]}")
    if data is None:
        raise RuntimeError(f"cannot parse JSON from: longbridge {' '.join(args)}\n{raw[:400]}")
    return data


def parse_json_loose(raw: str) -> Any:
    """longbridge may append upgrade banners after JSON."""
    raw = raw.strip()
    if not raw:
        return None
    # Try whole string
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    # First {..} or [..]
    for pattern in (r"(\[[\s\S]*\])", r"(\{[\s\S]*\})"):
        m = re.search(pattern, raw)
        if not m:
            continue
        blob = m.group(1)
        # Trim trailing non-json after last ] or }
        for end in range(len(blob), 0, -1):
            try:
                return json.loads(blob[:end])
            except json.JSONDecodeError:
                continue
    return None


def fnum(x: Any) -> float | None:
    if x is None or x == "":
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def summarize_depth(depth: dict) -> dict:
    asks = [a for a in (depth.get("asks") or []) if fnum(a.get("price")) and fnum(a.get("volume"))]
    bids = [b for b in (depth.get("bids") or []) if fnum(b.get("price")) and fnum(b.get("volume"))]
    best_ask = fnum(asks[0]["price"]) if asks else None
    best_bid = fnum(bids[0]["price"]) if bids else None
    spread = None
    spread_bps = None
    mid = None
    if best_ask is not None and best_bid is not None and best_ask > 0:
        spread = best_ask - best_bid
        mid = (best_ask + best_bid) / 2
        if mid > 0:
            spread_bps = spread / mid * 10000
    top_n = 5
    ask_vol = sum(fnum(a.get("volume")) or 0 for a in asks[:top_n])
    bid_vol = sum(fnum(b.get("volume")) or 0 for b in bids[:top_n])
    return {
        "available": bool(asks or bids),
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread": round(spread, 4) if spread is not None else None,
        "spread_bps": round(spread_bps, 2) if spread_bps is not None else None,
        "bid_vol_top5": bid_vol,
        "ask_vol_top5": ask_vol,
        "imbalance_top5": round(bid_vol / ask_vol, 3) if ask_vol else None,
        "note": None if (asks or bids) else "empty ladder (market closed or no L2)",
    }


def summarize_capital(cap: dict) -> dict:
    cin = cap.get("capital_in") or {}
    cout = cap.get("capital_out") or {}

    def bucket(side: dict) -> dict:
        return {
            "large": fnum(side.get("large")),
            "medium": fnum(side.get("medium")),
            "small": fnum(side.get("small")),
            "total": sum(filter(None, [fnum(side.get(k)) for k in ("large", "medium", "small")])) or 0.0,
        }

    inn = bucket(cin)
    out = bucket(cout)
    net = {
        "large": (inn["large"] or 0) - (out["large"] or 0),
        "medium": (inn["medium"] or 0) - (out["medium"] or 0),
        "small": (inn["small"] or 0) - (out["small"] or 0),
        "total": inn["total"] - out["total"],
    }
    return {
        "timestamp": cap.get("timestamp"),
        "in": inn,
        "out": out,
        "net": {k: round(v, 2) for k, v in net.items()},
    }


def summarize_flow(flow: list) -> dict:
    if not flow:
        return {"points": 0}
    vals = [fnum(x.get("inflow")) for x in flow]
    vals = [v for v in vals if v is not None]
    if not vals:
        return {"points": 0}
    last_n = vals[-30:]
    return {
        "points": len(vals),
        "last_inflow": vals[-1],
        "sum_last_30": round(sum(last_n), 2),
        "sum_all": round(sum(vals), 2),
        "last_time": flow[-1].get("time"),
    }


def summarize_option_volume(ov: dict) -> dict:
    c = fnum(ov.get("c"))
    p = fnum(ov.get("p"))
    pc = round(p / c, 4) if c and c > 0 and p is not None else None
    return {
        "call_volume": c,
        "put_volume": p,
        "put_call_ratio": pc,
        "call_heavy": (pc is not None and pc < 0.7),
        "put_heavy": (pc is not None and pc > 1.2),
    }


def pick_price(q: dict) -> dict:
    post = q.get("post_market_quote") or {}
    pre = q.get("pre_market_quote") or {}
    last = fnum(q.get("last"))
    session = "regular"
    ext_last = None
    if fnum(post.get("last")) is not None:
        ext_last = fnum(post.get("last"))
        session = "post"
    elif fnum(pre.get("last")) is not None:
        ext_last = fnum(pre.get("last"))
        session = "pre"
    return {
        "last_regular": last,
        "extended_last": ext_last,
        "session_hint": session,
        "volume": fnum(q.get("volume")),
        "turnover": fnum(q.get("turnover")),
        "change_pct_regular": fnum(q.get("change_percentage")),
    }


def load_positions(symbols: list[str] | None) -> list[dict]:
    if symbols:
        return [{"symbol": s, "quantity": None, "name": ""} for s in symbols]
    pos = run_lb(["positions"])
    if not isinstance(pos, list):
        raise RuntimeError(f"unexpected positions shape: {type(pos)}")
    # equity only
    out = []
    for p in pos:
        sym = p.get("symbol") or ""
        if not sym:
            continue
        out.append(
            {
                "symbol": sym,
                "name": p.get("name") or "",
                "quantity": fnum(p.get("quantity")),
                "currency": p.get("currency"),
                "market": p.get("market"),
            }
        )
    return out


def snapshot_symbol(sym: str, include_flow: bool) -> dict:
    row: dict[str, Any] = {"symbol": sym, "errors": []}

    try:
        qlist = run_lb(["quote", sym])
        q = qlist[0] if isinstance(qlist, list) else qlist
        row["quote"] = pick_price(q if isinstance(q, dict) else {})
    except Exception as e:
        row["errors"].append(f"quote: {e}")
        row["quote"] = {}

    try:
        depth = run_lb(["depth", sym])
        row["depth"] = summarize_depth(depth if isinstance(depth, dict) else {})
    except Exception as e:
        row["errors"].append(f"depth: {e}")
        row["depth"] = {"available": False, "note": str(e)}

    try:
        cap = run_lb(["capital", sym])
        row["capital"] = summarize_capital(cap if isinstance(cap, dict) else {})
    except Exception as e:
        row["errors"].append(f"capital: {e}")
        row["capital"] = {}

    if include_flow:
        try:
            flow = run_lb(["capital", sym, "--flow"])
            row["capital_flow"] = summarize_flow(flow if isinstance(flow, list) else [])
        except Exception as e:
            row["errors"].append(f"capital_flow: {e}")
            row["capital_flow"] = {}

    # Options: US only
    if sym.endswith(".US"):
        try:
            ov = run_lb(["option", "volume", sym])
            row["option_volume"] = summarize_option_volume(ov if isinstance(ov, dict) else {})
        except Exception as e:
            row["errors"].append(f"option: {e}")
            row["option_volume"] = {}
    else:
        row["option_volume"] = {"note": "option volume US-only in this script"}

    return row


def flags_for(row: dict) -> list[str]:
    flags = []
    d = row.get("depth") or {}
    if d.get("spread_bps") is not None and d["spread_bps"] >= 20:
        flags.append("wide_spread_ge_20bps")
    cap = row.get("capital") or {}
    net = (cap.get("net") or {}).get("total")
    if net is not None and net < 0:
        flags.append("capital_net_out")
    if net is not None and net > 0:
        flags.append("capital_net_in")
    ov = row.get("option_volume") or {}
    if ov.get("put_heavy"):
        flags.append("put_heavy_pc_gt_1.2")
    if ov.get("call_heavy"):
        flags.append("call_heavy_pc_lt_0.7")
    if d.get("available") is False:
        flags.append("no_l2_depth")
    return flags


def print_table(payload: dict) -> None:
    print(f"Holdings liquidity snapshot  ·  {payload['updated_utc']}")
    print(f"Positions: {len(payload['holdings'])}")
    print("-" * 100)
    hdr = f"{'Symbol':12} {'Last':>10} {'Sprd bp':>8} {'CapNet':>12} {'P/C':>6}  Flags"
    print(hdr)
    print("-" * 100)
    for h in payload["holdings"]:
        sym = h["symbol"]
        q = h.get("quote") or {}
        last = q.get("extended_last") or q.get("last_regular")
        last_s = f"{last:.2f}" if last is not None else "-"
        d = h.get("depth") or {}
        sp = d.get("spread_bps")
        sp_s = f"{sp:.1f}" if sp is not None else ("n/a" if not d.get("available") else "-")
        net = ((h.get("capital") or {}).get("net") or {}).get("total")
        net_s = f"{net:,.0f}" if net is not None else "-"
        pc = (h.get("option_volume") or {}).get("put_call_ratio")
        pc_s = f"{pc:.2f}" if pc is not None else "-"
        fl = ",".join(h.get("flags") or []) or "-"
        print(f"{sym:12} {last_s:>10} {sp_s:>8} {net_s:>12} {pc_s:>6}  {fl}")
    print("-" * 100)
    print("Notes: CapNet = large+med+small capital in − out (platform snapshot).")
    print("       depth empty after hours is normal. Not a trading signal.")
    print(f"JSON: {OUT_JSON}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Longbridge holdings liquidity snapshot")
    ap.add_argument("--symbols", type=str, default="", help="Comma list e.g. SNDK.US,AMAT.US (default: all positions)")
    ap.add_argument("--no-flow", action="store_true", help="Skip capital --flow (faster)")
    ap.add_argument("--out", type=str, default=str(OUT_JSON), help="Output JSON path")
    args = ap.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()] or None
    include_flow = not args.no_flow

    try:
        positions = load_positions(symbols)
    except Exception as e:
        print(f"ERROR loading positions: {e}", file=sys.stderr)
        return 1

    if not positions:
        print("No positions found.", file=sys.stderr)
        return 1

    holdings = []
    for p in positions:
        sym = p["symbol"]
        print(f"* {sym} ...", file=sys.stderr)
        row = snapshot_symbol(sym, include_flow=include_flow)
        row["quantity"] = p.get("quantity")
        row["name"] = p.get("name")
        row["flags"] = flags_for(row)
        holdings.append(row)

    payload = {
        "updated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "source": "longbridge CLI",
        "layer": "C-micro / B-flow proxy (holdings only)",
        "include_flow": include_flow,
        "holdings": holdings,
        "disclaimer": (
            "Platform capital flow is not official exchange tape. "
            "L2 may be empty when market closed. "
            "Option volume is same-day snapshot for US names. "
            "Not a trading signal."
        ),
    }

    out = Path(args.out)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print_table(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
