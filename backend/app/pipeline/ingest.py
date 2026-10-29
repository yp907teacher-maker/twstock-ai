"""匯入流程：官方每日增量、FinMind 歷史回填（可續傳）、示範資料。"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import BackfillState, DailyPrice, DataQualityIssue, Instrument, MarketIndex
from ..sources.base import RateLimitError, SourceError
from . import quality
from .store import (
    is_supported,
    InstrumentResolver,
    finish_run,
    finmind_flows_to_rows,
    mark_delisted,
    recompute_adj_factors,
    start_run,
    upsert,
    upsert_instruments,
    write_actions,
    write_financials,
    write_flows,
    write_prices,
    write_revenue,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------- 官方每日增量
def infer_actions(session: Session, rows: list[dict], res: InstrumentResolver, provider: str) -> list[dict]:
    """以「收盤 − 漲跌 = 參考價」與前一日收盤比較，推算除權息／減資（免費、無需額外呼叫）。

    參考價明顯偏離前收（> 0.2%）才視為事件；推算結果標記 provider=*_INFERRED，
    並記一筆 info 等級品質紀錄，之後可用 FinMind 除權息結果校正。
    """
    out = []
    for r in rows:
        if r.get("close") is None or r.get("change") is None:
            continue
        iid = res.get(r["symbol"])
        if iid is None:
            continue
        prev = session.execute(
            select(DailyPrice.close).where(DailyPrice.instrument_id == iid, DailyPrice.trade_date < r["trade_date"])
            .order_by(DailyPrice.trade_date.desc()).limit(1)
        ).scalar()
        if not prev:
            continue
        ref = r["close"] - r["change"]
        if ref <= 0:
            continue
        ratio = ref / prev
        if abs(ratio - 1) > 0.002 and 0.3 < ratio < 3:
            out.append({
                "symbol": r["symbol"], "ex_date": r["trade_date"],
                "kind": "dividend" if ratio < 1 else "capital_reduction",
                "before_price": prev, "after_price": round(ref, 4), "cash_dividend": None, "stock_dividend": None,
            })
    if out:
        write_actions(session, out, res, provider + "_INFERRED")
        for a in out:
            session.add(DataQualityIssue(dataset="corporate_action", instrument_id=res.get(a["symbol"]), trade_date=a["ex_date"],
                                         issue_code="inferred_corporate_action", severity="info",
                                         details={"before": a["before_price"], "after": a["after_price"], "symbol": a["symbol"]}))
    return out


def daily_official(session: Session) -> dict:
    from ..sources.official import TpexOpenAPI, TwseOpenAPI

    summary = {}
    res = InstrumentResolver(session)
    touched: set[int] = set()
    for src_cls, dataset in ((TwseOpenAPI, "twse_daily_quotes"), (TpexOpenAPI, "tpex_daily_quotes")):
        src = src_cls()
        run = start_run(session, "daily", src.provider, dataset)
        try:
            rows = src.daily_quotes()
            issues = quality.check_price_batch(rows, dataset)
            bad = {(i["symbol"], i["trade_date"]) for i in issues if i["severity"] == "error"}
            good = [r for r in rows if (r["symbol"], r["trade_date"]) not in bad]
            good = [r for r in good if is_supported(r["symbol"])]
            for r in good:
                res.ensure(r["symbol"], r.get("name", ""), r["market"], src.provider)
            acts = infer_actions(session, good, res, src.provider)
            n = write_prices(session, good, res)
            quality.save_issues(session, issues, res)
            data_date = max((r["trade_date"] for r in rows), default=None)
            touched |= {res.get(a["symbol"]) for a in acts}
            finish_run(run, "success" if not bad else "partial", n, data_date,
                       f"{len(issues)} 筆品質問題，{len(bad)} 筆因錯誤未寫入；推算除權息 {len(acts)} 筆")
            summary[dataset] = {"rows": n, "date": str(data_date), "issues": len(issues)}
        except (SourceError, Exception) as exc:  # noqa: BLE001  單一來源失敗不影響其他來源
            log.exception("%s 失敗", dataset)
            finish_run(run, "failed", 0, None, str(exc))
            summary[dataset] = {"error": str(exc)}
    if touched:
        recompute_adj_factors(session, [t for t in touched if t])
    # 大盤指數
    run = start_run(session, "daily", "TWSE_OPENAPI", "twse_index")
    try:
        idx = TwseOpenAPI().index_summary()
        rows = [{"name": i["name"], "trade_date": i["date"], "close": i["close"], "change_pct": i["change_pct"],
                 "provider": i["provider"]} for i in idx if i.get("date")]
        upsert(session, MarketIndex, rows, ["name", "trade_date"])
        finish_run(run, "success", len(rows), rows[0]["trade_date"] if rows else None)
    except Exception as exc:  # noqa: BLE001
        finish_run(run, "failed", 0, None, str(exc))
    return summary


# ---------------------------------------------------------------- FinMind 主檔與回填
def sync_instruments_finmind(session: Session) -> dict:
    from ..sources.finmind import FinMind

    fm = FinMind()
    run = start_run(session, "instruments", fm.provider, "TaiwanStockInfo")
    try:
        rows = fm.stock_info()
        # 同一代號可能因多產業別出現多筆，以最後一筆為準
        dedup = {r["symbol"]: r for r in rows}
        n = upsert_instruments(session, list(dedup.values()), fm.provider)
        dl = fm.delisting()
        m = mark_delisted(session, dl)
        finish_run(run, "success", n, None, f"下市櫃標記 {m} 檔")
        return {"instruments": n, "delisted": m}
    except Exception as exc:  # noqa: BLE001
        finish_run(run, "failed", 0, None, str(exc))
        return {"error": str(exc)}


BACKFILL_DATASETS = ("prices", "actions", "revenue", "financials", "flows")
# 首次回填後的補抓週期（天）：每日收盤價由官方 OpenAPI 更新，FinMind 只做低頻補漏，
# 避免每天對全市場重複請求（免費版有每小時次數上限）。
REFRESH_DAYS = {"prices": 30, "actions": 30, "revenue": 30, "financials": 45, "flows": 7}


def backfill_finmind(session_factory, symbols: list[str] | None = None, datasets=BACKFILL_DATASETS,
                     start: date | None = None, max_requests: int | None = None) -> dict:
    """逐檔回填，可中斷續傳（BackfillState 記錄每檔每資料集已完成到哪天）。

    免費版 600 次/小時：全市場約 2,400 檔 × 每檔 1–3 次請求，全部回填需數小時至一天；
    建議先回填自選股與流動性較高的標的。
    """
    from ..sources.finmind import FinMind

    fm = FinMind()
    start = start or date.fromisoformat(settings.backfill_start)
    today = date.today()
    stats = {"requests": 0, "rows": 0, "symbols": 0, "stopped": None}
    with session_factory() as s:
        # 含已下市標的（生存者偏誤）；下市者只抓到下市日
        q = select(Instrument)
        if symbols:
            q = q.where(Instrument.symbol.in_(symbols))
        # 優先順序：最新成交金額高者先回填（流動性高的先能評分），已下市者最後
        latest = s.scalar(select(func.max(DailyPrice.trade_date)))
        turn = dict(s.execute(select(DailyPrice.instrument_id, DailyPrice.turnover)
                              .where(DailyPrice.trade_date == latest)).all()) if latest else {}
        all_i = list(s.scalars(q))
        all_i.sort(key=lambda i: (i.status != "active", -(turn.get(i.instrument_id) or 0), i.symbol))
        insts = [(i.instrument_id, i.symbol, i.asset_type) for i in all_i]
        run = start_run(s, "backfill", fm.provider, ",".join(datasets))
        s.commit()
        run_id = run.id
    for iid, sym, asset_type in insts:
        for ds in datasets:
            if ds in ("revenue", "financials") and asset_type == "ETF":
                continue
            if max_requests is not None and stats["requests"] >= max_requests:
                stats["stopped"] = "max_requests"
                break
            with session_factory() as s:
                st = s.get(BackfillState, (ds, iid))
                begin = (st.done_until + timedelta(days=1)) if st and st.done_until else start
                if st and st.done_until and st.done_until >= today - timedelta(days=REFRESH_DAYS.get(ds, 7)):
                    continue
                if begin > today:
                    continue
                res = InstrumentResolver(s)
                try:
                    if ds == "prices":
                        rows = fm.prices(sym, begin, today)
                        n = write_prices(s, rows, res)
                        stats["requests"] += 1
                    elif ds == "actions":
                        rows = fm.dividends(sym, begin, today) + fm.capital_reductions(sym, begin, today)
                        n = write_actions(s, rows, res, fm.provider)
                        recompute_adj_factors(s, [iid])
                        stats["requests"] += 2
                    elif ds == "revenue":
                        n = write_revenue(s, fm.month_revenue(sym, begin - timedelta(days=400), today), res, fm.provider)
                        stats["requests"] += 1
                    elif ds == "financials":
                        n = write_financials(s, fm.financial_statements(sym, begin - timedelta(days=500), today), res, fm.provider)
                        stats["requests"] += 2
                    elif ds == "flows":
                        rows = finmind_flows_to_rows(sym, fm.institutional(sym, begin, today), fm.margin(sym, begin, today))
                        n = write_flows(s, rows, res, fm.provider)
                        stats["requests"] += 2
                    else:
                        continue
                    stats["rows"] += n
                    if st is None:
                        st = BackfillState(dataset=ds, instrument_id=iid)
                        s.add(st)
                    st.done_until = today
                    s.commit()
                except RateLimitError as exc:
                    s.rollback()
                    stats["stopped"] = f"rate_limit: {exc}"
                    break
                except SourceError as exc:
                    s.rollback()
                    s.add(DataQualityIssue(dataset=f"backfill_{ds}", instrument_id=iid, issue_code="source_error",
                                           severity="warn", details={"symbol": sym, "error": str(exc)[:300]}))
                    s.commit()
        stats["symbols"] += 1
        if stats["stopped"]:
            break
    with session_factory() as s:
        stats["remaining"] = backfill_remaining(s, datasets)
    with session_factory() as s:
        from ..models import IngestRun

        run = s.get(IngestRun, run_id)
        finish_run(run, "partial" if stats["stopped"] else "success", stats["rows"], today,
                   f"{stats['symbols']} 檔、{stats['requests']} 次請求；停止原因：{stats['stopped'] or '無'}")
        s.commit()
    return stats


def backfill_remaining(session: Session, datasets=BACKFILL_DATASETS) -> int:
    """尚未回填到今天的（標的 × 資料集）數量；0 表示全部完成。"""
    today = date.today()
    insts = session.execute(select(Instrument.instrument_id, Instrument.asset_type)).all()
    done = {(d, i) for d, i, u in session.execute(select(BackfillState.dataset, BackfillState.instrument_id,
                                                         BackfillState.done_until))
            if u and u >= today - timedelta(days=REFRESH_DAYS.get(d, 7))}
    n = 0
    for iid, at in insts:
        for ds in datasets:
            if ds in ("revenue", "financials") and at == "ETF":
                continue
            if (ds, iid) not in done:
                n += 1
    return n


# ---------------------------------------------------------------- 示範資料
def load_demo(session: Session, end: date | None = None) -> dict:
    from ..sources import demo

    data = demo.generate(end=end)
    rows = [
        {"symbol": i.symbol, "name": i.name, "market": i.market, "industry": i.industry}
        for i in data["universe"]
    ]
    upsert_instruments(session, rows, "DEMO")
    session.flush()
    res = InstrumentResolver(session)
    for i in data["universe"]:
        inst = session.get(Instrument, res.get(i.symbol))
        inst.asset_type, inst.etf_kind = i.asset_type, i.etf_kind
        if i.delist_date:
            inst.status, inst.valid_to = "delisted", i.delist_date
    run = start_run(session, "demo", "DEMO", "all")
    issues = quality.check_price_batch(data["prices"], "demo_prices")
    n = write_prices(session, data["prices"], res)
    quality.save_issues(session, issues, res)
    write_actions(session, data["actions"], res, "DEMO")
    write_revenue(session, data["revenue"], res, "DEMO")
    write_financials(session, data["financials"], res, "DEMO")
    write_flows(session, data["flows"], res, "DEMO")
    session.flush()
    recompute_adj_factors(session)
    # 示範大盤：以示範大盤50 ETF 的還原價表示
    etf = res.get("DE050")
    if etf:
        ps = session.execute(select(DailyPrice.trade_date, DailyPrice.close, DailyPrice.adj_factor)
                             .where(DailyPrice.instrument_id == etf).order_by(DailyPrice.trade_date)).all()
        prev = None
        idx_rows = []
        for d, c, f in ps:
            v = c * f * 100
            idx_rows.append({"name": "示範加權指數", "trade_date": d, "close": round(v, 2),
                             "change_pct": None if prev is None else round((v / prev - 1) * 100, 2), "provider": "DEMO"})
            prev = v
        upsert(session, MarketIndex, idx_rows, ["name", "trade_date"])
    last = max(r["trade_date"] for r in data["prices"])
    finish_run(run, "success", n, last, "示範資料（合成），非真實行情")
    return {"prices": n, "last_date": str(last)}


def latest_trade_date(session: Session) -> date | None:
    return session.scalar(select(func.max(DailyPrice.trade_date)))
