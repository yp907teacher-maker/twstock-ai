import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Badge, CompBars, Disclaimer, ErrorBox, InstName, Loading, ScorePill, useApi, useMeta, WatchButton } from "../components/ui";
import { pct, updown, yi } from "../format";
import PicksPanel from "../components/Picks";

const GROUPS: [string, string][] = [
  ["stock", "普通股"],
  ["etf", "一般 ETF"],
  ["etf_leveraged", "槓桿／反向 ETF"],
];
const COMPS = ["trend", "fundamental", "liquidity", "flow", "value_risk"];

export default function Screener() {
  const { meta } = useMeta();
  const [sp, setSp] = useSearchParams();
  const group = sp.get("group") || "stock";
  const [f, setF] = useState({
    min_total: "",
    market: "",
    industry: "",
    include_ineligible: false,
    hide_risk_flags: false,
    min_coverage: "",
    trend: "",
    fundamental: "",
    liquidity: "",
    flow: "",
    value_risk: "",
  });
  const [sort, setSort] = useState<{ k: string; desc: boolean }>({ k: "total", desc: true });
  const [watch, setWatch] = useState<Record<number, boolean>>({});

  const url = useMemo(() => {
    const p = new URLSearchParams({ group, sort: sort.k, desc: String(sort.desc), limit: "300" });
    if (f.min_total) p.set("min_total", f.min_total);
    if (f.market) p.set("market", f.market);
    if (f.industry) p.set("industry", f.industry);
    if (f.include_ineligible) p.set("include_ineligible", "true");
    if (f.hide_risk_flags) p.set("hide_risk_flags", "true");
    if (f.min_coverage) p.set("min_coverage", String(Number(f.min_coverage) / 100));
    for (const c of COMPS) if ((f as any)[c]) p.set(`min_${c}`, (f as any)[c]);
    return `/api/screener?${p}`;
  }, [group, f, sort]);
  const { data, error, loading } = useApi<any>(url);
  const wl = useApi<any>("/api/watchlist");
  const base = useMemo(() => new Set<number>((wl.data?.rows ?? []).map((r: any) => r.instrument_id)), [wl.data]);
  const isWatched = (id: number) => (id in watch ? watch[id] : base.has(id));

  const set = (k: string, v: any) => setF((o) => ({ ...o, [k]: v }));
  const th = (k: string, label: string, n = true) => (
    <th className={`sortable ${n ? "n" : ""}`} onClick={() => setSort((s) => ({ k, desc: s.k === k ? !s.desc : true }))}>
      {label}
      {sort.k === k ? (sort.desc ? " ▼" : " ▲") : ""}
    </th>
  );
  const labels = meta?.component_labels ?? {};

  return (
    <>
      <div className="page-head">
        <div>
          <h1>選股工作台</h1>
          <p>系統每天收盤後自動偵測並選出研究候選；下方保留完整排名供自訂篩選。評分權重：趨勢 30%、基本面 25%、流動性 15%、籌碼 15%、估值與風險 15%（初始假設）。</p>
        </div>
        <div className="tabs" role="tablist" style={{ marginLeft: "auto" }}>
          {GROUPS.map(([k, l]) => (
            <button key={k} className={group === k ? "on" : ""} onClick={() => setSp({ group: k })}>{l}</button>
          ))}
        </div>
      </div>

      <PicksPanel group={group} watched={isWatched} onWatch={(id, v) => setWatch((s) => ({ ...s, [id]: v }))} />

      <h2 style={{ margin: "8px 0 10px" }}>完整排名與自訂篩選</h2>

      <div className="card" style={{ marginBottom: 16 }}>
        <div className="filters">
          <label className="field">最低總分<input type="number" min={0} max={100} value={f.min_total} onChange={(e) => set("min_total", e.target.value)} /></label>
          <label className="field">市場
            <select value={f.market} onChange={(e) => set("market", e.target.value)}>
              <option value="">全部</option><option value="TWSE">上市</option><option value="TPEx">上櫃</option>
            </select>
          </label>
          {group === "stock" && (
            <label className="field">產業
              <select value={f.industry} onChange={(e) => set("industry", e.target.value)}>
                <option value="">全部</option>
                {(data?.industries ?? []).filter((x: string) => x !== "ETF").map((x: string) => <option key={x}>{x}</option>)}
              </select>
            </label>
          )}
          {COMPS.filter((c) => group === "stock" || c !== "fundamental").map((c) => (
            <label className="field" key={c}>{labels[c] ?? c} ≥
              <input type="number" min={0} max={100} value={(f as any)[c]} onChange={(e) => set(c, e.target.value)} />
            </label>
          ))}
          <label className="field">覆蓋率 ≥ %<input type="number" min={0} max={100} value={f.min_coverage} onChange={(e) => set("min_coverage", e.target.value)} /></label>
          <label className="check"><input type="checkbox" checked={f.hide_risk_flags} onChange={(e) => set("hide_risk_flags", e.target.checked)} />隱藏有風險標籤者</label>
          <label className="check"><input type="checkbox" checked={f.include_ineligible} onChange={(e) => set("include_ineligible", e.target.checked)} />顯示被排除標的</label>
        </div>
        {group === "etf_leveraged" && (
          <div className="notice warn" style={{ marginTop: 12 }}>槓桿／反向 ETF 每日重設倍數，長期報酬會偏離標的指數；此組與一般 ETF 分開排名，不納入一般均衡排名。</div>
        )}
      </div>

      <div className="card">
        <div className="card-head">
          <h2>{data ? `${data.total_rows} 檔` : ""}</h2>
          {data && <span className="muted small">評分日 {data.as_of_date} · 另有 {data.excluded_count} 檔未通過硬性排除條件</span>}
          <span className="spacer" />
          <span className="muted small">{data?.note}</span>
        </div>
        {error && <ErrorBox error={error} />}
        {loading && !data && <Loading />}
        {data && data.rows.length === 0 && <div className="empty">暫無符合條件標的</div>}
        {data && data.rows.length > 0 && (
          <div className="table-wrap">
            <table className="tbl">
              <thead>
                <tr>
                  <th className="n">名次</th>
                  {th("symbol", "標的", false)}
                  {th("total", "總分")}
                  <th>分項</th>
                  {th("ret_20", "20 日")}
                  {th("ret_60", "60 日")}
                  {th("vol_60", "波動率")}
                  {th("turnover_20", "日均成交")}
                  {group === "stock" && th("pe", "本益比")}
                  {group === "stock" && th("rev_yoy", "營收年增")}
                  <th className="n">覆蓋率</th>
                  <th>入選理由／排除原因</th>
                  <th>風險</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {data.rows.map((r: any) => (
                  <tr key={r.instrument_id} style={r.score.eligible ? undefined : { opacity: 0.72 }}>
                    <td className="n">{r.score.rank ?? "—"}</td>
                    <td><InstName i={r} /></td>
                    <td className="n"><ScorePill v={r.score.total} /></td>
                    <td><CompBars score={r.score} /></td>
                    <td className={`n ${updown(r.features.ret_20)}`}>{pct(r.features.ret_20)}</td>
                    <td className={`n ${updown(r.features.ret_60)}`}>{pct(r.features.ret_60)}</td>
                    <td className="n">{pct(r.features.vol_60, 0, false)}</td>
                    <td className="n">{yi(r.features.turnover_20)}</td>
                    {group === "stock" && <td className="n">{r.features.pe ? r.features.pe.toFixed(1) : "—"}</td>}
                    {group === "stock" && <td className={`n ${updown(r.features.rev_yoy)}`}>{pct(r.features.rev_yoy)}</td>}
                    <td className="n">{(r.score.coverage * 100).toFixed(0)}%</td>
                    <td className="small" style={{ maxWidth: 300 }}>
                      {r.score.eligible
                        ? r.score.positives.slice(0, 2).join("；") || <span className="muted">無突出訊號</span>
                        : <span className="down">{r.score.exclusion_reasons.join("；")}</span>}
                    </td>
                    <td>
                      {r.score.risk_flags.length ? <Badge kind="warn" title={r.score.risk_flags.join("\n")}>{r.score.risk_flags.length}</Badge> : <span className="muted">—</span>}
                    </td>
                    <td>
                      <WatchButton id={r.instrument_id} inList={isWatched(r.instrument_id)}
                        onChange={(v) => setWatch((s) => ({ ...s, [r.instrument_id]: v }))} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
      <Disclaimer />
    </>
  );
}
