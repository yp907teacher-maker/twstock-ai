"""資料表：對應 PRD 第 7 節資料字典。

原則：
- 行情保存未還原原始價，另以 adj_factor 記錄還原因子，兩者不混用。
- 每筆來源資料都保存 provider、retrieved_at、source_updated_at（無則為 null，不偽造）。
- 財務與籌碼保存 public_at，回測只能使用 public_at 之後的資料。
- 預測一經寫入不覆寫；actual_outcome 於結果成熟後回填。
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def utcnow() -> datetime:
    return datetime.utcnow()


class Instrument(Base):
    __tablename__ = "instrument"
    __table_args__ = (UniqueConstraint("symbol", "valid_from", name="uq_instrument_symbol_from"),)

    instrument_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    name: Mapped[str] = mapped_column(String(64))
    market: Mapped[str] = mapped_column(String(8))  # TWSE / TPEx
    asset_type: Mapped[str] = mapped_column(String(16))  # common_stock / ETF
    etf_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)  # normal/leveraged/inverse/bond
    industry: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active/suspended/delisted
    excluded_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)  # 使用者手動停用不可靠資料
    valid_from: Mapped[date] = mapped_column(Date, default=date(1900, 1, 1))
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    provider: Mapped[str] = mapped_column(String(32))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    @property
    def group(self) -> str:
        """排名分組：一般股票、一般 ETF、槓桿／反向 ETF 分開。"""
        if self.asset_type == "ETF":
            return "etf_leveraged" if self.etf_kind in {"leveraged", "inverse"} else "etf"
        return "stock"


class DailyPrice(Base):
    __tablename__ = "daily_price"

    instrument_id: Mapped[int] = mapped_column(ForeignKey("instrument.instrument_id"), primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True, index=True)
    open: Mapped[float | None] = mapped_column(Float, nullable=True)
    high: Mapped[float | None] = mapped_column(Float, nullable=True)
    low: Mapped[float | None] = mapped_column(Float, nullable=True)
    close: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume: Mapped[float | None] = mapped_column(Float, nullable=True)  # 單位：股
    turnover: Mapped[float | None] = mapped_column(Float, nullable=True)  # 單位：新台幣元
    trades: Mapped[float | None] = mapped_column(Float, nullable=True)
    adjusted_flag: Mapped[bool] = mapped_column(Boolean, default=False)  # 本表固定存未還原價
    adj_factor: Mapped[float] = mapped_column(Float, default=1.0)  # 還原價 = close * adj_factor
    provider: Mapped[str] = mapped_column(String(32))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class CorporateAction(Base):
    __tablename__ = "corporate_action"

    instrument_id: Mapped[int] = mapped_column(ForeignKey("instrument.instrument_id"), primary_key=True)
    ex_date: Mapped[date] = mapped_column(Date, primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), primary_key=True)  # dividend / capital_reduction / split
    before_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    after_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    cash_dividend: Mapped[float | None] = mapped_column(Float, nullable=True)
    stock_dividend: Mapped[float | None] = mapped_column(Float, nullable=True)
    provider: Mapped[str] = mapped_column(String(32))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Fundamental(Base):
    __tablename__ = "fundamental"

    instrument_id: Mapped[int] = mapped_column(ForeignKey("instrument.instrument_id"), primary_key=True)
    period_end: Mapped[date] = mapped_column(Date, primary_key=True)
    metric_code: Mapped[str] = mapped_column(String(32), primary_key=True)
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    null_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    unit: Mapped[str] = mapped_column(String(16))
    currency: Mapped[str] = mapped_column(String(8), default="TWD")
    public_at: Mapped[date] = mapped_column(Date, index=True)
    public_at_basis: Mapped[str] = mapped_column(String(32))  # source / legal_deadline
    provider: Mapped[str] = mapped_column(String(32))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Flow(Base):
    __tablename__ = "flow"

    instrument_id: Mapped[int] = mapped_column(ForeignKey("instrument.instrument_id"), primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    foreign_net: Mapped[float | None] = mapped_column(Float, nullable=True)  # 單位：股
    trust_net: Mapped[float | None] = mapped_column(Float, nullable=True)
    dealer_net: Mapped[float | None] = mapped_column(Float, nullable=True)
    margin_balance: Mapped[float | None] = mapped_column(Float, nullable=True)  # 單位：張
    short_balance: Mapped[float | None] = mapped_column(Float, nullable=True)
    provider: Mapped[str] = mapped_column(String(32))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class FeatureSnapshot(Base):
    __tablename__ = "feature_snapshot"

    instrument_id: Mapped[int] = mapped_column(ForeignKey("instrument.instrument_id"), primary_key=True)
    as_of_date: Mapped[date] = mapped_column(Date, primary_key=True, index=True)
    feature_version: Mapped[str] = mapped_column(String(16), primary_key=True)
    values: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ScoreSnapshot(Base):
    __tablename__ = "score_snapshot"

    instrument_id: Mapped[int] = mapped_column(ForeignKey("instrument.instrument_id"), primary_key=True)
    as_of_date: Mapped[date] = mapped_column(Date, primary_key=True, index=True)
    score_version: Mapped[str] = mapped_column(String(16), primary_key=True)
    group: Mapped[str] = mapped_column(String(16))
    eligible: Mapped[bool] = mapped_column(Boolean)
    total: Mapped[float | None] = mapped_column(Float, nullable=True)
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    coverage: Mapped[float] = mapped_column(Float)
    components: Mapped[dict] = mapped_column(JSON)
    exclusion_reasons: Mapped[list] = mapped_column(JSON)
    positives: Mapped[list] = mapped_column(JSON)
    negatives: Mapped[list] = mapped_column(JSON)
    risk_flags: Mapped[list] = mapped_column(JSON)
    weights: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ModelRun(Base):
    __tablename__ = "model_run"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    model_version: Mapped[str] = mapped_column(String(64))
    horizon_days: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    params: Mapped[dict] = mapped_column(JSON)
    data_version: Mapped[str] = mapped_column(String(64))
    train_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    train_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    test_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    test_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    metrics: Mapped[dict] = mapped_column(JSON)
    beats_baseline: Mapped[bool] = mapped_column(Boolean, default=False)
    interval_calibrated: Mapped[bool] = mapped_column(Boolean, default=False)
    prob_calibrated: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), default="experimental")


class Prediction(Base):
    __tablename__ = "prediction"
    __table_args__ = (
        UniqueConstraint("instrument_id", "as_of_date", "model_run_id", "horizon_days", name="uq_prediction"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instrument.instrument_id"), index=True)
    as_of_date: Mapped[date] = mapped_column(Date, index=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    model_run_id: Mapped[int] = mapped_column(ForeignKey("model_run.id"))
    horizon_days: Mapped[int] = mapped_column(Integer)
    target_definition: Mapped[str] = mapped_column(String(128))
    expected_return: Mapped[float | None] = mapped_column(Float, nullable=True)
    lower: Mapped[float | None] = mapped_column(Float, nullable=True)
    upper: Mapped[float | None] = mapped_column(Float, nullable=True)
    prob_up: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk_flags: Mapped[list] = mapped_column(JSON)
    actual_outcome: Mapped[float | None] = mapped_column(Float, nullable=True)
    outcome_filled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class BacktestRun(Base):
    __tablename__ = "backtest_run"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    name: Mapped[str] = mapped_column(String(128))
    group: Mapped[str] = mapped_column(String(16))
    period: Mapped[dict] = mapped_column(JSON)
    assumptions: Mapped[dict] = mapped_column(JSON)
    metrics: Mapped[dict] = mapped_column(JSON)
    equity_curve: Mapped[list] = mapped_column(JSON)


class DataQualityIssue(Base):
    __tablename__ = "data_quality"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dataset: Mapped[str] = mapped_column(String(32))
    instrument_id: Mapped[int | None] = mapped_column(ForeignKey("instrument.instrument_id"), nullable=True)
    trade_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    issue_code: Mapped[str] = mapped_column(String(32), index=True)
    severity: Mapped[str] = mapped_column(String(8))  # info / warn / error
    details: Mapped[dict] = mapped_column(JSON)
    detected_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)


class IngestRun(Base):
    __tablename__ = "ingest_run"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job: Mapped[str] = mapped_column(String(32))
    provider: Mapped[str] = mapped_column(String(32))
    dataset: Mapped[str] = mapped_column(String(48))
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="running")  # running/success/failed/partial
    rows: Mapped[int] = mapped_column(Integer, default=0)
    data_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)


class BackfillState(Base):
    __tablename__ = "backfill_state"

    dataset: Mapped[str] = mapped_column(String(48), primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instrument.instrument_id"), primary_key=True)
    done_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Watchlist(Base):
    __tablename__ = "watchlist"

    instrument_id: Mapped[int] = mapped_column(ForeignKey("instrument.instrument_id"), primary_key=True)
    note: Mapped[str] = mapped_column(Text, default="")
    added_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Quote(Base):
    __tablename__ = "quote"

    instrument_id: Mapped[int] = mapped_column(ForeignKey("instrument.instrument_id"), primary_key=True)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    change: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    quote_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_realtime: Mapped[bool] = mapped_column(Boolean, default=False)
    provider: Mapped[str] = mapped_column(String(32))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AIReport(Base):
    __tablename__ = "ai_report"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instrument.instrument_id"), index=True)
    as_of_date: Mapped[date] = mapped_column(Date)
    data_hash: Mapped[str] = mapped_column(String(64), index=True)
    prompt_version: Mapped[str] = mapped_column(String(16))
    mode: Mapped[str] = mapped_column(String(16))  # ai / fallback
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    content: Mapped[dict] = mapped_column(JSON)
    fidelity: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class MarketIndex(Base):
    __tablename__ = "market_index"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    close: Mapped[float | None] = mapped_column(Float, nullable=True)
    change_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    provider: Mapped[str] = mapped_column(String(32))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
