"""Fugle MarketData（基本用戶免費；需註冊取得 API key）。

用途：盤中手動刷新自選股／少量候選標的報價（FR-10）。
免費方案：盤中 API 60 次/分鐘、無 WebSocket。僅刷新報價欄位，不重新訓練模型。
"""
from __future__ import annotations

from datetime import datetime, timezone

from ..config import settings
from .base import HttpSource, RateLimiter, SourceError, to_float

BASE = "https://api.fugle.tw/marketdata/v1.0/stock"


class Fugle(HttpSource):
    provider = "FUGLE"

    def __init__(self, api_key: str | None = None):
        super().__init__(limiter=RateLimiter(55, 60.0))
        self.api_key = settings.fugle_api_key if api_key is None else api_key

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def quote(self, symbol: str) -> dict:
        if not self.enabled:
            raise SourceError("未設定 FUGLE_API_KEY，盤中刷新停用")
        r = self.get_json(f"{BASE}/intraday/quote/{symbol}", headers={"X-API-KEY": self.api_key})
        price = to_float(r.get("lastPrice")) or to_float(r.get("closePrice"))
        ts = r.get("lastUpdated")
        quote_time = None
        if isinstance(ts, (int, float)) and ts > 0:
            # Fugle 時間戳為微秒
            quote_time = datetime.fromtimestamp(ts / 1_000_000, tz=timezone.utc).replace(tzinfo=None)
        total = r.get("total") or {}
        return {
            "symbol": symbol,
            "price": price,
            "change": to_float(r.get("change")),
            "volume": to_float(total.get("tradeVolume")),
            "quote_time": quote_time,
            "is_realtime": True,
            "provider": self.provider,
        }
