"""資料品質檢查（FR-03）。

檢查：缺值、重複、價格邏輯、交易日、異常跳動、來源延遲、筆數驟降、個股缺漏。
error 等級的問題會讓該標的在近期內不產生正式分數與預測。
"""
from __future__ import annotations

from collections import Counter
from datetime import date, timedelta

import pandas as pd
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import CorporateAction, DailyPrice, DataQualityIssue, Instrument

LIMIT_MOVE = 0.105  # 台股漲跌幅 10%，加上四捨五入容忍


def check_price_batch(rows: list[dict], dataset: str) -> list[dict]:
    """匯入前對一批行情檢查；回傳 issue dict（尚未寫入）。"""
    issues = []
    keys = Counter((r["symbol"], r["trade_date"]) for r in rows)
    for (sym, d), c in keys.items():
        if c > 1:
            issues.append(_issue(dataset, sym, d, "duplicate_row", "warn", {"count": c}))
    for r in rows:
        d, sym = r["trade_date"], r["symbol"]
        if d.weekday() >= 5:
            issues.append(_issue(dataset, sym, d, "non_trading_day", "error", {"weekday": d.weekday()}))
        o, h, l, c = r.get("open"), r.get("high"), r.get("low"), r.get("close")
        if c is None:
            continue  # 無成交另行處理
        if None in (o, h, l):
            issues.append(_issue(dataset, sym, d, "missing_ohlc", "warn", {"open": o, "high": h, "low": l}))
            continue
        if min(o, h, l, c) <= 0 or h < max(o, c) - 1e-9 or l > min(o, c) + 1e-9 or h < l:
            issues.append(_issue(dataset, sym, d, "price_logic", "error", {"open": o, "high": h, "low": l, "close": c}))
        if r.get("volume") is not None and r["volume"] < 0:
            issues.append(_issue(dataset, sym, d, "negative_volume", "error", {"volume": r["volume"]}))
    return issues


def _issue(dataset, symbol, d, code, severity, details) -> dict:
    return {"dataset": dataset, "symbol": symbol, "trade_date": d, "issue_code": code, "severity": severity, "details": details}


def save_issues(session: Session, issues: list[dict], resolver) -> int:
    n = 0
    for it in issues:
        iid = resolver.get(it["symbol"]) if it.get("symbol") else None
        session.add(
            DataQualityIssue(
                dataset=it["dataset"],
                instrument_id=iid,
                trade_date=it.get("trade_date"),
                issue_code=it["issue_code"],
                severity=it["severity"],
                details={**it["details"], **({"symbol": it["symbol"]} if it.get("symbol") else {})},
            )
        )
        n += 1
    return n


def run_post_load_checks(session: Session, as_of: date | None = None, lookback_days: int = 30) -> dict:
    """匯入後全面檢查最近 lookback_days 的資料；舊的未解決同類問題先清除再重建。"""
    latest = session.scalar(select(func.max(DailyPrice.trade_date)))
    if latest is None:
        return {"latest": None, "issues": 0}
    as_of = as_of or latest
    start = as_of - timedelta(days=lookback_days * 2)
    session.execute(
        delete(DataQualityIssue).where(
            DataQualityIssue.dataset == "post_load",
            DataQualityIssue.resolved.is_(False),
        )
    )
    q = select(
        DailyPrice.instrument_id, DailyPrice.trade_date, DailyPrice.close, DailyPrice.adj_factor
    ).where(DailyPrice.trade_date >= start, DailyPrice.trade_date <= as_of)
    df = pd.DataFrame(session.execute(q).all(), columns=["iid", "d", "close", "f"])
    issues = 0
    if df.empty:
        return {"latest": str(latest), "issues": 0}

    # 1) 異常跳動：還原後日報酬超過漲跌幅限制（非除權息日）
    acts = {(a.instrument_id, a.ex_date) for a in session.scalars(select(CorporateAction).where(CorporateAction.ex_date >= start))}
    df = df.sort_values(["iid", "d"])
    df["adj"] = df["close"] * df["f"]
    df["ret"] = df.groupby("iid")["adj"].pct_change()
    jumps = df[(df["ret"].abs() > LIMIT_MOVE)]
    for r in jumps.itertuples():
        if (r.iid, r.d) in acts:
            continue
        session.add(DataQualityIssue(dataset="post_load", instrument_id=int(r.iid), trade_date=r.d,
                                     issue_code="abnormal_jump", severity="warn", details={"return": round(float(r.ret), 4)}))
        issues += 1

    # 2) 市場層級：最新日筆數驟降（> 20%）
    counts = df.groupby("d")["iid"].nunique().sort_index()
    if len(counts) >= 2:
        last, prev = counts.iloc[-1], counts.iloc[-6:-1].median()
        if prev and last < 0.8 * prev:
            session.add(DataQualityIssue(dataset="post_load", trade_date=counts.index[-1], issue_code="coverage_drop",
                                         severity="error", details={"rows": int(last), "median_prev": float(prev)}))
            issues += 1

    # 3) 個股缺漏：市場有交易但該檔無資料（排除已下市與停用）
    market_days = set(counts[counts >= max(1, 0.5 * counts.median())].index)
    recent_days = sorted(d for d in market_days if d > as_of - timedelta(days=lookback_days))
    active = {i.instrument_id: i for i in session.scalars(select(Instrument).where(Instrument.status == "active"))}
    have = df.groupby("iid")["d"].apply(set).to_dict()
    for iid, inst in active.items():
        got = have.get(iid, set())
        if not got:
            continue  # 期間內完全無資料者由 stale 檢查處理
        first = min(got)
        missing = [d for d in recent_days if d >= first and d not in got]
        if missing:
            sev = "error" if len(missing) >= 3 else "warn"
            session.add(DataQualityIssue(dataset="post_load", instrument_id=iid, trade_date=missing[-1],
                                         issue_code="missing_days", severity=sev,
                                         details={"missing": [str(m) for m in missing[-10:]], "count": len(missing)}))
            issues += 1

    # 4) 資料過舊：個股最後交易日落後市場最新日超過 stale_days 個交易日
    all_days = sorted(market_days)
    pos = {d: i for i, d in enumerate(all_days)}
    for iid, inst in active.items():
        got = have.get(iid)
        if not got:
            continue
        lag = len(all_days) - 1 - pos.get(max(got), len(all_days) - 1)
        if lag > settings.stale_days:
            session.add(DataQualityIssue(dataset="post_load", instrument_id=iid, trade_date=max(got),
                                         issue_code="stale_data", severity="error", details={"lag_trading_days": int(lag)}))
            issues += 1
    return {"latest": str(latest), "issues": issues}


def blocking_instruments(session: Session, as_of: date, days: int = 10) -> dict[int, list[str]]:
    """近期有未解決 error 等級問題的標的 → 阻止正式預測。"""
    q = select(DataQualityIssue.instrument_id, DataQualityIssue.issue_code).where(
        DataQualityIssue.severity == "error",
        DataQualityIssue.resolved.is_(False),
        DataQualityIssue.instrument_id.is_not(None),
        DataQualityIssue.trade_date >= as_of - timedelta(days=days * 2),
    )
    out: dict[int, list[str]] = {}
    for iid, code in session.execute(q):
        out.setdefault(iid, [])
        if code not in out[iid]:
            out[iid].append(code)
    return out
