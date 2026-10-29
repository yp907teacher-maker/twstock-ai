"""HTTP 共用：速率限制、重試、錯誤分類。"""
from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime

import httpx

log = logging.getLogger(__name__)


class SourceError(RuntimeError):
    """來源呼叫失敗（網路、HTTP 錯誤、格式不符）。"""


class RateLimitError(SourceError):
    """來源回報超過呼叫上限。"""


class RateLimiter:
    """以最小間隔控制呼叫頻率（執行緒安全）。"""

    def __init__(self, calls: int, per_seconds: float):
        self.min_interval = per_seconds / max(calls, 1)
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            delay = self._last + self.min_interval - now
            if delay > 0:
                time.sleep(delay)
            self._last = time.monotonic()


class HttpSource:
    provider = "base"
    user_agent = "twstock-ai-personal-research/0.1"

    def __init__(self, limiter: RateLimiter | None = None, timeout: float = 30.0, retries: int = 3):
        self.limiter = limiter
        self.retries = retries
        self.client = httpx.Client(timeout=timeout, headers={"User-Agent": self.user_agent}, follow_redirects=True)

    def get_json(self, url: str, params: dict | None = None, headers: dict | None = None):
        last_exc: Exception | None = None
        for attempt in range(1, self.retries + 1):
            if self.limiter:
                self.limiter.wait()
            try:
                r = self.client.get(url, params=params, headers=headers)
                if r.status_code in (402, 429):
                    raise RateLimitError(f"{self.provider} 達呼叫上限 HTTP {r.status_code}: {r.text[:200]}")
                if r.status_code >= 500:
                    raise SourceError(f"{self.provider} 伺服器錯誤 HTTP {r.status_code}")
                if r.status_code >= 400:
                    # 4xx 不重試
                    raise SourceError(f"{self.provider} HTTP {r.status_code}: {r.text[:200]}")
                return r.json()
            except RateLimitError:
                raise
            except (httpx.HTTPError, ValueError, SourceError) as exc:
                last_exc = exc
                if isinstance(exc, SourceError) and "HTTP 4" in str(exc):
                    raise
                log.warning("%s 第 %d 次呼叫失敗：%s", self.provider, attempt, exc)
                time.sleep(min(2**attempt, 10))
        raise SourceError(f"{self.provider} 呼叫失敗：{last_exc}")


def to_float(v) -> float | None:
    """'1,234.5' / '--' / '' / None → float 或 None（缺值不轉成 0）。"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "")
    if s in {"", "--", "---", "-", "X", "N/A", "null", "除權息", "除權", "除息"}:
        return None
    s = s.lstrip("+")
    try:
        return float(s)
    except ValueError:
        return None


def parse_tw_date(v) -> date | None:
    """支援 1151028（民國）、115/10/28、20261028、2026-10-28、2026/10/28。"""
    if v is None:
        return None
    if isinstance(v, date):
        return v
    s = str(v).strip()
    if not s:
        return None
    try:
        if "-" in s:
            return datetime.strptime(s[:10], "%Y-%m-%d").date()
        if "/" in s:
            parts = s.split("/")
            y = int(parts[0])
            if y < 1911:
                y += 1911
            return date(y, int(parts[1]), int(parts[2]))
        if s.isdigit():
            if len(s) == 8:
                return datetime.strptime(s, "%Y%m%d").date()
            if len(s) == 7:  # 民國 YYYMMDD
                return date(int(s[:3]) + 1911, int(s[3:5]), int(s[5:7]))
    except ValueError:
        return None
    return None


def pick(row: dict, *keys):
    """從多個候選欄位名取第一個存在的值（來源欄位名稱可能變動）。"""
    for k in keys:
        if k in row and row[k] not in (None, ""):
            return row[k]
    return None
