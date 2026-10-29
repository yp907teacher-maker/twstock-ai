"""技術、基本面、籌碼特徵（FR-05、FR-11、FR-12）。

- 技術特徵一律用還原價計算（收盤 × adj_factor），避免除權息造成假跌幅。
- 基本面與籌碼以 public_at 做 as-of 對齊（merge_asof），只使用當時已公告資料。
- 標籤（未來 N 日報酬）只在模型訓練時產生，不會進入特徵。
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import FEATURE_VERSION
from ..models import DailyPrice, Flow, Fundamental, Instrument

FEATURE_LABELS = {
    "ret_5": "5 日報酬",
    "ret_20": "20 日報酬",
    "ret_60": "60 日報酬",
    "ret_120": "120 日報酬",
    "px_ma20": "股價相對 20 日均線",
    "px_ma60": "股價相對 60 日均線",
    "ma20_slope": "20 日均線斜率",
    "rsi14": "RSI(14)",
    "vol_20": "20 日年化波動率",
    "vol_60": "60 日年化波動率",
    "down_vol_60": "60 日下行波動率",
    "max_dd_120": "近期最大回撤",
    "turnover_20": "20 日平均成交金額",
    "turnover_cv20": "成交金額變異係數",
    "vol_ratio": "5/20 日量比",
    "rev_yoy": "月營收年增率",
    "rev_yoy_3m": "近 3 月營收年增率",
    "eps_ttm": "近四季 EPS",
    "eps_growth": "近四季 EPS 年增率",
    "gross_margin": "近四季毛利率",
    "roe": "近四季 ROE",
    "debt_ratio": "負債比率",
    "pe": "本益比",
    "foreign_20": "外資 20 日淨買超／均量",
    "trust_20": "投信 20 日淨買超／均量",
    "inst_persist": "法人買超天數比例",
    "margin_chg_20": "融資餘額 20 日變化",
}
PCT_FEATURES = {"ret_5", "ret_20", "ret_60", "ret_120", "px_ma20", "px_ma60", "ma20_slope", "vol_20", "vol_60",
                "down_vol_60", "max_dd_120", "rev_yoy", "rev_yoy_3m", "eps_growth", "gross_margin", "roe",
                "debt_ratio", "margin_chg_20", "inst_persist"}


def load_panel(session: Session, start: date | None = None) -> dict[str, pd.DataFrame]:
    q = select(
        DailyPrice.instrument_id, DailyPrice.trade_date, DailyPrice.open, DailyPrice.high, DailyPrice.low,
        DailyPrice.close, DailyPrice.volume, DailyPrice.turnover, DailyPrice.adj_factor,
    )
    if start:
        q = q.where(DailyPrice.trade_date >= start)
    df = pd.DataFrame(session.execute(q).all(),
                      columns=["iid", "d", "open", "high", "low", "close", "volume", "turnover", "f"])
    if df.empty:
        return {}
    df["d"] = pd.to_datetime(df["d"])
    df["f"] = df["f"].fillna(1.0)
    df["adj"] = df["close"] * df["f"]
    df["adj_open"] = df["open"] * df["f"]
    panel = {}
    for col in ["close", "adj", "adj_open", "volume", "turnover", "high", "low"]:
        panel[col] = df.pivot(index="d", columns="iid", values=col).sort_index()
    return panel


def _rsi(adj: pd.DataFrame, n: int = 14) -> pd.DataFrame:
    diff = adj.diff()
    gain = diff.clip(lower=0).ewm(alpha=1 / n, min_periods=n).mean()
    loss = (-diff.clip(upper=0)).ewm(alpha=1 / n, min_periods=n).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def technical_features(panel: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    adj = panel["adj"]
    # 停牌日以前值計算報酬會低估波動，因此報酬只在有價日計算
    ret = adj.pct_change(fill_method=None)
    f = {}
    for n in (5, 20, 60, 120):
        f[f"ret_{n}"] = adj / adj.ffill().shift(n) - 1
    ma20 = adj.rolling(20, min_periods=16).mean()
    ma60 = adj.rolling(60, min_periods=48).mean()
    f["px_ma20"] = adj / ma20 - 1
    f["px_ma60"] = adj / ma60 - 1
    f["ma20_slope"] = ma20 / ma20.shift(5) - 1
    f["rsi14"] = _rsi(adj.ffill())
    f["vol_20"] = ret.rolling(20, min_periods=16).std() * np.sqrt(252)
    f["vol_60"] = ret.rolling(60, min_periods=48).std() * np.sqrt(252)
    f["down_vol_60"] = np.sqrt((ret.clip(upper=0) ** 2).rolling(60, min_periods=48).mean()) * np.sqrt(252)
    dd = adj / adj.rolling(120, min_periods=60).max() - 1
    f["max_dd_120"] = dd.rolling(120, min_periods=60).min()
    t20 = panel["turnover"].rolling(20, min_periods=16).mean()
    f["turnover_20"] = t20
    f["turnover_cv20"] = panel["turnover"].rolling(20, min_periods=16).std() / t20
    f["vol_ratio"] = panel["volume"].rolling(5, min_periods=4).mean() / panel["volume"].rolling(20, min_periods=16).mean()
    f["hist_days"] = adj.notna().cumsum().where(adj.notna())
    return f


def flow_features(session: Session, panel: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    q = select(Flow.instrument_id, Flow.trade_date, Flow.foreign_net, Flow.trust_net, Flow.dealer_net,
               Flow.margin_balance)
    df = pd.DataFrame(session.execute(q).all(), columns=["iid", "d", "foreign", "trust", "dealer", "margin"])
    if df.empty:
        return {}
    df["d"] = pd.to_datetime(df["d"])
    idx, cols = panel["adj"].index, panel["adj"].columns
    w = {c: df.pivot(index="d", columns="iid", values=c).reindex(index=idx, columns=cols) for c in
         ["foreign", "trust", "margin"]}
    avg_vol = panel["volume"].rolling(20, min_periods=16).mean()
    has = w["foreign"].notna() | w["trust"].notna()
    out = {
        "foreign_20": w["foreign"].rolling(20, min_periods=12).sum() / (avg_vol * 20),
        "trust_20": w["trust"].rolling(20, min_periods=12).sum() / (avg_vol * 20),
        "inst_persist": ((w["foreign"].fillna(0) + w["trust"].fillna(0)) > 0).where(has).rolling(20, min_periods=12).mean(),
        "margin_chg_20": w["margin"] / w["margin"].shift(20) - 1,
    }
    return out


def fundamental_events(session: Session) -> pd.DataFrame:
    """每一次公告後的衍生基本面指標：(iid, public_at, rev_yoy, ..., debt_ratio)。"""
    q = select(Fundamental.instrument_id, Fundamental.period_end, Fundamental.metric_code, Fundamental.value,
               Fundamental.public_at)
    df = pd.DataFrame(session.execute(q).all(), columns=["iid", "pe", "m", "v", "pub"])
    if df.empty:
        return pd.DataFrame(columns=["iid", "pub"])
    df["pe"] = pd.to_datetime(df["pe"])
    df["pub"] = pd.to_datetime(df["pub"])
    events = []

    rev = df[df["m"] == "revenue_month"].sort_values(["iid", "pe"])
    if not rev.empty:
        rev = rev.set_index("pe")
        for iid, g in rev.groupby("iid"):
            s = g["v"].asfreq("ME") if len(g) > 1 else g["v"]
            pub = g["pub"].reindex(s.index)
            yoy = s / s.shift(12) - 1
            s3 = s.rolling(3, min_periods=3).sum()
            yoy3 = s3 / s3.shift(12) - 1
            e = pd.DataFrame({"iid": iid, "pub": pub, "rev_yoy": yoy, "rev_yoy_3m": yoy3}).dropna(subset=["pub"])
            events.append(e)

    qm = df[df["m"].isin(["revenue_q", "gross_profit_q", "net_income_q", "eps_q", "equity", "total_assets", "liabilities"])]
    if not qm.empty:
        wide = qm.pivot_table(index=["iid", "pe"], columns="m", values="v", aggfunc="last").reset_index()
        pubs = qm.groupby(["iid", "pe"])["pub"].max().reset_index()
        wide = wide.merge(pubs, on=["iid", "pe"]).sort_values(["iid", "pe"])
        for col in ["revenue_q", "gross_profit_q", "net_income_q", "eps_q", "equity", "total_assets", "liabilities"]:
            if col not in wide:
                wide[col] = np.nan
        parts = []
        for iid, g in wide.groupby("iid"):
            g = g.copy()
            # 假設來源為單季數值（FinMind 綜合損益表）；四季加總為 TTM
            r4 = g["revenue_q"].rolling(4, min_periods=4).sum()
            eps4 = g["eps_q"].rolling(4, min_periods=4).sum()
            ni4 = g["net_income_q"].rolling(4, min_periods=4).sum()
            gp4 = g["gross_profit_q"].rolling(4, min_periods=4).sum()
            eq_avg = (g["equity"] + g["equity"].shift(4)) / 2
            prev_eps4 = eps4.shift(4)
            parts.append(pd.DataFrame({
                "iid": iid,
                "pub": g["pub"],
                "eps_ttm": eps4,
                "eps_growth": np.where(prev_eps4 > 0, eps4 / prev_eps4 - 1, np.nan),
                "gross_margin": gp4 / r4,
                "roe": ni4 / eq_avg,
                "debt_ratio": g["liabilities"] / g["total_assets"],
            }))
        events.extend(parts)
    if not events:
        return pd.DataFrame(columns=["iid", "pub"])
    ev = pd.concat(events, ignore_index=True)
    # 同一天多筆公告合併：後者補前者缺值
    ev = ev.sort_values(["iid", "pub"]).groupby(["iid", "pub"]).last().reset_index()
    # 讓每一列帶到目前為止最新的各指標（月營收與季報公告時間不同）
    val_cols = [c for c in ev.columns if c not in ("iid", "pub")]
    ev[val_cols] = ev.groupby("iid")[val_cols].ffill()
    return ev


def build_long(session: Session, panel: dict[str, pd.DataFrame], dates: list[pd.Timestamp] | None = None,
               horizons: tuple[int, ...] = ()) -> pd.DataFrame:
    """把 wide 特徵展開為 (date, iid) 長表，只保留 dates 指定的交易日。"""
    tech = technical_features(panel)
    flows = flow_features(session, panel)
    feats = {**tech, **flows}
    idx = panel["adj"].index
    dates = list(idx) if dates is None else [d for d in dates if d in idx]
    frames = []
    for name, w in feats.items():
        s = w.loc[dates].stack(future_stack=True)
        s.name = name
        frames.append(s)
    base = pd.concat(frames, axis=1)
    base.index.names = ["d", "iid"]
    base = base.reset_index()
    base["close"] = panel["close"].loc[dates].stack(future_stack=True).reindex(pd.MultiIndex.from_frame(base[["d", "iid"]])).values
    base = base[base["close"].notna()]  # 只保留當日有成交者
    for h in horizons:
        fwd = panel["adj"].shift(-h) / panel["adj"] - 1
        base[f"fwd_{h}"] = fwd.loc[dates].stack(future_stack=True).reindex(pd.MultiIndex.from_frame(base[["d", "iid"]])).values

    ev = fundamental_events(session)
    if not ev.empty:
        base = base.sort_values("d")
        ev = ev.sort_values("pub")
        base = pd.merge_asof(base, ev.rename(columns={"pub": "d_pub"}), left_on="d", right_on="d_pub", by="iid",
                             direction="backward", allow_exact_matches=True)
        base = base.drop(columns=["d_pub"])
    for col in ["rev_yoy", "rev_yoy_3m", "eps_ttm", "eps_growth", "gross_margin", "roe", "debt_ratio"]:
        if col not in base:
            base[col] = np.nan
    base["pe"] = np.where(base["eps_ttm"] > 0, base["close"] / base["eps_ttm"], np.nan)

    inst = pd.DataFrame(session.execute(select(Instrument.instrument_id, Instrument.asset_type, Instrument.etf_kind)).all(),
                        columns=["iid", "asset_type", "etf_kind"])
    base = base.merge(inst, on="iid", how="left")
    base["group"] = np.where(base["asset_type"] == "ETF",
                             np.where(base["etf_kind"].isin(["leveraged", "inverse"]), "etf_leveraged", "etf"), "stock")
    # ETF 不適用企業財務指標
    etf = base["asset_type"] == "ETF"
    base.loc[etf, ["rev_yoy", "rev_yoy_3m", "eps_ttm", "eps_growth", "gross_margin", "roe", "debt_ratio", "pe"]] = np.nan
    return base.sort_values(["d", "iid"]).reset_index(drop=True)


def snapshot_rows(long_df: pd.DataFrame, as_of: pd.Timestamp) -> list[dict]:
    day = long_df[long_df["d"] == as_of]
    cols = [c for c in FEATURE_LABELS if c in day.columns] + ["hist_days", "close"]
    out = []
    for r in day.itertuples(index=False):
        vals = {c: (None if pd.isna(getattr(r, c)) else float(getattr(r, c))) for c in cols}
        out.append({"instrument_id": int(r.iid), "as_of_date": as_of.date(), "feature_version": FEATURE_VERSION,
                    "values": vals})
    return out
