# Net Liquidity Dashboard · 美元水位计

> Daily-updated dashboard tracking US Net Liquidity (Fed balance sheet − TGA − RRP) and its components. Refreshed automatically via GitHub Actions.
>
> **在线白话看板（中文 plain-language, daily）: https://decolo.github.io/net-liquidity-dashboard/**

![Net Liquidity vs BTC](charts/net_liquidity.png)

Net Liquidity is the most-watched coincident proxy for the dollar liquidity that drives risk assets. The formula is simple; reading the regimes is not.

```
Net Liquidity  =  Fed Balance Sheet  −  Treasury General Account  −  Overnight Reverse Repo
                  (WALCL)               (WTREGEN)                    (RRPONTSYD)
```

- **WALCL** rising → QE / asset purchases adding base money.
- **WTREGEN** rising → Treasury issuance draining bank reserves (and vice versa as it spends).
- **RRPONTSYD** rising → cash parked at the Fed instead of chasing risk assets.

The top panel shows Net Liquidity in trillions against BTC/USD on a log scale. The bottom panel shows the 30-day change — green when liquidity is being added to the system, red when it's being withdrawn.

## Components

![Components](charts/components.png)

## Methodology

- Data is pulled from FRED's public CSV endpoints — no API key required.
- WALCL is reported in millions of dollars; WTREGEN and RRPONTSYD in billions. All series are normalized to trillions before plotting.
- Series are forward-filled across non-aligned release schedules (WALCL is weekly Thursday; WTREGEN/RRPONTSYD are business-daily).
- Charts re-render daily at **22:00 UTC** — chosen to land after the Thursday H.4.1 release at ~21:30 UTC.

## Why this matters

Asset prices respond to liquidity changes before they respond to fundamentals. Net Liquidity has historically led BTC by 0–8 weeks at major turns; it has the same relationship — weaker but present — with the SPX growth complex.

It is not a strategy. It is a regime gauge. Use it to:

1. **Frame the bias.** Adding liquidity → don't fight risk; draining → respect downside.
2. **Time TGA refills.** Quarterly refunding announcements move WTREGEN; the announcement-vs-actual gap is the trade.
3. **Watch the RRP drain.** Falling RRP releases cash into bills and back into the system — a positive flow even without WALCL moving.

## Running locally

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python update.py
```

Outputs land in `charts/` and `latest.json`.

## Leveraged ETF AUM monitor (`update_lev_etf.py`)

Daily snapshot of leveraged ETF AUM (semiconductor / broad / tech, bull + bear) —
a retail-speculative positioning proxy used to gauge momentum-unwind progress.

- **Primary data**: Yahoo `info.totalAssets` per ETF. Caveat: Yahoo refreshes this
  field with a **lag of days**, so the JSON carries a freshness ledger:
  - `data_as_of` — the date the current AUM value was first observed
  - `stale_days` — consecutive days the total has been unchanged
  - `heuristic_flags: aum_stale_Nd` — fires once `stale_days >= 2`
  Read AUM as of `data_as_of`, not `updated_utc` (which is only the fetch time).
- **History**: every run appends to `data/lev_etf_history.jsonl` (one row per
  date; same-day reruns replace that day's row) so the AUM trajectory survives
  without git archaeology. `semi_aum_B` is the semiconductor subset
  (SOXL/SOXS/USD/SSG) aggregate used in market reviews.
- Chart: `charts/lev_etf.png`.

## Static site & machine-readable brief

`python build_site.py` assembles a deployable static site into `site/`:

- `site/index.html` — the 白话 dashboard (same file as `web/index.html`, dual-mode)
- `site/snapshot.json` — merged multi-layer snapshot (sanitized: no local paths, no holdings)
- `site/api/brief.json` — **schema v1 contract**: rating 🟢🟡🔴, headline, rating drivers,
  regime labels (zh), vitals, 7-day sparkline series. Deterministic template output, no LLM.
  Consumed by the [us-liquidity-monitor](https://github.com/Decolo/us-liquidity-monitor)
  skill as its macro layer (`compute.py --mode part1`).
- `site/report.txt` — plain-text analysis skeleton

The GitHub Actions workflow runs the full daily bucket (see `jobs.json`), builds the site,
commits data back, and deploys `site/` to GitHub Pages. Weekly (Mondays) and monthly (1st)
buckets run automatically; `workflow_dispatch` accepts an optional extra bucket.
`data/brief_history.jsonl` accumulates one record per build for sparkline trends.

Local full server (live pulls + holdings layer, operator view): `python server.py`
→ http://127.0.0.1:8765. The `session` bucket (`holdings_liquidity_snapshot.py`)
requires the Longbridge CLI and runs locally only — its output is gitignored and never
published.

## Data sources

- [FRED — WALCL](https://fred.stlouisfed.org/series/WALCL) — Federal Reserve total assets.
- [FRED — WTREGEN](https://fred.stlouisfed.org/series/WTREGEN) — Treasury General Account.
- [FRED — RRPONTSYD](https://fred.stlouisfed.org/series/RRPONTSYD) — Overnight reverse repurchase agreements.
- [Yahoo Finance — BTC-USD](https://finance.yahoo.com/quote/BTC-USD) — Reference price for the overlay.

## Related

- [awesome-macro-liquidity](https://github.com/ruleaker/awesome-macro-liquidity) — Curated resource list for tracking macro liquidity.
- [awesome-derivatives-data](https://github.com/ruleaker/awesome-derivatives-data) — Derivatives-side data that the liquidity flows feed into.

## License

[MIT](LICENSE)
