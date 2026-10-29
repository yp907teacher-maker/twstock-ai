"""量化選股評分（FR-06，PRD 第 9 節）。

- 每個特徵在「同一交易日、同一分組」內轉為百分位（0–1），限制極端值影響。
- 分項分數 = 可用特徵百分位的平均（可用特徵少於一半視為該分項缺失）。
- 總分 = 可用分項的加權平均，缺失分項的權重重新正規化，並揭露覆蓋率；不以 0 代替缺值。
- 總分不是上漲機率。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import SCORE_VERSION, settings
from .features import FEATURE_LABELS, PCT_FEATURES

WEIGHTS = {"trend": 0.30, "fundamental": 0.25, "liquidity": 0.15, "flow": 0.15, "value_risk": 0.15}
COMPONENT_LABELS = {"trend": "趨勢與動能", "fundamental": "基本面與成長", "liquidity": "流動性", "flow": "籌碼",
                    "value_risk": "估值與風險"}

# (特徵, 方向)：+1 越高越好，-1 越低越好
COMPONENTS = {
    "trend": [("ret_20", 1), ("ret_60", 1), ("px_ma20", 1), ("px_ma60", 1), ("ma20_slope", 1)],
    "fundamental": [("rev_yoy", 1), ("rev_yoy_3m", 1), ("eps_growth", 1), ("gross_margin", 1), ("roe", 1),
                    ("debt_ratio", -1)],
    "liquidity": [("turnover_20", 1), ("turnover_cv20", -1)],
    "flow": [("foreign_20", 1), ("trust_20", 1), ("inst_persist", 1), ("margin_chg_20", -1)],
    "value_risk": [("vol_60", -1), ("down_vol_60", -1), ("max_dd_120", 1), ("pe_score", 1)],
}
MIN_COVERAGE = 0.6


def _pct_rank(s: pd.Series) -> pd.Series:
    return s.rank(pct=True, method="average")


def _fmt(name: str, v) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "—"
    if name in PCT_FEATURES:
        return f"{v * 100:.1f}%"
    if name == "turnover_20":
        return f"{v / 1e8:.2f} 億元"
    if name in ("pe", "rsi14", "eps_ttm", "vol_ratio", "turnover_cv20"):
        return f"{v:.2f}"
    return f"{v:.3f}"


def score_cross_section(day: pd.DataFrame, blocked: dict[int, list[str]] | None = None,
                        excluded: dict[int, str] | None = None, detail: bool = True) -> pd.DataFrame:
    """對單一交易日的長表計算分數；回傳每檔一列。detail=False 供回測快速計算（只回總分與資格）。"""
    blocked = blocked or {}
    excluded = excluded or {}
    day = day.copy()
    # 估值：本益比不是越低越好。中低分位最佳、極高與極低（可能為價值陷阱）扣分；EPS ≤ 0 無本益比 → 缺值
    out_rows = []
    for grp, g in day.groupby("group"):
        g = g.copy()
        pe_r = _pct_rank(g["pe"])
        g["pe_score"] = np.where(pe_r.isna(), np.nan, np.where(pe_r < 0.05, 0.3, 1 - np.abs(pe_r - 0.35) / 0.65))
        # 流動性門檻：同組 20 日平均成交金額後 X% 排除
        # 成員太少（< 5 檔）的分組不套用分位數門檻，避免硬性排除一半標的
        t_thr = (g["turnover_20"].quantile(settings.min_turnover_quantile)
                 if g["turnover_20"].notna().sum() >= 5 else np.nan)
        ranks = {}
        for comp, feats in COMPONENTS.items():
            for f, sign in feats:
                if f not in g:
                    g[f] = np.nan
                r = _pct_rank(g[f] * sign) if f != "pe_score" else g["pe_score"]
                ranks[f] = r
        rank_df = pd.DataFrame(ranks, index=g.index)
        comp_scores = {}
        for comp, feats in COMPONENTS.items():
            cols = [f for f, _ in feats]
            avail = rank_df[cols].notna().sum(axis=1)
            sc = rank_df[cols].mean(axis=1)
            comp_scores[comp] = sc.where(avail >= max(1, int(np.ceil(len(cols) / 2))))
        comp_df = pd.DataFrame(comp_scores, index=g.index)
        # ETF 不適用企業財務分項 → 自權重中移除
        w = pd.DataFrame({c: WEIGHTS[c] for c in WEIGHTS}, index=g.index)
        if grp != "stock":
            w["fundamental"] = 0.0
        avail_w = (w * comp_df.notna()).sum(axis=1)
        total_w = w.sum(axis=1)
        coverage = avail_w / total_w
        total = (comp_df.fillna(0) * w).sum(axis=1) / avail_w.replace(0, np.nan)

        # 短期暴漲：避免追高（5 日漲幅超過 15% 扣 5 分並標示）
        spike = g["ret_5"] > 0.15
        total = total - spike.astype(float) * 0.05

        if not detail:
            elig = (g["hist_days"] >= settings.min_history_days) & (coverage >= MIN_COVERAGE) & total.notna()
            if pd.notna(t_thr):
                elig &= g["turnover_20"] >= t_thr
            out_rows.extend({"instrument_id": int(a), "group": grp, "eligible": bool(b), "total": None if pd.isna(c) else float(c) * 100,
                             "coverage": float(d)} for a, b, c, d in zip(g["iid"], elig, total, coverage))
            continue

        for i in g.index:
            r = g.loc[i]
            iid = int(r["iid"])
            reasons = []
            if iid in excluded:
                reasons.append(f"已手動停用：{excluded[iid]}")
            if iid in blocked:
                reasons.append("資料品質問題：" + "、".join(blocked[iid]))
            if pd.isna(r.get("hist_days")) or r["hist_days"] < settings.min_history_days:
                reasons.append(f"可用歷史不足 {settings.min_history_days} 個交易日")
            if pd.notna(t_thr) and (pd.isna(r["turnover_20"]) or r["turnover_20"] < t_thr):
                reasons.append(f"流動性低於同組後 {int(settings.min_turnover_quantile * 100)}%")
            if coverage[i] < MIN_COVERAGE:
                reasons.append(f"資料覆蓋率 {coverage[i] * 100:.0f}% 低於 {int(MIN_COVERAGE * 100)}%")
            eligible = not reasons and pd.notna(total[i])

            pos, neg = [], []
            for f, sign in sum(COMPONENTS.values(), []):
                rv = rank_df.at[i, f]
                if pd.isna(rv) or f == "pe_score":
                    continue
                label = FEATURE_LABELS.get(f, f)
                txt = f"{label} {_fmt(f, r[f])}（同組百分位 {rv * 100:.0f}）"
                if rv >= 0.8:
                    pos.append((rv, txt))
                elif rv <= 0.2:
                    neg.append((rv, txt))
            pos = [t for _, t in sorted(pos, reverse=True)[:4]]
            neg = [t for _, t in sorted(neg)[:4]]

            flags = []
            if grp == "etf_leveraged":
                flags.append("槓桿／反向 ETF：每日重設，長期持有報酬會偏離標的")
            if pd.notna(r.get("vol_60")) and rank_df.at[i, "vol_60"] <= 0.1:
                flags.append(f"波動偏高（60 日年化 {_fmt('vol_60', r['vol_60'])}）")
            if pd.notna(r.get("max_dd_120")) and r["max_dd_120"] < -0.3:
                flags.append(f"近期回撤深（{_fmt('max_dd_120', r['max_dd_120'])}）")
            if bool(spike[i]):
                flags.append(f"短期急漲（5 日 {_fmt('ret_5', r['ret_5'])}），留意追高風險")
            if grp == "stock" and pd.notna(r.get("eps_ttm")) and r["eps_ttm"] <= 0:
                flags.append("近四季 EPS 為負")
            if pd.notna(r.get("margin_chg_20")) and r["margin_chg_20"] > 0.3:
                flags.append("融資餘額快速增加")
            if coverage[i] < 0.85:
                missing = [COMPONENT_LABELS[c] for c in WEIGHTS if w.at[i, c] > 0 and pd.isna(comp_df.at[i, c])]
                if missing:
                    flags.append("資料不完整：缺 " + "、".join(missing))

            out_rows.append({
                "instrument_id": iid,
                "group": grp,
                "eligible": bool(eligible),
                "total": None if pd.isna(total[i]) else round(float(total[i]) * 100, 2),
                "coverage": round(float(coverage[i]), 3),
                "components": {c: (None if pd.isna(comp_df.at[i, c]) else round(float(comp_df.at[i, c]) * 100, 1))
                               for c in WEIGHTS if not (grp != "stock" and c == "fundamental")},
                "exclusion_reasons": reasons,
                "positives": pos,
                "negatives": neg,
                "risk_flags": flags,
                "weights": {c: float(w.at[i, c]) for c in WEIGHTS},
            })
    res = pd.DataFrame(out_rows)
    if res.empty:
        return res
    res["rank"] = None
    if not detail:
        return res
    for grp, g in res[res["eligible"]].groupby("group"):
        order = g.sort_values("total", ascending=False).index
        res.loc[order, "rank"] = range(1, len(order) + 1)
    res["score_version"] = SCORE_VERSION
    return res
