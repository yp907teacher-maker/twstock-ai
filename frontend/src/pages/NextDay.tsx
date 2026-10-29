import { useMemo, useState } from "react";
import { Badge, Disclaimer, ErrorBox, InstName, Loading, ScorePill, useApi, WatchButton } from "../components/ui";
import { pct, updown } from "../format";

const GROUPS: [string, string][] = [["stock", "普通股"], ["etf", "一般 ETF"], ["etf_leveraged", "槓桿／反向 ETF"]];

function ConfBar({ p, trusted }: { p: number | null; trusted: boolean }) {
  if (p === null || p === undefined) return <span className="muted">—</span>;
  const v = Math.round(p * 100);
  const cls = v >= 60 ? "hi" : v >= 53 ? "mid" : v <= 45 ? "lo" : "";
  return (
    <div className={`conf ${cls} ${trusted ? "" : "untrusted"}`} title={trusted ? "" : "模型機率未通過校準，此數值僅供參考"}>
      <div className="conf-track"><i style={{ width: `${v}%` }} /><span className="conf-mid" /></div>
      <b>{v}%</b>
    </div>
  );
}

export default function NextDay() {
  const [group, setGroup] = useState("stock");
  const [showAll, setShowAll] = useState(false);
  const { data, error, loading } = useApi<any>(`/api/nextday?group=${group}&limit=5000&include_ineligible=${showAll}`);
  const [sort, setSort] = useState<"score" | "prob" | "ret">("score");
  const sorted = useMemo(() => {
    const rows = [...(data?.rows ?? [])];
    const key = (r: any) => (sort === "score" ? r.score?.total : sort === "prob" ? r.prob_up : r.expected_return);
    rows.sort((a, b) => (key(b) ?? -1e9) - (key(a) ?? -1e9) || (b.prob_up ?? 0) - (a.prob_up ?? 0));
    return rows;
  }, [data, sort]);
  const m = data?.model;
  const trusted = !!m?.prob_calibrated;
  const [n, setN] = useState(30);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>次日預測名單</h1>
          <p>列出每檔「下一個交易日收盤會比今天高」的模型信心度，可依均衡總分、信心度或預估報酬排序。資料截點 {data?.as_of_date ?? "—"} 收盤，預測對象為下一個交易日。</p>
        </div>
        <div className="tabs" style={{ marginLeft: "auto" }}>
          {GROUPS.map(([k, l]) => <button key={k} className={group === k ? "on" : ""} onClick={() => setGroup(k)}>{l}</button>)}
        </div>
      </div>

      {error && <ErrorBox error={error} />}
      {loading && !data && <Loading />}
      {data && !m && <div className="card empty">{data.note}</div>}

      {m && (
        <div className={`notice ${trusted ? "" : "warn"}`} style={{ marginBottom: 16 }}>
          {trusted ? (
            <>信心度已通過歷史校準：模型說 60% 的股票，過去實際約有六成在隔天上漲。</>
          ) : (
            <><b>注意：次日模型目前未通過校準，信心度僅供參考。</b> 短期股價接近隨機，模型在最終測試期間沒有穩定勝過「直接猜上漲比例」的簡單方法。</>
          )}
          {" "}過去測試期間（{m.test_period[0]} ~ {m.test_period[1]}），每天信心度最高的前 10% 股票，隔天實際上漲比例為
          <b> {pct(m.top_decile_hit, 1, false)}</b>，平均報酬 {pct(m.top_decile_mean_return, 2)}；同期全體上漲比例 {pct(m.test_up_rate, 1, false)}。
        </div>
      )}

      {m && (
        <div className="card">
          <div className="card-head">
            <h2>{data.total_rows} 檔</h2>
            <span className="muted small">{showAll ? "含未通過排除條件者" : "僅列通過資料品質、流動性等排除條件的標的"}</span>
            <span className="spacer" />
            <div className="tabs">
              {([["score", "依總分"], ["prob", "依信心度"], ["ret", "依預估報酬"]] as const).map(([k, l]) => (
                <button key={k} className={sort === k ? "on" : ""} onClick={() => setSort(k)}>{l}</button>
              ))}
            </div>
            <label className="check"><input type="checkbox" checked={showAll} onChange={(e) => setShowAll(e.target.checked)} />顯示全部</label>
            {m.beats_baseline ? <Badge kind="ok">樣本外優於基準</Badge> : <Badge kind="warn">未證實優於基準</Badge>}
            {trusted ? <Badge kind="ok">機率已校準</Badge> : <Badge kind="warn">機率未校準</Badge>}
          </div>
          {data.rows.length === 0 ? <div className="empty">暫無資料</div> : (
            <div className="table-wrap">
              <table className="tbl">
                <thead>
                  <tr>
                    <th className="n">#</th><th>標的</th><th style={{ width: 200 }}>信心度（隔日上漲機率）</th>
                    <th className="n">預估報酬</th><th className="n">80% 區間</th><th className="n">均衡總分</th><th>風險</th><th />
                  </tr>
                </thead>
                <tbody>
                  {sorted.slice(0, n).map((r: any, i: number) => (
                    <tr key={r.instrument_id}>
                      <td className="n">{i + 1}</td>
                      <td><InstName i={r} /></td>
                      <td><ConfBar p={r.prob_up} trusted={trusted} /></td>
                      <td className={`n ${updown(r.expected_return)}`}>{pct(r.expected_return, 2)}</td>
                      <td className="n small">{pct(r.lower, 1)} ~ {pct(r.upper, 1)}</td>
                      <td className="n"><ScorePill v={r.score?.total} /></td>
                      <td>{r.score?.risk_flags?.length ? <Badge kind="warn" title={r.score.risk_flags.join("\n")}>{r.score.risk_flags.length}</Badge> : <span className="muted">—</span>}</td>
                      <td><WatchButton id={r.instrument_id} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {sorted.length > n && <div style={{ textAlign: "center", marginTop: 10 }}><button className="btn small" onClick={() => setN(n + 50)}>顯示更多</button></div>}
            </div>
          )}
          <p className="muted small" style={{ marginBottom: 0 }}>{data.note} 模型 {m.version}。</p>
        </div>
      )}
      <Disclaimer />
    </>
  );
}
