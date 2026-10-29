"""AI 研究報告（FR-09、PRD 第 11 節）。

- 輸入只含資料庫中的結構化資料、模型輸出與時間戳。
- 固定段落：摘要、支持證據、反向證據、預測情境、主要風險、資料完整度、模型版本。
- 忠實度檢查：輸出中的每個數字必須能在輸入中找到（容許四捨五入與百分比換算），
  不通過時改用程式產生的量化摘要，並保留檢查紀錄。
- 同一資料版本（payload 雜湊）只生成一次並快取；每月呼叫次數有上限。
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import PROMPT_VERSION, settings
from ..models import (
    AIReport,
    DailyPrice,
    DataQualityIssue,
    FeatureSnapshot,
    Fundamental,
    Instrument,
    ModelRun,
    Prediction,
    ScoreSnapshot,
)
from .features import FEATURE_LABELS, PCT_FEATURES
from .scoring import COMPONENT_LABELS

log = logging.getLogger(__name__)
GROUP_LABELS = {"stock": "普通股", "etf": "一般 ETF", "etf_leveraged": "槓桿／反向 ETF"}

SYSTEM_PROMPT = """你是台股研究助理，替一位個人投資者把系統已計算好的結構化資料整理成研究摘要。
嚴格規則：
1. 只能使用 <data> 中提供的數字與事實；不得引入任何外部資訊、新聞、產業知識或你自行計算的新數字。
2. 引用數字時照抄 <data> 中的數值（百分比可用 data 中已格式化的字串）。
3. 評分是同組相對排序，不是上漲機率；不得寫「建議買進／賣出」或暗示保證獲利。
4. 若 model.beats_baseline 為 false，必須明說「模型尚未證實優於簡單基準」。若 prob_up 為 null，不得提及上漲機率。
5. 資料缺漏時寫「資料不足」，不要猜測。
6. 區分「事實」（資料值）與「模型觀點」（預測）。
只輸出一個 JSON 物件，鍵為：summary（字串，3 句內）、supporting（字串陣列）、opposing（字串陣列）、
scenarios（字串陣列，每個預測期間一項）、risks（字串陣列）、data_completeness（字串）。使用繁體中文。"""


def _fmt_feature(k: str, v):
    if v is None:
        return None
    if k in PCT_FEATURES:
        return f"{v * 100:.1f}%"
    if k == "turnover_20":
        return f"{v / 1e8:.2f} 億元"
    return round(v, 2)


def build_payload(session: Session, iid: int) -> dict | None:
    inst = session.get(Instrument, iid)
    if inst is None:
        return None
    sc = session.scalar(select(ScoreSnapshot).where(ScoreSnapshot.instrument_id == iid)
                        .order_by(ScoreSnapshot.as_of_date.desc()).limit(1))
    if sc is None:
        return None
    group_n = session.scalar(select(func.count()).select_from(ScoreSnapshot).where(
        ScoreSnapshot.as_of_date == sc.as_of_date, ScoreSnapshot.group == sc.group, ScoreSnapshot.eligible.is_(True)))
    fs = session.scalar(select(FeatureSnapshot).where(FeatureSnapshot.instrument_id == iid,
                                                      FeatureSnapshot.as_of_date == sc.as_of_date))
    feats = {}
    if fs:
        for k, label in FEATURE_LABELS.items():
            v = _fmt_feature(k, fs.values.get(k))
            if v is not None:
                feats[label] = v
    preds = []
    for h in (20, 60):
        p = session.scalar(select(Prediction).where(Prediction.instrument_id == iid, Prediction.horizon_days == h)
                           .order_by(Prediction.as_of_date.desc(), Prediction.id.desc()).limit(1))
        if not p:
            continue
        mr = session.get(ModelRun, p.model_run_id)
        test = (mr.metrics or {}).get("test", {}).get("model", {})
        preds.append({
            "horizon_days": h,
            "as_of_date": str(p.as_of_date),
            "target": p.target_definition,
            "expected_return": f"{p.expected_return * 100:.1f}%" if p.expected_return is not None else None,
            "interval_80": [f"{p.lower * 100:.1f}%", f"{p.upper * 100:.1f}%"] if p.lower is not None else None,
            "interval_calibrated": mr.interval_calibrated,
            "prob_up": f"{p.prob_up * 100:.0f}%" if p.prob_up is not None else None,
            "model": {"version": mr.model_version, "beats_baseline": mr.beats_baseline,
                      "test_rank_ic": round(test.get("rank_ic") or 0, 3),
                      "test_period": f"{mr.test_start} ~ {mr.test_end}"},
        })
    last_px = session.execute(select(DailyPrice.trade_date, DailyPrice.close, DailyPrice.provider, DailyPrice.retrieved_at)
                              .where(DailyPrice.instrument_id == iid).order_by(DailyPrice.trade_date.desc()).limit(1)).first()
    last_fund = session.scalar(select(func.max(Fundamental.public_at)).where(Fundamental.instrument_id == iid,
                                                                             Fundamental.public_at <= sc.as_of_date))
    issues = session.scalars(select(DataQualityIssue.issue_code).where(
        DataQualityIssue.instrument_id == iid, DataQualityIssue.resolved.is_(False),
        DataQualityIssue.severity != "info")).all()
    return {
        "instrument": {"symbol": inst.symbol, "name": inst.name, "market": inst.market,
                       "group": GROUP_LABELS.get(sc.group, sc.group), "industry": inst.industry},
        "as_of_date": str(sc.as_of_date),
        "score": {
            "total": sc.total, "rank": sc.rank, "group_eligible_count": group_n, "eligible": sc.eligible,
            "coverage": f"{sc.coverage * 100:.0f}%",
            "components": {COMPONENT_LABELS[k]: v for k, v in sc.components.items()},
            "exclusion_reasons": sc.exclusion_reasons,
            "note": "評分為同組相對排序（0–100），不是上漲機率",
        },
        "supporting_signals": sc.positives,
        "opposing_signals": sc.negatives,
        "risk_flags": sc.risk_flags,
        "features": feats,
        "predictions": preds,
        "data_sources": {
            "last_price_date": str(last_px[0]) if last_px else None,
            "last_close": last_px[1] if last_px else None,
            "price_provider": last_px[2] if last_px else None,
            "latest_fundamental_public_at": str(last_fund) if last_fund else None,
            "open_quality_issues": sorted(set(issues)),
        },
    }


def payload_hash(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


NUM_RE = re.compile(r"-?\d+(?:,\d{3})*(?:\.\d+)?")


def _numbers(obj) -> list[float]:
    s = json.dumps(obj, ensure_ascii=False, default=str)
    out = []
    for m in NUM_RE.findall(s):
        try:
            out.append(float(m.replace(",", "")))
        except ValueError:
            pass
    return out


def fidelity_check(content: dict, payload: dict) -> dict:
    """輸出中的數字必須出現在輸入中（容許 ±0.051 的四捨五入差、正負號與 20/60 等期間數字）。"""
    allowed = set()
    for v in _numbers(payload):
        for cand in (v, abs(v), round(v, 0), round(v, 1), round(v, 2)):
            allowed.add(round(cand, 2))
    allowed |= {20.0, 60.0, 80.0, 0.0}
    unmatched = []
    for v in _numbers(content):
        if any(abs(abs(v) - a) <= 0.051 for a in allowed):
            continue
        unmatched.append(v)
    return {"passed": not unmatched, "unmatched_numbers": unmatched[:20], "checked_at": datetime.utcnow().isoformat()}


def fallback_report(p: dict) -> dict:
    sc = p["score"]
    inst = p["instrument"]
    rank_txt = f"{inst['group']}組第 {sc['rank']} 名（共 {sc['group_eligible_count']} 檔合格）" if sc["rank"] else "未列入正式排名"
    summary = (f"{inst['name']}（{inst['symbol']}）截至 {p['as_of_date']} 的均衡評分為 {sc['total']}，{rank_txt}，"
               f"資料覆蓋率 {sc['coverage']}。評分是同組相對排序，不是上漲機率。")
    if sc["exclusion_reasons"]:
        summary += " 未列入排名原因：" + "；".join(sc["exclusion_reasons"]) + "。"
    scen = []
    for pr in p["predictions"]:
        t = f"未來 {pr['horizon_days']} 個交易日：模型中位估計 {pr['expected_return']}"
        if pr["interval_80"]:
            t += f"，80% 區間 {pr['interval_80'][0]} ~ {pr['interval_80'][1]}"
            if not pr["interval_calibrated"]:
                t += "（區間未通過校準，僅供參考）"
        if pr["prob_up"]:
            t += f"，經校準上漲機率 {pr['prob_up']}"
        t += "。" + ("模型在最終測試期優於基準。" if pr["model"]["beats_baseline"] else "模型尚未證實優於簡單基準。")
        scen.append(t)
    ds = p["data_sources"]
    completeness = f"最新行情日 {ds['last_price_date'] or '資料不足'}（來源 {ds['price_provider'] or '—'}）；"
    completeness += f"最新財務公告 {ds['latest_fundamental_public_at'] or '無／不適用'}；"
    completeness += "未解決品質問題：" + ("、".join(ds["open_quality_issues"]) if ds["open_quality_issues"] else "無")
    return {
        "summary": summary,
        "supporting": p["supporting_signals"] or ["資料不足：無明顯高於同組的訊號"],
        "opposing": p["opposing_signals"] or ["無明顯低於同組的訊號"],
        "scenarios": scen or ["資料不足：尚無模型預測"],
        "risks": p["risk_flags"] or ["未偵測到特別風險標籤（不代表沒有風險）"],
        "data_completeness": completeness,
    }


def _calls_this_month(session: Session) -> int:
    start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return session.scalar(select(func.count()).select_from(AIReport).where(AIReport.mode == "ai",
                                                                           AIReport.created_at >= start)) or 0


def call_claude(payload: dict) -> dict:
    import anthropic

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    msg = client.messages.create(
        model=settings.anthropic_model,
        max_tokens=1500,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": "<data>\n" + json.dumps(payload, ensure_ascii=False, indent=1) + "\n</data>"}],
    )
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("模型輸出不是 JSON")
    data = json.loads(m.group(0))
    for k in ("summary", "supporting", "opposing", "scenarios", "risks", "data_completeness"):
        if k not in data:
            raise ValueError(f"模型輸出缺少 {k}")
    return data


def get_report(session: Session, iid: int, force: bool = False) -> dict | None:
    payload = build_payload(session, iid)
    if payload is None:
        return None
    h = payload_hash(payload)
    cached = session.scalar(select(AIReport).where(AIReport.instrument_id == iid, AIReport.data_hash == h,
                                                   AIReport.prompt_version == PROMPT_VERSION)
                            .order_by(AIReport.id.desc()).limit(1))
    if cached and not (force and cached.mode == "fallback"):
        return _serialize(cached, payload)
    mode, model, content, fidelity = "fallback", None, None, {"passed": True, "note": "程式產生，數字直接取自資料庫"}
    reason = None
    if not settings.anthropic_api_key:
        reason = "未設定 ANTHROPIC_API_KEY，使用程式產生的量化摘要"
    elif _calls_this_month(session) >= settings.ai_monthly_call_limit:
        reason = f"本月 AI 呼叫已達上限 {settings.ai_monthly_call_limit} 次，降級為量化摘要"
    else:
        try:
            ai = call_claude(payload)
            fc = fidelity_check(ai, payload)
            if fc["passed"]:
                mode, model, content, fidelity = "ai", settings.anthropic_model, ai, fc
            else:
                reason = "AI 輸出含有無法對應資料的數字，已改用量化摘要"
                fidelity = fc
                # 仍記錄一次 AI 呼叫（計入月用量）
                session.add(AIReport(instrument_id=iid, as_of_date=datetime.fromisoformat(payload["as_of_date"]).date(),
                                     data_hash=h, prompt_version=PROMPT_VERSION, mode="ai_rejected",
                                     model=settings.anthropic_model, content=ai, fidelity=fc))
        except Exception as exc:  # noqa: BLE001
            log.warning("Claude 呼叫失敗：%s", exc)
            reason = f"AI 呼叫失敗（{str(exc)[:120]}），使用量化摘要"
    if content is None:
        content = fallback_report(payload)
    if reason:
        fidelity = {**fidelity, "fallback_reason": reason}
    rep = AIReport(instrument_id=iid, as_of_date=datetime.fromisoformat(payload["as_of_date"]).date(), data_hash=h,
                   prompt_version=PROMPT_VERSION, mode=mode, model=model, content=content, fidelity=fidelity)
    session.add(rep)
    session.flush()
    return _serialize(rep, payload)


def _serialize(rep: AIReport, payload: dict) -> dict:
    model_versions = sorted({p["model"]["version"] for p in payload["predictions"]})
    return {
        "id": rep.id, "mode": rep.mode, "model": rep.model, "prompt_version": rep.prompt_version,
        "created_at": rep.created_at.isoformat() if rep.created_at else None, "as_of_date": str(rep.as_of_date),
        "content": rep.content, "fidelity": rep.fidelity, "model_versions": model_versions,
        "data_sources": payload["data_sources"],
    }
