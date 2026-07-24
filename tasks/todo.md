# Net Liquidity Dashboard — 核心缺口补全

计划文件: ~/.claude/plans/dazzling-wibbling-bubble.md (2026-07-18)

## Checklist

- [x] Phase 1: `jobs.json` + `run_jobs.py` 配置驱动重构
- [x] Phase 2: `update_h8.py` — H.8 商业银行信贷（A 层传导）
- [x] Phase 3: `regime.py` 制度标记 + server/前端集成
- [x] Phase 4: `update_bc.py` 扩展 — 离岸美元融资压力
- [x] Phase 5: `update_buybacks.py` — 企业回购代理
- [x] Phase 6: `update_breadth.py` — 市场宽度
- [x] Phase 7: server 集成 + 端到端验证

## Review (2026-07-18)

### 实施结果

**新增数据模块（4 个脚本，全部验证通过）**
- `update_h8.py`: TLAACBW027SBOG ($25.62T) + BUSLOANS，4w/13w 变化，收缩标记
- `update_buybacks.py`: PKW/SPY 比值（日频）+ S&P 季度回购 CSV（13 季种子数据，需手动追加）
- `update_breadth.py`: 11 个 SPDR 板块 ETF 参与度 + SPY 均线位置 + 大小盘风格
- `update_bc.py` 扩展: 金融/非金融 CP−国库券利差 + 外国官方逆回购池（WLRRAFOIAL）

**制度标记层**
- `regime.py`: 3 组互斥状态（balance_sheet/rates/funding）+ 5 个布尔标记
- 阈值集中在 `THRESHOLDS` 字典，输出含 inputs/thresholds 便于审计
- 注入 `/api/snapshot`，新增 `/api/regime` 端点，前端 tag 行展示

**架构**
- `run_jobs.py` 改为 `jobs.json` 配置驱动，加脚本不再改代码
- `update.py` 增加 regime 输入字段（walcl_chg_30d_B / tga_chg_30d_B / iorb_chg_60d_bp）

### 与计划的偏差（数据源勘误）

| 计划 | 实际 | 原因 |
|------|------|------|
| TEDRATE | DCPF3M−DTB3 | TEDRATE 2022-01 已停更（LIBOR 废止）；金融 CP 利差是现代继任者 |
| CPN3M | DCPN3M | CPN3M 月频，DCPN3M 日频 |
| RRPONFIMAD | WLRRAFOIAL | 原系列 ID 不存在；WLRRAFOIAL 为正确的外国官方逆回购池 |

### 验证记录

- 6 个日频脚本 `run_jobs.py daily` 全部通过
- `/api/health` `/api/snapshot` `/api/regime` `/api/h8` `/api/buybacks` `/api/breadth` 均 200
- 前端浏览器验证：regime 标记行、主表 4 个新行、8 张图表全部渲染
- 当日快照: 净流动性 $5.987T (30d +0.153T)、regime = qe_active/on_hold/easy、
  flags = rrp_near_zero + buyback_proxy_weak、金融 CP 利差 22bp (20d +15bp 走阔，值得关注)

### 遗留事项

- `data/sp500_buybacks_quarterly.csv` 需每季手动追加（S&P DJI 新闻稿，季末后 2-3 周）
- 2025-Q2 之后的回购数据待补
- debt_ceiling_watch 是启发式（TGA 30d 降幅 >$100B），确认仍需人工
- 真实 XCCY basis、期权 gamma、CTA 仓位仍无免费源（已在 excluded 列表声明）
