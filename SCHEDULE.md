# Liquidity data jobs — schedule guide

Local project: `~/net-liquidity-dashboard`

All scripts are **pull-only** (no trading). Use different cron periods.

## One-shot setup

```bash
cd ~/net-liquidity-dashboard
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
# Longbridge holdings snapshot also needs: longbridge auth login
```

## Scripts

| Script | Layer | Cadence | Output |
|--------|-------|---------|--------|
| `update.py` | A Fed net liq + SOFR/IORB/WRESBAL | daily / weekly | `latest.json`, charts |
| `update_bc.py` | B/C HY VIX USDJPY dollar curve + funding stress (TED/CP/FIMA) | daily | `latest_bc.json`, charts |
| `update_h8.py` | A bank credit transmission (H.8 loans) | daily (weekly release) | `latest_h8.json`, chart |
| `update_buybacks.py` | B corporate buyback proxy (PKW/SPY + S&P quarterly) | daily | `latest_buybacks.json`, chart |
| `update_breadth.py` | C market breadth (sector ETFs, MAs) | daily | `latest_breadth.json`, chart |
| `holdings_liquidity_snapshot.py` | C holdings micro (Longbridge) | session / on demand | `latest_holdings_liq.json` |
| `update_finra_margin.py` | B market margin debt | monthly | `latest_finra_margin.json` |
| `update_ici_flows.py` | B fund/ETF flows | weekly | `latest_ici_flows.json` |
| `update_bond_issuance.py` | B tech debt SEC forms | daily / 2–3d | `latest_bond_issuance.json` |
| `run_jobs.py` | bucket runner (config: `jobs.json`) | — | runs above |
| `regime.py` | regime flags (pure compute, no fetch) | on snapshot | injected into `/api/snapshot` |

Buckets are defined in `jobs.json` — add a script there, no code change needed.

## Suggested cron (macOS / Linux)

Activate venv inside each line or use full path to venv python.

```cron
# Daily ~08:30 local — A + B/C FRED + SEC debt poll
30 8 * * 1-5  cd /Users/decolo/net-liquidity-dashboard && .venv/bin/python run_jobs.py daily >> logs/daily.log 2>&1

# Weekly Fri 18:00 — ICI flows (release often mid/late week)
0 18 * * 5  cd /Users/decolo/net-liquidity-dashboard && .venv/bin/python run_jobs.py weekly >> logs/weekly.log 2>&1

# Monthly 25th 10:00 — FINRA margin (publishes ~3rd week after month-end; 25th is safe recheck)
0 10 25 * *  cd /Users/decolo/net-liquidity-dashboard && .venv/bin/python run_jobs.py monthly >> logs/monthly.log 2>&1

# Optional: US cash open check holdings depth (Mon-Fri 22:00 CST ≈ market hours vary)
0 22 * * 1-5  cd /Users/decolo/net-liquidity-dashboard && .venv/bin/python run_jobs.py session >> logs/session.log 2>&1
```

Create log dir once: `mkdir -p ~/net-liquidity-dashboard/logs`

## Manual

```bash
source .venv/bin/activate
python run_jobs.py daily
python run_jobs.py weekly
python run_jobs.py monthly
python run_jobs.py session
python run_jobs.py all
```

## Web desk (local)

```bash
cd ~/net-liquidity-dashboard && source .venv/bin/activate
python server.py
# open http://127.0.0.1:8765/
# agent: curl -s http://127.0.0.1:8765/api/snapshot | jq .
# no server: python snapshot_report.py
```

## Not daily observation

FINRA / ICI / bond SEC feed are **enhancements**:
- FINRA = monthly leverage stock
- ICI = weekly fund pipe
- Bond = event proxy for CSP capital markets docs

Core daily still: `update.py` + `update_bc.py`.

---

## CI 接管说明（2026-07-24）

GitHub Actions（update.yml）现在负责：
- **daily** bucket：每天 22:00 UTC
- **weekly** bucket：每周一 22:00 UTC（date 派生，非 cron 字符串匹配）
- **monthly** bucket：每月 1 日 22:00 UTC
- 构建 `site/` → commit 数据回仓库 → 部署 GitHub Pages

**session** bucket（holdings_liquidity_snapshot.py）留在本地手动跑——
需要 Longbridge CLI 登录态，输出文件被 .gitignore 排除，永不发布。

本地 cron 可以退役；保留 `python run_jobs.py <bucket>` 手动入口不变。
