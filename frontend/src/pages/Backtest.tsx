import { useState } from "react";
import { IS_STATIC } from "../api";
import { EquityChart, ReliabilityChart } from "../components/charts";
import { Badge, Disclaimer, ErrorBox, Loading, useApi } from "../components/ui";
import { dt, num, pct } from "../format";

const STRAT_LABEL: Record<string, string> = {
  score_base: "評分策略（基準成本）",
  score_high: "評分策略（高成本壓力）",
  momentum_base: "簡單動能（基準成本）",
  momentum_high: "簡單動能（高成本壓力）",
  universe_equal_weight: "等權全體合格（未計成本）",
  market_proxy: "市場 ETF 代理（買進持有）",
};
const GROUP_LABEL: Record<string, string> = { stock: "普通股", etf: "一般 ETF" };

export default function Backtest() {
  const bt = useApi<any[]>("/api/backtests");
  const md = useApi<any[]>("/api/models");
  const [group, setGroup] = useState("stock");
  const [h, setH] = useState(20);

  const runs = bt.data ?? [];
  const groups = [...new Set(runs.map((r) => r.group))].sort((a, b) => (a === "stock" ? -1 : b === "stock" ? 1 : 0));
  const run = runs.find((r) => r.group === group) ?? runs[0];
  const models = md.data ?? [];
  const model = models.find((m) => m.horizon_days === h);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>回測與模型監控</h1>
          <p>時間序列切分、下一交易日開盤成交、計入手續費／證交稅／滑價；所有結果都與簡單基準並列。</p>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <div className="card-head">
          <h2>策略回測</h2>
          {groups.length > 1 && (
            <div className="tabs">{groups.map((g) => <button key={g} className={(run?.group ?? "") === g ? "on" : ""} onClick={() => setGroup(g)}>{GROUP_LABEL[g] ?? g}</button>)}</div>
          )}
          <span className="spacer" />
          {run && !IS_STATIC && <a className="btn small" href={`/api/backtests/${run.run_id}/export.csv`}>匯出 CSV</a>}
        </div>
        {bt.error && <ErrorBox error={bt.error} />}
        {bt.loading && !bt.data && <Loading />}
        {bt.data && !run && <div className="empty">尚無回測結果（需先完成歷史回填並執行「重新計算」）</div>}
        {run && (
          <>
            <div className="muted small" style={{ marginBottom: 8 }}>
              {run.name} · {run.period.start} ~ {run.period.end} · 再平衡 {run.period.rebalances} 次 · 建立於 {dt(run.created_at)} · run {run.run_id}
            </div>
            <EquityChart curves={run.equity_curve} />
            <div className="table-wrap" style={{ marginTop: 12 }}>
              <table className="tbl">
                <thead>
                  <tr>
                    <th>策略</th><th className="n">累積報酬</th><th className="n">年化報酬</th><th className="n">年化波動</th>
                    <th className="n">最大回撤</th><th className="n">Sharpe</th><th className="n">Sortino</th><th className="n">勝率</th>
                    <th className="n">平均換手</th><th className="n">最後 20% 期間報酬</th><th className="n">最後 20% 回撤</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(run.metrics).map(([k, m]: [string, any]) => (
                    <tr key={k} style={k.startsWith("score") ? { fontWeight: 600 } : undefined}>
                      <td>{STRAT_LABEL[k] ?? k}</td>
                      <td className="n">{pct(m.total_return)}</td>
                      <td className="n">{pct(m.cagr)}</td>
                      <td className="n">{pct(m.ann_vol, 1, false)}</td>
                      <td className="n down">{pct(m.max_drawdown)}</td>
                      <td className="n">{m.sharpe == null ? "樣本不足" : num(m.sharpe)}</td>
                      <td className="n">{m.sortino == null ? "—" : num(m.sortino)}</td>
                      <td className="n">{pct(m.win_rate, 0, false)}</td>
                      <td className="n">{m.avg_turnover == null ? "—" : pct(m.avg_turnover, 0, false)}</td>
                      <td className="n">{pct(m.last20pct?.total_return)}</td>
                      <td className="n">{pct(m.last20pct?.max_drawdown)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <details style={{ marginTop: 10 }}>
              <summary className="small">回測假設</summary>
              <pre className="mono small" style={{ whiteSpace: "pre-wrap" }}>{JSON.stringify(run.assumptions, null, 2)}</pre>
            </details>
            <p className="muted small">評分權重為事前假設，未依回測結果調整。扣成本後若不如基準，應視為未證實有效。</p>
          </>
        )}
      </div>

      <div className="card">
        <div className="card-head">
          <h2>預測模型</h2>
          <div className="tabs">{[20, 60].map((x) => <button key={x} className={h === x ? "on" : ""} onClick={() => setH(x)}>{x} 交易日</button>)}</div>
          <span className="spacer" />
          {model && (
            <>
              {model.beats_baseline ? <Badge kind="ok">最終測試期優於基準</Badge> : <Badge kind="warn">未證實優於基準</Badge>}
              {model.interval_calibrated ? <Badge kind="ok">區間已校準</Badge> : <Badge kind="warn">區間未校準</Badge>}
              {model.prob_calibrated ? <Badge kind="ok">機率已校準</Badge> : <Badge kind="warn">機率不顯示</Badge>}
            </>
          )}
        </div>
        {md.error && <ErrorBox error={md.error} />}
        {md.data && !model && <div className="empty">尚無模型（資料不足或尚未執行重新計算）</div>}
        {model && <ModelDetail m={model} />}
      </div>
      <Disclaimer />
    </>
  );
}

function ModelDetail({ m }: { m: any }) {
  const t = m.metrics.test;
  const rows: [string, string][] = [["model", "梯度提升模型"], ["momentum", "簡單動能基準"], ["hist_mean", "歷史平均基準"], ["zero", "零報酬基準"]];
  const pr = m.metrics.probability;
  return (
    <>
      <div className="muted small" style={{ marginBottom: 10 }}>
        {m.model_version} · 訓練 {m.periods.train[0]} ~ {m.periods.train[1]} · 最終測試 {m.periods.test[0]} ~ {m.periods.test[1]}（未用於調參）
        · 樣本 訓練 {m.metrics.samples.train.toLocaleString()} / 校準 {m.metrics.samples.calib.toLocaleString()} / 測試 {m.metrics.samples.test.toLocaleString()} · 建立 {dt(m.created_at)}
      </div>
      <div className="grid g2">
        <div>
          <h3 style={{ marginBottom: 6 }}>最終測試集：模型 vs 基準</h3>
          <table className="tbl">
            <thead><tr><th>模型</th><th className="n">MAE</th><th className="n">RMSE</th><th className="n">Rank IC</th><th className="n">IC t 值</th><th className="n">方向命中</th><th className="n">平衡準確率</th></tr></thead>
            <tbody>
              {rows.map(([k, l]) => (
                <tr key={k} style={k === "model" ? { fontWeight: 600 } : undefined}>
                  <td>{l}</td><td className="n">{pct(t[k].mae, 2, false)}</td><td className="n">{pct(t[k].rmse, 2, false)}</td>
                  <td className="n">{t[k].rank_ic == null ? "—" : num(t[k].rank_ic, 3)}</td>
                  <td className="n">{t[k].rank_ic_t == null ? "—" : num(t[k].rank_ic_t, 2)}</td>
                  <td className="n">{pct(t[k].hit_rate, 1, false)}</td>
                  <td className="n">{t[k].balanced_acc == null ? "—" : pct(t[k].balanced_acc, 1, false)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="muted small">判定規則：Rank IC 需 &gt; 0.02 且高於動能基準、t 值 &gt; 2，且 MAE 不劣於歷史平均／零報酬基準。</p>

          <h3 style={{ margin: "14px 0 6px" }}>預測區間與機率校準</h3>
          <table className="tbl"><tbody>
            <tr><td>80% 區間實際覆蓋率</td><td className="n">{pct(m.metrics.interval.coverage, 1, false)}</td><td className="muted small">目標 80% ± 5%</td></tr>
            <tr><td>平均區間寬度</td><td className="n">{pct(m.metrics.interval.mean_width, 1, false)}</td><td /></tr>
            <tr><td>Brier score（模型／基準率）</td><td className="n">{num(pr.brier, 4)} / {num(pr.brier_baseline, 4)}</td><td className="muted small">需低於基準 1% 以上</td></tr>
            <tr><td>ECE（期望校準誤差）</td><td className="n">{num(pr.ece, 3)}</td><td className="muted small">需 &lt; 0.05</td></tr>
            <tr><td>上線後已成熟預測</td><td className="n">{m.live.matured}</td><td className="muted small">MAE {m.live.mae == null ? "—" : pct(m.live.mae, 2, false)}</td></tr>
          </tbody></table>
        </div>
        <div>
          <h3 style={{ marginBottom: 6 }}>機率可靠度圖（測試集）</h3>
          <ReliabilityChart points={pr.reliability} />
          <h3 style={{ margin: "14px 0 6px" }}>穩定性：分年度／分組 Rank IC</h3>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            {Object.entries(m.metrics.stability.by_year).map(([y, v]: [string, any]) => <Badge key={y}>{y}：{v == null ? "—" : num(v, 3)}</Badge>)}
            {Object.entries(m.metrics.stability.by_group).map(([g, v]: [string, any]) => <Badge key={g} kind="info">{g}：{v == null ? "—" : num(v, 3)}</Badge>)}
          </div>
          <h3 style={{ margin: "14px 0 6px" }}>Walk-forward 驗證（開發期）</h3>
          <table className="tbl">
            <thead><tr><th>驗證期間</th><th className="n">Rank IC</th><th className="n">MAE</th><th className="n">命中率</th></tr></thead>
            <tbody>{m.metrics.walk_forward.map((f: any, i: number) => (
              <tr key={i}><td>{f.start} ~ {f.end}</td><td className="n">{f.rank_ic == null ? "—" : num(f.rank_ic, 3)}</td><td className="n">{pct(f.mae, 2, false)}</td><td className="n">{pct(f.hit_rate, 1, false)}</td></tr>
            ))}</tbody>
          </table>
        </div>
      </div>
    </>
  );
}
