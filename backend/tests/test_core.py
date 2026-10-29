"""核心規則的單元測試：日期解析、品質檢查、還原因子、前視偏誤、缺值處理、忠實度、回測成交時點。"""
from datetime import date

import numpy as np
import pandas as pd
import pytest

from app.db import init_engine, session_scope
from app.models import CorporateAction, DailyPrice, Fundamental, Instrument
from app.pipeline import features, quality
from app.pipeline.reports import fidelity_check
from app.pipeline.scoring import score_cross_section
from app.pipeline.store import classify, quarter_public_deadline, recompute_adj_factors
from app.sources.base import parse_tw_date, to_float


@pytest.fixture()
def db(tmp_path):
    init_engine(f"sqlite:///{tmp_path / 't.db'}")
    yield


def test_parse_dates_and_numbers():
    assert parse_tw_date("1151028") == date(2026, 10, 28)
    assert parse_tw_date("115/10/28") == date(2026, 10, 28)
    assert parse_tw_date("20261028") == date(2026, 10, 28)
    assert parse_tw_date("2026-10-28") == date(2026, 10, 28)
    assert parse_tw_date("") is None
    assert to_float("1,234.5") == 1234.5
    assert to_float("--") is None  # 缺值不轉為 0
    assert to_float("+0.50") == 0.5


def test_classify_etf_kinds():
    assert classify("2330", "台積電", "半導體業") == ("common_stock", None)
    assert classify("0050", "元大台灣50", "ETF") == ("ETF", "normal")
    assert classify("00631L", "元大台灣50正2", "ETF") == ("ETF", "leveraged")
    assert classify("00632R", "元大台灣50反1", "ETF") == ("ETF", "inverse")
    assert classify("00679B", "元大美債20年", "ETF") == ("ETF", "bond")


def test_quality_batch_checks():
    rows = [
        {"symbol": "A", "trade_date": date(2026, 10, 24), "open": 10, "high": 11, "low": 9, "close": 10},  # 週六
        {"symbol": "B", "trade_date": date(2026, 10, 23), "open": 10, "high": 9, "low": 9.5, "close": 10},  # 高 < 收
        {"symbol": "C", "trade_date": date(2026, 10, 23), "open": 10, "high": 11, "low": 9, "close": 10},
        {"symbol": "C", "trade_date": date(2026, 10, 23), "open": 10, "high": 11, "low": 9, "close": 10},
    ]
    codes = {(i["symbol"], i["issue_code"]) for i in quality.check_price_batch(rows, "t")}
    assert ("A", "non_trading_day") in codes
    assert ("B", "price_logic") in codes
    assert ("C", "duplicate_row") in codes


def test_quarter_deadlines():
    assert quarter_public_deadline(date(2025, 3, 31)) == date(2025, 5, 15)
    assert quarter_public_deadline(date(2025, 12, 31)) == date(2026, 3, 31)


def test_adjustment_factor(db):
    with session_scope() as s:
        s.add(Instrument(symbol="X", name="X", market="TWSE", asset_type="common_stock", provider="t"))
        s.flush()
        iid = s.query(Instrument).one().instrument_id
        for d, c in [(date(2026, 7, 1), 100.0), (date(2026, 7, 2), 95.0), (date(2026, 7, 3), 96.0)]:
            s.add(DailyPrice(instrument_id=iid, trade_date=d, open=c, high=c, low=c, close=c, provider="t"))
        s.add(CorporateAction(instrument_id=iid, ex_date=date(2026, 7, 2), kind="dividend", before_price=100, after_price=95, provider="t"))
    with session_scope() as s:
        recompute_adj_factors(s)
    with session_scope() as s:
        px = {p.trade_date: p for p in s.query(DailyPrice).all()}
        assert px[date(2026, 7, 1)].adj_factor == pytest.approx(0.95)
        assert px[date(2026, 7, 2)].adj_factor == 1.0
        # 還原後除息日不應出現假跌幅
        assert px[date(2026, 7, 2)].close * 1.0 / (px[date(2026, 7, 1)].close * 0.95) - 1 == pytest.approx(0.0)


def test_fundamentals_use_public_date_only(db):
    """營收要等到公告期限（次月 10 日）之後才可被特徵使用，避免前視偏誤。"""
    with session_scope() as s:
        s.add(Instrument(symbol="Y", name="Y", market="TWSE", asset_type="common_stock", provider="t"))
        s.flush()
        iid = s.query(Instrument).one().instrument_id
        days = pd.bdate_range("2025-01-01", "2026-03-31")
        for d in days:
            s.add(DailyPrice(instrument_id=iid, trade_date=d.date(), open=10, high=10, low=10, close=10, volume=1000,
                             turnover=10000, provider="t"))
        for m in range(1, 15):
            y, mm = 2025 + (m - 1) // 12, (m - 1) % 12 + 1
            pe = (pd.Timestamp(y, mm, 1) + pd.offsets.MonthEnd(0)).date()
            pub = (pd.Timestamp(y, mm, 1) + pd.DateOffset(months=1)).date().replace(day=10)
            s.add(Fundamental(instrument_id=iid, period_end=pe, metric_code="revenue_month", value=100.0 if m <= 12 else 200.0,
                              unit="TWD", public_at=pub, public_at_basis="legal_deadline", provider="t"))
    with session_scope() as s:
        panel = features.load_panel(s)
        long = features.build_long(s, panel)
        jan26 = long[long["d"] == pd.Timestamp("2026-02-09")]["rev_yoy"].iloc[0]  # 2026/1 營收 2/10 才公告
        after = long[long["d"] == pd.Timestamp("2026-02-10")]["rev_yoy"].iloc[0]
        assert pd.isna(jan26)  # 2/9 只看得到 2025/12 營收，沒有 2024/12 可比 → 年增率為缺值，不得偷看 2026/1
        assert after == pytest.approx(1.0)  # 200 / 100 - 1


def test_scoring_renormalizes_missing_components():
    rng = np.random.default_rng(0)
    n = 30
    day = pd.DataFrame({
        "iid": range(n), "group": "stock", "hist_days": 500, "turnover_20": rng.uniform(1e7, 1e9, n),
        "turnover_cv20": rng.uniform(0.2, 1, n), "ret_5": 0.01, "ret_20": rng.normal(0, 0.1, n), "ret_60": rng.normal(0, 0.2, n),
        "px_ma20": rng.normal(0, 0.05, n), "px_ma60": rng.normal(0, 0.1, n), "ma20_slope": rng.normal(0, 0.02, n),
        "vol_60": rng.uniform(0.2, 0.5, n), "down_vol_60": rng.uniform(0.1, 0.3, n), "max_dd_120": rng.uniform(-0.4, 0, n),
        "pe": rng.uniform(5, 40, n),
        # 基本面與籌碼完全缺失
        "rev_yoy": np.nan, "rev_yoy_3m": np.nan, "eps_growth": np.nan, "gross_margin": np.nan, "roe": np.nan,
        "debt_ratio": np.nan, "foreign_20": np.nan, "trust_20": np.nan, "inst_persist": np.nan, "margin_chg_20": np.nan,
        "eps_ttm": np.nan,
    })
    res = score_cross_section(day)
    assert res["coverage"].iloc[0] == pytest.approx(0.6)  # 只剩趨勢、流動性、估值風險
    assert all(r["fundamental"] is None for r in res["components"])  # 缺值保留 None，不以 0 代替
    assert res["total"].between(0, 100).all()
    assert res["eligible"].sum() > 0
    # 流動性後 20% 被排除
    assert any("流動性" in " ".join(r) for r in res["exclusion_reasons"])


def test_fidelity_check_flags_invented_numbers():
    payload = {"score": {"total": 66.36}, "x": "12.6%"}
    assert fidelity_check({"summary": "總分 66.36，20 日報酬 12.6%"}, payload)["passed"]
    bad = fidelity_check({"summary": "目標價 888 元"}, payload)
    assert not bad["passed"] and 888.0 in bad["unmatched_numbers"]


def test_backtest_executes_next_day_open():
    """訊號日收盤後才產生訊號，第一筆報酬必須用隔日開盤價計算。"""
    from app.pipeline.backtest import run_backtest

    days = pd.bdate_range("2024-01-01", periods=400)
    ids = list(range(12))
    rng = np.random.default_rng(1)
    adj = pd.DataFrame(100 * np.exp(np.cumsum(rng.normal(0, 0.01, (400, 12)), axis=0)), index=days, columns=ids)
    opn = adj * 1.001
    panel = {"adj": adj, "adj_open": opn}
    rows = []
    for d in days:
        for i in ids:
            rows.append({"d": d, "iid": i, "group": "stock", "hist_days": 999, "turnover_20": 1e9 + i, "turnover_cv20": 0.3,
                         "ret_5": 0, "ret_20": i, "ret_60": i, "px_ma20": i, "px_ma60": i, "ma20_slope": i, "vol_60": 0.2,
                         "down_vol_60": 0.1, "max_dd_120": -0.1, "pe": np.nan, "eps_ttm": np.nan})
    long = pd.DataFrame(rows)
    for c in ["rev_yoy", "rev_yoy_3m", "eps_growth", "gross_margin", "roe", "debt_ratio", "foreign_20", "trust_20",
              "inst_persist", "margin_chg_20"]:
        long[c] = np.nan
    bt = run_backtest(long, panel, group="stock", top_n=3, period=20)
    assert bt["status"] == "ok"
    first = bt["results"]["universe_equal_weight"]["curve"][0]
    # 第一個績效點的日期 = 第二次再平衡日的「下一交易日」
    assert pd.Timestamp(first["d"]) in days
    assert bt["assumptions"]["execution"].startswith("下一交易日開盤")


def test_official_parsers_with_sample_payload(monkeypatch):
    """以官方 OpenAPI 常見欄位格式模擬回應（實際欄位須於上線前實測確認）。"""
    from app.sources.base import SourceError
    from app.sources.official import TpexOpenAPI, TwseOpenAPI

    twse = [{"Date": "1151028", "Code": "2330", "Name": "台積電", "TradeVolume": "30,123,456", "TradeValue": "45,000,000,000",
             "OpeningPrice": "1,480.00", "HighestPrice": "1,495.00", "LowestPrice": "1,470.00", "ClosingPrice": "1,490.00",
             "Change": "+10.0000", "Transaction": "45,678"},
            {"Date": "1151028", "Code": "9999", "Name": "停牌股", "TradeVolume": "0", "TradeValue": "0", "OpeningPrice": "--",
             "HighestPrice": "--", "LowestPrice": "--", "ClosingPrice": "--", "Change": "0.0000", "Transaction": "0"}]
    t = TwseOpenAPI()
    monkeypatch.setattr(t, "get_json", lambda *a, **k: twse)
    rows = t.daily_quotes()
    assert rows[0]["close"] == 1490.0 and rows[0]["volume"] == 30123456 and rows[0]["trade_date"] == date(2026, 10, 28)
    assert rows[1]["close"] is None  # 無成交保持缺值

    tpex = [{"Date": "1151028", "SecuritiesCompanyCode": "6488", "CompanyName": "環球晶", "Close": "450.00", "Change": "-5.00",
             "Open": "455.00", "High": "458.00", "Low": "449.00", "TradingShares": "1,234,000", "TransactionAmount": "556,000,000",
             "TransactionNumber": "3,210"}]
    p = TpexOpenAPI()
    monkeypatch.setattr(p, "get_json", lambda *a, **k: tpex)
    r = p.daily_quotes()[0]
    assert r["market"] == "TPEx" and r["close"] == 450.0 and r["change"] == -5.0

    monkeypatch.setattr(t, "get_json", lambda *a, **k: [{"Code": "2330", "ClosingPrice": "1"}])
    with pytest.raises(SourceError):
        t.daily_quotes()  # 缺日期時拒絕寫入，不猜測
