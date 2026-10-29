"""命令列工具。

  python -m app.cli init                     建立資料庫
  python -m app.cli instruments              同步上市櫃股票／ETF 主檔（FinMind）
  python -m app.cli backfill [--symbols 2330 0050] [--datasets prices actions] [--max-requests 500]
  python -m app.cli daily                    收盤後更新（官方 OpenAPI → 品質檢查 → 評分 → 預測）
  python -m app.cli compute [--no-retrain] [--no-backtest]
  python -m app.cli demo                     載入示範資料並完整計算（需 DEMO_MODE=true）
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from .config import settings


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(prog="twstock-ai")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    sub.add_parser("instruments")
    b = sub.add_parser("backfill")
    b.add_argument("--symbols", nargs="*")
    b.add_argument("--datasets", nargs="*", choices=["prices", "actions", "revenue", "financials", "flows"])
    b.add_argument("--max-requests", type=int)
    d = sub.add_parser("daily")
    d.add_argument("--retrain", action="store_true")
    c = sub.add_parser("compute")
    c.add_argument("--no-retrain", action="store_true")
    c.add_argument("--no-backtest", action="store_true")
    sub.add_parser("demo")
    ci = sub.add_parser("ci")
    ci.add_argument("--max-requests", type=int, default=2000)
    args = ap.parse_args(argv)

    from .db import init_engine

    init_engine()
    from . import jobs

    if args.cmd == "init":
        res = {"database": settings.db_url, "demo_mode": settings.demo_mode}
    elif args.cmd == "instruments":
        res = jobs.run_job("instruments")
    elif args.cmd == "backfill":
        res = jobs.run_job("backfill", symbols=args.symbols, datasets=args.datasets, max_requests=args.max_requests)
    elif args.cmd == "daily":
        res = jobs.run_job("daily", retrain=True if args.retrain else None)
    elif args.cmd == "compute":
        res = jobs.run_job("compute", retrain=not args.no_retrain, backtest=not args.no_backtest)
    elif args.cmd == "ci":
        res = jobs.run_job("ci", max_requests=args.max_requests)
        if "error" in res or res.get("compute", {}).get("status") == "no_data":
            print(json.dumps(res, ensure_ascii=False, indent=2, default=str))
            return 1
    elif args.cmd == "demo":
        if not settings.demo_mode:
            print("請先設定 DEMO_MODE=true（示範資料寫入獨立的 twstock_demo.db）", file=sys.stderr)
            return 2
        res = jobs.run_job("daily", retrain=True)
    print(json.dumps(res, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
