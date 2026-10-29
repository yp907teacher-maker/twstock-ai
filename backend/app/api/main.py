"""FastAPI：提供前端所需資料，並在根路徑提供建置後的前端頁面。"""
from __future__ import annotations

import csv
import os
import io
import logging
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from .. import jobs
from ..config import settings
from ..db import SessionLocal, get_engine
from ..models import (
    BacktestRun,
    DailyPrice,
    DataQualityIssue,
    FeatureSnapshot,
    Flow,
    Fundamental,
    IngestRun,
    Instrument,
    MarketIndex,
    ModelRun,
    Prediction,
    Quote,
    ScoreSnapshot,
    Watchlist,
    utcnow,
)
from ..pipeline.features import FEATURE_LABELS
from ..pipeline.reports import GROUP_LABELS, get_report
from ..pipeline.scoring import COMPONENT_LABELS, WEIGHTS

log = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

PROVIDER_NOTES = {
    "TWSE_OPENAPI": {"name": "證交所 OpenAPI", "license": "政府資料開放授權條款第 1 版；免費；僅提供最新一期資料"},
    "TPEX_OPENAPI": {"name": "櫃買中心 OpenAPI", "license": "政府資料開放授權條款第 1 版；免費；僅提供最新一期資料"},
    "FINMIND": {"name": "FinMind", "license": "免費版 600 次/小時（帶 token）；Free~Sponsor 方案限非商業用途"},
    "FUGLE": {"name": "Fugle MarketData", "license": "基本用戶免費（需 API key）；盤中 60 次/分；個人方案"},
    "DEMO": {"name": "示範資料", "license": "合成資料，非真實行情"},
    "internal": {"name": "系統計算", "license": "—"},
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_engine()
    sched = jobs.start_scheduler()
    if jobs.needs_bootstrap() and os.getenv("AUTO_BOOTSTRAP", "true").lower() != "false":
        log.info("尚無足夠歷史資料，背景執行首次建置（真實資料）")
        jobs.run_job_async("bootstrap")
    yield
    if sched:
        sched.shutdown(wait=False)


app = FastAPI(title="台股 AI 分析與預測（個人研究用）", lifespan=lifespan)


def db():
    get_engine()
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


# ------------------------------------------------------------------ helpers
def latest_score_date(s: Session) -> date | None:
    return s.scalar(select(func.max(ScoreSnapshot.as_of_date)))


def latest_price_date(s: Session) -> date | None:
    return s.scalar(select(func.max(DailyPrice.trade_date)))


def inst_dict(i: Instrument) -> dict:
    return {"instrument_id": i.instrument_id, "symbol": i.symbol, "name": i.name, "market": i.market,
            "asset_type": i.asset_type, "etf_kind": i.etf_kind, "industry": i.industry, "status": i.status,
            "group": i.group, "group_label": GROUP_LABELS.get(i.group, i.group), "excluded_reason": i.excluded_reason}


def score_dict(sc: ScoreSnapshot | None) -> dict | None:
    if sc is None:
        return None
    return {"as_of_date": str(sc.as_of_date), "total": sc.total, "rank": sc.rank, "eligible": sc.eligible,
            "coverage": sc.coverage, "components": sc.components, "exclusion_reasons": sc.exclusion_reasons,
            "positives": sc.positives, "negatives": sc.negatives, "risk_flags": sc.risk_flags, "weights": sc.weights,
            "score_version": sc.score_version, "group": sc.group}


def last_runs(s: Session) -> list[dict]:
    sub = select(IngestRun.provider, IngestRun.dataset, func.max(IngestRun.id).label("mid")).group_by(
        IngestRun.provider, IngestRun.dataset).subquery()
    runs = s.scalars(select(IngestRun).join(sub, IngestRun.id == sub.c.mid)).all()
    out = []
    for r in runs:
        ok = s.scalar(select(func.max(IngestRun.finished_at)).where(IngestRun.provider == r.provider,
                                                                     IngestRun.dataset == r.dataset,
                                                                     IngestRun.status.in_(["success", "partial"])))
        fails = s.scalar(select(func.count()).select_from(IngestRun).where(
            IngestRun.provider == r.provider, IngestRun.dataset == r.dataset, IngestRun.status == "failed",
            IngestRun.started_at >= datetime.utcnow() - timedelta(days=30)))
        out.append({"provider": r.provider, "provider_name": PROVIDER_NOTES.get(r.provider, {}).get("name", r.provider),
                    "license_note": PROVIDER_NOTES.get(r.provider, {}).get("license", ""), "dataset": r.dataset,
                    "job": r.job, "last_status": r.status, "last_started_at": _iso(r.started_at),
                    "last_finished_at": _iso(r.finished_at), "last_success_at": _iso(ok), "rows": r.rows,
                    "data_date": str(r.data_date) if r.data_date else None, "message": r.message,
                    "failures_30d": fails})
    return sorted(out, key=lambda x: (x["provider"], x["dataset"]))


def _iso(dt):
    return dt.isoformat() + "Z" if dt else None


# ------------------------------------------------------------------ meta
@app.get("/api/meta")
def meta(s: Session = Depends(db)):
    from ..models import BackfillState

    progress = None
    if jobs.STATE.get("running") in ("bootstrap", "backfill"):
        total = s.scalar(select(func.count()).select_from(Instrument)) or 0
        done = s.scalar(select(func.count(func.distinct(BackfillState.instrument_id))).where(BackfillState.dataset == "prices")) or 0
        progress = {"phase": jobs.STATE.get("phase"), "done": done, "total": total}
    return {
        "progress": progress,
        "demo_mode": settings.demo_mode,
        "latest_price_date": str(latest_price_date(s) or ""),
        "latest_score_date": str(latest_score_date(s) or ""),
        "fugle_enabled": bool(settings.fugle_api_key),
        "ai_enabled": bool(settings.anthropic_api_key),
        "ai_model": settings.anthropic_model,
        "scheduler": {"enabled": settings.enable_scheduler, "time": f"{settings.daily_job_hour:02d}:{settings.daily_job_minute:02d}"},
        "job": jobs.STATE,
        "weights": WEIGHTS,
        "component_labels": COMPONENT_LABELS,
        "feature_labels": FEATURE_LABELS,
        "group_labels": GROUP_LABELS,
        "disclaimer": "本系統為個人研究輔助，評分為同組相對排序、不是上漲機率，模型預測不保證準確，不構成投資建議。",
    }


# ------------------------------------------------------------------ market overview
@app.get("/api/market/overview")
def market_overview(s: Session = Depends(db)):
    d = latest_price_date(s)
    if d is None:
        return {"empty": True, "datasets": last_runs(s)}
    prev = s.scalar(select(func.max(DailyPrice.trade_date)).where(DailyPrice.trade_date < d))
    cur = pd.DataFrame(s.execute(select(DailyPrice.instrument_id, DailyPrice.close, DailyPrice.adj_factor, DailyPrice.turnover)
                                 .where(DailyPrice.trade_date == d)).all(), columns=["iid", "c", "f", "t"])
    pv = pd.DataFrame(s.execute(select(DailyPrice.instrument_id, DailyPrice.close, DailyPrice.adj_factor)
                                .where(DailyPrice.trade_date == prev)).all(), columns=["iid", "pc", "pf"]) if prev else pd.DataFrame(columns=["iid", "pc", "pf"])
    m = cur.merge(pv, on="iid", how="left")
    chg = (m["c"] * m["f"]) / (m["pc"] * m["pf"]) - 1
    breadth = {"date": str(d), "advancers": int((chg > 1e-9).sum()), "decliners": int((chg < -1e-9).sum()),
               "unchanged": int((chg.abs() <= 1e-9).sum()), "total_turnover": float(m["t"].sum(skipna=True)),
               "count": int(len(m))}
    idx_date = s.scalar(select(func.max(MarketIndex.trade_date)))
    indices = []
    if idx_date:
        rows = s.scalars(select(MarketIndex).where(MarketIndex.trade_date == idx_date)).all()
        prio = ["發行量加權股價指數", "示範加權指數"]
        rows = sorted(rows, key=lambda r: (0 if r.name in prio else 1, r.name))[:8]
        for r in rows:
            hist = s.execute(select(MarketIndex.trade_date, MarketIndex.close).where(MarketIndex.name == r.name)
                             .order_by(MarketIndex.trade_date.desc()).limit(120)).all()
            indices.append({"name": r.name, "close": r.close, "change_pct": r.change_pct, "date": str(r.trade_date),
                            "provider": r.provider, "history": [{"d": str(h[0]), "v": h[1]} for h in reversed(hist)]})
    sd = latest_score_date(s)
    cands = {}
    if sd:
        for grp, n in (("stock", 10), ("etf", 5)):
            rows = s.execute(select(ScoreSnapshot, Instrument).join(Instrument, Instrument.instrument_id == ScoreSnapshot.instrument_id)
                             .where(ScoreSnapshot.as_of_date == sd, ScoreSnapshot.group == grp, ScoreSnapshot.eligible.is_(True))
                             .order_by(ScoreSnapshot.rank).limit(n)).all()
            cands[grp] = [{**inst_dict(i), "score": score_dict(sc)} for sc, i in rows]
    issues = s.execute(select(DataQualityIssue.severity, func.count()).where(DataQualityIssue.resolved.is_(False))
                       .group_by(DataQualityIssue.severity)).all()
    return {"empty": False, "breadth": breadth, "indices": indices, "candidates": cands, "score_date": str(sd) if sd else None,
            "quality": {k: v for k, v in issues}, "datasets": last_runs(s)}


# ------------------------------------------------------------------ system picks
@app.get("/api/picks")
def picks(group: str = "stock", s: Session = Depends(db)):
    from ..pipeline.picks import compute_picks

    return compute_picks(s, group)


# ------------------------------------------------------------------ search & screener
@app.get("/api/instruments/search")
def search(q: str = Query("", max_length=32), limit: int = 20, s: Session = Depends(db)):
    if not q.strip():
        return []
    like = f"%{q.strip()}%"
    rows = s.scalars(select(Instrument).where(or_(Instrument.symbol.like(f"{q.strip()}%"), Instrument.name.like(like)))
                     .order_by(Instrument.status, Instrument.symbol).limit(limit)).all()
    return [inst_dict(i) for i in rows]


SORTABLE_FEATS = {"ret_20", "ret_60", "vol_60", "turnover_20", "pe", "rev_yoy", "max_dd_120"}


@app.get("/api/screener")
def screener(
    group: str = "stock",
    min_total: float | None = None,
    market: str | None = None,
    industry: str | None = None,
    include_ineligible: bool = False,
    hide_risk_flags: bool = False,
    min_trend: float | None = None,
    min_fundamental: float | None = None,
    min_liquidity: float | None = None,
    min_flow: float | None = None,
    min_value_risk: float | None = None,
    min_coverage: float | None = None,
    sort: str = "total",
    desc: bool = True,
    limit: int = Query(100, le=5000),
    s: Session = Depends(db),
):
    sd = latest_score_date(s)
    if sd is None:
        return {"as_of_date": None, "rows": [], "industries": []}
    q = (select(ScoreSnapshot, Instrument, FeatureSnapshot)
         .join(Instrument, Instrument.instrument_id == ScoreSnapshot.instrument_id)
         .outerjoin(FeatureSnapshot, and_(FeatureSnapshot.instrument_id == ScoreSnapshot.instrument_id,
                                          FeatureSnapshot.as_of_date == ScoreSnapshot.as_of_date))
         .where(ScoreSnapshot.as_of_date == sd, ScoreSnapshot.group == group))
    if not include_ineligible:
        q = q.where(ScoreSnapshot.eligible.is_(True))
    if market:
        q = q.where(Instrument.market == market)
    if industry:
        q = q.where(Instrument.industry == industry)
    rows = s.execute(q).all()
    mins = {"trend": min_trend, "fundamental": min_fundamental, "liquidity": min_liquidity, "flow": min_flow,
            "value_risk": min_value_risk}
    out = []
    for sc, inst, fs in rows:
        if min_total is not None and (sc.total is None or sc.total < min_total):
            continue
        if min_coverage is not None and sc.coverage < min_coverage:
            continue
        if hide_risk_flags and sc.risk_flags:
            continue
        skip = False
        for k, v in mins.items():
            if v is not None:
                cv = sc.components.get(k)
                if cv is None or cv < v:
                    skip = True
        if skip:
            continue
        fv = fs.values if fs else {}
        out.append({**inst_dict(inst), "score": score_dict(sc),
                    "features": {k: fv.get(k) for k in SORTABLE_FEATS | {"ret_5", "rsi14"}}})

    def keyf(r):
        if sort == "total":
            v = r["score"]["total"]
        elif sort in WEIGHTS:
            v = r["score"]["components"].get(sort)
        elif sort in SORTABLE_FEATS:
            v = r["features"].get(sort)
        elif sort == "symbol":
            return r["symbol"]
        else:
            v = r["score"]["total"]
        rank = r["score"]["rank"] or 10**6
        return (v is not None, v if v is not None else 0, -rank)

    out.sort(key=keyf, reverse=desc if sort != "symbol" else not desc)
    industries = sorted({i for (i,) in s.execute(select(Instrument.industry).where(Instrument.industry.is_not(None)).distinct())})
    excluded_count = s.scalar(select(func.count()).select_from(ScoreSnapshot).where(
        ScoreSnapshot.as_of_date == sd, ScoreSnapshot.group == group, ScoreSnapshot.eligible.is_(False)))
    return {"as_of_date": str(sd), "rows": out[:limit], "total_rows": len(out), "industries": industries,
            "excluded_count": excluded_count,
            "note": "總分為同組百分位加權平均（0–100），不是上漲機率；缺失分項權重已重新正規化並揭露覆蓋率。"}


# ------------------------------------------------------------------ instrument detail
def _get_inst(s: Session, iid: int) -> Instrument:
    inst = s.get(Instrument, iid)
    if inst is None:
        raise HTTPException(404, "找不到此標的")
    return inst


@app.get("/api/instruments/{iid}")
def instrument_detail(iid: int, s: Session = Depends(db)):
    inst = _get_inst(s, iid)
    sc = s.scalar(select(ScoreSnapshot).where(ScoreSnapshot.instrument_id == iid).order_by(ScoreSnapshot.as_of_date.desc()).limit(1))
    fs = s.scalar(select(FeatureSnapshot).where(FeatureSnapshot.instrument_id == iid).order_by(FeatureSnapshot.as_of_date.desc()).limit(1))
    preds = []
    for h in (20, 60):
        p = s.scalar(select(Prediction).where(Prediction.instrument_id == iid, Prediction.horizon_days == h)
                     .order_by(Prediction.as_of_date.desc(), Prediction.id.desc()).limit(1))
        if p:
            mr = s.get(ModelRun, p.model_run_id)
            tm = (mr.metrics or {}).get("test", {})
            preds.append({
                "horizon_days": h, "as_of_date": str(p.as_of_date), "generated_at": _iso(p.generated_at),
                "target_definition": p.target_definition, "expected_return": p.expected_return, "lower": p.lower,
                "upper": p.upper, "prob_up": p.prob_up, "risk_flags": p.risk_flags,
                "model": {"id": mr.id, "version": mr.model_version, "beats_baseline": mr.beats_baseline,
                          "interval_calibrated": mr.interval_calibrated, "prob_calibrated": mr.prob_calibrated,
                          "status": mr.status, "test_period": [str(mr.test_start), str(mr.test_end)],
                          "test_rank_ic": tm.get("model", {}).get("rank_ic"),
                          "baseline_rank_ic": tm.get("momentum", {}).get("rank_ic"),
                          "interval_coverage": (mr.metrics or {}).get("interval", {}).get("coverage")},
            })
    past = s.scalars(select(Prediction).where(Prediction.instrument_id == iid, Prediction.actual_outcome.is_not(None))
                     .order_by(Prediction.as_of_date.desc()).limit(20)).all()
    last_px = s.scalar(select(DailyPrice).where(DailyPrice.instrument_id == iid).order_by(DailyPrice.trade_date.desc()).limit(1))
    rev = s.scalars(select(Fundamental).where(Fundamental.instrument_id == iid, Fundamental.metric_code == "revenue_month")
                    .order_by(Fundamental.period_end.desc()).limit(25)).all()
    eps = s.scalars(select(Fundamental).where(Fundamental.instrument_id == iid, Fundamental.metric_code == "eps_q")
                    .order_by(Fundamental.period_end.desc()).limit(8)).all()
    flows = s.scalars(select(Flow).where(Flow.instrument_id == iid).order_by(Flow.trade_date.desc()).limit(60)).all()
    issues = s.scalars(select(DataQualityIssue).where(DataQualityIssue.instrument_id == iid, DataQualityIssue.resolved.is_(False))
                       .order_by(DataQualityIssue.detected_at.desc()).limit(20)).all()
    wl = s.get(Watchlist, iid)
    quote = s.get(Quote, iid)
    return {
        "instrument": inst_dict(inst),
        "score": score_dict(sc),
        "features": fs.values if fs else {},
        "feature_date": str(fs.as_of_date) if fs else None,
        "feature_version": fs.feature_version if fs else None,
        "predictions": preds,
        "past_predictions": [{"as_of_date": str(p.as_of_date), "horizon_days": p.horizon_days,
                              "expected_return": p.expected_return, "lower": p.lower, "upper": p.upper,
                              "actual_outcome": p.actual_outcome} for p in past],
        "last_price": None if not last_px else {
            "date": str(last_px.trade_date), "close": last_px.close, "provider": last_px.provider,
            "retrieved_at": _iso(last_px.retrieved_at), "source_updated_at": _iso(last_px.source_updated_at)},
        "revenue": [{"period_end": str(r.period_end), "value": r.value, "public_at": str(r.public_at),
                     "public_at_basis": r.public_at_basis, "provider": r.provider} for r in reversed(rev)],
        "eps": [{"period_end": str(r.period_end), "value": r.value, "public_at": str(r.public_at), "provider": r.provider}
                for r in reversed(eps)],
        "flows": [{"date": str(f.trade_date), "foreign_net": f.foreign_net, "trust_net": f.trust_net, "dealer_net": f.dealer_net,
                   "margin_balance": f.margin_balance} for f in reversed(flows)],
        "quality_issues": [{"id": i.id, "code": i.issue_code, "severity": i.severity, "date": str(i.trade_date) if i.trade_date else None,
                            "details": i.details} for i in issues],
        "watchlist": {"in": wl is not None, "note": wl.note if wl else ""},
        "quote": None if not quote else {"price": quote.price, "change": quote.change, "quote_time": _iso(quote.quote_time),
                                         "is_realtime": quote.is_realtime, "provider": quote.provider,
                                         "retrieved_at": _iso(quote.retrieved_at)},
    }


@app.get("/api/instruments/{iid}/prices")
def instrument_prices(iid: int, days: int = Query(250, ge=20, le=5000), adjusted: bool = True, s: Session = Depends(db)):
    _get_inst(s, iid)
    rows = s.execute(select(DailyPrice.trade_date, DailyPrice.open, DailyPrice.high, DailyPrice.low, DailyPrice.close,
                            DailyPrice.volume, DailyPrice.adj_factor)
                     .where(DailyPrice.instrument_id == iid).order_by(DailyPrice.trade_date.desc()).limit(days + 60)).all()
    if not rows:
        return {"adjusted": adjusted, "rows": []}
    df = pd.DataFrame(rows, columns=["d", "o", "h", "l", "c", "v", "f"]).iloc[::-1]
    k = df["f"] if adjusted else 1.0
    for col in ["o", "h", "l", "c"]:
        df[col] = (df[col] * k).round(3)
    df["ma20"] = df["c"].rolling(20).mean().round(3)
    df["ma60"] = df["c"].rolling(60).mean().round(3)
    df = df.tail(days)
    return {"adjusted": adjusted, "rows": [
        {"d": str(r.d), "o": r.o, "h": r.h, "l": r.l, "c": r.c, "v": r.v,
         "ma20": None if pd.isna(r.ma20) else r.ma20, "ma60": None if pd.isna(r.ma60) else r.ma60}
        for r in df.itertuples()]}


@app.post("/api/instruments/{iid}/report")
def instrument_report(iid: int, force: bool = False, s: Session = Depends(db)):
    _get_inst(s, iid)
    rep = get_report(s, iid, force=force)
    if rep is None:
        raise HTTPException(409, "此標的尚無評分資料，無法產生報告")
    return rep


class ExcludeBody(BaseModel):
    reason: str


@app.post("/api/instruments/{iid}/exclude")
def exclude(iid: int, body: ExcludeBody, s: Session = Depends(db)):
    inst = _get_inst(s, iid)
    inst.excluded_reason = body.reason[:120] or "使用者停用"
    return inst_dict(inst)


@app.delete("/api/instruments/{iid}/exclude")
def unexclude(iid: int, s: Session = Depends(db)):
    inst = _get_inst(s, iid)
    inst.excluded_reason = None
    return inst_dict(inst)


# ------------------------------------------------------------------ watchlist
class WatchBody(BaseModel):
    instrument_id: int
    note: str = ""


@app.get("/api/watchlist")
def watchlist(s: Session = Depends(db)):
    sd = latest_score_date(s)
    prev_sd = s.scalar(select(func.max(ScoreSnapshot.as_of_date)).where(ScoreSnapshot.as_of_date < sd)) if sd else None
    out = []
    for w, inst in s.execute(select(Watchlist, Instrument).join(Instrument, Instrument.instrument_id == Watchlist.instrument_id)
                             .order_by(Watchlist.added_at)).all():
        sc = s.scalar(select(ScoreSnapshot).where(ScoreSnapshot.instrument_id == inst.instrument_id,
                                                  ScoreSnapshot.as_of_date == sd)) if sd else None
        psc = s.scalar(select(ScoreSnapshot).where(ScoreSnapshot.instrument_id == inst.instrument_id,
                                                   ScoreSnapshot.as_of_date == prev_sd)) if prev_sd else None
        last_px = s.execute(select(DailyPrice.trade_date, DailyPrice.close).where(DailyPrice.instrument_id == inst.instrument_id)
                            .order_by(DailyPrice.trade_date.desc()).limit(1)).first()
        q = s.get(Quote, inst.instrument_id)
        out.append({**inst_dict(inst), "note": w.note, "added_at": _iso(w.added_at), "score": score_dict(sc),
                    "score_change": (sc.total - psc.total) if sc and psc and sc.total is not None and psc.total is not None else None,
                    "last_price": {"date": str(last_px[0]), "close": last_px[1]} if last_px else None,
                    "quote": None if not q else {"price": q.price, "change": q.change, "quote_time": _iso(q.quote_time),
                                                 "provider": q.provider, "is_realtime": q.is_realtime}})
    return {"as_of_date": str(sd) if sd else None, "rows": out}


@app.post("/api/watchlist")
def watch_add(body: WatchBody, s: Session = Depends(db)):
    _get_inst(s, body.instrument_id)
    w = s.get(Watchlist, body.instrument_id)
    if w is None:
        s.add(Watchlist(instrument_id=body.instrument_id, note=body.note))
    else:
        w.note = body.note
    return {"ok": True}


@app.patch("/api/watchlist/{iid}")
def watch_note(iid: int, body: WatchBody, s: Session = Depends(db)):
    w = s.get(Watchlist, iid)
    if w is None:
        raise HTTPException(404, "不在自選股中")
    w.note = body.note
    return {"ok": True}


@app.delete("/api/watchlist/{iid}")
def watch_remove(iid: int, s: Session = Depends(db)):
    w = s.get(Watchlist, iid)
    if w:
        s.delete(w)
    return {"ok": True}


# ------------------------------------------------------------------ intraday quotes
class QuoteBody(BaseModel):
    instrument_ids: list[int]


@app.post("/api/quotes/refresh")
def refresh_quotes(body: QuoteBody, s: Session = Depends(db)):
    from ..sources.base import SourceError
    from ..sources.fugle import Fugle

    if settings.demo_mode:
        raise HTTPException(400, "示範模式不提供盤中報價")
    fg = Fugle()
    if not fg.enabled:
        raise HTTPException(400, "未設定 FUGLE_API_KEY，盤中刷新停用（可至 Fugle 開發者頁面免費申請）")
    ids = body.instrument_ids[:30]  # 免費方案 60 次/分，單次最多 30 檔
    out, errors = [], []
    for iid in ids:
        inst = s.get(Instrument, iid)
        if not inst:
            continue
        try:
            q = fg.quote(inst.symbol)
            row = s.get(Quote, iid) or Quote(instrument_id=iid, provider=fg.provider)
            row.price, row.change, row.volume = q["price"], q["change"], q["volume"]
            row.quote_time, row.is_realtime, row.provider, row.retrieved_at = q["quote_time"], q["is_realtime"], q["provider"], utcnow()
            s.merge(row)
            out.append({"instrument_id": iid, "symbol": inst.symbol, **{k: q[k] for k in ("price", "change", "is_realtime", "provider")},
                        "quote_time": _iso(q["quote_time"])})
        except SourceError as exc:
            errors.append({"instrument_id": iid, "symbol": inst.symbol, "error": str(exc)[:200]})
    return {"quotes": out, "errors": errors, "note": "僅更新報價欄位，不重新計算評分與模型"}


# ------------------------------------------------------------------ models & backtests
@app.get("/api/models")
def models(s: Session = Depends(db)):
    rows = s.scalars(select(ModelRun).order_by(ModelRun.id.desc()).limit(20)).all()
    out = []
    for m in rows:
        filled = s.execute(select(func.count(), func.avg(func.abs(Prediction.expected_return - Prediction.actual_outcome)))
                           .where(Prediction.model_run_id == m.id, Prediction.actual_outcome.is_not(None))).first()
        out.append({"id": m.id, "model_version": m.model_version, "horizon_days": m.horizon_days, "created_at": _iso(m.created_at),
                    "status": m.status, "beats_baseline": m.beats_baseline, "interval_calibrated": m.interval_calibrated,
                    "prob_calibrated": m.prob_calibrated, "periods": {"train": [str(m.train_start), str(m.train_end)],
                                                                      "test": [str(m.test_start), str(m.test_end)]},
                    "data_version": m.data_version, "params": m.params, "metrics": m.metrics,
                    "live": {"matured": filled[0] if filled else 0, "mae": filled[1] if filled else None}})
    return out


@app.get("/api/backtests")
def backtests(s: Session = Depends(db)):
    rows = s.scalars(select(BacktestRun).order_by(BacktestRun.created_at.desc()).limit(10)).all()
    return [{"run_id": b.run_id, "name": b.name, "group": b.group, "created_at": _iso(b.created_at), "period": b.period,
             "assumptions": b.assumptions, "metrics": b.metrics, "equity_curve": b.equity_curve} for b in rows]


@app.get("/api/backtests/{run_id}/export.csv")
def backtest_export(run_id: str, s: Session = Depends(db)):
    b = s.get(BacktestRun, run_id)
    if b is None:
        raise HTTPException(404, "找不到回測")
    buf = io.StringIO()
    w = csv.writer(buf)
    keys = list(b.equity_curve.keys())
    dates = sorted({p["d"] for k in keys for p in b.equity_curve[k]})
    lut = {k: {p["d"]: p["v"] for p in b.equity_curve[k]} for k in keys}
    w.writerow(["date"] + keys)
    for d in dates:
        w.writerow([d] + [lut[k].get(d, "") for k in keys])
    buf.seek(0)
    return StreamingResponse(iter(["﻿" + buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f"attachment; filename=backtest_{run_id}.csv"})


# ------------------------------------------------------------------ health
@app.get("/api/health")
def health(s: Session = Depends(db)):
    issues = s.execute(select(DataQualityIssue, Instrument.symbol, Instrument.name)
                       .outerjoin(Instrument, Instrument.instrument_id == DataQualityIssue.instrument_id)
                       .where(DataQualityIssue.resolved.is_(False))
                       .order_by(DataQualityIssue.detected_at.desc()).limit(300)).all()
    summary = s.execute(select(DataQualityIssue.issue_code, DataQualityIssue.severity, func.count())
                        .where(DataQualityIssue.resolved.is_(False)).group_by(DataQualityIssue.issue_code, DataQualityIssue.severity)).all()
    d = latest_price_date(s)
    n_active = s.scalar(select(func.count()).select_from(Instrument).where(Instrument.status == "active"))
    n_today = s.scalar(select(func.count()).select_from(DailyPrice).where(DailyPrice.trade_date == d)) if d else 0
    excluded = s.scalars(select(Instrument).where(Instrument.excluded_reason.is_not(None))).all()
    return {
        "datasets": last_runs(s),
        "latest_price_date": str(d) if d else None,
        "coverage": {"active_instruments": n_active, "priced_latest": n_today,
                     "missing_rate": (1 - n_today / n_active) if n_active else None},
        "issue_summary": [{"code": c, "severity": sv, "count": n} for c, sv, n in summary],
        "issues": [{"id": i.id, "dataset": i.dataset, "code": i.issue_code, "severity": i.severity,
                    "date": str(i.trade_date) if i.trade_date else None, "symbol": sym, "name": nm,
                    "instrument_id": i.instrument_id, "details": i.details, "detected_at": _iso(i.detected_at)}
                   for i, sym, nm in issues],
        "excluded": [inst_dict(i) for i in excluded],
        "job": jobs.STATE,
        "providers": PROVIDER_NOTES,
    }


@app.post("/api/quality/{issue_id}/resolve")
def resolve_issue(issue_id: int, s: Session = Depends(db)):
    it = s.get(DataQualityIssue, issue_id)
    if it is None:
        raise HTTPException(404, "找不到此問題")
    it.resolved = True
    return {"ok": True}


class JobBody(BaseModel):
    symbols: list[str] | None = None
    datasets: list[str] | None = None
    max_requests: int | None = None
    retrain: bool | None = None


@app.post("/api/jobs/{name}")
def trigger_job(name: str, body: JobBody | None = None):
    if name not in jobs.JOBS:
        raise HTTPException(404, "未知的工作")
    body = body or JobBody()
    kw = {}
    if name == "backfill":
        kw = {"symbols": body.symbols, "datasets": body.datasets, "max_requests": body.max_requests}
    elif name == "daily" and body.retrain is not None:
        kw = {"retrain": body.retrain}
    res = jobs.run_job_async(name, **kw)
    if not res.get("accepted"):
        return JSONResponse(res, status_code=409)
    return res


# ------------------------------------------------------------------ frontend
if (STATIC_DIR / "assets").exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")


@app.get("/{full_path:path}", include_in_schema=False)
def spa(full_path: str):
    if full_path.startswith("api/"):
        raise HTTPException(404)
    f = STATIC_DIR / full_path
    if full_path and f.is_file():
        return FileResponse(f)
    index = STATIC_DIR / "index.html"
    if index.exists():
        return FileResponse(index)
    return JSONResponse({"message": "前端尚未建置：請在 frontend 目錄執行 npm install && npm run build"}, status_code=200)
