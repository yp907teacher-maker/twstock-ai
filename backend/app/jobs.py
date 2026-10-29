"""可由 CLI、排程或 API 觸發的工作。同一時間只跑一個工作。"""
from __future__ import annotations

import logging
import threading
from datetime import datetime

from .config import settings
from .db import SessionLocal, get_engine, session_scope

log = logging.getLogger(__name__)
_lock = threading.Lock()
STATE = {"running": None, "started_at": None, "last": None, "phase": None}


def _session_factory():
    get_engine()
    return SessionLocal()


def job_daily(retrain: bool | None = None) -> dict:
    """收盤後完整流程。"""
    from .pipeline import ingest, quality, run

    out = {}
    if settings.demo_mode:
        with session_scope() as s:
            if ingest.latest_trade_date(s) is None:
                out["demo"] = ingest.load_demo(s)
    else:
        with session_scope() as s:
            out["official"] = ingest.daily_official(s)
    with session_scope() as s:
        out["quality"] = quality.run_post_load_checks(s)
    with session_scope() as s:
        out["compute"] = run.compute(s, retrain=retrain)
    return out


def job_compute(retrain: bool = True, backtest: bool = True) -> dict:
    from .pipeline import quality, run

    with session_scope() as s:
        quality.run_post_load_checks(s)
    with session_scope() as s:
        return run.compute(s, retrain=retrain, run_backtest=backtest)


def job_instruments() -> dict:
    from .pipeline import ingest

    with session_scope() as s:
        return ingest.sync_instruments_finmind(s)


def job_backfill(symbols: list[str] | None = None, datasets: list[str] | None = None,
                 max_requests: int | None = None) -> dict:
    from .pipeline import ingest

    kw = {"symbols": symbols, "max_requests": max_requests}
    if datasets:
        kw["datasets"] = tuple(datasets)
    return ingest.backfill_finmind(_session_factory, **kw)


def job_bootstrap() -> dict:
    """真實資料首次啟動：主檔 → 今日收盤（官方，免金鑰）→ 回填股價與除權息 → 計算 → 回填基本面與籌碼 → 再計算。

    每一步完成就寫入，中斷後再啟動會從斷點續傳；完成後由每日排程接手。
    """
    from .pipeline import ingest, quality, run

    out = {}
    with session_scope() as s:
        out["instruments"] = ingest.sync_instruments_finmind(s)
    with session_scope() as s:
        out["official"] = ingest.daily_official(s)
    if "error" in out["instruments"] and all("error" in v for v in out["official"].values()):
        STATE["phase"] = None
        raise RuntimeError("連不到資料來源（FinMind 與證交所／櫃買都失敗），請確認網路後重新啟動。"
                           f"FinMind：{out['instruments']['error']}")
    STATE["phase"] = "回填股價與除權息"
    out["backfill_prices"] = ingest.backfill_finmind(_session_factory, datasets=("prices", "actions"))
    STATE["phase"] = "計算評分與模型"
    with session_scope() as s:
        quality.run_post_load_checks(s)
    with session_scope() as s:
        out["compute_1"] = run.compute(s, retrain=True, run_backtest=True)
    STATE["phase"] = "回填月營收、財報、法人"
    out["backfill_rest"] = ingest.backfill_finmind(_session_factory, datasets=("revenue", "financials", "flows"))
    STATE["phase"] = "重新計算"
    with session_scope() as s:
        out["compute_2"] = run.compute(s, retrain=True, run_backtest=True)
    STATE["phase"] = None
    return out


def job_ci(max_requests: int = 2000) -> dict:
    """雲端排程（GitHub Actions）一次執行：今日收盤 → 分段回填 → 計算 → （由工作流程建置靜態網站）。

    回填一次只做 max_requests 次請求，下一次排程接續；回填仍有進度時重新訓練模型。
    """
    from sqlalchemy import func, select

    from .models import Instrument
    from .pipeline import ingest, quality, run

    out = {}
    with session_scope() as s:
        n_inst = s.scalar(select(func.count()).select_from(Instrument)) or 0
    if n_inst < 100:
        with session_scope() as s:
            out["instruments"] = ingest.sync_instruments_finmind(s)
    from .pipeline.store import purge_unsupported

    with session_scope() as s:
        out["purged"] = purge_unsupported(s)
    with session_scope() as s:
        out["official"] = ingest.daily_official(s)
    with session_scope() as s:
        remaining = ingest.backfill_remaining(s, ("prices", "actions"))
    phase = ("prices", "actions") if remaining else ("revenue", "financials", "flows")
    with session_scope() as s:
        rest = ingest.backfill_remaining(s, phase)
    if rest:
        out["backfill"] = ingest.backfill_finmind(_session_factory, datasets=phase, max_requests=max_requests)
    with session_scope() as s:
        out["quality"] = quality.run_post_load_checks(s)
    progressed = bool(out.get("backfill", {}).get("rows"))
    with session_scope() as s:
        out["compute"] = run.compute(s, retrain=True if progressed else None)
    return out


def needs_bootstrap() -> bool:
    """真實模式下，若歷史資料不足 120 個交易日，啟動時自動執行首次建置。"""
    if settings.demo_mode:
        return False
    from sqlalchemy import func, select

    from .models import DailyPrice

    with session_scope() as s:
        n = s.scalar(select(func.count(func.distinct(DailyPrice.trade_date)))) or 0
    return n < settings.min_history_days


JOBS = {"ci": job_ci, "bootstrap": job_bootstrap, "daily": job_daily, "compute": job_compute, "instruments": job_instruments, "backfill": job_backfill}


def run_job(name: str, **kwargs) -> dict:
    if not _lock.acquire(blocking=False):
        return {"error": f"已有工作執行中：{STATE['running']}"}
    STATE.update(running=name, started_at=datetime.utcnow().isoformat())
    try:
        res = JOBS[name](**kwargs)
        STATE["last"] = {"job": name, "status": "success", "finished_at": datetime.utcnow().isoformat(),
                         "result": str(res)[:2000]}
        return res
    except Exception as exc:  # noqa: BLE001
        import traceback

        log.exception("工作 %s 失敗", name)
        tb = traceback.format_exc().strip().splitlines()
        msg = f"{type(exc).__name__}: {exc} @ " + " | ".join(l.strip() for l in tb[-6:-1])
        STATE["last"] = {"job": name, "status": "failed", "finished_at": datetime.utcnow().isoformat(), "error": msg}
        return {"error": msg}
    finally:
        STATE.update(running=None, started_at=None)
        _lock.release()


def run_job_async(name: str, **kwargs) -> dict:
    if STATE["running"]:
        return {"accepted": False, "error": f"已有工作執行中：{STATE['running']}"}
    threading.Thread(target=run_job, args=(name,), kwargs=kwargs, daemon=True).start()
    return {"accepted": True, "job": name}


def start_scheduler():
    """平日（週一至週五）台北時間收盤後執行 daily；可用 ENABLE_SCHEDULER=false 關閉。"""
    if not settings.enable_scheduler:
        return None
    from apscheduler.schedulers.background import BackgroundScheduler

    sched = BackgroundScheduler(timezone="Asia/Taipei")
    sched.add_job(lambda: run_job("daily"), "cron", day_of_week="mon-fri", hour=settings.daily_job_hour,
                  minute=settings.daily_job_minute, id="daily", misfire_grace_time=3600, coalesce=True)
    sched.start()
    log.info("排程已啟動：平日 %02d:%02d 執行收盤後更新", settings.daily_job_hour, settings.daily_job_minute)
    return sched
