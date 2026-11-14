"""在 GitHub Actions 輸出資料狀態摘要（::notice / ::warning 註記，可在 Actions 頁面看到）。"""
import os
import sys
from pathlib import Path

os.environ.setdefault("ENABLE_SCHEDULER", "false")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from sqlalchemy import func, select  # noqa: E402

from app.db import session_scope  # noqa: E402
from app.models import BackfillState, DailyPrice, IngestRun, Instrument, ScoreSnapshot  # noqa: E402

with session_scope() as s:
    sub = select(IngestRun.dataset, func.max(IngestRun.id).label("mid")).group_by(IngestRun.dataset).subquery()
    for r in s.scalars(select(IngestRun).join(sub, IngestRun.id == sub.c.mid)):
        level = "notice" if r.status in ("success", "partial") else "warning"
        msg = f"{r.provider}/{r.dataset}: {r.status}，{r.rows} 筆，資料日 {r.data_date}；{(r.message or '')[:300]}"
        print(f"::{level} title=資料來源::{msg}".replace("\n", " "))
    n_inst = s.scalar(select(func.count()).select_from(Instrument))
    days = s.scalar(select(func.count(func.distinct(DailyPrice.trade_date))))
    latest = s.scalar(select(func.max(DailyPrice.trade_date)))
    n_latest = s.scalar(select(func.count()).select_from(DailyPrice).where(DailyPrice.trade_date == latest)) if latest else 0
    bf = s.scalar(select(func.count(func.distinct(BackfillState.instrument_id))).where(BackfillState.dataset == "prices"))
    sd = s.scalar(select(func.max(ScoreSnapshot.as_of_date)))
    elig = s.scalar(select(func.count()).select_from(ScoreSnapshot).where(ScoreSnapshot.as_of_date == sd,
                                                                         ScoreSnapshot.eligible.is_(True))) if sd else 0
    print(f"::notice title=資料庫摘要::標的 {n_inst} 檔；交易日 {days} 天；最新 {latest}（{n_latest} 檔有價）；"
          f"已回填股價 {bf} 檔；評分日 {sd}，合格 {elig} 檔")

from app.models import ModelRun  # noqa: E402

with session_scope() as s:
    for h in (1, 20, 60):
        m = s.scalar(select(ModelRun).where(ModelRun.horizon_days == h).order_by(ModelRun.id.desc()).limit(1))
        if not m:
            continue
        t = (m.metrics or {}).get("test", {})
        pm = (m.metrics or {}).get("probability", {})
        md, mo = t.get("model", {}), t.get("momentum", {})

        def f(v, pct=False):
            return "—" if v is None else (f"{v * 100:.1f}%" if pct else f"{v:.3f}")
        print(f"::notice title=模型 {h} 日::#{m.id} {m.model_version}；測試 {m.test_start}~{m.test_end}；"
              f"Rank IC {f(md.get('rank_ic'))}（動能 {f(mo.get('rank_ic'))}，t={f(md.get('rank_ic_t'))}）；"
              f"方向命中 {f(md.get('hit_rate'), True)}、平衡準確率 {f(md.get('balanced_acc'), True)}；"
              f"信心前10%上漲比例 {f(pm.get('top_decile_hit'), True)}（全體 {f(pm.get('test_up_rate'), True)}）；"
              f"Brier {f(pm.get('brier'))}/{f(pm.get('brier_baseline'))}、ECE {f(pm.get('ece'))}；"
              f"優於基準={m.beats_baseline}、機率校準={m.prob_calibrated}")
