"""資料寫入：可重跑（upsert）、不產生重複紀錄。"""
from __future__ import annotations

import re
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    CorporateAction,
    DailyPrice,
    Flow,
    Fundamental,
    IngestRun,
    Instrument,
    utcnow,
)

LEVERAGED_PAT = re.compile(r"(正2|正二|槓桿|2X|2x)")
INVERSE_PAT = re.compile(r"(反1|反一|反向|-1X|-1x)")
BOND_PAT = re.compile(r"(債|Bond|BOND)")


def classify(symbol: str, name: str, industry: str | None) -> tuple[str, str | None]:
    """依代號與名稱判斷 asset_type 與 ETF 類型。"""
    is_etf = (industry or "").upper().find("ETF") >= 0 or symbol.startswith("00") or symbol.startswith("DE")
    if not is_etf:
        return "common_stock", None
    if symbol.endswith("L") or LEVERAGED_PAT.search(name):
        return "ETF", "leveraged"
    if symbol.endswith("R") or INVERSE_PAT.search(name):
        return "ETF", "inverse"
    if symbol.endswith("B") or BOND_PAT.search(name):
        return "ETF", "bond"
    return "ETF", "normal"


def upsert(session: Session, model, rows: list[dict], pk: list[str], chunk: int = 2000) -> int:
    """依資料庫方言做 INSERT ... ON CONFLICT DO UPDATE。"""
    if not rows:
        return 0
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    cols = [c.name for c in model.__table__.columns]
    n = 0
    for i in range(0, len(rows), chunk):
        part = [{k: v for k, v in r.items() if k in cols} for r in rows[i : i + chunk]]
        stmt = insert(model).values(part)
        update_cols = {c: stmt.excluded[c] for c in part[0].keys() if c not in pk}
        if update_cols:
            stmt = stmt.on_conflict_do_update(index_elements=pk, set_=update_cols)
        else:
            stmt = stmt.on_conflict_do_nothing(index_elements=pk)
        session.execute(stmt)
        n += len(part)
    return n


class InstrumentResolver:
    """symbol → 目前有效的 instrument_id；不存在時建立。"""

    def __init__(self, session: Session):
        self.s = session
        self.cache: dict[str, int] = {}
        # 同代號若有多筆（下市後代號重用），優先取目前有效者，否則取最近一筆
        rank: dict[str, tuple] = {}
        for inst in session.scalars(select(Instrument)):
            key = (inst.valid_to is None, inst.valid_to or date.max, inst.valid_from)
            if inst.symbol not in rank or key > rank[inst.symbol]:
                rank[inst.symbol] = key
                self.cache[inst.symbol] = inst.instrument_id

    def get(self, symbol: str) -> int | None:
        return self.cache.get(symbol)

    def ensure(self, symbol: str, name: str, market: str, provider: str, industry: str | None = None,
               valid_from: date | None = None) -> int:
        iid = self.cache.get(symbol)
        if iid:
            return iid
        asset_type, etf_kind = classify(symbol, name, industry)
        inst = Instrument(
            symbol=symbol,
            name=name or symbol,
            market=market,
            asset_type=asset_type,
            etf_kind=etf_kind,
            industry=industry,
            status="active",
            valid_from=valid_from or date(1900, 1, 1),
            provider=provider,
        )
        self.s.add(inst)
        self.s.flush()
        self.cache[symbol] = inst.instrument_id
        return inst.instrument_id


def upsert_instruments(session: Session, rows: list[dict], provider: str) -> int:
    """rows: symbol, name, market, industry。已存在則更新名稱／產業／市場。"""
    res = InstrumentResolver(session)
    n = 0
    for r in rows:
        iid = res.get(r["symbol"])
        if iid:
            inst = session.get(Instrument, iid)
            if r.get("name"):
                inst.name = r["name"]
            if r.get("industry"):
                inst.industry = r["industry"]
                inst.asset_type, inst.etf_kind = classify(inst.symbol, inst.name, inst.industry)
            if r.get("market"):
                inst.market = r["market"]
        else:
            res.ensure(r["symbol"], r.get("name", ""), r.get("market", "TWSE"), provider, r.get("industry"))
        n += 1
    return n


def mark_delisted(session: Session, rows: list[dict], provider: str = "FINMIND") -> int:
    """標記下市櫃；主檔沒有的下市標的也建立（回測宇宙需包含，以降低生存者偏誤）。"""
    res = InstrumentResolver(session)
    n = 0
    for r in rows:
        iid = res.get(r["symbol"]) or res.ensure(r["symbol"], r.get("name") or r["symbol"], r.get("market", "TWSE"), provider)
        if iid and r.get("date"):
            inst = session.get(Instrument, iid)
            inst.status = "delisted"
            inst.valid_to = r["date"]
            n += 1
    return n


def write_prices(session: Session, rows: list[dict], res: InstrumentResolver) -> int:
    now = utcnow()
    out = []
    for r in rows:
        iid = res.get(r["symbol"])
        if iid is None:
            iid = res.ensure(r["symbol"], r.get("name", ""), r.get("market", "TWSE"), r["provider"])
        if r.get("close") is None:
            continue  # 當日無成交：不寫入價格（不以 0 或前值填補）
        out.append({**r, "instrument_id": iid, "retrieved_at": now, "adjusted_flag": False})
    return upsert(session, DailyPrice, out, ["instrument_id", "trade_date"])


def write_actions(session: Session, rows: list[dict], res: InstrumentResolver, provider: str) -> int:
    out = []
    for r in rows:
        iid = res.get(r["symbol"])
        if iid is None:
            continue
        out.append({**r, "instrument_id": iid, "provider": provider, "retrieved_at": utcnow()})
    return upsert(session, CorporateAction, out, ["instrument_id", "ex_date", "kind"])


def quarter_public_deadline(period_end: date) -> date:
    """一般公司季報法定公告期限（保守採用，避免前視偏誤）。"""
    y, m = period_end.year, period_end.month
    return {3: date(y, 5, 15), 6: date(y, 8, 14), 9: date(y, 11, 14), 12: date(y + 1, 3, 31)}.get(m, period_end)


def write_revenue(session: Session, rows: list[dict], res: InstrumentResolver, provider: str) -> int:
    out = []
    for r in rows:
        iid = res.get(r["symbol"])
        if iid is None:
            continue
        y, m = r["year"], r["month"]
        period_end = (date(y + (m == 12), m % 12 + 1, 1) - (date(2000, 1, 2) - date(2000, 1, 1)))
        deadline = date(y + (m == 12), m % 12 + 1, 10)  # 次月 10 日
        out.append(
            {
                "instrument_id": iid,
                "period_end": period_end,
                "metric_code": "revenue_month",
                "value": r.get("revenue"),
                "null_reason": None if r.get("revenue") is not None else "source_missing",
                "unit": "TWD",
                "currency": "TWD",
                "public_at": deadline,
                "public_at_basis": "legal_deadline",
                "provider": provider,
                "retrieved_at": utcnow(),
            }
        )
    return upsert(session, Fundamental, out, ["instrument_id", "period_end", "metric_code"])


FIN_MAP = {
    "Revenue": ("revenue_q", "TWD"),
    "GrossProfit": ("gross_profit_q", "TWD"),
    "IncomeAfterTaxes": ("net_income_q", "TWD"),
    "EPS": ("eps_q", "TWD/share"),
    "Equity": ("equity", "TWD"),
    "EquityAttributableToOwnersOfParent": ("equity", "TWD"),
    "TotalAssets": ("total_assets", "TWD"),
    "Liabilities": ("liabilities", "TWD"),
}


def write_financials(session: Session, rows: list[dict], res: InstrumentResolver, provider: str) -> int:
    out = {}
    for r in rows:
        iid = res.get(r["symbol"])
        mapped = FIN_MAP.get(r.get("type") or "")
        if iid is None or not mapped or r.get("period_end") is None:
            continue
        code, unit = mapped
        pe = r["period_end"]
        out[(iid, pe, code)] = {
            "instrument_id": iid,
            "period_end": pe,
            "metric_code": code,
            "value": r.get("value"),
            "null_reason": None if r.get("value") is not None else "source_missing",
            "unit": unit,
            "currency": "TWD",
            "public_at": quarter_public_deadline(pe),
            "public_at_basis": "legal_deadline",
            "provider": provider,
            "retrieved_at": utcnow(),
        }
    return upsert(session, Fundamental, list(out.values()), ["instrument_id", "period_end", "metric_code"])


def write_flows(session: Session, rows: list[dict], res: InstrumentResolver, provider: str) -> int:
    out = []
    for r in rows:
        iid = res.get(r["symbol"])
        if iid is None:
            continue
        out.append({**r, "instrument_id": iid, "provider": provider, "retrieved_at": utcnow()})
    return upsert(session, Flow, out, ["instrument_id", "trade_date"])


def finmind_flows_to_rows(symbol: str, inst_rows: list[dict], margin_rows: list[dict]) -> list[dict]:
    from ..sources.base import parse_tw_date, to_float

    agg: dict[date, dict] = {}
    for r in inst_rows:
        d = parse_tw_date(r.get("date"))
        if d is None:
            continue
        net = (to_float(r.get("buy")) or 0) - (to_float(r.get("sell")) or 0)
        name = r.get("name", "")
        row = agg.setdefault(d, {"symbol": symbol, "trade_date": d, "foreign_net": None, "trust_net": None, "dealer_net": None,
                                  "margin_balance": None, "short_balance": None})
        key = "foreign_net" if name.startswith("Foreign") else "trust_net" if name == "Investment_Trust" else "dealer_net" if name.startswith("Dealer") else None
        if key:
            row[key] = (row[key] or 0) + net
    for r in margin_rows:
        d = parse_tw_date(r.get("date"))
        if d is None:
            continue
        row = agg.setdefault(d, {"symbol": symbol, "trade_date": d, "foreign_net": None, "trust_net": None, "dealer_net": None,
                                  "margin_balance": None, "short_balance": None})
        row["margin_balance"] = to_float(r.get("MarginPurchaseTodayBalance"))
        row["short_balance"] = to_float(r.get("ShortSaleTodayBalance"))
    return list(agg.values())


def recompute_adj_factors(session: Session, instrument_ids: list[int] | None = None) -> None:
    """依除權息／減資的 before/after 價格計算向後還原因子。

    還原價 = 原始價 × adj_factor；最新一日 adj_factor = 1。
    """
    import numpy as np
    from sqlalchemy import update

    q = select(Instrument.instrument_id)
    if instrument_ids:
        q = q.where(Instrument.instrument_id.in_(instrument_ids))
    for iid in session.scalars(q).all():
        acts = session.execute(
            select(CorporateAction.ex_date, CorporateAction.before_price, CorporateAction.after_price)
            .where(CorporateAction.instrument_id == iid)
        ).all()
        rows = session.execute(
            select(DailyPrice.trade_date, DailyPrice.adj_factor).where(DailyPrice.instrument_id == iid)
            .order_by(DailyPrice.trade_date)
        ).all()
        if not rows:
            continue
        dates = np.array([r[0].toordinal() for r in rows])
        factor = np.ones(len(rows))
        for ex, before, after in acts:
            if before and after and before > 0:
                factor[dates < ex.toordinal()] *= after / before
        old = np.array([r[1] if r[1] is not None else 1.0 for r in rows])
        changed = np.where(np.abs(old - factor) > 1e-12)[0]
        if len(changed):
            session.execute(
                update(DailyPrice),
                [{"instrument_id": iid, "trade_date": rows[i][0], "adj_factor": float(factor[i])} for i in changed],
            )


def start_run(session: Session, job: str, provider: str, dataset: str) -> IngestRun:
    run = IngestRun(job=job, provider=provider, dataset=dataset, started_at=utcnow(), status="running")
    session.add(run)
    session.flush()
    return run


def finish_run(run: IngestRun, status: str, rows: int = 0, data_date: date | None = None, message: str | None = None):
    run.status = status
    run.rows = rows
    run.data_date = data_date
    run.message = (message or "")[:4000] or None
    run.finished_at = datetime.utcnow()
