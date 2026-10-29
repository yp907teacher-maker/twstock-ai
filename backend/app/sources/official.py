"""證交所／櫃買中心官方 OpenAPI（政府資料開放授權條款第 1 版，免費）。

注意：
- 這兩組 OpenAPI 只回傳「最新一期」資料，沒有日期參數，因此只用於每日增量。
- 欄位名稱以多個候選名稱解析；若來源改版導致缺欄，會丟出 SourceError，
  由資料品質檢查記錄，不會以猜測值寫入。
- 官網查詢頁（非 OpenAPI）依網站使用條款禁止未經同意的自動化下載，本系統不呼叫。
"""
from __future__ import annotations

from .base import HttpSource, RateLimiter, SourceError, parse_tw_date, pick, to_float

TWSE_BASE = "https://openapi.twse.com.tw/v1"
TPEX_BASE = "https://www.tpex.org.tw/openapi/v1"


class TwseOpenAPI(HttpSource):
    provider = "TWSE_OPENAPI"

    def __init__(self):
        super().__init__(limiter=RateLimiter(2, 1.0))

    def daily_quotes(self) -> list[dict]:
        """上市個股日成交資訊（含 ETF），exchangeReport/STOCK_DAY_ALL。"""
        data = self.get_json(f"{TWSE_BASE}/exchangeReport/STOCK_DAY_ALL")
        if not isinstance(data, list):
            raise SourceError("TWSE STOCK_DAY_ALL 回傳格式不是陣列")
        rows = []
        for r in data:
            d = parse_tw_date(pick(r, "Date", "日期"))
            code = pick(r, "Code", "證券代號")
            if not code:
                continue
            if d is None:
                raise SourceError("TWSE STOCK_DAY_ALL 缺少交易日期欄位，拒絕以推測日期寫入")
            rows.append(
                {
                    "symbol": str(code).strip(),
                    "name": str(pick(r, "Name", "證券名稱") or "").strip(),
                    "market": "TWSE",
                    "trade_date": d,
                    "open": to_float(pick(r, "OpeningPrice", "開盤價")),
                    "high": to_float(pick(r, "HighestPrice", "最高價")),
                    "low": to_float(pick(r, "LowestPrice", "最低價")),
                    "close": to_float(pick(r, "ClosingPrice", "收盤價")),
                    "volume": to_float(pick(r, "TradeVolume", "成交股數")),
                    "turnover": to_float(pick(r, "TradeValue", "成交金額")),
                    "trades": to_float(pick(r, "Transaction", "成交筆數")),
                    "change": to_float(pick(r, "Change", "漲跌價差")),
                    "provider": self.provider,
                    "source_updated_at": None,
                }
            )
        return rows

    def index_summary(self) -> list[dict]:
        """大盤統計資訊 exchangeReport/MI_INDEX（指數名稱、收盤、漲跌）。"""
        data = self.get_json(f"{TWSE_BASE}/exchangeReport/MI_INDEX")
        out = []
        for r in data if isinstance(data, list) else []:
            name = pick(r, "指數", "Index")
            close = to_float(pick(r, "收盤指數", "CloseIndex"))
            if name and close is not None:
                out.append(
                    {
                        "name": str(name),
                        "close": close,
                        "change_pct": to_float(pick(r, "漲跌百分比(%)", "漲跌百分比", "ChangePercent")),
                        "date": parse_tw_date(pick(r, "日期", "Date")),
                        "provider": self.provider,
                    }
                )
        return out


class TpexOpenAPI(HttpSource):
    provider = "TPEX_OPENAPI"

    def __init__(self):
        super().__init__(limiter=RateLimiter(2, 1.0))

    def daily_quotes(self) -> list[dict]:
        """上櫃股票收盤行情（含 ETF），tpex_mainboard_daily_close_quotes。"""
        data = self.get_json(f"{TPEX_BASE}/tpex_mainboard_daily_close_quotes")
        if not isinstance(data, list):
            raise SourceError("TPEx daily_close_quotes 回傳格式不是陣列")
        rows = []
        for r in data:
            code = pick(r, "SecuritiesCompanyCode", "Code", "代號")
            if not code:
                continue
            d = parse_tw_date(pick(r, "Date", "資料日期"))
            if d is None:
                raise SourceError("TPEx daily_close_quotes 缺少資料日期欄位，拒絕以推測日期寫入")
            rows.append(
                {
                    "symbol": str(code).strip(),
                    "name": str(pick(r, "CompanyName", "Name", "名稱") or "").strip(),
                    "market": "TPEx",
                    "trade_date": d,
                    "open": to_float(pick(r, "Open", "開盤")),
                    "high": to_float(pick(r, "High", "最高")),
                    "low": to_float(pick(r, "Low", "最低")),
                    "close": to_float(pick(r, "Close", "收盤")),
                    "volume": to_float(pick(r, "TradingShares", "TradeVolume", "成交股數")),
                    "turnover": to_float(pick(r, "TransactionAmount", "TradeValue", "成交金額")),
                    "trades": to_float(pick(r, "TransactionNumber", "Transaction", "成交筆數")),
                    "change": to_float(pick(r, "Change", "漲跌")),
                    "provider": self.provider,
                    "source_updated_at": None,
                }
            )
        return rows
