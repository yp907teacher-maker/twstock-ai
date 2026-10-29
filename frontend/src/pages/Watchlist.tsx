import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { Badge, CompBars, Disclaimer, ErrorBox, InstName, Loading, ScorePill, useApi, useMeta, useToast, useWatch } from "../components/ui";
import { dt, num, updown } from "../format";

type SortKey = "added" | "score" | "change" | "symbol";

export default function WatchlistPage() {
  const { meta } = useMeta();
  const toast = useToast();
  const watch = useWatch();
  const [gid, setGid] = useState<number | null>(null);
  const current = watch.groups.find((g) => g.id === gid) ?? watch.groups[0];
  const { data, error, loading, reload: reloadRows } = useApi<any>(current ? `/api/watchlist?group=${current.id}` : null, [watch.groups]);
  const reload = () => { reloadRows(); watch.reload(); };
  const [mode, setMode] = useState<null | "new" | "rename">(null);
  const [nameInput, setNameInput] = useState("");
  const [confirmDel, setConfirmDel] = useState(false);
  const [sort, setSort] = useState<SortKey>("added");
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState<Record<number, string>>({});

  if (error) return <ErrorBox error={error} />;
  if (loading && !data) return <Loading />;
  const rows = [...(data?.rows ?? [])];
  rows.sort((a: any, b: any) => {
    if (sort === "score") return (b.score?.total ?? -1) - (a.score?.total ?? -1);
    if (sort === "change") return (b.score_change ?? -999) - (a.score_change ?? -999);
    if (sort === "symbol") return a.symbol.localeCompare(b.symbol);
    return 0;
  });

  const refresh = async () => {
    setBusy(true);
    try {
      const r = await api.post("/api/quotes/refresh", { instrument_ids: rows.map((r: any) => r.instrument_id) });
      toast(`已刷新 ${r.quotes.length} 檔${r.errors.length ? `，${r.errors.length} 檔失敗` : ""}`);
      reload();
    } catch (e: any) {
      toast(e.message);
    } finally {
      setBusy(false);
    }
  };
  const saveNote = async (id: number) => {
    await api.patch(`/api/watchlist/${id}?group=${current!.id}`, { instrument_id: id, note: editing[id] ?? "" });
    setEditing((e) => { const n = { ...e }; delete n[id]; return n; });
    reload();
  };
  const remove = async (id: number) => {
    await api.del(`/api/watchlist/${id}?group=${current!.id}`);
    toast("已移除");
    reload();
  };

  return (
    <>
      <div className="page-head">
        <div>
          <h1>自選股</h1>
          <p>評分日 {data?.as_of_date ?? "—"}。盤中刷新只更新報價（Fugle 免費方案每分鐘 60 次），不重新計算評分。</p>
        </div>
        <div style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
          <div className="tabs">
            {([["added", "加入順序"], ["score", "總分"], ["change", "分數變化"], ["symbol", "代號"]] as [SortKey, string][]).map(([k, l]) => (
              <button key={k} className={sort === k ? "on" : ""} onClick={() => setSort(k)}>{l}</button>
            ))}
          </div>
          <button className="btn primary" onClick={refresh} disabled={busy || !rows.length || !meta?.fugle_enabled || meta?.demo_mode}
            title={meta?.fugle_enabled ? "" : "需設定 FUGLE_API_KEY"}>
            {busy ? "刷新中…" : "盤中刷新報價"}
          </button>
        </div>
      </div>
      <div className="group-tabs">
        {watch.groups.map((g) => (
          <button key={g.id} className={`group-tab ${current?.id === g.id ? "on" : ""}`} onClick={() => { setGid(g.id); setMode(null); setConfirmDel(false); }}>
            {g.name}<span className="n">{g.count}</span>
          </button>
        ))}
        {mode ? (
          <form style={{ display: "inline-flex", gap: 4 }} onSubmit={async (e) => {
            e.preventDefault();
            if (mode === "new") {
              const g = await watch.create(nameInput.trim() || `自選${["一", "二", "三", "四", "五", "六", "七", "八", "九", "十"][watch.groups.length] ?? watch.groups.length + 1}`);
              if (g) setGid(g.id);
            } else if (current && nameInput.trim()) {
              await watch.rename(current.id, nameInput.trim());
            }
            setMode(null); setNameInput("");
          }}>
            <input type="text" autoFocus placeholder={mode === "new" ? "新清單名稱，例如 存股" : "新名稱"} value={nameInput} onChange={(e) => setNameInput(e.target.value)} style={{ width: 170 }} />
            <button className="btn small primary" type="submit">{mode === "new" ? "建立" : "改名"}</button>
            <button className="btn small ghost" type="button" onClick={() => setMode(null)}>取消</button>
          </form>
        ) : (
          <>
            <button className="btn small" onClick={() => { setMode("new"); setNameInput(""); }}>＋ 新增清單</button>
            {current && <button className="btn small ghost" onClick={() => { setMode("rename"); setNameInput(current.name); }}>改名</button>}
            {current && watch.groups.length > 1 && (confirmDel ? (
              <>
                <span className="small down">確定刪除「{current.name}」？</span>
                <button className="btn small" onClick={async () => { await watch.remove(current.id); setGid(null); setConfirmDel(false); }}>刪除</button>
                <button className="btn small ghost" onClick={() => setConfirmDel(false)}>取消</button>
              </>
            ) : <button className="btn small ghost" onClick={() => setConfirmDel(true)}>刪除此清單</button>)}
          </>
        )}
      </div>
      <div className="card">
        {rows.length === 0 ? (
          <div className="empty">「{current?.name}」還沒有股票。可在 <Link to="/screener">選股工作台</Link> 或個股頁按「☆ 自選」。</div>
        ) : (
          <div className="table-wrap">
            <table className="tbl">
              <thead>
                <tr>
                  <th>標的</th><th className="n">總分</th><th className="n">較前次</th><th>分項</th><th className="n">名次</th>
                  <th className="n">收盤</th><th>盤中報價</th><th>風險</th><th style={{ width: 260 }}>觀察備註</th><th />
                </tr>
              </thead>
              <tbody>
                {rows.map((r: any) => (
                  <tr key={r.instrument_id}>
                    <td><InstName i={r} /></td>
                    <td className="n"><ScorePill v={r.score?.total} /></td>
                    <td className={`n ${updown(r.score_change)}`}>{r.score_change == null ? "—" : `${r.score_change > 0 ? "+" : ""}${r.score_change.toFixed(1)}`}</td>
                    <td>{r.score && <CompBars score={r.score} />}</td>
                    <td className="n">{r.score?.rank ?? (r.score && !r.score.eligible ? <span className="muted small" title={r.score.exclusion_reasons.join("\n")}>排除</span> : "—")}</td>
                    <td className="n">{num(r.last_price?.close)}<div className="muted small">{r.last_price?.date}</div></td>
                    <td className="small">
                      {r.quote ? <><b className={updown(r.quote.change)}>{num(r.quote.price)}</b> <span className={updown(r.quote.change)}>{r.quote.change > 0 ? "+" : ""}{num(r.quote.change)}</span>
                        <div className="muted">{dt(r.quote.quote_time)} · {r.quote.provider}</div></> : <span className="muted">—</span>}
                    </td>
                    <td>{r.score?.risk_flags?.length ? <Badge kind="warn" title={r.score.risk_flags.join("\n")}>{r.score.risk_flags.length}</Badge> : <span className="muted">—</span>}</td>
                    <td>
                      {r.instrument_id in editing ? (
                        <div style={{ display: "flex", gap: 4 }}>
                          <input type="text" style={{ flex: 1 }} value={editing[r.instrument_id]} autoFocus
                            onChange={(e) => setEditing((s) => ({ ...s, [r.instrument_id]: e.target.value }))}
                            onKeyDown={(e) => e.key === "Enter" && saveNote(r.instrument_id)} />
                          <button className="btn small" onClick={() => saveNote(r.instrument_id)}>儲存</button>
                        </div>
                      ) : (
                        <span onClick={() => setEditing((s) => ({ ...s, [r.instrument_id]: r.note }))} style={{ cursor: "text" }} title="點擊編輯">
                          {r.note || <span className="muted small">點擊新增備註</span>}
                        </span>
                      )}
                    </td>
                    <td><button className="btn small ghost" onClick={() => remove(r.instrument_id)}>移除</button></td>
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
