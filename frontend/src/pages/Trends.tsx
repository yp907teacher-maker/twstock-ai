import { Link } from "react-router-dom";
import { Badge, Disclaimer, ErrorBox, Loading, ScorePill, useApi, WatchButton } from "../components/ui";
import { pct, updown } from "../format";

const STATUS_KIND: Record<string, "err" | "ok" | "warn" | "info"> = { 升溫: "err", 反彈: "warn", 高檔整理: "info", 轉弱: "ok" };
const STATUS_TIP: Record<string, string> = {
  升溫: "5 日與 20 日報酬都在類股前半",
  反彈: "5 日報酬轉強，20 日仍落後",
  高檔整理: "20 日報酬領先，5 日轉弱",
  轉弱: "5 日與 20 日報酬都在類股後半",
};

function Related({ ids, names }: { ids: number[]; names: Record<string, any> }) {
  const list = ids.map((i) => names[String(i)]).filter(Boolean);
  if (!list.length) return <span className="muted small">—</span>;
  return (
    <span className="rel-chips">
      {list.map((r: any) => (
        <Link key={r.instrument_id} to={`/instrument/${r.instrument_id}`} className="rel-chip" title={`${r.industry ?? ""} · 20 日 ${pct(r.ret_20)}`}>
          {r.name}
        </Link>
      ))}
    </span>
  );
}

function StockTable({ rows, names, extra }: { rows: any[]; names: Record<string, any>; extra: "breakout" | "drop" }) {
  if (!rows.length) return <div className="empty small">目前沒有符合條件的個股</div>;
  return (
    <div className="table-wrap">
      <table className="tbl">
        <thead>
          <tr>
            <th>標的</th><th className="n">今日</th><th className="n">5 日</th><th className="n">20 日</th>
            {extra === "breakout" && <th className="n">量能</th>}<th className="n">總分</th><th>相關公司</th><th />
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.instrument_id}>
              <td className="name-cell">
                <Link to={`/instrument/${r.instrument_id}`}>{r.name}</Link>
                <span className="sub"><span className="sym">{r.symbol}</span> · {r.industry}</span>
              </td>
              <td className={`n ${updown(r.ret_1)}`}>{pct(r.ret_1)}</td>
              <td className={`n ${updown(r.ret_5)}`}>{pct(r.ret_5)}</td>
              <td className={`n ${updown(r.ret_20)}`}>{pct(r.ret_20)}</td>
              {extra === "breakout" && <td className="n">{r.vol_shock ? `${r.vol_shock.toFixed(1)} 倍` : "—"}</td>}
              <td className="n"><ScorePill v={r.total} /></td>
              <td><Related ids={r.related_ids} names={names} /></td>
              <td><WatchButton id={r.instrument_id} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function Trends() {
  const { data, error, loading } = useApi<any>("/api/trends");
  if (error) return <ErrorBox error={error} />;
  if (loading && !data) return <Loading />;
  if (!data?.as_of_date) return <div className="card empty">尚無資料</div>;
  const mk = data.market ?? {};
  const names = data.names ?? {};
  const hot = data.sectors.filter((s: any) => s.status === "升溫" || s.status === "反彈").slice(0, 6);
  const weak = data.sectors.filter((s: any) => s.status === "轉弱").slice(-4).reverse();

  return (
    <>
      <div className="page-head">
        <div>
          <h1>近期趨勢</h1>
          <p>資料日 {data.as_of_date}。哪些類股正在轉強、哪些股票帶量突破，以及和它們走勢相近的相關公司。</p>
        </div>
      </div>

      <div className="grid g4" style={{ marginBottom: 16 }}>
        {([["大盤今日（等權）", mk.mkt_ret_1], ["大盤 5 日", mk.mkt_ret_5], ["大盤 20 日", mk.mkt_ret_20]] as [string, number][]).map(([l, v]) => (
          <div className="card" key={l}><div className="kpi"><span className="label">{l}</span><span className={`value ${updown(v)}`}>{pct(v)}</span></div></div>
        ))}
        <div className="card"><div className="kpi"><span className="label">今日上漲家數比例</span><span className="value">{pct(mk.mkt_breadth, 0, false)}</span><span className="sub">普通股</span></div></div>
      </div>

      <h2 style={{ margin: "4px 0 10px" }}>轉強中的類股</h2>
      <div className="sector-grid" style={{ marginBottom: 18 }}>
        {hot.length === 0 && <div className="muted">目前沒有明顯轉強的類股</div>}
        {hot.map((s: any) => (
          <div className="sector-card" key={s.industry}>
            <div className="card-head" style={{ marginBottom: 6 }}>
              <b>{s.industry}</b>
              <Badge kind={STATUS_KIND[s.status]} title={STATUS_TIP[s.status]}>{s.status}</Badge>
              <span className="spacer" />
              <span className="small muted">熱度 {Math.round(s.heat)}</span>
            </div>
            <div className="sector-rets">
              {([["今日", s.ret_1], ["5 日", s.ret_5], ["20 日", s.ret_20]] as [string, number][]).map(([l, v]) => (
                <div key={l}><span className="small muted">{l}</span><b className={updown(v)}>{pct(v, 1)}</b></div>
              ))}
              <div><span className="small muted">量比</span><b>{s.vol_ratio ? s.vol_ratio.toFixed(2) : "—"}</b></div>
            </div>
            <div className="small muted" style={{ margin: "6px 0" }}>
              站上月線 {pct(s.breadth, 0, false)} · 創 60 日新高 {s.new_high} 檔 · 共 {s.count} 檔
            </div>
            <table className="tbl"><tbody>
              {s.leaders.slice(0, 4).map((l: any) => (
                <tr key={l.instrument_id}>
                  <td style={{ padding: "5px 4px" }}><Link to={`/instrument/${l.instrument_id}`}>{l.name}</Link> <span className="sym">{l.symbol}</span>
                    <div style={{ marginTop: 2 }}><Related ids={l.related_ids} names={names} /></div></td>
                  <td className={`n ${updown(l.ret_20)}`} style={{ padding: "5px 4px" }}>{pct(l.ret_20, 1)}</td>
                  <td className="n" style={{ padding: "5px 4px" }}><ScorePill v={l.total} /></td>
                </tr>
              ))}
            </tbody></table>
          </div>
        ))}
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <div className="card-head"><h2>帶量突破 60 日新高</h2><span className="muted small">今日成交量 ≥ 20 日均量 1.5 倍，且收在近 60 日高點附近</span></div>
        <StockTable rows={data.breakouts} names={names} extra="breakout" />
      </div>

      <div className="grid g2" style={{ marginBottom: 16 }}>
        <div className="card">
          <div className="card-head"><h2>轉弱類股</h2></div>
          {weak.length === 0 ? <div className="muted">無</div> : (
            <table className="tbl"><tbody>
              {weak.map((s: any) => (
                <tr key={s.industry}>
                  <td><b>{s.industry}</b></td>
                  <td className={`n ${updown(s.ret_5)}`}>5 日 {pct(s.ret_5, 1)}</td>
                  <td className={`n ${updown(s.ret_20)}`}>20 日 {pct(s.ret_20, 1)}</td>
                  <td className="small muted">站上月線 {pct(s.breadth, 0, false)}</td>
                </tr>
              ))}
            </tbody></table>
          )}
          <p className="small" style={{ marginBottom: 0 }}><Link to="/sectors">看全部類股表現 →</Link></p>
        </div>
        <div className="card">
          <div className="card-head"><h2>5 日急跌留意</h2><span className="muted small">5 日跌幅 ≥ 8%</span></div>
          <StockTable rows={data.drops.slice(0, 8)} names={names} extra="drop" />
        </div>
      </div>
      <p className="muted small">{data.note}</p>
      <Disclaimer />
    </>
  );
}
