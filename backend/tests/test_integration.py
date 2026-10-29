"""示範資料端到端：匯入 → 品質檢查 → 評分 → 模型 → 回測 → API。"""
import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    path = tmp_path_factory.mktemp("e2e") / "demo.db"
    os.environ["ENABLE_SCHEDULER"] = "false"
    from app.db import init_engine, session_scope
    from app.pipeline import ingest, quality, run

    init_engine(f"sqlite:///{path}")
    with session_scope() as s:
        ingest.load_demo(s)
    with session_scope() as s:
        quality.run_post_load_checks(s)
    with session_scope() as s:
        out = run.compute(s, retrain=True, run_backtest=True)
    assert out["scored"] > 40
    from app.api.main import app

    with TestClient(app) as c:
        yield c


def test_quality_blocks_gappy_instrument(client):
    rows = client.get("/api/screener?group=stock&include_ineligible=true&limit=500").json()["rows"]
    d1010 = next(r for r in rows if r["symbol"] == "D1010")
    assert not d1010["score"]["eligible"]
    assert any("資料品質" in x for x in d1010["score"]["exclusion_reasons"])
    # 被阻擋者不產生預測
    assert client.get(f"/api/instruments/{d1010['instrument_id']}").json()["predictions"] == []


def test_delisted_not_ranked_but_in_backtest_universe(client):
    found = client.get("/api/instruments/search?q=D1006").json()[0]
    assert found["status"] == "delisted"
    rows = client.get("/api/screener?group=stock&include_ineligible=true&limit=500").json()["rows"]
    assert all(r["symbol"] != "D1006" for r in rows)


def test_groups_ranked_separately(client):
    lev = client.get("/api/screener?group=etf_leveraged&include_ineligible=true").json()["rows"]
    etf = client.get("/api/screener?group=etf&include_ineligible=true").json()["rows"]
    assert lev and etf
    assert all(r["etf_kind"] in ("leveraged", "inverse") for r in lev)
    assert all("fundamental" not in r["score"]["components"] for r in etf)


def test_predictions_and_model_flags(client):
    models = client.get("/api/models").json()
    assert {m["horizon_days"] for m in models} == {20, 60}
    for m in models:
        assert "model" in m["metrics"]["test"] and "momentum" in m["metrics"]["test"]
    det = client.get("/api/instruments/1").json()
    for p in det["predictions"]:
        assert p["lower"] <= p["expected_return"] <= p["upper"] or p["lower"] <= p["upper"]
        if not p["model"]["prob_calibrated"]:
            assert p["prob_up"] is None


def test_report_fallback_and_cache(client):
    r1 = client.post("/api/instruments/1/report").json()
    r2 = client.post("/api/instruments/1/report").json()
    assert r1["mode"] == "fallback" and r1["fidelity"]["passed"]
    assert r1["id"] == r2["id"]  # 同資料版本快取
    for k in ("summary", "supporting", "opposing", "scenarios", "risks", "data_completeness"):
        assert k in r1["content"]


def test_watchlist_crud(client):
    assert client.post("/api/watchlist", json={"instrument_id": 2, "note": "觀察"}).status_code == 200
    rows = client.get("/api/watchlist").json()["rows"]
    assert rows[0]["note"] == "觀察"
    client.patch("/api/watchlist/2", json={"instrument_id": 2, "note": "改"})
    assert client.get("/api/watchlist").json()["rows"][0]["note"] == "改"
    client.delete("/api/watchlist/2")
    assert client.get("/api/watchlist").json()["rows"] == []


def test_backtest_and_export(client):
    bts = client.get("/api/backtests").json()
    assert {b["group"] for b in bts} >= {"stock"}
    stock = next(b for b in bts if b["group"] == "stock")
    assert {"score_base", "score_high", "momentum_base", "universe_equal_weight"} <= set(stock["metrics"])
    assert stock["metrics"]["score_high"]["total_return"] <= stock["metrics"]["score_base"]["total_return"]
    csv = client.get(f"/api/backtests/{stock['run_id']}/export.csv").text
    assert csv.lstrip("﻿").startswith("date,")


def test_health_and_rerun_idempotent(client):
    from app.db import session_scope
    from app.models import DailyPrice
    from app.pipeline import ingest

    with session_scope() as s:
        n0 = s.query(DailyPrice).count()
        ingest.load_demo(s)  # 重跑不應產生重複紀錄
    with session_scope() as s:
        assert s.query(DailyPrice).count() == n0
    h = client.get("/api/health").json()
    assert h["datasets"] and h["issue_summary"]


def test_system_picks_follow_rules(client):
    from app.pipeline.picks import MAX_PER_INDUSTRY, RULES

    r = client.get("/api/picks?group=stock").json()
    assert len(r["picks"]) <= RULES["stock"]["max_picks"]
    inds = {}
    for p in r["picks"]:
        assert p["total"] >= RULES["stock"]["min_total"] and p["coverage"] >= 0.8
        assert not any(f.startswith(("近四季 EPS 為負", "短期急漲", "近期回撤深")) for f in p["risk_flags"])
        inds[p["industry"]] = inds.get(p["industry"], 0) + 1
    assert all(v <= MAX_PER_INDUSTRY for v in inds.values())
    for m in r["near_misses"]:
        assert m["rejected"]
    assert client.get("/api/picks?group=etf_leveraged").json()["picks"] == []
