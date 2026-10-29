"""把目前資料庫的 API 回應匯出成靜態 JSON，供 GitHub Pages／線上唯讀版使用。

用法：python dump_static.py <輸出資料夾> [--price-days 750]
會沿用目前的 DEMO_MODE / DATABASE_URL 設定。
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("ENABLE_SCHEDULER", "false")
os.environ["AUTO_BOOTSTRAP"] = "false"
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fastapi.testclient import TestClient  # noqa: E402

from app.api.main import app  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("out")
ap.add_argument("--price-days", type=int, default=750)
args = ap.parse_args()

out = Path(args.out)
(out / "inst").mkdir(parents=True, exist_ok=True)


def dumps(o):
    return json.dumps(o, ensure_ascii=False, separators=(",", ":"))


with TestClient(app) as c:
    def g(u):
        r = c.get(u)
        r.raise_for_status()
        return r.json()

    meta = g("/api/meta")
    meta["scheduler"]["enabled"] = False
    meta["static"] = True
    meta["built_at"] = datetime.now(timezone.utc).isoformat()
    meta["job"] = {"running": None, "started_at": None, "last": None}
    meta["progress"] = None
    common = {
        "meta": meta,
        "overview": g("/api/market/overview"),
        "screener": {grp: g(f"/api/screener?group={grp}&include_ineligible=true&limit=5000")
                     for grp in ("stock", "etf", "etf_leveraged")},
        "picks": {grp: g(f"/api/picks?group={grp}") for grp in ("stock", "etf", "etf_leveraged")},
        "nextday": {grp: g(f"/api/nextday?group={grp}&limit=5000&include_ineligible=true") for grp in ("stock", "etf", "etf_leveraged")},
        "sectors": g("/api/sectors"),
        "models": g("/api/models"),
        "backtests": g("/api/backtests"),
        "health": g("/api/health"),
    }
    from sqlalchemy import func, select

    from app.db import session_scope
    from app.models import DailyPrice, Instrument

    with session_scope() as s:
        ids = [iid for (iid,) in s.execute(
            select(Instrument.instrument_id).join(DailyPrice, DailyPrice.instrument_id == Instrument.instrument_id)
            .group_by(Instrument.instrument_id).having(func.count() > 0))]
    search = []
    for k, iid in enumerate(ids):
        det = g(f"/api/instruments/{iid}")
        det["past_predictions"] = det["past_predictions"][:10]
        search.append(det["instrument"])
        px = g(f"/api/instruments/{iid}/prices?days={args.price_days}&adjusted=false")["rows"]
        adj = g(f"/api/instruments/{iid}/prices?days={args.price_days}&adjusted=true")["rows"]
        rows = [[r["d"], r["o"], r["h"], r["l"], r["c"], r["v"], round(a["c"] / r["c"], 6) if r["c"] else 1]
                for r, a in zip(px, adj)]
        rep = c.post(f"/api/instruments/{iid}/report")
        payload = {"detail": det, "prices": rows, "report": rep.json() if rep.status_code == 200 else None}
        (out / "inst" / f"{iid}.json").write_text(dumps(payload))
        if k % 200 == 0:
            print(f"匯出 {k}/{len(ids)}", flush=True)
    common["search"] = search
    (out / "common.json").write_text(dumps(common))
print("完成，共", len(ids), "檔")
