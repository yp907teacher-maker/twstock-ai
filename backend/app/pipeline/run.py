"""收盤後計算流程：特徵 → 評分 → 模型（週期性重訓）→ 預測快照 → 結果回填 → 回測。"""
from __future__ import annotations

import logging
import pickle
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import DATA_DIR, settings
from ..models import (
    BacktestRun,
    DailyPrice,
    FeatureSnapshot,
    Instrument,
    ModelRun,
    Prediction,
    ScoreSnapshot,
    utcnow,
)
from . import backtest, features, forecast, quality, scoring
from .store import finish_run, start_run, upsert

log = logging.getLogger(__name__)
MODEL_DIR = DATA_DIR / "models"
MODEL_DIR.mkdir(exist_ok=True)
HORIZONS = (1, 20, 60)


def _excluded(session: Session) -> dict[int, str]:
    return {i.instrument_id: i.excluded_reason for i in
            session.scalars(select(Instrument).where(Instrument.excluded_reason.is_not(None)))}


def _inactive(session: Session) -> set[int]:
    return {i.instrument_id for i in session.scalars(select(Instrument).where(Instrument.status != "active"))}


def compute(session: Session, retrain: bool | None = None, run_backtest: bool | None = None) -> dict:
    run = start_run(session, "compute", "internal", "features_scores_models")
    try:
        out = _compute(session, retrain, run_backtest)
        finish_run(run, "success", out.get("scored", 0), out.get("as_of"), _summary(out))
        return out
    except Exception as exc:
        finish_run(run, "failed", 0, None, str(exc))
        raise


def _compute(session: Session, retrain: bool | None, run_backtest: bool | None) -> dict:
    panel = features.load_panel(session)
    if not panel:
        return {"status": "no_data"}
    # 評分日：最近一個「多數標的都有收盤價」的交易日（避免某來源只先更新少數標的時誤用）
    counts = panel["close"].notna().sum(axis=1)
    ref = counts.iloc[-15:].max()
    as_of = counts[counts >= 0.6 * ref].index[-1]
    has_model = {h: session.scalar(select(ModelRun).where(ModelRun.horizon_days == h).order_by(ModelRun.id.desc()).limit(1))
                 for h in HORIZONS}
    if retrain is None:
        retrain = as_of.weekday() == 4 or any(m is None for m in has_model.values())
    if run_backtest is None:
        run_backtest = retrain or session.scalar(select(BacktestRun).limit(1)) is None

    full = retrain or run_backtest
    dates = None if full else [as_of]
    long_df = features.build_long(session, panel, dates=dates, horizons=HORIZONS if full else ())
    inactive = _inactive(session)
    day = long_df[(long_df["d"] == as_of) & (~long_df["iid"].isin(inactive))]

    # ---- 特徵與評分快照 ----
    upsert(session, FeatureSnapshot, features.snapshot_rows(day, as_of), ["instrument_id", "as_of_date", "feature_version"])
    blocked = quality.blocking_instruments(session, as_of.date())
    excluded = _excluded(session)
    sc = scoring.score_cross_section(day, blocked=blocked, excluded=excluded, detail=True)
    rows = []
    for r in sc.to_dict("records"):
        r["as_of_date"] = as_of.date()
        r["rank"] = None if r["rank"] is None or (isinstance(r["rank"], float) and np.isnan(r["rank"])) else int(r["rank"])
        rows.append(r)
    upsert(session, ScoreSnapshot, rows, ["instrument_id", "as_of_date", "score_version"])
    risk_by_iid = {r["instrument_id"]: r["risk_flags"] for r in rows}
    out = {"as_of": as_of.date(), "scored": len(rows), "eligible": int(sum(r["eligible"] for r in rows)),
           "retrained": bool(retrain), "backtest": bool(run_backtest)}

    # ---- 模型 ----
    for h in HORIZONS:
        mr = has_model[h]
        model_obj = None
        if retrain:
            ev = forecast.evaluate(long_df, h)
            if ev.get("status") != "ok":
                out[f"model_{h}"] = ev
                continue
            data = ev.pop("_data")
            metrics = {k: v for k, v in ev.items() if k not in ("params", "periods", "model_version", "status", "horizon")}
            mr = ModelRun(
                model_version=ev["model_version"], horizon_days=h, params=ev["params"],
                data_version=f"{as_of.date()}|rows={len(long_df)}",
                train_start=pd.Timestamp(ev["periods"]["train_start"]).date(),
                train_end=pd.Timestamp(ev["periods"]["train_end"]).date(),
                test_start=pd.Timestamp(ev["periods"]["test_start"]).date(),
                test_end=pd.Timestamp(ev["periods"]["test_end"]).date(),
                metrics=_json_safe(metrics), beats_baseline=ev["beats_baseline"],
                interval_calibrated=ev["interval_calibrated"], prob_calibrated=ev["prob_calibrated"],
                status="validated" if ev["beats_baseline"] else "not_better_than_baseline",
            )
            session.add(mr)
            session.flush()
            model_obj = forecast.fit_live(data, h)
            with open(MODEL_DIR / f"model_{mr.id}.pkl", "wb") as fh:
                pickle.dump(model_obj, fh)
            out[f"model_{h}"] = {"id": mr.id, "beats_baseline": mr.beats_baseline,
                                 "rank_ic": metrics["test"]["model"]["rank_ic"]}
        elif mr is not None:
            p = MODEL_DIR / f"model_{mr.id}.pkl"
            if p.exists():
                with open(p, "rb") as fh:
                    model_obj = pickle.load(fh)
        if model_obj is None or mr is None:
            continue
        # 最新一日預測：資料品質有 error、手動停用、歷史不足者不產生
        latest = forecast.prepare(long_df[long_df["d"] == as_of], step=1)
        latest = latest[~latest["iid"].isin(set(blocked) | set(excluded) | inactive)]
        latest = latest[latest["hist_days"] >= settings.min_history_days]
        if latest.empty:
            continue
        pr = model_obj.predict(latest)
        preds = []
        for (iid, grp), p in zip(latest[["iid", "group"]].itertuples(index=False), pr.itertuples(index=False)):
            flags = list(risk_by_iid.get(int(iid), []))
            if not mr.beats_baseline:
                flags.append("模型未證實優於基準，預測僅供參考")
            preds.append({
                "instrument_id": int(iid), "as_of_date": as_of.date(), "generated_at": utcnow(),
                "model_run_id": mr.id, "horizon_days": h, "target_definition": forecast.target_definition(h),
                "expected_return": float(p.pred), "lower": float(p.lo), "upper": float(p.hi),
                # 機率一律以校準後數值保存；是否顯示由 model_run.prob_calibrated 決定
                "prob_up": float(p.prob),
                "risk_flags": flags,
            })
        # 不覆寫既有預測：同鍵已存在則略過
        _insert_ignore(session, preds)
        out[f"predictions_{h}"] = len(preds)

    out["outcomes_filled"] = fill_outcomes(session, panel)

    # ---- 回測 ----
    if run_backtest and full:
        bench = session.scalar(select(Instrument.instrument_id).where(Instrument.symbol.in_(["0050", "DE050"])).limit(1))
        for grp in ("stock", "etf"):
            bt = backtest.run_backtest(long_df, panel, group=grp, benchmark_iid=bench)
            if bt.get("status") != "ok":
                out[f"backtest_{grp}"] = bt
                continue
            upsert(session, BacktestRun, [{
                "run_id": bt["run_id"], "created_at": utcnow(), "name": f"評分前 {bt['assumptions']['top_n']} 名等權（{grp}）",
                "group": grp, "period": bt["period"], "assumptions": bt["assumptions"],
                "metrics": _json_safe({k: v["metrics"] for k, v in bt["results"].items()}),
                "equity_curve": _json_safe({k: v["curve"] for k, v in bt["results"].items()}),
            }], ["run_id"])
            out[f"backtest_{grp}"] = bt["run_id"]
    return out


def _summary(out: dict) -> str:
    if out.get("status") == "no_data":
        return "尚無行情資料"
    parts = [f"評分 {out.get('scored', 0)} 檔（合格 {out.get('eligible', 0)}）"]
    for h in HORIZONS:
        m = out.get(f"model_{h}")
        if isinstance(m, dict) and "id" in m:
            parts.append(f"{h} 日模型 #{m['id']}（{'優於基準' if m['beats_baseline'] else '未證實優於基準'}）")
        elif isinstance(m, dict):
            parts.append(f"{h} 日模型未訓練：{m.get('reason', m.get('status'))}")
        if f"predictions_{h}" in out:
            parts.append(f"{h} 日預測 {out[f'predictions_{h}']} 筆")
    parts.append(f"回填實際結果 {out.get('outcomes_filled', 0)} 筆")
    if out.get("backtest"):
        parts.append("回測已更新")
    return "；".join(parts)


def _insert_ignore(session: Session, rows: list[dict]):
    if not rows:
        return
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    for i in range(0, len(rows), 1000):
        session.execute(insert(Prediction).values(rows[i:i + 1000]).on_conflict_do_nothing())


def fill_outcomes(session: Session, panel: dict[str, pd.DataFrame]) -> int:
    """結果成熟後回填 actual_outcome（不改動原預測欄位）。"""
    pending = session.scalars(select(Prediction).where(Prediction.actual_outcome.is_(None))).all()
    if not pending:
        return 0
    adj = panel["adj"]
    pos = {d.date(): i for i, d in enumerate(adj.index)}
    n = 0
    for p in pending:
        i0 = pos.get(p.as_of_date)
        if i0 is None or i0 + p.horizon_days >= len(adj.index) or p.instrument_id not in adj.columns:
            continue
        a0, a1 = adj.iloc[i0][p.instrument_id], adj.iloc[i0 + p.horizon_days][p.instrument_id]
        if pd.notna(a0) and pd.notna(a1) and a0 > 0:
            p.actual_outcome = float(a1 / a0 - 1)
            p.outcome_filled_at = datetime.utcnow()
            n += 1
    return n


def _json_safe(o):
    if isinstance(o, dict):
        return {str(k): _json_safe(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_json_safe(v) for v in o]
    if isinstance(o, (np.floating, float)):
        return None if not np.isfinite(o) else float(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    return o
