"""回測框架（FR-08）。

規則：
- 訊號於再平衡日收盤後產生，下一交易日開盤（還原開盤價）成交；不使用當日之後的資料。
- 每 20 個交易日再平衡，等權持有前 N 名；成本含手續費（折扣後）、證交稅（股票 0.3%／ETF 0.1%）、滑價。
- 宇宙包含期間內已下市標的；持股若下市，以最後可得價格出場（假設，可能高估）。
- 同時報告基準（等權全體合格標的、市場 ETF 代理）與簡單動能策略，並做高成本壓力測試。
- 評分權重為事前假設，並未以回測結果調整；若之後調整權重，須以最後測試區間另行驗證。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from ..config import settings
from .scoring import score_cross_section


@dataclass
class CostModel:
    fee_rate: float
    fee_discount: float
    tax: float
    slippage: float

    @property
    def buy(self) -> float:
        return self.fee_rate * self.fee_discount + self.slippage

    @property
    def sell(self) -> float:
        return self.fee_rate * self.fee_discount + self.tax + self.slippage


def cost_models(group: str) -> dict[str, CostModel]:
    tax = settings.tax_stock if group == "stock" else settings.tax_etf
    return {
        "base": CostModel(settings.fee_rate, settings.fee_discount, tax, settings.slippage),
        "high": CostModel(settings.fee_rate, 1.0, tax, 0.003),
    }


def _metrics(rets: pd.Series, period_days: int) -> dict:
    rets = rets.dropna()
    if rets.empty:
        return {}
    eq = (1 + rets).cumprod()
    years = len(rets) * period_days / 252
    ann = np.sqrt(252 / period_days)
    dd = eq / eq.cummax() - 1
    down = rets[rets < 0]
    enough = len(rets) >= 24
    return {
        "total_return": float(eq.iloc[-1] - 1),
        "cagr": float(eq.iloc[-1] ** (1 / years) - 1) if years > 0 else None,
        "ann_vol": float(rets.std(ddof=1) * ann) if len(rets) > 1 else None,
        "max_drawdown": float(dd.min()),
        "sharpe": float(rets.mean() / rets.std(ddof=1) * ann) if enough and rets.std(ddof=1) > 0 else None,
        "sortino": float(rets.mean() / np.sqrt((down ** 2).mean()) * ann) if enough and len(down) > 0 else None,
        "periods": int(len(rets)),
        "win_rate": float((rets > 0).mean()),
    }


def run_backtest(long_df: pd.DataFrame, panel: dict[str, pd.DataFrame], group: str = "stock", top_n: int = 10,
                 period: int = 20, benchmark_iid: int | None = None) -> dict:
    df = long_df[long_df["group"] == group]
    if df.empty:
        return {"status": "no_data"}
    dates = sorted(df["d"].unique())
    all_days = panel["adj"].index
    pos = {d: i for i, d in enumerate(all_days)}
    warm = settings.min_history_days
    reb = [d for d in dates if pos[d] >= warm][::period]
    if len(reb) < 6:
        return {"status": "insufficient_data", "reason": f"再平衡次數僅 {len(reb)} 次"}
    adj_open = panel["adj_open"].ffill()
    adj_close = panel["adj"].ffill()

    picks = {"score": [], "momentum": [], "universe": []}
    by_day = dict(tuple(df.groupby("d")))
    for d in reb:
        day = by_day[d]
        sc = score_cross_section(day, detail=False)
        el = sc[sc["eligible"]]
        picks["score"].append(list(el.sort_values("total", ascending=False)["instrument_id"].head(top_n)))
        mom = day[day["iid"].isin(el["instrument_id"])].sort_values("ret_60", ascending=False)
        picks["momentum"].append(list(mom["iid"].head(top_n)))
        picks["universe"].append(list(el["instrument_id"]))

    def exec_px(i_day: int, ids: list[int]) -> pd.Series:
        j = min(i_day + 1, len(all_days) - 1)
        px = adj_open.iloc[j].reindex(ids)
        return px.fillna(adj_close.iloc[j].reindex(ids))

    results = {}
    costs = cost_models(group)
    for strat, plist in picks.items():
        for cname, cm in costs.items():
            if strat == "universe" and cname == "high":
                continue
            rets, turns, curve = [], [], []
            w_prev = pd.Series(dtype=float)
            for k in range(len(reb) - 1):
                ids = plist[k]
                i0, i1 = pos[reb[k]], pos[reb[k + 1]]
                if not ids:
                    rets.append(0.0)
                    turns.append(0.0)
                    w_prev = pd.Series(dtype=float)
                    continue
                p0, p1 = exec_px(i0, ids), exec_px(i1, ids)
                r = (p1 / p0 - 1).fillna(0.0)
                w_new = pd.Series(1 / len(ids), index=ids)
                union = w_new.index.union(w_prev.index)
                diff = w_new.reindex(union, fill_value=0) - w_prev.reindex(union, fill_value=0)
                buys, sells = diff.clip(lower=0).sum(), (-diff.clip(upper=0)).sum()
                apply_cost = strat != "universe"
                c = (buys * cm.buy + sells * cm.sell) if apply_cost else 0.0
                gross = float((w_new * r).sum())
                rets.append(gross - c)
                turns.append(float(buys + sells) / 2)
                w_drift = w_new * (1 + r)
                w_prev = w_drift / w_drift.sum() if w_drift.sum() > 0 else w_drift
            idx = [pd.Timestamp(all_days[min(pos[d] + 1, len(all_days) - 1)]) for d in reb[1:]]
            s = pd.Series(rets, index=idx)
            eq = (1 + s).cumprod()
            key = f"{strat}_{cname}" if strat != "universe" else "universe_equal_weight"
            m = _metrics(s, period)
            m["avg_turnover"] = float(np.mean(turns)) if turns else None
            # 最後 20% 期間（與模型測試集對應）
            cut = int(len(s) * 0.8)
            m["last20pct"] = _metrics(s.iloc[cut:], period)
            results[key] = {"metrics": m, "curve": [{"d": str(i.date()), "v": float(v)} for i, v in eq.items()]}

    if benchmark_iid is not None and benchmark_iid in adj_close.columns:
        idx = [pd.Timestamp(all_days[min(pos[d] + 1, len(all_days) - 1)]) for d in reb]
        px = adj_close[benchmark_iid].reindex(idx)
        s = px.pct_change().iloc[1:]
        eq = (1 + s.fillna(0)).cumprod()
        results["market_proxy"] = {"metrics": _metrics(s, period),
                                   "curve": [{"d": str(i.date()), "v": float(v)} for i, v in eq.items()]}

    assumptions = {
        "signal": "再平衡日收盤後計算分數", "execution": "下一交易日開盤（還原價）", "rebalance_days": period,
        "top_n": top_n, "weighting": "等權", "costs": {k: asdict(v) for k, v in costs.items()},
        "delisting": "以最後可得價格出場", "universe": f"group={group}，含已下市標的，需通過硬性排除條件",
        "benchmark_note": "等權全體合格標的未計交易成本，僅作被動參考",
    }
    run_id = hashlib.sha1(json.dumps([group, top_n, period, str(reb[0]), str(reb[-1]), assumptions], sort_keys=True,
                                     default=str).encode()).hexdigest()[:16]
    return {
        "status": "ok",
        "run_id": run_id,
        "group": group,
        "period": {"start": str(pd.Timestamp(reb[0]).date()), "end": str(pd.Timestamp(reb[-1]).date()),
                   "rebalances": len(reb)},
        "assumptions": assumptions,
        "results": results,
    }
