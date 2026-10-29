"""系統精選：從最新評分中自動挑出研究候選（不是買賣指令）。

規則（初始假設，與評分權重一樣需以樣本外結果檢討）：
1. 已通過硬性排除條件（資料品質、歷史長度、流動性、覆蓋率）。
2. 資料覆蓋率 ≥ 80%。
3. 總分達門檻（普通股 ≥ 60、ETF ≥ 55）。
4. 均衡要求：趨勢分項 ≥ 50（不逆勢），估值與風險分項 ≥ 30（不過度冒險）。
5. 排除重大風險標籤：近四季 EPS 為負、短期急漲（追高）、近期回撤超過 30%。
6. 模型：只有在 20 日模型「樣本外優於基準」時，才剔除模型中位估計為負者；否則模型不作為篩選依據。
7. 分散：同一產業最多 2 檔；普通股最多 10 檔、一般 ETF 最多 3 檔。槓桿／反向 ETF 不列入精選。
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Instrument, ModelRun, Prediction, ScoreSnapshot

RULES = {
    "stock": {"min_total": 60.0, "max_picks": 10},
    "etf": {"min_total": 55.0, "max_picks": 3},
}
MIN_COVERAGE = 0.8
MIN_TREND = 50.0
MIN_VALUE_RISK = 30.0
MAX_PER_INDUSTRY = 2
SEVERE_FLAGS = ("近四季 EPS 為負", "短期急漲", "近期回撤深")

RULE_TEXT = [
    "通過硬性排除條件（資料品質、歷史長度、流動性、覆蓋率）",
    f"資料覆蓋率 ≥ {int(MIN_COVERAGE * 100)}%",
    "總分門檻：普通股 ≥ 60、一般 ETF ≥ 55",
    f"趨勢分項 ≥ {int(MIN_TREND)}（不逆勢）、估值與風險分項 ≥ {int(MIN_VALUE_RISK)}（不過度冒險）",
    "無重大風險標籤：近四季 EPS 為負、短期急漲、近期回撤超過 30%",
    "20 日模型若樣本外優於基準，剔除模型估計為負者；模型未證實時不作為篩選依據",
    f"同一產業最多 {MAX_PER_INDUSTRY} 檔；普通股最多 10 檔、一般 ETF 最多 3 檔；槓桿／反向 ETF 不列入",
]


def _latest_preds(session: Session, as_of, iids: list[int]) -> dict[int, dict]:
    out: dict[int, dict] = {}
    rows = session.execute(
        select(Prediction, ModelRun).join(ModelRun, ModelRun.id == Prediction.model_run_id)
        .where(Prediction.as_of_date == as_of, Prediction.instrument_id.in_(iids))
        .order_by(Prediction.id)
    ).all()
    for p, m in rows:
        out.setdefault(p.instrument_id, {})[p.horizon_days] = {
            "expected_return": p.expected_return, "lower": p.lower, "upper": p.upper, "prob_up": p.prob_up,
            "beats_baseline": m.beats_baseline,
        }
    return out


def compute_picks(session: Session, group: str) -> dict:
    sd = session.scalar(select(ScoreSnapshot.as_of_date).order_by(ScoreSnapshot.as_of_date.desc()).limit(1))
    if group not in RULES:
        return {"as_of_date": str(sd) if sd else None, "group": group, "picks": [], "near_misses": [], "rules": RULE_TEXT,
                "note": "槓桿／反向 ETF 每日重設倍數、長期報酬偏離標的，依 PRD 不列入精選。"}
    if sd is None:
        return {"as_of_date": None, "group": group, "picks": [], "near_misses": [], "rules": RULE_TEXT}
    rule = RULES[group]
    rows = session.execute(
        select(ScoreSnapshot, Instrument).join(Instrument, Instrument.instrument_id == ScoreSnapshot.instrument_id)
        .where(ScoreSnapshot.as_of_date == sd, ScoreSnapshot.group == group, Instrument.status == "active")
        .order_by(ScoreSnapshot.total.desc())
    ).all()
    preds = _latest_preds(session, sd, [i.instrument_id for _, i in rows])
    picks, misses, per_ind = [], [], {}
    for sc, inst in rows:
        if sc.total is None:
            continue
        reasons: list[str] = []
        comp = sc.components or {}
        if not sc.eligible:
            reasons += sc.exclusion_reasons
        if sc.coverage < MIN_COVERAGE:
            reasons.append(f"覆蓋率 {sc.coverage * 100:.0f}% 未達 {int(MIN_COVERAGE * 100)}%")
        if sc.total < rule["min_total"]:
            reasons.append(f"總分 {sc.total:.1f} 未達 {rule['min_total']:.0f}")
        if comp.get("trend") is not None and comp["trend"] < MIN_TREND:
            reasons.append(f"趨勢分項 {comp['trend']:.0f} 低於 {int(MIN_TREND)}")
        if comp.get("value_risk") is not None and comp["value_risk"] < MIN_VALUE_RISK:
            reasons.append(f"估值與風險分項 {comp['value_risk']:.0f} 低於 {int(MIN_VALUE_RISK)}")
        severe = [f for f in sc.risk_flags if f.startswith(SEVERE_FLAGS)]
        if severe:
            reasons.append("重大風險：" + "；".join(severe))
        p20 = preds.get(inst.instrument_id, {}).get(20)
        if p20 and p20["beats_baseline"] and (p20["expected_return"] or 0) < 0:
            reasons.append(f"已驗證模型的 20 日估計為負（{p20['expected_return'] * 100:.1f}%）")
        ind = inst.industry or "其他"
        if not reasons and per_ind.get(ind, 0) >= MAX_PER_INDUSTRY:
            reasons.append(f"同產業（{ind}）已選滿 {MAX_PER_INDUSTRY} 檔")
        if not reasons and len(picks) >= rule["max_picks"]:
            reasons.append(f"已選滿 {rule['max_picks']} 檔")

        item = {
            "instrument_id": inst.instrument_id, "symbol": inst.symbol, "name": inst.name, "market": inst.market,
            "industry": inst.industry, "group": group, "total": sc.total, "rank": sc.rank, "coverage": sc.coverage,
            "components": comp, "positives": sc.positives[:3], "negatives": sc.negatives[:3],
            "risk_flags": sc.risk_flags, "pred20": p20, "pred60": preds.get(inst.instrument_id, {}).get(60),
        }
        if reasons:
            if len(misses) < 8 and sc.total >= rule["min_total"] - 10:
                misses.append({**item, "rejected": reasons})
            continue
        per_ind[ind] = per_ind.get(ind, 0) + 1
        model_note = None
        if p20 and not p20["beats_baseline"]:
            model_note = "20 日模型未證實優於基準，僅供參考，未用於篩選"
        picks.append({**item, "pick_no": len(picks) + 1, "model_note": model_note})
    return {
        "as_of_date": str(sd), "group": group, "picks": picks, "near_misses": misses, "rules": RULE_TEXT,
        "scanned": len(rows),
        "note": "系統依固定規則自動偵測的研究候選，不是買賣指令；進場前請自行確認基本面、消息面與自身風險承受度。",
    }
