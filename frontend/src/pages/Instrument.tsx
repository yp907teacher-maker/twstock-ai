import { useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api";
import { FlowChart, PriceChart, RevenueChart } from "../components/charts";
import {
  Badge, CompDetail, Disclaimer, ErrorBox, Loading, ScorePill, useApi, useMeta, useToast, WatchButton,
} from "../components/ui";
import { dt, featureValue, num, pct, updown } from "../format";

const RANGES: [string, number][] = [["3 個月", 63], ["6 個月", 126], ["1 年", 250], ["3 年", 750], ["10 年", 2500]];
const FEATURE_GROUPS: [string, string[]][] = [
  ["趨勢與動能", ["ret_5", "ret_20", "ret_60", "ret_120", "px_ma20", "px_ma60", "ma20_slope", "rsi14"]],
  ["風險與流動性", ["vol_20", "vol_60", "down_vol_60", "max_dd_120", "turnover_20", "turnover_cv20", "vol_ratio"]],
  ["基本面（依公告日）", ["rev_yoy", "rev_yoy_3m", "eps_ttm", "eps_growth", "gross_margin", "roe", "debt_ratio", "pe"]],
  ["籌碼", ["foreign_20", "trust_20", "inst_persist", "margin_chg_20"]],
];

export default function InstrumentRoute() {
  const { id } = useParams();
  return <InstrumentPage key={id} id={id!} />;
}

function InstrumentPage({ id }: { id: string }) {
  const { meta } = useMeta();
  const toast = useToast();
  const [range, setRange] = useState(250);
  const [adjusted, setAdjusted] = useState(true);
  const d = useApi<any>(`/api/instruments/${id}`);
  const px = useApi<any>(`/api/instruments/${id}/prices?days=${range}&adjusted=${adjusted}`);
  const [report, setReport] = useState<any>(null);
  const [repLoading, setRepLoading] = useState(false);
  const [quoteBusy, setQuoteBusy] = useState(false);

  if (d.error) return <ErrorBox error={d.error} />;
  if (!d.data) return <Loading />;
  const { instrument: inst, score, features, predictions, last_price, quote } = d.data;
  const labels = meta?.feature_labels ?? {};

  const genReport = async (force = false) => {
    setRepLoading(true);
    try {
      setReport(await api.post(`/api/instruments/${id}/report?force=${force}`));
    } catch (e: any) {
      toast(e.message);
    } finally {
      setRepLoading(false);
    }
  };
  const refreshQuote = async () => {
    setQuoteBusy(true);
    try {
      const r = await api.post("/api/quotes/refresh", { instrument_ids: [inst.instrument_id] });
      if (r.errors?.length) toast(r.errors[0].error);
      d.reload();
    } catch (e: any) {
      toast(e.message);
    } finally {
      setQuoteBusy(false);
    }
  };
  const toggleExclude = async () => {
    try {
      if (inst.excluded_reason) await api.del(`/api/instruments/${id}/exclude`);
      else {
        const reason = window.prompt("停用原因（例如：資料異常待查）", "資料異常待查");
        if (!reason) return;
        await api.post(`/api/instruments/${id}/exclude`, { reason });
      }
      toast("已更新；下次計算時生效");
      d.reload();
    } catch (e: any) {
      toast(e.message);
    }
  };

  return (
    <>
      <div className="page-head">
        <div>
          <h1>
            {inst.name} <span className="sym" style={{ fontSize: 16 }}>{inst.symbol}</span>
          </h1>
          <p>
            {inst.market === "TWSE" ? "上市" : "上櫃"} · {inst.group_label}
            {inst.industry && inst.industry !== "ETF" ? ` · ${inst.industry}` : ""}
            {inst.status !== "active" && <> · <Badge kind="err">已下市／停止交易</Badge></>}
            {inst.excluded_reason && <> · <Badge kind="err">已停用：{inst.excluded_reason}</Badge></>}
          </p>
        </div>
        <div style={{ marginLeft: "auto", display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <div className="kpi" style={{ textAlign: "right", marginRight: 8 }}>
            <span className="value">{num(last_price?.close, 2)}</span>
            <span className="sub">收盤 {last_price?.date} · 來源 {last_price?.provider} · 抓取 {dt(last_price?.retrieved_at)}</span>
          </div>
          <WatchButton id={inst.instrument_id} />
          <button className="btn small" onClick={refreshQuote} disabled={quoteBusy || !meta?.fugle_enabled || meta?.demo_mode}
            title={meta?.fugle_enabled ? "以 Fugle 刷新盤中報價（不重新計算評分）" : "需設定 FUGLE_API_KEY"}>
            {quoteBusy ? "刷新中…" : "盤中刷新"}
          </button>
          {!meta?.static && <button className="btn small ghost" onClick={toggleExclude}>{inst.excluded_reason ? "恢復使用此標的資料" : "停用此標的資料"}</button>}
        </div>
      </div>
      {quote && (
        <div className="notice" style={{ marginBottom: 12 }}>
          盤中報價 <b className={updown(quote.change)}>{num(quote.price, 2)}</b>（{quote.change > 0 ? "+" : ""}{num(quote.change, 2)}）
          · 報價時間 {dt(quote.quote_time)} · {quote.is_realtime ? "即時" : "延遲"} · 來源 {quote.provider}。盤中刷新不更新評分與模型。
        </div>
      )}

      <div className="grid g3" style={{ marginBottom: 16 }}>
        <div className="card span2">
          <div className="card-head">
            <h2>價格與成交量</h2>
            <span className="spacer" />
            <div className="tabs">
              {RANGES.map(([l, n]) => <button key={n} className={range === n ? "on" : ""} onClick={() => setRange(n)}>{l}</button>)}
            </div>
            <label className="check"><input type="checkbox" checked={adjusted} onChange={(e) => setAdjusted(e.target.checked)} />還原權息</label>
          </div>
          {px.data ? <PriceChart rows={px.data.rows} /> : <Loading />}
          <div className="muted small">
            {adjusted ? "還原價 = 原始收盤 × 還原因子（依除權息／減資的參考價計算）。" : "顯示未還原原始價格；除權息日會出現跳空。"}
          </div>
        </div>
        <div className="card">
          <div className="card-head">
            <h2>均衡評分</h2>
            <span className="spacer" />
            <ScorePill v={score?.total} />
          </div>
          {!score ? <div className="empty">尚無評分</div> : (
            <>
              <div className="small" style={{ marginBottom: 8 }}>
                {score.eligible
                  ? <>{inst.group_label}組第 <b>{score.rank}</b> 名 · 資料覆蓋率 {(score.coverage * 100).toFixed(0)}% · {score.as_of_date}</>
                  : <span className="down">未列入正式排名：{score.exclusion_reasons.join("；")}</span>}
              </div>
              <CompDetail score={score} labels={meta?.component_labels ?? {}} />
              <p className="muted small" style={{ marginBottom: 0 }}>各分項為同組百分位平均（0–100）；缺失分項不以 0 計，權重重新分配。總分不是上漲機率。</p>
            </>
          )}
        </div>
      </div>

      {score && (
        <div className="grid g3" style={{ marginBottom: 16 }}>
          <div className="card">
            <h2>支持訊號</h2>
            {score.positives.length ? <ul className="signals pos">{score.positives.map((t: string) => <li key={t}>{t}</li>)}</ul> : <div className="muted">無明顯高於同組的訊號</div>}
          </div>
          <div className="card">
            <h2>反向訊號</h2>
            {score.negatives.length ? <ul className="signals neg">{score.negatives.map((t: string) => <li key={t}>{t}</li>)}</ul> : <div className="muted">無明顯低於同組的訊號</div>}
          </div>
          <div className="card">
            <h2>風險標籤</h2>
            {score.risk_flags.length ? <ul className="signals risk">{score.risk_flags.map((t: string) => <li key={t}>{t}</li>)}</ul> : <div className="muted">未偵測到特別風險標籤（不代表沒有風險）</div>}
          </div>
        </div>
      )}

      <div className="card" style={{ marginBottom: 16 }}>
        <div className="card-head">
          <h2>模型情境</h2>
          <span className="muted small">目標：含息總報酬；區間為 80% 預測區間</span>
        </div>
        {predictions.length === 0 ? (
          <div className="empty">尚無預測（可能因資料品質問題、歷史不足或模型尚未訓練）</div>
        ) : (
          <div className="grid g3">
            {predictions.map((p: any) => <PredCard key={p.horizon_days} p={p} />)}
          </div>
        )}
        {d.data.past_predictions.length > 0 && (
          <details style={{ marginTop: 12 }}>
            <summary className="small">歷史預測與實際結果（{d.data.past_predictions.length} 筆）</summary>
            <table className="tbl" style={{ marginTop: 8 }}>
              <thead><tr><th>預測日</th><th className="n">期間</th><th className="n">預測</th><th className="n">區間</th><th className="n">實際</th><th>落在區間</th></tr></thead>
              <tbody>
                {d.data.past_predictions.map((p: any, i: number) => (
                  <tr key={i}>
                    <td>{p.as_of_date}</td><td className="n">{p.horizon_days} 日</td>
                    <td className="n">{pct(p.expected_return)}</td>
                    <td className="n">{pct(p.lower)} ~ {pct(p.upper)}</td>
                    <td className={`n ${updown(p.actual_outcome)}`}>{pct(p.actual_outcome)}</td>
                    <td>{p.actual_outcome >= p.lower && p.actual_outcome <= p.upper ? "是" : "否"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </details>
        )}
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <div className="card-head">
          <h2>AI 研究報告</h2>
          <span className="muted small">{meta?.ai_enabled ? `Claude（${meta.ai_model}），按需生成並快取` : "未設定 ANTHROPIC_API_KEY：將產生程式化量化摘要"}</span>
          <span className="spacer" />
          {report?.mode === "fallback" && meta?.ai_enabled && <button className="btn small" onClick={() => genReport(true)} disabled={repLoading}>改用 AI 重新生成</button>}
          <button className="btn primary small" onClick={() => genReport(false)} disabled={repLoading}>{repLoading ? "生成中…" : report ? "重新整理" : "產生報告"}</button>
        </div>
        {!report ? <div className="muted">報告只引用資料庫中的評分、特徵與模型輸出；所有數字經過忠實度檢查。</div> : <Report r={report} />}
      </div>

      <div className="grid g2" style={{ marginBottom: 16 }}>
        <div className="card">
          <div className="card-head"><h2>特徵值</h2><span className="muted small">資料截止 {d.data.feature_date} · {d.data.feature_version}</span></div>
          {FEATURE_GROUPS.map(([g, ks]) => {
            const rows = ks.filter((k) => features[k] !== undefined);
            if (!rows.length) return null;
            return (
              <div key={g} style={{ marginBottom: 10 }}>
                <h3 style={{ marginBottom: 4 }}>{g}</h3>
                <table className="tbl"><tbody>
                  {rows.map((k) => (
                    <tr key={k}><td>{labels[k] ?? k}</td><td className="n">{featureValue(k, features[k])}</td></tr>
                  ))}
                </tbody></table>
              </div>
            );
          })}
        </div>
        <div className="grid" style={{ alignContent: "start" }}>
          {inst.asset_type !== "ETF" && (
            <div className="card">
              <div className="card-head"><h2>月營收</h2><span className="muted small">單位：億元；依公告期限（次月 10 日）才納入評分</span></div>
              <RevenueChart rows={d.data.revenue} />
              {d.data.eps.length > 0 && (
                <table className="tbl" style={{ marginTop: 8 }}>
                  <thead><tr><th>季度</th><th className="n">EPS</th><th>可用日（公告期限）</th></tr></thead>
                  <tbody>{d.data.eps.map((e: any) => <tr key={e.period_end}><td>{e.period_end}</td><td className="n">{num(e.value, 2)}</td><td>{e.public_at}</td></tr>)}</tbody>
                </table>
              )}
            </div>
          )}
          <div className="card">
            <div className="card-head"><h2>法人買賣超（近 60 日）</h2><span className="muted small">單位：張</span></div>
            <FlowChart rows={d.data.flows} />
          </div>
          {d.data.quality_issues.length > 0 && (
            <div className="card">
              <h2>資料品質問題</h2>
              <table className="tbl"><tbody>
                {d.data.quality_issues.map((q: any) => (
                  <tr key={q.id}><td><Badge kind={q.severity === "error" ? "err" : q.severity === "warn" ? "warn" : "info"}>{q.severity}</Badge></td>
                    <td className="mono">{q.code}</td><td>{q.date}</td><td className="small muted">{JSON.stringify(q.details)}</td></tr>
                ))}
              </tbody></table>
            </div>
          )}
        </div>
      </div>
      <Disclaimer />
    </>
  );
}

function PredCard({ p }: { p: any }) {
  const lo = p.lower ?? 0, hi = p.upper ?? 0, mid = p.expected_return ?? 0;
  const span = Math.max(Math.abs(lo), Math.abs(hi), 0.05) * 1.15;
  const x = (v: number) => `${((v + span) / (2 * span)) * 100}%`;
  return (
    <div className="pred-card">
      <div className="card-head" style={{ marginBottom: 4 }}>
        <h3>{p.horizon_days === 1 ? "次一交易日" : `未來 ${p.horizon_days} 個交易日`}</h3>
        <span className="spacer" />
        {p.model.beats_baseline ? <Badge kind="ok">樣本外優於基準</Badge> : <Badge kind="warn">未證實優於基準</Badge>}
      </div>
      <div className={`big ${updown(mid)}`}>{pct(mid)}</div>
      <div className="muted small">模型中位估計（不是保證）</div>
      <div className="range-bar" aria-label="預測區間">
        <div className="axis" />
        <div className="band" style={{ left: x(lo), width: `calc(${x(hi)} - ${x(lo)})` }} />
        <div className="zero" style={{ left: x(0) }} />
        <div className="mid" style={{ left: x(mid) }} />
      </div>
      <div className="small">
        80% 區間 {pct(lo)} ~ {pct(hi)}{" "}
        {p.model.interval_calibrated ? <Badge kind="ok">區間已校準</Badge> : <Badge kind="warn">區間未通過校準</Badge>}
      </div>
      <div className="small" style={{ marginTop: 4 }}>
        {p.prob_up !== null ? <>經校準上漲機率 <b>{(p.prob_up * 100).toFixed(0)}%</b></> : <span className="muted">上漲機率未通過校準，不顯示</span>}
      </div>
      <div className="muted small" style={{ marginTop: 8 }}>
        模型 {p.model.version} · 測試期 {p.model.test_period[0]} ~ {p.model.test_period[1]} · Rank IC {p.model.test_rank_ic?.toFixed(3) ?? "—"}（動能基準 {p.model.baseline_rank_ic?.toFixed(3) ?? "—"}）· 資料截點 {p.as_of_date}
      </div>
    </div>
  );
}

function Report({ r }: { r: any }) {
  const c = r.content;
  return (
    <div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 12 }}>
        <Badge kind={r.mode === "ai" ? "info" : undefined}>{r.mode === "ai" ? `AI 生成 · ${r.model}` : "程式化量化摘要"}</Badge>
        <Badge kind={r.fidelity?.passed ? "ok" : "err"}>{r.fidelity?.passed ? "數字忠實度檢查通過" : "忠實度檢查未通過"}</Badge>
        <Badge>資料截點 {r.as_of_date}</Badge>
        <Badge>提示版本 {r.prompt_version}</Badge>
        {r.model_versions?.map((m: string) => <Badge key={m}>{m}</Badge>)}
      </div>
      {r.fidelity?.fallback_reason && <div className="notice warn" style={{ marginBottom: 12 }}>{r.fidelity.fallback_reason}</div>}
      <div className="report-sec"><h3>摘要</h3><p>{c.summary}</p></div>
      <div className="grid g2">
        <div className="report-sec"><h3>支持證據</h3><ul>{c.supporting.map((t: string, i: number) => <li key={i}>{t}</li>)}</ul></div>
        <div className="report-sec"><h3>反向證據</h3><ul>{c.opposing.map((t: string, i: number) => <li key={i}>{t}</li>)}</ul></div>
      </div>
      <div className="report-sec"><h3>預測情境（模型觀點）</h3><ul>{c.scenarios.map((t: string, i: number) => <li key={i}>{t}</li>)}</ul></div>
      <div className="report-sec"><h3>主要風險</h3><ul>{c.risks.map((t: string, i: number) => <li key={i}>{t}</li>)}</ul></div>
      <div className="report-sec"><h3>資料完整度</h3><p>{c.data_completeness}</p></div>
    </div>
  );
}
