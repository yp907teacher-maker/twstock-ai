"""20／60 交易日報酬預測與驗證（FR-07、PRD 第 10 節）。

流程：
1. 每 5 個交易日取樣一次，特徵在同日同組內轉百分位（降低市場狀態漂移）。
2. 時間順序切分：開發期（前 80% 日期）做 walk-forward 驗證；最後 20% 為最終測試集，不用於調參。
   訓練集與驗證／測試集之間保留 horizon 長度的間隔（embargo），避免標籤重疊造成洩漏。
3. 基準：零報酬、歷史平均、簡單動能（60 日報酬線性模型）。複雜模型必須在最終測試集勝過基準。
4. 區間：分位數模型 + conformal 校正（以開發期最後一段校準），以測試集實際覆蓋率檢驗。
5. 機率：分類模型 + isotonic 校準；Brier 未優於基準率時不顯示機率。
"""
from __future__ import annotations

import hashlib
import json
import logging

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LinearRegression
from sklearn.metrics import balanced_accuracy_score

from ..config import FEATURE_VERSION

log = logging.getLogger(__name__)

MODEL_FEATURES = [
    "ret_5", "ret_20", "ret_60", "ret_120", "px_ma20", "px_ma60", "ma20_slope", "rsi14", "vol_20", "vol_60",
    "down_vol_60", "max_dd_120", "turnover_20", "turnover_cv20", "vol_ratio", "rev_yoy", "rev_yoy_3m",
    "eps_growth", "gross_margin", "roe", "debt_ratio", "pe", "foreign_20", "trust_20", "inst_persist",
    "margin_chg_20",
]
PARAMS = {"max_iter": 200, "learning_rate": 0.05, "max_leaf_nodes": 31, "min_samples_leaf": 200,
          "l2_regularization": 1.0, "random_state": 42}
INTERVAL = 0.8
MAX_ROWS = 600_000


def target_definition(h: int) -> str:
    if h == 1:
        return "次一交易日含息報酬（今日收盤到下一交易日收盤，還原價）"
    return f"未來 {h} 個交易日含息總報酬（還原收盤價對還原收盤價）"


def model_version(h: int) -> str:
    digest = hashlib.sha1(json.dumps(PARAMS, sort_keys=True).encode()).hexdigest()[:8]
    return f"hgb-h{h}-{FEATURE_VERSION}-{digest}"


def prepare(long_df: pd.DataFrame, step: int = 5) -> pd.DataFrame:
    dates = sorted(long_df["d"].unique())
    keep = set(dates[::-1][::step])  # 由最新日往回每 step 日取一次，確保最新日在內
    df = long_df[long_df["d"].isin(keep)].copy()
    feats = [f for f in MODEL_FEATURES if f in df.columns]
    ranks = df.groupby(["d", "group"])[feats].rank(pct=True)
    ranks.columns = [f"r_{c}" for c in feats]
    df = pd.concat([df, ranks], axis=1)
    df["g_etf"] = (df["group"] == "etf").astype(float)
    df["g_lev"] = (df["group"] == "etf_leveraged").astype(float)
    return df


def xcols(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if c.startswith("r_")] + ["g_etf", "g_lev"]


def _rank_ic(d: pd.Series, y: pd.Series, p: pd.Series) -> tuple[float, float, int]:
    ics = []
    for _, idx in d.groupby(d).groups.items():
        if len(idx) >= 10:
            yy, pp = y.loc[idx], p.loc[idx]
            if yy.nunique() > 1 and pp.nunique() > 1:
                ics.append(spearmanr(yy, pp).statistic)
    if not ics:
        return float("nan"), float("nan"), 0
    ics = np.array(ics)
    t = ics.mean() / (ics.std(ddof=1) / np.sqrt(len(ics))) if len(ics) > 1 and ics.std(ddof=1) > 0 else float("nan")
    return float(ics.mean()), float(t), len(ics)


def _reg_metrics(df: pd.DataFrame, y: str, p: str) -> dict:
    e = df[p] - df[y]
    ic, t, n = _rank_ic(df["d"], df[y], df[p])
    sign_y = df[y] > 0
    sign_p = df[p] > 0
    hit = float((sign_y == sign_p).mean())
    bal = float(balanced_accuracy_score(sign_y, sign_p)) if sign_y.nunique() > 1 else float("nan")
    return {"mae": float(e.abs().mean()), "rmse": float(np.sqrt((e ** 2).mean())), "rank_ic": ic, "rank_ic_t": t,
            "ic_dates": n, "hit_rate": hit, "balanced_acc": bal}


def _ece(prob: np.ndarray, y: np.ndarray, bins: int = 10) -> float:
    edges = np.linspace(0, 1, bins + 1)
    ece = 0.0
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (prob >= lo) & (prob < hi if hi < 1 else prob <= hi)
        if m.any():
            ece += m.mean() * abs(prob[m].mean() - y[m].mean())
    return float(ece)


def _reliability(prob: np.ndarray, y: np.ndarray, bins: int = 10) -> list[dict]:
    edges = np.linspace(0, 1, bins + 1)
    out = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (prob >= lo) & (prob < hi if hi < 1 else prob <= hi)
        if m.sum() >= 30:
            out.append({"bin": f"{lo:.1f}-{hi:.1f}", "pred": float(prob[m].mean()), "actual": float(y[m].mean()),
                        "n": int(m.sum())})
    return out


class Fitted:
    """一組完成訓練與校準的模型。"""

    def __init__(self, h: int):
        self.h = h
        self.reg = HistGradientBoostingRegressor(loss="squared_error", **PARAMS)
        self.q_lo = HistGradientBoostingRegressor(loss="quantile", quantile=(1 - INTERVAL) / 2, **PARAMS)
        self.q_hi = HistGradientBoostingRegressor(loss="quantile", quantile=1 - (1 - INTERVAL) / 2, **PARAMS)
        self.clf = HistGradientBoostingClassifier(**PARAMS)
        self.iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
        self.qhat = 0.0
        self.cols: list[str] = []

    def fit(self, train: pd.DataFrame, calib: pd.DataFrame):
        y = f"fwd_{self.h}"
        # 全為缺值或常數的欄位不納入（例如尚未回填籌碼資料時）
        self.cols = [c for c in xcols(train) if train[c].nunique(dropna=True) > 1]
        X, yt = train[self.cols], train[y].clip(train[y].quantile(0.01), train[y].quantile(0.99))
        self.reg.fit(X, yt)
        self.q_lo.fit(X, train[y])
        self.q_hi.fit(X, train[y])
        self.clf.fit(X, (train[y] > 0).astype(int))
        # 校準（未參與訓練的較新資料）
        Xc = calib[self.cols]
        lo, hi = self.q_lo.predict(Xc), self.q_hi.predict(Xc)
        lo, hi = np.minimum(lo, hi), np.maximum(lo, hi)
        score = np.maximum(lo - calib[y].values, calib[y].values - hi)
        n = len(score)
        self.qhat = float(np.quantile(score, min(1.0, np.ceil((n + 1) * INTERVAL) / n))) if n else 0.0
        self.iso.fit(self.clf.predict_proba(Xc)[:, 1], (calib[y] > 0).astype(int))
        return self

    def predict(self, df: pd.DataFrame) -> pd.DataFrame:
        X = df[self.cols]
        lo, hi = self.q_lo.predict(X), self.q_hi.predict(X)
        lo, hi = np.minimum(lo, hi) - self.qhat, np.maximum(lo, hi) + self.qhat
        return pd.DataFrame({"pred": self.reg.predict(X), "lo": lo, "hi": hi,
                             "prob": self.iso.predict(self.clf.predict_proba(X)[:, 1])}, index=df.index)


def evaluate(long_df: pd.DataFrame, h: int) -> dict:
    """完整驗證；回傳指標與判定，以及可直接用於最新預測的模型。"""
    y = f"fwd_{h}"
    data = prepare(long_df)
    lab = data[np.isfinite(data[y])].copy()
    if len(lab) > MAX_ROWS:
        lab = lab.sample(MAX_ROWS, random_state=0).sort_values("d")
    dates = np.array(sorted(lab["d"].unique()))
    if len(dates) < 60:
        return {"status": "insufficient_data", "reason": f"可用取樣日期僅 {len(dates)} 個，不足以驗證"}
    gap = int(np.ceil(h / 5)) + 1  # embargo（以取樣日期計）
    n_test = int(len(dates) * 0.2)
    dev_dates, test_dates = dates[: -n_test], dates[-n_test:]
    dev_train_dates = dev_dates[:-gap]

    # ---- walk-forward（開發期）----
    folds = []
    k = 4
    fold_len = len(dev_dates) // (k + 2)
    for i in range(k):
        val = dev_dates[fold_len * (i + 2): fold_len * (i + 3)]
        tr = dev_dates[: max(0, fold_len * (i + 2) - gap)]
        if len(tr) < 20 or len(val) == 0:
            continue
        cal_cut = int(len(tr) * 0.85)
        tr_df = lab[lab["d"].isin(tr[:cal_cut])]
        cal_df = lab[lab["d"].isin(tr[cal_cut + gap:])] if cal_cut + gap < len(tr) else lab[lab["d"].isin(tr[cal_cut:])]
        val_df = lab[lab["d"].isin(val)].copy()
        m = Fitted(h).fit(tr_df, cal_df)
        val_df["p"] = m.predict(val_df)["pred"].values
        met = _reg_metrics(val_df, y, "p")
        met.update({"start": str(pd.Timestamp(val[0]).date()), "end": str(pd.Timestamp(val[-1]).date())})
        folds.append(met)

    # ---- 最終測試 ----
    cal_cut = int(len(dev_train_dates) * 0.85)
    tr_df = lab[lab["d"].isin(dev_train_dates[:cal_cut])]
    cal_df = lab[lab["d"].isin(dev_train_dates[min(cal_cut + gap, len(dev_train_dates) - 1):])]
    te = lab[lab["d"].isin(test_dates)].copy()
    model = Fitted(h).fit(tr_df, cal_df)
    pr = model.predict(te)
    te["p_model"] = pr["pred"].values
    te["p_zero"] = 0.0
    te["p_mean"] = float(tr_df[y].mean())
    mom = LinearRegression().fit(tr_df[["r_ret_60"]].fillna(0.5), tr_df[y])
    te["p_mom"] = mom.predict(te[["r_ret_60"]].fillna(0.5))
    metrics = {name: _reg_metrics(te, y, col) for name, col in
               [("model", "p_model"), ("zero", "p_zero"), ("hist_mean", "p_mean"), ("momentum", "p_mom")]}
    # 區間
    inside = (te[y] >= pr["lo"]) & (te[y] <= pr["hi"])
    interval = {"target": INTERVAL, "coverage": float(inside.mean()), "mean_width": float((pr["hi"] - pr["lo"]).mean())}
    # 機率
    yb = (te[y] > 0).astype(int).values
    base_rate = float((tr_df[y] > 0).mean())
    prob = pr["prob"].values
    brier = float(np.mean((prob - yb) ** 2))
    brier_base = float(np.mean((base_rate - yb) ** 2))
    te["prob"] = prob
    top = te[te.groupby("d")["prob"].rank(pct=True, ascending=False) <= 0.1]
    top_hit = float((top[y] > 0).mean()) if len(top) else None
    top_ret = float(top[y].mean()) if len(top) else None
    prob_m = {"top_decile_hit": top_hit, "top_decile_mean_return": top_ret, "test_up_rate": float(yb.mean()),
              "brier": brier, "brier_baseline": brier_base, "ece": _ece(prob, yb), "base_rate": base_rate,
              "n": int(len(yb)), "reliability": _reliability(prob, yb)}
    # 穩定性：分年、分組
    te["year"] = pd.to_datetime(te["d"]).dt.year
    stability = {
        "by_year": {str(yy): _rank_ic(g["d"], g[y], g["p_model"])[0] for yy, g in te.groupby("year")},
        "by_group": {gg: _rank_ic(g["d"], g[y], g["p_model"])[0] for gg, g in te.groupby("group")},
    }
    mm, best_base = metrics["model"], min(metrics["hist_mean"]["mae"], metrics["zero"]["mae"])
    beats = bool(
        np.isfinite(mm["rank_ic"]) and mm["rank_ic"] > max(0.02, metrics["momentum"]["rank_ic"] if np.isfinite(metrics["momentum"]["rank_ic"]) else 0)
        and np.isfinite(mm["rank_ic_t"]) and mm["rank_ic_t"] > 2 and mm["mae"] <= best_base * 1.01
    )
    interval_ok = abs(interval["coverage"] - INTERVAL) <= 0.05
    prob_ok = brier < brier_base * 0.99 and prob_m["ece"] < 0.05
    return {
        "status": "ok",
        "horizon": h,
        "model_version": model_version(h),
        "params": PARAMS,
        "periods": {
            "train_start": str(pd.Timestamp(dates[0]).date()),
            "train_end": str(pd.Timestamp(dev_train_dates[-1]).date()),
            "test_start": str(pd.Timestamp(test_dates[0]).date()),
            "test_end": str(pd.Timestamp(test_dates[-1]).date()),
        },
        "samples": {"train": int(len(tr_df)), "calib": int(len(cal_df)), "test": int(len(te))},
        "walk_forward": folds,
        "test": metrics,
        "interval": interval,
        "probability": prob_m,
        "stability": stability,
        "beats_baseline": beats,
        "interval_calibrated": bool(interval_ok),
        "prob_calibrated": bool(prob_ok),
        "_data": data,
    }


def fit_live(data: pd.DataFrame, h: int) -> Fitted:
    """以所有已有標籤的資料重新訓練（保留最近 15% 日期做校準），供最新一日預測。"""
    y = f"fwd_{h}"
    lab = data[np.isfinite(data[y])]
    if len(lab) > MAX_ROWS:
        lab = lab.sample(MAX_ROWS, random_state=0)
    dates = np.array(sorted(lab["d"].unique()))
    gap = int(np.ceil(h / 5)) + 1
    cut = int(len(dates) * 0.85)
    tr = lab[lab["d"].isin(dates[: max(1, cut - gap)])]
    cal = lab[lab["d"].isin(dates[cut:])]
    return Fitted(h).fit(tr, cal)
