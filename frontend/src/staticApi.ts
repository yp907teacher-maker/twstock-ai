// 線上展示版（VITE_STATIC=1）：不連後端，改讀建置時匯出的 data/*.json。
// 自選股存在瀏覽器（localStorage，不可用時存在記憶體），其餘寫入動作一律回覆唯讀。
/* eslint-disable @typescript-eslint/no-explicit-any */

const cache = new Map<string, Promise<any>>();
const load = (path: string) => {
  if (!cache.has(path)) {
    cache.set(path, fetch(path).then((r) => {
      if (!r.ok) throw new Error(`讀取 ${path} 失敗`);
      return r.json();
    }));
  }
  return cache.get(path)!;
};
const common = () => load("data/common.json");
const inst = (id: number | string) => load(`data/inst/${id}.json`);

// ---- 自選股（瀏覽器端）----
type WL = Record<string, { note: string; added_at: string }>;
let memWL: WL = {};
const readWL = (): WL => {
  try {
    const v = localStorage.getItem("twstock-demo-watchlist");
    return v ? JSON.parse(v) : memWL;
  } catch {
    return memWL;
  }
};
const writeWL = (w: WL) => {
  memWL = w;
  try {
    localStorage.setItem("twstock-demo-watchlist", JSON.stringify(w));
  } catch {
    /* 無法儲存時只保留在記憶體 */
  }
};

const READONLY = "線上展示版為唯讀示範：此功能需在自己電腦執行完整版（含後端）才能使用";

function screener(c: any, p: URLSearchParams) {
  const group = p.get("group") || "stock";
  const src = c.screener[group];
  let rows: any[] = src.rows.slice();
  const num = (k: string) => (p.get(k) !== null ? Number(p.get(k)) : null);
  if (p.get("include_ineligible") !== "true") rows = rows.filter((r) => r.score.eligible);
  if (p.get("market")) rows = rows.filter((r) => r.market === p.get("market"));
  if (p.get("industry")) rows = rows.filter((r) => r.industry === p.get("industry"));
  const mt = num("min_total");
  if (mt !== null) rows = rows.filter((r) => r.score.total !== null && r.score.total >= mt);
  const mc = num("min_coverage");
  if (mc !== null) rows = rows.filter((r) => r.score.coverage >= mc);
  if (p.get("hide_risk_flags") === "true") rows = rows.filter((r) => !r.score.risk_flags.length);
  for (const k of ["trend", "fundamental", "liquidity", "flow", "value_risk"]) {
    const v = num(`min_${k}`);
    if (v !== null) rows = rows.filter((r) => r.score.components[k] != null && r.score.components[k] >= v);
  }
  const sort = p.get("sort") || "total";
  const desc = p.get("desc") !== "false";
  const val = (r: any) =>
    sort === "total" ? r.score.total : sort in r.score.components ? r.score.components[sort] : sort === "symbol" ? r.symbol : r.features[sort];
  rows.sort((a, b) => {
    const va = val(a), vb = val(b);
    if (sort === "symbol") return desc ? String(vb).localeCompare(String(va)) : String(va).localeCompare(String(vb));
    if (va == null && vb == null) return (a.score.rank ?? 1e6) - (b.score.rank ?? 1e6);
    if (va == null) return 1;
    if (vb == null) return -1;
    return desc ? vb - va || (a.score.rank ?? 1e6) - (b.score.rank ?? 1e6) : va - vb;
  });
  const excluded = src.rows.filter((r: any) => !r.score.eligible).length;
  return { ...src, rows, total_rows: rows.length, excluded_count: excluded };
}

function prices(rows: any[], days: number, adjusted: boolean) {
  const out = rows.map(([d, o, h, l, c, v, f]) => {
    const k = adjusted ? f : 1;
    const r3 = (x: number | null) => (x == null ? null : Math.round(x * k * 1000) / 1000);
    return { d, o: r3(o), h: r3(h), l: r3(l), c: r3(c), v, ma20: null as number | null, ma60: null as number | null };
  });
  for (const [n, key] of [[20, "ma20"], [60, "ma60"]] as const) {
    let sum = 0;
    for (let i = 0; i < out.length; i++) {
      sum += out[i].c ?? 0;
      if (i >= n) sum -= out[i - n].c ?? 0;
      if (i >= n - 1) out[i][key] = Math.round((sum / n) * 1000) / 1000;
    }
  }
  return { adjusted, rows: out.slice(-days) };
}

async function watchlistRows() {
  const c = await common();
  const wl = readWL();
  const all = [...c.screener.stock.rows, ...c.screener.etf.rows, ...c.screener.etf_leveraged.rows];
  const rows = [];
  for (const [id, w] of Object.entries(wl)) {
    const d = (await inst(id)).detail;
    const sr = all.find((r: any) => r.instrument_id === Number(id));
    rows.push({
      ...d.instrument, note: w.note, added_at: w.added_at, score: sr?.score ?? d.score, score_change: null,
      last_price: d.last_price ? { date: d.last_price.date, close: d.last_price.close } : null, quote: null,
    });
  }
  return { as_of_date: c.meta.latest_score_date, rows };
}

export async function staticRequest(method: string, url: string, body?: any): Promise<any> {
  const u = new URL(url, "http://x");
  const path = u.pathname;
  const p = u.searchParams;
  let m: RegExpMatchArray | null;

  if (method === "GET") {
    if (path === "/api/meta") return (await common()).meta;
    if (path === "/api/market/overview") return (await common()).overview;
    if (path === "/api/models") return (await common()).models;
    if (path === "/api/picks") return (await common()).picks[p.get("group") || "stock"];
    if (path === "/api/backtests") return (await common()).backtests;
    if (path === "/api/health") return (await common()).health;
    if (path === "/api/screener") return screener(await common(), p);
    if (path === "/api/watchlist") return watchlistRows();
    if (path === "/api/instruments/search") {
      const q = (p.get("q") || "").trim().toLowerCase();
      if (!q) return [];
      return (await common()).search
        .filter((i: any) => i.symbol.toLowerCase().startsWith(q) || i.name.toLowerCase().includes(q))
        .slice(0, 20);
    }
    if ((m = path.match(/^\/api\/instruments\/(\d+)\/prices$/))) {
      return prices((await inst(m[1])).prices, Number(p.get("days") || 250), p.get("adjusted") !== "false");
    }
    if ((m = path.match(/^\/api\/instruments\/(\d+)$/))) {
      const d = (await inst(m[1])).detail;
      const w = readWL()[m[1]];
      return { ...d, watchlist: { in: !!w, note: w?.note ?? "" } };
    }
  }
  if ((m = path.match(/^\/api\/instruments\/(\d+)\/report$/)) && method === "POST") {
    const r = (await inst(m[1])).report;
    if (!r) throw new Error("此標的尚無評分資料，無法產生報告");
    return r;
  }
  if (path === "/api/watchlist" && method === "POST") {
    const w = readWL();
    w[body.instrument_id] = { note: body.note ?? "", added_at: w[body.instrument_id]?.added_at ?? new Date().toISOString() };
    writeWL(w);
    return { ok: true };
  }
  if ((m = path.match(/^\/api\/watchlist\/(\d+)$/))) {
    const w = readWL();
    if (method === "DELETE") delete w[m[1]];
    if (method === "PATCH" && w[m[1]]) w[m[1]].note = body?.note ?? "";
    writeWL(w);
    return { ok: true };
  }
  throw new Error(READONLY);
}
