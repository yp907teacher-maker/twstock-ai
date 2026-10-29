import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Disclaimer, ErrorBox, Loading, ScorePill, useApi } from "../components/ui";
import { pct, updown, yi } from "../format";

const KEY = "twstock-followed-sectors";
const readFollow = (): string[] => {
  try {
    return JSON.parse(localStorage.getItem(KEY) || "[]");
  } catch {
    return [];
  }
};
let memFollow: string[] = readFollow();

type SortKey = "turnover" | "ret_1" | "ret_5" | "ret_20" | "ret_60" | "avg_score";
const SORTS: [SortKey, string][] = [["turnover", "成交金額"], ["ret_1", "今日"], ["ret_5", "5 日"], ["ret_20", "20 日"], ["ret_60", "60 日"], ["avg_score", "平均分數"]];

function heat(v: number | null | undefined) {
  if (v === null || v === undefined) return undefined;
  const a = Math.min(Math.abs(v) / 0.05, 1) * 0.35;
  return { background: v >= 0 ? `rgba(198,47,40,${a})` : `rgba(29,133,73,${a})` };
}

export default function Sectors() {
  const { data, error, loading } = useApi<any>("/api/sectors");
  const [follow, setFollow] = useState<string[]>(memFollow);
  const [sort, setSort] = useState<SortKey>("turnover");
  const [onlyFollow, setOnlyFollow] = useState(false);
  const toggle = (ind: string) => {
    const next = follow.includes(ind) ? follow.filter((x) => x !== ind) : [...follow, ind];
    memFollow = next;
    setFollow(next);
    try {
      localStorage.setItem(KEY, JSON.stringify(next));
    } catch {
      /* 無法儲存時只保留在本次瀏覽 */
    }
  };
  const rows = useMemo(() => {
    const r = [...(data?.rows ?? [])];
    r.sort((a: any, b: any) => (b[sort] ?? -1e9) - (a[sort] ?? -1e9));
    return r;
  }, [data, sort]);

  if (error) return <ErrorBox error={error} />;
  if (loading && !data) return <Loading />;
  const followed = rows.filter((r: any) => follow.includes(r.industry));
  const shown = onlyFollow ? followed : rows;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>類股表現</h1>
          <p>資料日 {data?.as_of_date ?? "—"}（今日漲跌對比 {data?.prev_date ?? "—"}）。按 ☆ 關注類股，會固定顯示在上方。</p>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <div className="card-head"><h2>關注類股</h2><span className="muted small">關注清單存在你的瀏覽器</span></div>
        {followed.length === 0 ? (
          <div className="muted">尚未關注任何類股。在下方表格按 ☆ 加入。</div>
        ) : (
          <div className="sector-grid">
            {followed.map((r: any) => (
              <div className="sector-card" key={r.industry}>
                <div className="card-head" style={{ marginBottom: 6 }}>
                  <b>{r.industry}</b>
                  <span className="spacer" />
                  <button className="btn small ghost" onClick={() => toggle(r.industry)} title="取消關注">★</button>
                </div>
                <div className="sector-rets">
                  {([["今日", r.ret_1], ["5 日", r.ret_5], ["20 日", r.ret_20], ["60 日", r.ret_60]] as [string, number | null][]).map(([l, v]) => (
                    <div key={l} style={heat(v)}><span className="small muted">{l}</span><b className={updown(v)}>{pct(v, 1)}</b></div>
                  ))}
                </div>
                <div className="small muted" style={{ margin: "6px 0" }}>
                  漲 <span className="up">{r.advancers}</span>／跌 <span className="down">{r.decliners}</span> · 成交 {yi(r.turnover)} · 合格 {r.eligible}/{r.count} 檔
                </div>
                <div className="small">
                  {r.leaders.length ? r.leaders.map((l: any) => (
                    <div key={l.instrument_id} style={{ display: "flex", gap: 6, alignItems: "center", padding: "2px 0" }}>
                      <Link to={`/instrument/${l.instrument_id}`}>{l.name}</Link><span className="sym">{l.symbol}</span>
                      <span className="spacer" style={{ flex: 1 }} />
                      <span className={updown(l.ret_20)}>{pct(l.ret_20, 1)}</span><ScorePill v={l.total} />
                    </div>
                  )) : <span className="muted">無合格標的</span>}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="card">
        <div className="card-head">
          <h2>全部類股（{rows.length}）</h2>
          <span className="spacer" />
          <label className="check"><input type="checkbox" checked={onlyFollow} onChange={(e) => setOnlyFollow(e.target.checked)} />只看關注</label>
          <div className="tabs">{SORTS.map(([k, l]) => <button key={k} className={sort === k ? "on" : ""} onClick={() => setSort(k)}>{l}</button>)}</div>
        </div>
        <div className="table-wrap">
          <table className="tbl">
            <thead>
              <tr>
                <th /><th>類股</th><th className="n">檔數</th><th className="n">今日</th><th className="n">5 日</th><th className="n">20 日</th><th className="n">60 日</th>
                <th className="n">漲／跌</th><th className="n">成交金額</th><th className="n">平均分數</th><th>分數領先</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((r: any) => (
                <tr key={r.industry}>
                  <td><button className="btn small ghost" onClick={() => toggle(r.industry)} title={follow.includes(r.industry) ? "取消關注" : "關注"}>{follow.includes(r.industry) ? "★" : "☆"}</button></td>
                  <td><b>{r.industry}</b></td>
                  <td className="n">{r.count}</td>
                  {(["ret_1", "ret_5", "ret_20", "ret_60"] as const).map((k) => (
                    <td key={k} className={`n ${updown(r[k])}`} style={heat(r[k])}>{pct(r[k], 1)}</td>
                  ))}
                  <td className="n"><span className="up">{r.advancers}</span>／<span className="down">{r.decliners}</span></td>
                  <td className="n">{yi(r.turnover)}</td>
                  <td className="n"><ScorePill v={r.avg_score} /></td>
                  <td className="small">{r.leaders.slice(0, 2).map((l: any) => <Link key={l.instrument_id} to={`/instrument/${l.instrument_id}`} style={{ marginRight: 8 }}>{l.name}</Link>)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="muted small" style={{ marginBottom: 0 }}>{data?.note}</p>
      </div>
      <Disclaimer />
    </>
  );
}
