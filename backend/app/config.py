"""系統設定：全部由環境變數或 backend/.env 讀入，金鑰只放後端。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except Exception:  # dotenv 為選配
    pass

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)


def _bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    # 示範模式：使用合成資料，獨立資料庫檔，畫面全程標示「示範資料」
    demo_mode: bool = field(default_factory=lambda: _bool("DEMO_MODE", False))
    database_url: str = field(default_factory=lambda: os.getenv("DATABASE_URL", ""))

    finmind_token: str = field(default_factory=lambda: os.getenv("FINMIND_TOKEN", ""))
    fugle_api_key: str = field(default_factory=lambda: os.getenv("FUGLE_API_KEY", ""))
    anthropic_api_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    anthropic_model: str = field(default_factory=lambda: os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5"))
    # 每月 AI 報告呼叫上限；達上限時降級為程式產生的量化摘要
    ai_monthly_call_limit: int = field(default_factory=lambda: int(os.getenv("AI_MONTHLY_CALL_LIMIT", "200")))

    backfill_start: str = field(default_factory=lambda: os.getenv("BACKFILL_START", "2015-01-01"))
    # FinMind 免費版：帶 token 600 次/小時，未帶 300 次/小時；預設保守抓 550
    finmind_hourly_limit: int = field(default_factory=lambda: int(os.getenv("FINMIND_HOURLY_LIMIT", "550")))

    # 交易成本假設（回測用，皆為單邊比例）
    fee_rate: float = field(default_factory=lambda: _float("FEE_RATE", 0.001425))
    fee_discount: float = field(default_factory=lambda: _float("FEE_DISCOUNT", 0.6))
    tax_stock: float = field(default_factory=lambda: _float("TAX_STOCK", 0.003))
    tax_etf: float = field(default_factory=lambda: _float("TAX_ETF", 0.001))
    slippage: float = field(default_factory=lambda: _float("SLIPPAGE", 0.001))

    # 收盤更新排程（台北時間）
    daily_job_hour: int = field(default_factory=lambda: int(os.getenv("DAILY_JOB_HOUR", "17")))
    daily_job_minute: int = field(default_factory=lambda: int(os.getenv("DAILY_JOB_MINUTE", "40")))
    enable_scheduler: bool = field(default_factory=lambda: _bool("ENABLE_SCHEDULER", True))

    # 資料品質門檻（初始假設，待試跑校準）
    min_history_days: int = field(default_factory=lambda: int(os.getenv("MIN_HISTORY_DAYS", "120")))
    stale_days: int = field(default_factory=lambda: int(os.getenv("STALE_DAYS", "5")))
    min_turnover_quantile: float = field(default_factory=lambda: _float("MIN_TURNOVER_QUANTILE", 0.2))

    @property
    def db_url(self) -> str:
        if self.database_url:
            return self.database_url
        name = "twstock_demo.db" if self.demo_mode else "twstock.db"
        return f"sqlite:///{DATA_DIR / name}"


settings = Settings()

FEATURE_VERSION = "feat-v1"
SCORE_VERSION = "score-v1"
PROMPT_VERSION = "report-v1"
