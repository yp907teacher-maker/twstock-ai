import { Link } from "react-router-dom";
import { MiniLine } from "../components/charts";
import { Badge, CompBars, Disclaimer, ErrorBox, InstName, Loading, ScorePill, useApi, useMeta } from "../components/ui";
import { dt, num, updown, yi } from "../format";

export function statusDot(s: string) {
  return s === "success" ? "ok" : s === "partial" || s === "running" ? "warn" : "err";
}

export default function Overview() {
  const { meta } = useMeta();
  const { data, error, loading } = useApi<any>("/api/market/overview", [meta?.job.running, meta?.progress?.done]);
  if (error) return <ErrorBox error={error} />;
  if (loading && !data) return <Loading />;
  if (!data) return null;

  if (data.empty && meta?.job.running === "bootstrap") {
    return (
      <div className="card">
        <h2>正在抓取真實行情</h2>
        <p>第一次啟動會自動從證交所、櫃買中心與 FinMind 抓資料，先抓今日收盤，再回填歷史股價。
          {meta.progress && <> 目前階段：{meta.progress.phase ?? "同步主檔與今日收盤"}，已回填 {meta.progress.done} / {meta.progress.total} 檔。</>}</p>
        <p className="muted small">評分與精選需要至少 120 個交易日的歷史；回填完成後會自動計算。可以先到「資料健康」看進度。</p>
      </div>
    );
  }
  if (data.empty) {
    return (
      <div className="card">
        <h2>尚無資料</h2>
        <p>請先依 README 執行：同步主檔 → 歷史回填 → 收盤後更新。也可在「資料健康」頁觸發工作。</p>
        <Link className="btn primary" to="/health">前往資料健康</Link>
      </div>
    );
  }
  const b = data.breadth;
  const main = data.indices?.[0];
  return (
    <>
      <div className="page-head">
        <div>
          <h1>市場總覽</h1>
          <p>收盤資料日 {b.date}；評分日 {data.score_date ?? "—"}。候選清單為研究起點，不是買賣指令。</p>
        </div>
      </div>

      <div className="status-strip" style={{ marginBottom: 16 }}>
        {data.datasets.map((d: any) => (
          <div className="status-item" key={d.provider + d.dataset} title={d.message || ""}>
            <span className={`dot ${statusDot(d.last_status)}`} />
            <b>{d.provider_name}</b>
            <span className="muted">{d.dataset}</span>
            <span className="muted">資料日 {d.data_date ?? "—"}</span>
            <span className="muted">成功 {dt(d.last_success_at)}</span>
          </div>
        ))}
        {(data.quality.error || data.quality.warn) && (
          <Link className="status-item" to="/health">
            <span className="dot err" /> 品質問題 錯誤 {data.quality.error ?? 0} · 警告 {data.quality.warn ?? 0}
          </Link>
        )}
      </div>

      <div className="grid g4" style={{ marginBottom: 16 }}>
        <div className="card">
          <div className="kpi">
            <span className="label">{main?.name ?? "大盤指數"}</span>
            <span className={`value ${updown(main?.change_pct)}`}>{main ? num(main.close, 2) : "—"}</span>
            <span className={`sub ${updown(main?.change_pct)}`}>
              {main?.change_pct != null ? `${main.change_pct > 0 ? "+" : ""}${main.change_pct.toFixed(2)}%` : "—"} · {main?.date}
            </span>
          </div>
          {main?.history && <MiniLine data={main.history} />}
        </div>
        <div className="card">
          <div className="kpi">
            <span className="label">上漲／下跌／平盤</span>
            <span className="value">
              <span className="up">{b.advancers}</span> / <span className="down">{b.decliners}</span> / <span className="flat">{b.unchanged}</span>
            </span>
            <span className="sub">共 {b.count} 檔有成交（依還原價計算）</span>
          </div>
        </div>
        <div className="card">
          <div className="kpi">
            <span className="label">成交金額（上市櫃合計，含 ETF）</span>
            <span className="value">{yi(b.total_turnover)}</span>
            <span className="sub">新台幣元</span>
          </div>
        </div>
        <div className="card">
          <div className="kpi">
            <span className="label">其他指數</span>
            {data.indices.slice(1, 4).map((i: any) => (
              <span key={i.name} className="small">
                {i.name} <b className={updown(i.change_pct)}>{num(i.close, 2)}</b>{" "}
                <span className={updown(i.change_pct)}>{i.change_pct != null ? `${i.change_pct > 0 ? "+" : ""}${i.change_pct.toFixed(2)}%` : ""}</span>
              </span>
            ))}
            {data.indices.length <= 1 && <span className="sub">尚無其他指數資料</span>}
          </div>
        </div>
      </div>

      <div className="grid g2">
        <CandidateCard title="普通股評分前 10 名" rows={data.candidates.stock ?? []} group="stock" />
        <CandidateCard title="一般 ETF 評分前 5 名" rows={data.candidates.etf ?? []} group="etf" />
      </div>
      <Disclaimer />
    </>
  );
}

function CandidateCard({ title, rows, group }: { title: string; rows: any[]; group: string }) {
  return (
    <div className="card">
      <div className="card-head">
        <h2>{title}</h2>
        <span className="spacer" />
        <Link to={`/screener?group=${group}`} className="small">看系統精選 →</Link>
      </div>
      {rows.length === 0 ? (
        <div className="empty">暫無符合條件標的</div>
      ) : (
        <div className="table-wrap">
          <table className="tbl">
            <thead>
              <tr>
                <th className="n">#</th>
                <th>標的</th>
                <th className="n">總分</th>
                <th>分項</th>
                <th>風險／注意</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.instrument_id}>
                  <td className="n">{r.score.rank}</td>
                  <td><InstName i={r} /></td>
                  <td className="n"><ScorePill v={r.score.total} /></td>
                  <td><CompBars score={r.score} /></td>
                  <td>
                    {r.score.risk_flags.length ? (
                      <Badge kind="warn" title={r.score.risk_flags.join("\n")}>{r.score.risk_flags.length} 項</Badge>
                    ) : (
                      <span className="muted small">無標籤</span>
                    )}
                    {r.score.negatives.length > 0 && <span className="muted small"> · 反向訊號 {r.score.negatives.length}</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
