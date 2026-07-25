#!/usr/bin/env python3
"""白话 voice layer — deterministic Chinese plain-language mappings.

Pure functions over regime.classify() output + the snapshot summary dict.
No I/O, no randomness: same inputs always produce the same words. This is
what lets a static site speak without an LLM in the loop.
"""

from __future__ import annotations

from typing import Any, Optional

RATING_ORDER = ("green", "yellow", "red")

# Flags that trigger yellow. rrp_near_zero is deliberately absent:
# rrp_T ≈ 0 is a multi-year structural state (the parking lot emptied
# in 2023 and stayed empty), not a signal. Shown as info, never a trigger.
YELLOW_FLAGS = {"credit_tightening", "h8_loans_contracting", "debt_ceiling_watch"}
RED_FLAGS = {"vol_elevated"}

# The only flags the rating understands — regime.py's five. server.py merges
# per-layer heuristic_flags (buyback_proxy_weak etc.) into snapshot.flags;
# those must never move the rating.
KNOWN_FLAGS = YELLOW_FLAGS | RED_FLAGS | {"rrp_near_zero"}

REGIME_LABELS_ZH = {
    "balance_sheet": {
        "qt_active": "缩表抽水中",
        "qe_active": "扩表注水中",
        "stable": "资产负债表平稳",
        "unknown": "数据未知",
    },
    "rates": {
        "hiking": "加息周期",
        "cutting": "降息周期",
        "on_hold": "利率按兵不动",
        "unknown": "数据未知",
    },
    "funding": {
        "stress": "资金面紧张",
        "neutral": "资金面平稳",
        "easy": "资金面宽松",
        "unknown": "数据未知",
    },
}

FLAG_LABELS_ZH = {
    "rrp_near_zero": "缓冲水池见底",
    "credit_tightening": "信用利差走阔",
    "vol_elevated": "恐慌指数偏高",
    "h8_loans_contracting": "银行贷款收缩",
    "debt_ceiling_watch": "债务上限扰动",
}

RATING_LABELS_ZH = {"green": "充裕", "yellow": "正常偏紧", "red": "紧张"}

GLOSSARY = {
    "净流动性": "美联储放进体系、没被财政部和隔夜理财占住的钱。= 美联储资产 − 财政部钱包 − 隔夜 parking。",
    "WALCL": "美联储资产负债表总规模。变大=放水（QE），变小=抽水（QT）。",
    "TGA": "财政部在美联储的支票账户。它鼓起来=钱从市场被收走；花出去=钱回到市场。",
    "RRP": "货币基金隔夜存在美联储的 parking 池。池子见底=缓冲垫用完，QT 开始直接抽银行准备金。",
    "SOFR-IORB": "隔夜借钱成本与美联储付给银行的利率之差。越高=钱越渴；≥15bp 算紧张。",
    "HY OAS": "垃圾债相对国债多要的利息。走阔=市场开始担心还不上钱。",
    "IG OAS": "投资级公司债相对国债多要的利息。走阔=连好公司借钱都变贵了，系统性信用风险。",
    "VIX": "期权价格隐含的恐慌指数。≥25 算偏高。",
    "T10Y2Y": "10年与2年国债利差。倒挂（负值） historically 是衰退预警。",
    "市场宽度": "上涨股票占比。指数涨但宽度差=少数大块头硬拉，底子虚。",
    "杠杆ETF规模": "散户/交易型资金杠杆押注的总规模。急剧缩水=赌客在离场，市场情绪退潮。",
}


def _num(v: Any) -> Optional[float]:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def rate(states: dict, flags: list) -> str:
    """green / yellow / red from regime states + regime.py's known flags only."""
    fs = set(flags) & KNOWN_FLAGS
    if states.get("funding") == "stress" or fs & RED_FLAGS:
        return "red"
    if (
        states.get("balance_sheet") == "qt_active"
        or states.get("rates") == "hiking"
        or fs & YELLOW_FLAGS
    ):
        return "yellow"
    return "green"


def rating_drivers(regime: dict, summary: dict) -> list[str]:
    """1–3 short zh sentences naming what triggered the rating, with values.
    Empty for green. rrp_near_zero is never a driver (info only)."""
    states = regime.get("states") or {}
    flags = set(regime.get("flags") or []) & KNOWN_FLAGS
    inputs = regime.get("inputs") or {}
    thresholds = regime.get("thresholds") or {}
    drivers: list[str] = []

    if states.get("funding") == "stress":
        sofr = _num(summary.get("sofr_minus_iorb_bp"))
        limit = _num(thresholds.get("sofr_stress_bp"))
        drivers.append(
            f"隔夜资金喊渴：SOFR-IORB {sofr:+.0f}bp"
            + (f"（≥{limit:.0f}bp 为紧）" if limit is not None else "")
            if sofr is not None
            else "隔夜资金面紧张"
        )
    if "vol_elevated" in flags:
        vix = _num(summary.get("vix"))
        drivers.append(f"市场恐慌偏高：VIX {vix:.1f}（≥25 为高）" if vix is not None else "恐慌指数偏高")
    if states.get("balance_sheet") == "qt_active":
        w = _num(inputs.get("walcl_chg_30d_B"))
        drivers.append(f"美联储抽水中：资产负债表近30日 {w:+.0f}B" if w is not None else "美联储缩表抽水中")
    if states.get("rates") == "hiking":
        i = _num(inputs.get("iorb_chg_60d_bp"))
        drivers.append(f"利率上行周期：IORB 近60日 {i:+.0f}bp" if i is not None else "加息周期")
    if "credit_tightening" in flags:
        hy = _num(summary.get("hy_oas"))
        drivers.append(f"信用利差走阔：HY OAS {hy:.1f}%（≥4.5% 为紧）" if hy is not None else "信用利差走阔")
    if "h8_loans_contracting" in flags:
        h = _num(inputs.get("h8_loans_chg_4w_B"))
        drivers.append(f"银行贷款收缩：近4周 {h:+.0f}B" if h is not None else "银行贷款收缩")
    if "debt_ceiling_watch" in flags:
        t = _num(inputs.get("tga_chg_30d_B"))
        drivers.append(
            f"财政部钱包快速缩水：TGA 近30日 {t:+.0f}B，警惕债务上限扰动"
            if t is not None
            else "债务上限扰动"
        )
    return drivers[:3]


def headline(rating: str, summary: dict) -> str:
    """One zh sentence: water level + 30d direction + verdict."""
    nl = _num(summary.get("net_liquidity_T"))
    d30 = _num(summary.get("delta_30d_T"))
    verdict = RATING_LABELS_ZH.get(rating, rating)

    if nl is None:
        return f"水位数据暂缺——{verdict}"
    level = f"美元净水位 {nl:.2f} 万亿"
    if d30 is None:
        flow = ""
    else:
        direction = "净流入" if d30 >= 0 else "净流出"
        flow = f"，近30日{direction} {abs(d30) * 10000:.0f} 亿"
    return f"{level}{flow}——{verdict}"


def card_lines(summary: dict, states: dict, flags: list) -> dict:
    """Four zh one-liners for the hero cards. Missing values → 数据暂缺."""
    fs = set(flags or [])
    lines: dict[str, str] = {}

    nl = _num(summary.get("net_liquidity_T"))
    d30 = _num(summary.get("delta_30d_T"))
    if nl is None:
        lines["net_liq"] = "总水量数据暂缺"
    else:
        line = f"池子里的总水量 {nl:.2f} 万亿"
        if d30 is not None:
            line += f"，30日{'净增' if d30 >= 0 else '净减'} {abs(d30) * 10000:.0f} 亿"
        if "rrp_near_zero" in fs:
            line += "；缓冲垫（RRP）已用完，波动会更直接"
        lines["net_liq"] = line

    sofr = _num(summary.get("sofr_minus_iorb_bp"))
    funding = states.get("funding")
    if sofr is None or funding in (None, "unknown"):
        lines["sofr"] = "隔夜资金价格数据暂缺"
    elif funding == "stress":
        lines["sofr"] = f"银行间开始喊渴：SOFR-IORB {sofr:+.0f}bp（≥15bp 为紧）"
    elif funding == "easy":
        lines["sofr"] = f"银行间借钱容易，水管不堵（SOFR-IORB {sofr:+.0f}bp）"
    else:
        lines["sofr"] = f"隔夜资金价格平稳（SOFR-IORB {sofr:+.0f}bp）"

    hy = _num(summary.get("hy_oas"))
    vix = _num(summary.get("vix"))
    if hy is None and vix is None:
        lines["credit_vol"] = "信用与恐慌数据暂缺"
    else:
        parts = []
        if hy is not None:
            parts.append(f"信用利差 {hy:.1f}%")
        if vix is not None:
            parts.append(f"恐慌指数 {vix:.1f}")
        calm = (hy is None or hy < 4.5) and (vix is None or vix < 25)
        lines["credit_vol"] = "、".join(parts) + ("——暂时都不闹心" if calm else "——有指标在报警")

    lines["regime"] = " · ".join(
        REGIME_LABELS_ZH[k].get(states.get(k, "unknown"), "数据未知")
        for k in ("balance_sheet", "rates", "funding")
    )
    return lines


def regime_labels_zh(states: dict, flags: list) -> dict:
    """Translate regime states + flags to zh labels for display."""
    return {
        "states": {
            k: REGIME_LABELS_ZH[k].get(states.get(k, "unknown"), "数据未知")
            for k in REGIME_LABELS_ZH
        },
        "flags": [FLAG_LABELS_ZH.get(f, f) for f in (flags or [])],
    }
