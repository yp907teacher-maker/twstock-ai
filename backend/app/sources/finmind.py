"""FinMind 開放資料 API（免費版：註冊並帶 token 600 次/小時，未帶 300 次/小時）。

用途：歷史回填（股價、除權息、月營收、財報、法人、融資融券、下市櫃）。
授權：Free / Backer / Sponsor 方案僅限非商業用途；本專案定位為個人自用研究。
免費版限制：單次請求需指定 data_id（個股），不可一次抓全市場；還原股價資料集需付費，
因此本系統以除權息結果自行計算還原因子。
"""
from __future__ import annotations

from datetime import date

from ..config import settings
from .base import HttpSource, RateLimiter, SourceError, parse_tw_date, to_float

API = "https://api.finmindtrade.com/api/v4/data"


class FinMind(HttpSource):
    provider = "FINMIND"

    def __init__(self, token: str | None = None, hourly_limit: int | None = None):
        token = settings.finmind_token if token is None else token
        limit = hourly_limit or (settings.finmind_hourly_limit if token else min(settings.finmind_hourly_limit, 280))
        super().__init__(limiter=RateLimiter(limit, 3600.0))
        self.token = token

    def _fetch(self, dataset: str, data_id: str | None = None, start: date | None = None, end: date | None = None):
        params = {"dataset": dataset}
        if data_id:
            params["data_id"] = data_id
        if start:
            params["start_date"] = start.isoformat()
        if end:
            params["end_date"] = end.isoformat()
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else None
        payload = self.get_json(API, params=params, headers=headers)
        if not isinstance(payload, dict) or payload.get("status") not in (200, None):
            raise SourceError(f"FinMind {dataset} 回應異常：{str(payload)[:200]}")
        return payload.get("data", []) or []

    # ---- 主檔 ----
    def stock_info(self) -> list[dict]:
        out = []
        for r in self._fetch("TaiwanStockInfo"):
            t = (r.get("type") or "").lower()
            if t not in {"twse", "tpex"}:
                continue  # MVP 不含興櫃
            out.append(
                {
                    "symbol": str(r.get("stock_id")).strip(),
                    "name": str(r.get("stock_name") or "").strip(),
                    "market": "TWSE" if t == "twse" else "TPEx",
                    "industry": r.get("industry_category"),
                    "date": parse_tw_date(r.get("date")),
                }
            )
        return out

    def delisting(self) -> list[dict]:
        return [
            {"symbol": str(r.get("stock_id")).strip(), "name": r.get("stock_name"), "date": parse_tw_date(r.get("date"))}
            for r in self._fetch("TaiwanStockDelisting")
        ]

    # ---- 行情 ----
    def prices(self, symbol: str, start: date, end: date | None = None) -> list[dict]:
        rows = []
        for r in self._fetch("TaiwanStockPrice", symbol, start, end):
            d = parse_tw_date(r.get("date"))
            if d is None:
                continue
            rows.append(
                {
                    "symbol": symbol,
                    "trade_date": d,
                    "open": to_float(r.get("open")),
                    "high": to_float(r.get("max")),
                    "low": to_float(r.get("min")),
                    "close": to_float(r.get("close")),
                    "volume": to_float(r.get("Trading_Volume")),  # 股
                    "turnover": to_float(r.get("Trading_money")),  # 元
                    "trades": to_float(r.get("Trading_turnover")),
                    "provider": self.provider,
                    "source_updated_at": None,
                }
            )
        return rows

    def dividends(self, symbol: str, start: date, end: date | None = None) -> list[dict]:
        out = []
        for r in self._fetch("TaiwanStockDividendResult", symbol, start, end):
            d = parse_tw_date(r.get("date"))
            before, after = to_float(r.get("before_price")), to_float(r.get("after_price"))
            if d is None or not before or not after:
                continue
            kind_txt = str(r.get("stock_or_cache_dividend") or "")
            cash = to_float(r.get("stock_and_cache_dividend")) if "息" in kind_txt else None
            out.append(
                {
                    "symbol": symbol,
                    "ex_date": d,
                    "kind": "dividend",
                    "before_price": before,
                    "after_price": after,
                    "cash_dividend": cash,
                    "stock_dividend": None,
                }
            )
        return out

    def capital_reductions(self, symbol: str, start: date, end: date | None = None) -> list[dict]:
        out = []
        for r in self._fetch("TaiwanStockCapitalReductionReferencePrice", symbol, start, end):
            d = parse_tw_date(r.get("date"))
            before = to_float(r.get("ClosingPriceonTheLastTradingDay"))
            after = to_float(r.get("PostReductionReferencePrice"))
            if d is None or not before or not after:
                continue
            out.append(
                {
                    "symbol": symbol,
                    "ex_date": d,
                    "kind": "capital_reduction",
                    "before_price": before,
                    "after_price": after,
                    "cash_dividend": None,
                    "stock_dividend": None,
                }
            )
        return out

    # ---- 基本面（P1）----
    def month_revenue(self, symbol: str, start: date, end: date | None = None) -> list[dict]:
        out = []
        for r in self._fetch("TaiwanStockMonthRevenue", symbol, start, end):
            y, m = r.get("revenue_year"), r.get("revenue_month")
            if not y or not m:
                continue
            out.append(
                {
                    "symbol": symbol,
                    "year": int(y),
                    "month": int(m),
                    "revenue": to_float(r.get("revenue")),
                    "source_date": parse_tw_date(r.get("date")),
                }
            )
        return out

    def financial_statements(self, symbol: str, start: date, end: date | None = None) -> list[dict]:
        rows = self._fetch("TaiwanStockFinancialStatements", symbol, start, end)
        rows += self._fetch("TaiwanStockBalanceSheet", symbol, start, end)
        return [
            {
                "symbol": symbol,
                "period_end": parse_tw_date(r.get("date")),
                "type": r.get("type"),
                "value": to_float(r.get("value")),
            }
            for r in rows
            if parse_tw_date(r.get("date"))
        ]

    # ---- 籌碼（P1）----
    def institutional(self, symbol: str, start: date, end: date | None = None) -> list[dict]:
        return self._fetch("TaiwanStockInstitutionalInvestorsBuySell", symbol, start, end)

    def margin(self, symbol: str, start: date, end: date | None = None) -> list[dict]:
        return self._fetch("TaiwanStockMarginPurchaseShortSale", symbol, start, end)
