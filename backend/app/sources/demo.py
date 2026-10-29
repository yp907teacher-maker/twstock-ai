"""示範資料產生器（合成資料，provider = DEMO）。

只在 DEMO_MODE=true 時使用，寫入獨立的 twstock_demo.db，前端全程顯示「示範資料」橫幅。
代號與名稱皆為虛構（9xxx / 00xx 範圍外的自編號），不可當作真實行情。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd

INDUSTRIES = ["半導體", "電子零組件", "金融保險", "航運", "生技醫療", "傳產食品", "電腦週邊", "鋼鐵"]


@dataclass
class DemoInstrument:
    symbol: str
    name: str
    market: str
    asset_type: str
    etf_kind: str | None
    industry: str | None
    delist_date: date | None = None
    liquidity: float = 1.0


def _business_days(start: date, end: date) -> list[date]:
    return [d.date() for d in pd.bdate_range(start, end)]


def build_universe(rng: np.random.Generator) -> list[DemoInstrument]:
    out: list[DemoInstrument] = []
    n = 0
    for i, ind in enumerate(INDUSTRIES):
        for j in range(6):
            n += 1
            market = "TWSE" if n % 3 else "TPEx"
            out.append(
                DemoInstrument(
                    symbol=f"D{1000 + n:04d}",
                    name=f"示範{ind[:2]}{j + 1}號",
                    market=market,
                    asset_type="common_stock",
                    etf_kind=None,
                    industry=ind,
                    liquidity=float(np.exp(rng.normal(0, 1.0))),
                )
            )
    # 兩檔在期間內下市（生存者偏誤測試）
    out[5].delist_date = date(2022, 6, 30)
    out[17].delist_date = date(2023, 11, 15)
    # 一檔流動性極低
    out[30].liquidity = 0.01
    etfs = [
        ("DE050", "示範大盤50", "normal"),
        ("DE056", "示範高股息", "normal"),
        ("DE878", "示範永續高息", "normal"),
        ("DE900", "示範科技龍頭", "normal"),
        ("DE631", "示範正2槓桿", "leveraged"),
        ("DE632", "示範反1反向", "inverse"),
        ("DE679", "示範美債20年", "bond"),
    ]
    for sym, name, kind in etfs:
        out.append(DemoInstrument(sym, name, "TWSE", "ETF", kind, "ETF", liquidity=3.0))
    return out


def generate(end: date | None = None, start: date = date(2019, 1, 2), seed: int = 7) -> dict:
    rng = np.random.default_rng(seed)
    if end is None:
        # 最近一個已收盤的交易日（今天之前）
        end = (pd.Timestamp(date.today()) - pd.offsets.BDay(1)).date()
    days = _business_days(start, end)
    T = len(days)
    universe = build_universe(rng)

    mkt = rng.normal(0.0003, 0.010, T)
    # 市場狀態：加入一段空頭
    crash = slice(int(T * 0.35), int(T * 0.42))
    mkt[crash] -= 0.004
    ind_f = {ind: rng.normal(0, 0.006, T) for ind in INDUSTRIES}

    prices, actions, revenue, fins, flows = [], [], [], [], []
    stock_rets = {}
    for inst in universe:
        if inst.asset_type == "ETF":
            continue
        # 緩慢變動的個股漂移（讓動能與營收成長有微弱關聯）
        drift = np.zeros(T)
        x = rng.normal(0, 0.0004)
        for t in range(T):
            x = 0.995 * x + rng.normal(0, 0.00004)
            drift[t] = x
        beta = rng.uniform(0.6, 1.4)
        idio = rng.normal(0, rng.uniform(0.012, 0.025), T)
        stock_rets[inst.symbol] = (beta * mkt + ind_f[inst.industry] + drift + idio, drift)

    etf_base = np.mean([v[0] for v in stock_rets.values()], axis=0)
    for inst in universe:
        if inst.asset_type == "ETF":
            if inst.etf_kind == "leveraged":
                r = 2 * etf_base - 0.00005
            elif inst.etf_kind == "inverse":
                r = -etf_base - 0.00005
            elif inst.etf_kind == "bond":
                r = rng.normal(0.0001, 0.005, T)
            else:
                r = etf_base * rng.uniform(0.8, 1.1) + rng.normal(0, 0.003, T)
            drift = np.zeros(T)
        else:
            r, drift = stock_rets[inst.symbol]
        r = np.clip(r, -0.095, 0.095)  # 漲跌幅限制 10%
        p0 = float(rng.uniform(20, 300))
        close = p0 * np.exp(np.cumsum(r))

        # 除息：每年 7 月第一個交易日，現金股利約 2–5%
        ex_idx = set()
        for y in range(start.year, end.year + 1):
            cands = [i for i, d in enumerate(days) if d.year == y and d.month == 7]
            if cands and (inst.asset_type == "common_stock" or inst.etf_kind == "normal"):
                ex_idx.add(cands[0])
        adj_close = close.copy()
        raw = close.copy()
        cum = 1.0
        for i in range(T):
            if i in ex_idx and i > 0:
                prev = raw[i - 1]
                div = round(prev * rng.uniform(0.02, 0.05), 2)
                actions.append(
                    {
                        "symbol": inst.symbol,
                        "ex_date": days[i],
                        "kind": "dividend",
                        "before_price": round(prev, 2),
                        "after_price": round(prev - div, 2),
                        "cash_dividend": div,
                        "stock_dividend": None,
                    }
                )
                cum *= (prev - div) / prev
            raw[i] = adj_close[i] * cum

        open_ = raw * (1 + rng.normal(0, 0.004, T))
        high = np.maximum(open_, raw) * (1 + np.abs(rng.normal(0, 0.006, T)))
        low = np.minimum(open_, raw) * (1 - np.abs(rng.normal(0, 0.006, T)))
        vol = np.maximum(1000, rng.lognormal(13.5, 0.5, T) * inst.liquidity).round(-3)
        last = T
        if inst.delist_date:
            last = sum(1 for d in days if d <= inst.delist_date)
        for i in range(last):
            # 製造少量缺漏（品質檢查測試）：D1010 缺 3 天
            if inst.symbol == "D1010" and T - 8 <= i < T - 5:
                continue
            prices.append(
                {
                    "symbol": inst.symbol,
                    "trade_date": days[i],
                    "open": round(float(open_[i]), 2),
                    "high": round(float(high[i]), 2),
                    "low": round(float(low[i]), 2),
                    "close": round(float(raw[i]), 2),
                    "volume": float(vol[i]),
                    "turnover": float(round(vol[i] * raw[i], 0)),
                    "trades": float(round(vol[i] / 2000)),
                    "provider": "DEMO",
                    "source_updated_at": None,
                }
            )

        if inst.asset_type != "common_stock":
            continue
        # 月營收：與漂移正相關，於次月 10 日公告
        base_rev = rng.uniform(5e8, 5e10)
        months = pd.period_range(start, end, freq="M")
        growth = 0.0
        day_ord = np.array([d.toordinal() for d in days])
        for k, pm in enumerate(months):
            idx = min(T - 1, int(np.searchsorted(day_ord, pm.start_time.date().toordinal())))
            growth = 0.8 * growth + 0.2 * drift[idx] * 250 + rng.normal(0, 0.03)
            base_rev *= np.exp(growth / 12 + rng.normal(0, 0.05))
            pub = (pm + 1).start_time.date() + timedelta(days=9)
            if pub > end or (inst.delist_date and pub > inst.delist_date):
                continue
            revenue.append({"symbol": inst.symbol, "year": pm.year, "month": pm.month, "revenue": float(base_rev), "source_date": None})
        # 季報：依法定期限公告
        margin = rng.uniform(0.1, 0.5)
        equity = base_rev * 4
        from ..pipeline.store import quarter_public_deadline

        for q in pd.period_range(start, end, freq="Q"):
            pe = q.end_time.date()
            if quarter_public_deadline(pe) > end or (inst.delist_date and quarter_public_deadline(pe) > inst.delist_date):
                continue
            q_rev = base_rev * 3 * rng.uniform(0.9, 1.1)
            gp = q_rev * np.clip(margin + rng.normal(0, 0.02), 0.02, 0.9)
            ni = gp * rng.uniform(0.2, 0.6)
            shares = equity / 10 / 15
            liab = equity * rng.uniform(0.3, 1.5)
            for code, val in {
                "Revenue": q_rev,
                "GrossProfit": gp,
                "IncomeAfterTaxes": ni,
                "EPS": ni / shares,
                "Equity": equity,
                "TotalAssets": equity + liab,
                "Liabilities": liab,
            }.items():
                fins.append({"symbol": inst.symbol, "period_end": pe, "type": code, "value": float(val)})
            equity += ni * 0.5
        # 法人與融資
        for i in range(0, last):
            if rng.random() < 0.97:
                flows.append(
                    {
                        "symbol": inst.symbol,
                        "trade_date": days[i],
                        "foreign_net": float(round(rng.normal(drift[i] * 5e8, 2e5) * inst.liquidity, -3)),
                        "trust_net": float(round(rng.normal(drift[i] * 1e8, 5e4) * inst.liquidity, -3)),
                        "dealer_net": float(round(rng.normal(0, 3e4) * inst.liquidity, -3)),
                        "margin_balance": float(max(0, round(rng.normal(5000, 1500) * inst.liquidity))),
                        "short_balance": float(max(0, round(rng.normal(400, 150) * inst.liquidity))),
                    }
                )

    return {
        "universe": universe,
        "prices": prices,
        "actions": actions,
        "revenue": revenue,
        "financials": fins,
        "flows": flows,
        "days": days,
    }
