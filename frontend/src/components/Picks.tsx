import { useState } from "react";
import { Link } from "react-router-dom";
import { Badge, ErrorBox, Loading, ScorePill, useApi, WatchButton } from "./ui";
import { pct, updown } from "../format";

const COMP_SHORT: Record<string, string> = { trend: "趨勢", fundamental: "基本面", liquidity: "流動性", flow: "籌碼", value_risk: "估值風險" };

export default function PicksPanel({ group, watched, onWatch }: { group: string; watched: (id: number) => boolean; onWatch: (id: number, v: boolean) => void }) {
  const { data, error, loading } = useApi<any>(`/api/picks?group=${group}`);
  const [showRules, setShowRules] = useState(false);
  if (error) return <ErrorBox error={error} />;
  if (loading && !data) return <div className="card"><Loading text="系統偵測中…" /></div>;
  if (!data) return null;

  return (
    <div className="card picks" style={{ marginBottom: 16 }}>
      <div className="card-head">
        <h2>系統精選</h2>
        <span className="muted small">
          評分日 {data.as_of_date ?? "—"} · 掃描 {data.scanned ?? 0} 檔 · 選出 <b>{data.picks.length}</b> 檔
        </span>
        <span className="spacer" />
        <button className="btn small ghost" onClick={() => setShowRules((v) => !v)}>{showRules ? "收起規則" : "精選規則"}</button>
      </div>
      {showRules && (
        <ol className="small" style={{ marginTop: 0, paddingLeft: 20, color: "var(--ink-2)" }}>
          {data.rules.map((r: string) => <li key={r}>{r}</li>)}
        </ol>
      )}
      {data.picks.length === 0 ? (
        <div className="empty">{data.note || "今日暫無符合全部條件的標的"}</div>
      ) : (
        <div className="pick-grid">
          {data.picks.map((p: any) => (
            <div className="pick" key={p.instrument_id}>
              <div className="pick-head">
                <span className="pick-no">{p.pick_no}</span>
                <div style={{ minWidth: 0 }}>
                  <Link to={`/instrument/${p.instrument_id}`} className="pick-name">{p.name}</Link>
                  <div className="muted small"><span className="sym">{p.symbol}</span> · {p.market} · {p.industry}</div>
                </div>
                <span className="spacer" />
                <ScorePill v={p.total} />
              </div>
              <div className="pick-comps">
                {Object.entries(p.components).map(([k, v]: [string, any]) => (
                  <span key={k} className="small">{COMP_SHORT[k] ?? k} <b>{v == null ? "缺" : Math.round(v)}</b></span>
                ))}
              </div>
              <div className="pick-sec">
                <div className="small label-up">入選理由</div>
                <ul>{p.positives.length ? p.positives.map((t: string) => <li key={t}>{t}</li>) : <li className="muted">各分項均衡，無單一突出訊號</li>}</ul>
              </div>
              {(p.negatives.length > 0 || p.risk_flags.length > 0) && (
                <div className="pick-sec">
                  <div className="small label-down">需留意</div>
                  <ul>
                    {p.risk_flags.map((t: string) => <li key={t}>{t}</li>)}
                    {p.negatives.slice(0, 2).map((t: string) => <li key={t}>{t}</li>)}
                  </ul>
                </div>
              )}
              <div className="pick-foot">
                {p.pred20 ? (
                  <span className="small">
                    20 日模型 <b className={updown(p.pred20.expected_return)}>{pct(p.pred20.expected_return)}</b>
                    <span className="muted">（{pct(p.pred20.lower)} ~ {pct(p.pred20.upper)}）</span>
                    {!p.pred20.beats_baseline && <Badge kind="warn" title={p.model_note}>未證實</Badge>}
                  </span>
                ) : <span className="muted small">無模型預測</span>}
                <span className="spacer" />
                <WatchButton id={p.instrument_id} inList={watched(p.instrument_id)} onChange={(v) => onWatch(p.instrument_id, v)} />
              </div>
            </div>
          ))}
        </div>
      )}
      {data.near_misses.length > 0 && (
        <details style={{ marginTop: 12 }}>
          <summary className="small">高分但未入選（{data.near_misses.length} 檔）與原因</summary>
          <table className="tbl" style={{ marginTop: 8 }}>
            <tbody>
              {data.near_misses.map((m: any) => (
                <tr key={m.instrument_id}>
                  <td style={{ width: 200 }}><Link to={`/instrument/${m.instrument_id}`}>{m.name}</Link> <span className="sym">{m.symbol}</span></td>
                  <td className="n" style={{ width: 70 }}><ScorePill v={m.total} /></td>
                  <td className="small down">{m.rejected.join("；")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
      <p className="muted small" style={{ marginBottom: 0 }}>{data.picks.length > 0 && data.note}</p>
    </div>
  );
}
