"""相關公司：以近 120 個交易日的日報酬相關係數找出走勢最相近的標的。"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..models import Instrument, RelatedSnapshot
from .store import upsert

WINDOW = 120
MIN_OBS = 90
TOP = 8


def compute_related(session: Session, panel: dict[str, pd.DataFrame], as_of) -> int:
    insts = {i.instrument_id: i for i in session.scalars(select(Instrument).where(Instrument.status == "active"))}
    keep = [iid for iid, i in insts.items()
            if iid in panel["adj"].columns and not (i.asset_type == "ETF" and i.etf_kind in ("leveraged", "inverse"))]
    adj = panel["adj"].loc[:as_of, keep].iloc[-(WINDOW + 1):]
    ret = adj.pct_change(fill_method=None).iloc[1:]
    ok = ret.notna().sum() >= MIN_OBS
    ret = ret.loc[:, ok]
    if ret.shape[1] < 3:
        return 0
    # 去除大盤共同成分後再算相關，避免所有股票都只因大盤同漲同跌而高度相關
    mkt = ret.mean(axis=1)
    x = ret.sub(mkt, axis=0)
    x = (x - x.mean()) / x.std(ddof=0)
    m = x.fillna(0.0).to_numpy()
    n = ret.notna().astype(float).to_numpy()
    cnt = n.T @ n
    corr = (m.T @ m) / np.maximum(cnt, 1)
    np.fill_diagonal(corr, -np.inf)
    corr[cnt < MIN_OBS] = -np.inf
    ids = list(ret.columns)
    rows = []
    for k, iid in enumerate(ids):
        top = np.argsort(-corr[k])[:TOP]
        rel = [{"instrument_id": int(ids[j]), "corr": round(float(corr[k, j]), 3)} for j in top
               if np.isfinite(corr[k, j]) and corr[k, j] > 0.2]
        rows.append({"instrument_id": int(iid), "as_of_date": as_of.date(), "related": rel})
    session.execute(delete(RelatedSnapshot))
    upsert(session, RelatedSnapshot, rows, ["instrument_id"])
    return len(rows)
