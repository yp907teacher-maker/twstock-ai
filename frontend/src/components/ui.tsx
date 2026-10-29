import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, type Inst, type Meta, type Score } from "../api";

// ---------------- 資料載入 hook ----------------
export function useApi<T = any>(url: string | null, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!url) return;
    let alive = true;
    setLoading(true);
    api
      .get<T>(url)
      .then((d) => alive && (setData(d), setError(null)))
      .catch((e) => alive && setError(e.message))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url, tick, ...deps]);
  return { data, error, loading, reload: () => setTick((t) => t + 1), setData };
}

// ---------------- Meta context ----------------
export const MetaCtx = createContext<{ meta: Meta | null; reload: () => void }>({ meta: null, reload: () => {} });
export const useMeta = () => useContext(MetaCtx);

// ---------------- Toast ----------------
const ToastCtx = createContext<(msg: string) => void>(() => {});
export const useToast = () => useContext(ToastCtx);
export function ToastProvider({ children }: { children: ReactNode }) {
  const [msg, setMsg] = useState<string | null>(null);
  const timer = useRef<number | undefined>(undefined);
  const show = useCallback((m: string) => {
    setMsg(m);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setMsg(null), 4200);
  }, []);
  return (
    <ToastCtx.Provider value={show}>
      {children}
      {msg && <div className="toast" role="status">{msg}</div>}
    </ToastCtx.Provider>
  );
}

// ---------------- 小元件 ----------------
export function Loading({ text = "載入中…" }: { text?: string }) {
  return <div className="empty">{text}</div>;
}

export function ErrorBox({ error }: { error: string }) {
  return <div className="notice err">讀取失敗：{error}</div>;
}

export function Badge({ kind, children, title }: { kind?: "ok" | "warn" | "err" | "info"; children: ReactNode; title?: string }) {
  return (
    <span className={`badge ${kind ?? ""}`} title={title}>
      {children}
    </span>
  );
}

export function ScorePill({ v }: { v: number | null | undefined }) {
  if (v === null || v === undefined) return <span className="score-pill">—</span>;
  const cls = v >= 70 ? "hi" : v >= 55 ? "mid" : "";
  return <span className={`score-pill ${cls}`}>{v.toFixed(1)}</span>;
}

const COMP_ORDER = ["trend", "fundamental", "liquidity", "flow", "value_risk"];
const COMP_SHORT: Record<string, string> = { trend: "趨勢", fundamental: "基本面", liquidity: "流動性", flow: "籌碼", value_risk: "估值風險" };

export function CompBars({ score }: { score: Score }) {
  return (
    <div className="bars" aria-label="分項分數">
      {COMP_ORDER.map((k) => {
        if (!(k in score.components)) return null;
        const v = score.components[k];
        return (
          <div key={k} className={`b ${v === null ? "na" : ""}`} title={`${COMP_SHORT[k]}：${v === null ? "資料缺失" : v.toFixed(0)}`}>
            {v !== null && <i style={{ height: `${Math.max(4, v)}%` }} />}
          </div>
        );
      })}
    </div>
  );
}

export function CompDetail({ score, labels }: { score: Score; labels: Record<string, string> }) {
  return (
    <div>
      {COMP_ORDER.map((k) => {
        const w = score.weights?.[k] ?? 0;
        const total = Object.values(score.weights || {}).reduce((a, b) => a + b, 0) || 1;
        if (!(k in score.components) && w === 0) {
          return (
            <div className="comp-row" key={k}>
              <span className="muted">{labels[k]}</span>
              <span className="small muted">ETF 不適用</span>
              <span />
            </div>
          );
        }
        const v = score.components[k];
        return (
          <div className="comp-row" key={k}>
            <span>
              {labels[k]} <span className="w">{((w / total) * 100).toFixed(0)}%</span>
            </span>
            <div className="track">{v !== null && v !== undefined && <i style={{ width: `${v}%` }} />}</div>
            <span className="num">{v === null || v === undefined ? <span className="muted small">缺失</span> : v.toFixed(0)}</span>
          </div>
        );
      })}
    </div>
  );
}

export function InstName({ i, sub }: { i: Inst; sub?: ReactNode }) {
  return (
    <div className="name-cell">
      <Link to={`/instrument/${i.instrument_id}`}>{i.name}</Link>
      <span className="sub">
        <span className="sym">{i.symbol}</span> · {i.market} {i.industry && i.industry !== "ETF" ? `· ${i.industry}` : ""}
        {sub}
      </span>
    </div>
  );
}

// ---------------- 自選清單（自選一、自選二…）----------------
export type WatchGroup = { id: number; name: string; count: number; instrument_ids: number[] };
type WatchCtxT = {
  groups: WatchGroup[];
  groupsOf: (iid: number) => number[];
  toggle: (iid: number, gid: number) => Promise<void>;
  create: (name: string) => Promise<WatchGroup | null>;
  rename: (gid: number, name: string) => Promise<void>;
  remove: (gid: number) => Promise<void>;
  reload: () => void;
};
const WatchCtx = createContext<WatchCtxT>({
  groups: [], groupsOf: () => [], toggle: async () => {}, create: async () => null, rename: async () => {},
  remove: async () => {}, reload: () => {},
});
export const useWatch = () => useContext(WatchCtx);

export function WatchProvider({ children }: { children: ReactNode }) {
  const toast = useToast();
  const [groups, setGroups] = useState<WatchGroup[]>([]);
  const reload = useCallback(() => {
    api.get<WatchGroup[]>("/api/watchlists").then(setGroups).catch(() => {});
  }, []);
  useEffect(reload, [reload]);
  const groupsOf = (iid: number) => groups.filter((g) => g.instrument_ids.includes(iid)).map((g) => g.id);
  const toggle = async (iid: number, gid: number) => {
    // 剛建立的清單可能還不在 groups（尚未重新載入），視為空清單
    const g = groups.find((x) => x.id === gid) ?? { id: gid, name: "新清單", count: 0, instrument_ids: [] as number[] };
    const has = g.instrument_ids.includes(iid);
    try {
      if (has) await api.del(`/api/watchlist/${iid}?group=${gid}`);
      else await api.post("/api/watchlist", { instrument_id: iid, note: "", group_id: gid });
      toast(has ? `已移出「${g.name}」` : `已加入「${g.name}」`);
      reload();
    } catch (e: any) {
      toast(e.message);
    }
  };
  const create = async (name: string) => {
    try {
      const g = await api.post<WatchGroup>("/api/watchlists", { name });
      reload();
      return g;
    } catch (e: any) {
      toast(e.message);
      return null;
    }
  };
  const rename = async (gid: number, name: string) => {
    await api.patch(`/api/watchlists/${gid}`, { name });
    reload();
  };
  const remove = async (gid: number) => {
    try {
      await api.del(`/api/watchlists/${gid}`);
      reload();
    } catch (e: any) {
      toast(e.message);
    }
  };
  return <WatchCtx.Provider value={{ groups, groupsOf, toggle, create, rename, remove, reload }}>{children}</WatchCtx.Provider>;
}

export function WatchButton({ id }: { id: number; inList?: boolean; onChange?: (v: boolean) => void }) {
  const { groups, groupsOf, toggle, create, reload } = useWatch();
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [newName, setNewName] = useState("");
  const ref = useRef<HTMLDivElement>(null);
  const mine = groupsOf(id);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);
  const quick = async () => {
    // 只有一個清單時直接加入／移出，不必開選單
    if (groups.length === 1) await toggle(id, groups[0].id);
    else setOpen((v) => !v);
  };
  const label = mine.length === 0 ? "☆ 自選" : groups.length === 1 ? "★ 已自選" :
    `★ ${groups.filter((g) => mine.includes(g.id)).map((g) => g.name).join("、")}`;
  return (
    <div className="watch-wrap" ref={ref}>
      <button className="btn small" onClick={quick} title="加入或移出自選清單" style={{ maxWidth: 180, overflow: "hidden", textOverflow: "ellipsis" }}>
        {label}
      </button>
      {groups.length === 1 && (
        <button className="btn small ghost" title="選擇自選清單" onClick={() => setOpen((v) => !v)} style={{ padding: "3px 6px" }}>▾</button>
      )}
      {open && (
        <div className="watch-pop">
          <div className="small muted" style={{ marginBottom: 6 }}>加入到自選清單</div>
          {groups.map((g) => (
            <label key={g.id} className="check" style={{ display: "flex", padding: "3px 0" }}>
              <input type="checkbox" checked={mine.includes(g.id)} onChange={() => toggle(id, g.id)} />
              {g.name} <span className="muted small">（{g.count}）</span>
            </label>
          ))}
          <form
            style={{ display: "flex", gap: 4, marginTop: 8 }}
            onSubmit={async (e) => {
              e.preventDefault();
              const g = await create(newName.trim() || `自選${["一", "二", "三", "四", "五", "六", "七", "八", "九", "十"][groups.length] ?? groups.length + 1}`);
              if (g) {
                await api.post("/api/watchlist", { instrument_id: id, note: "", group_id: g.id });
                toast(`已建立並加入「${g.name}」`);
                reload();
              }
              setNewName("");
            }}
          >
            <input type="text" placeholder="新清單名稱" value={newName} onChange={(e) => setNewName(e.target.value)} style={{ flex: 1, minWidth: 0 }} />
            <button className="btn small" type="submit">新增</button>
          </form>
        </div>
      )}
    </div>
  );
}

// ---------------- 搜尋 ----------------
export function SearchBox() {
  const [q, setQ] = useState("");
  const [res, setRes] = useState<Inst[]>([]);
  const [open, setOpen] = useState(false);
  const [sel, setSel] = useState(0);
  const nav = useNavigate();
  useEffect(() => {
    if (!q.trim()) {
      setRes([]);
      return;
    }
    const t = window.setTimeout(() => {
      api.get<Inst[]>(`/api/instruments/search?q=${encodeURIComponent(q.trim())}`).then((r) => {
        setRes(r);
        setSel(0);
      }).catch(() => setRes([]));
    }, 180);
    return () => window.clearTimeout(t);
  }, [q]);
  const go = (i: Inst) => {
    setOpen(false);
    setQ("");
    nav(`/instrument/${i.instrument_id}`);
  };
  return (
    <div className="search">
      <span className="ico">⌕</span>
      <input
        type="text"
        placeholder="搜尋代號或名稱，例如 2330、台積電、0050"
        value={q}
        onChange={(e) => {
          setQ(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => window.setTimeout(() => setOpen(false), 150)}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") setSel((s) => Math.min(s + 1, res.length - 1));
          if (e.key === "ArrowUp") setSel((s) => Math.max(s - 1, 0));
          if (e.key === "Enter" && res[sel]) go(res[sel]);
        }}
        aria-label="搜尋標的"
      />
      {open && res.length > 0 && (
        <div className="drop">
          {res.map((i, k) => (
            <a key={i.instrument_id} className={k === sel ? "sel" : ""} onMouseDown={() => go(i)} href="#">
              <span className="sym">{i.symbol}</span>
              <span>{i.name}</span>
              <span className="muted small" style={{ marginLeft: "auto" }}>
                {i.group_label}
                {i.status !== "active" ? " · 已下市" : ""}
              </span>
            </a>
          ))}
        </div>
      )}
    </div>
  );
}

export function Disclaimer() {
  const { meta } = useMeta();
  return <div className="disclaimer">{meta?.disclaimer ?? "本系統為個人研究輔助，不構成投資建議。"}</div>;
}

export function DataDate({ label, value }: { label: string; value?: string | null }) {
  return (
    <span className="badge" title={label}>
      {label} {value || "—"}
    </span>
  );
}
