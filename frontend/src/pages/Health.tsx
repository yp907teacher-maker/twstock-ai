import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { Badge, Disclaimer, ErrorBox, Loading, useApi, useMeta, useToast } from "../components/ui";
import { dt, pct } from "../format";
import { statusDot } from "./Overview";

const ISSUE_LABEL: Record<string, string> = {
  missing_days: "個股缺漏交易日",
  stale_data: "資料過舊",
  abnormal_jump: "異常跳動",
  coverage_drop: "筆數驟降",
  price_logic: "價格邏輯錯誤",
  non_trading_day: "非交易日資料",
  duplicate_row: "重複紀錄",
  missing_ohlc: "缺開高低價",
  negative_volume: "負成交量",
  source_error: "來源呼叫錯誤",
  inferred_corporate_action: "推算除權息（待校正）",
};

export default function Health() {
  const { meta, reload: reloadMeta } = useMeta();
  const toast = useToast();
  const { data, error, loading, reload } = useApi<any>("/api/health");
  const [sev, setSev] = useState<string>("");
  const [symbols, setSymbols] = useState("");
  const [maxReq, setMaxReq] = useState("500");

  if (error) return <ErrorBox error={error} />;
  if (loading && !data) return <Loading />;
  if (!data) return null;

  const trigger = async (name: string, body: any = {}) => {
    try {
      await api.post(`/api/jobs/${name}`, body);
      toast(`已開始：${name}（完成後重新整理此頁）`);
      reloadMeta();
    } catch (e: any) {
      toast(e.message);
    }
  };
  const resolve = async (id: number) => {
    await api.post(`/api/quality/${id}/resolve`);
    reload();
  };
  const unexclude = async (id: number) => {
    await api.del(`/api/instruments/${id}/exclude`);
    reload();
  };
  const issues = data.issues.filter((i: any) => !sev || i.severity === sev);
  const running = meta?.job.running;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>資料健康狀態</h1>
          <p>每個資料集的最後成功時間、缺漏與錯誤；錯誤等級問題會阻止該標的產生正式分數與預測。</p>
        </div>
      </div>

      <div className="grid g3" style={{ marginBottom: 16 }}>
        <div className="card">
          <div className="kpi">
            <span className="label">最新行情日</span>
            <span className="value">{data.latest_price_date ?? "—"}</span>
            <span className="sub">有報價 {data.coverage.priced_latest} / 有效標的 {data.coverage.active_instruments}（缺漏率 {pct(data.coverage.missing_rate, 1, false)}）</span>
          </div>
        </div>
        <div className="card">
          <div className="kpi">
            <span className="label">未解決品質問題</span>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 4 }}>
              {data.issue_summary.length === 0 && <span className="muted">無</span>}
              {data.issue_summary.map((s: any) => (
                <Badge key={s.code + s.severity} kind={s.severity === "error" ? "err" : s.severity === "warn" ? "warn" : "info"}>
                  {ISSUE_LABEL[s.code] ?? s.code} {s.count}
                </Badge>
              ))}
            </div>
          </div>
        </div>
        <div className="card">
          <div className="kpi">
            <span className="label">工作狀態</span>
            <span className="value" style={{ fontSize: 16 }}>{running ? `執行中：${running}` : "閒置"}</span>
            <span className="sub">
              上次：{meta?.job.last ? `${meta.job.last.job} ${meta.job.last.status === "success" ? "成功" : "失敗"} · ${dt(meta.job.last.finished_at)}` : "—"}
            </span>
          </div>
        </div>
      </div>

      {meta?.static ? (
        <div className="notice" style={{ marginBottom: 16 }}>線上展示版為唯讀：手動執行匯入、回填與重新計算需在自己電腦執行完整版。</div>
      ) : (
      <div className="card" style={{ marginBottom: 16 }}>
        <div className="card-head"><h2>手動執行</h2><span className="muted small">同一時間只執行一個工作</span></div>
        <div className="filters">
          <button className="btn primary" disabled={!!running} onClick={() => trigger("daily")}>收盤後更新</button>
          <button className="btn" disabled={!!running} onClick={() => trigger("compute", {})}>重新計算評分／模型／回測</button>
          {!meta?.demo_mode && <button className="btn" disabled={!!running} onClick={() => trigger("instruments")}>同步標的主檔（FinMind）</button>}
          {!meta?.demo_mode && (
            <>
              <label className="field">回填代號（空白＝全部，以空格分隔）<input type="text" value={symbols} onChange={(e) => setSymbols(e.target.value)} placeholder="2330 0050 2454" style={{ width: 260 }} /></label>
              <label className="field">本次最多請求數<input type="number" value={maxReq} onChange={(e) => setMaxReq(e.target.value)} /></label>
              <button className="btn" disabled={!!running} onClick={() => trigger("backfill", { symbols: symbols.trim() ? symbols.trim().split(/\s+/) : null, max_requests: maxReq ? Number(maxReq) : null })}>
                歷史回填（FinMind）
              </button>
            </>
          )}
        </div>
        <p className="muted small" style={{ marginBottom: 0 }}>FinMind 免費版每小時 600 次（帶 token）。全市場回填需數小時；中斷後可再次執行，會從上次完成處續傳。</p>
      </div>
      )}

      <div className="card" style={{ marginBottom: 16 }}>
        <h2>資料來源</h2>
        <div className="table-wrap">
          <table className="tbl">
            <thead><tr><th>來源</th><th>資料集</th><th>最後狀態</th><th>最後成功</th><th>資料日</th><th className="n">筆數</th><th className="n">30 日失敗</th><th>訊息</th><th>授權備註</th></tr></thead>
            <tbody>
              {data.datasets.map((d: any) => (
                <tr key={d.provider + d.dataset}>
                  <td><span className={`dot ${statusDot(d.last_status)}`} /> {d.provider_name}</td>
                  <td className="mono">{d.dataset}</td>
                  <td>{d.last_status}</td>
                  <td>{dt(d.last_success_at)}</td>
                  <td>{d.data_date ?? "—"}</td>
                  <td className="n">{d.rows.toLocaleString()}</td>
                  <td className="n">{d.failures_30d}</td>
                  <td className="small" style={{ maxWidth: 320 }}>{d.message}</td>
                  <td className="small muted" style={{ maxWidth: 260 }}>{d.license_note}</td>
                </tr>
              ))}
              {data.datasets.length === 0 && <tr><td colSpan={9} className="empty">尚未執行任何匯入</td></tr>}
            </tbody>
          </table>
        </div>
      </div>

      {data.excluded.length > 0 && (
        <div className="card" style={{ marginBottom: 16 }}>
          <h2>已手動停用的標的</h2>
          <table className="tbl"><tbody>
            {data.excluded.map((i: any) => (
              <tr key={i.instrument_id}><td><Link to={`/instrument/${i.instrument_id}`}>{i.symbol} {i.name}</Link></td><td>{i.excluded_reason}</td>
                <td><button className="btn small" onClick={() => unexclude(i.instrument_id)}>恢復</button></td></tr>
            ))}
          </tbody></table>
        </div>
      )}

      <div className="card">
        <div className="card-head">
          <h2>品質問題明細</h2>
          <div className="tabs">
            {[["", "全部"], ["error", "錯誤"], ["warn", "警告"], ["info", "資訊"]].map(([k, l]) => (
              <button key={k} className={sev === k ? "on" : ""} onClick={() => setSev(k)}>{l}</button>
            ))}
          </div>
        </div>
        {issues.length === 0 ? <div className="empty">沒有符合條件的問題</div> : (
          <div className="table-wrap">
            <table className="tbl">
              <thead><tr><th>等級</th><th>類型</th><th>標的</th><th>日期</th><th>資料集</th><th>內容</th><th>發現時間</th><th /></tr></thead>
              <tbody>
                {issues.map((i: any) => (
                  <tr key={i.id}>
                    <td><Badge kind={i.severity === "error" ? "err" : i.severity === "warn" ? "warn" : "info"}>{i.severity}</Badge></td>
                    <td>{ISSUE_LABEL[i.code] ?? i.code}</td>
                    <td>{i.instrument_id ? <Link to={`/instrument/${i.instrument_id}`}>{i.symbol} {i.name}</Link> : "全市場"}</td>
                    <td>{i.date ?? "—"}</td>
                    <td className="mono">{i.dataset}</td>
                    <td className="small mono" style={{ maxWidth: 360, wordBreak: "break-all" }}>{JSON.stringify(i.details)}</td>
                    <td className="small">{dt(i.detected_at)}</td>
                    <td>{!meta?.static && <button className="btn small ghost" onClick={() => resolve(i.id)}>標記已處理</button>}</td>
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
