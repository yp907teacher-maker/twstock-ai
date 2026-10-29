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

export function WatchButton({ id, inList, onChange }: { id: number; inList: boolean; onChange?: (v: boolean) => void }) {
  const toast = useToast();
  const [on, setOn] = useState(inList);
  useEffect(() => setOn(inList), [inList]);
  const toggle = async () => {
    try {
      if (on) await api.del(`/api/watchlist/${id}`);
      else await api.post("/api/watchlist", { instrument_id: id, note: "" });
      setOn(!on);
      onChange?.(!on);
      toast(on ? "已移出自選股" : "已加入自選股");
    } catch (e: any) {
      toast(e.message);
    }
  };
  return (
    <button className="btn small" onClick={toggle} title={on ? "移出自選股" : "加入自選股"}>
      {on ? "★ 已自選" : "☆ 自選"}
    </button>
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
