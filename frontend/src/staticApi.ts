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

// ---- 自選股（瀏覽器端，多清單）----
type Item = { note: string; added_at: string };
type Store = { groups: { id: number; name: string }[]; items: Record<string, Record<string, Item>> };
const KEY = "twstock-watchlists-v2";
let mem: Store | null = null;
const readStore = (): Store => {
  try {
    const v = localStorage.getItem(KEY);
    if (v) return JSON.parse(v);
    const old = localStorage.getItem("twstock-demo-watchlist"); // 舊版單一清單
    if (old) return { groups: [{ id: 1, name: "自選一" }], items: { "1": JSON.parse(old) } };
  } catch {
    /* 無法使用 localStorage */
  }
  return mem ?? { groups: [{ id: 1, name: "自選一" }], items: { "1": {} } };
};
const writeStore = (st: Store) => {
  mem = st;
  try {
    localStorage.setItem(KEY, JSON.stringify(st));
  } catch {
    /* 只保留在記憶體 */
  }
};
const groupId = (st: Store, g: string | null | undefined) => (g ? Number(g) : st.groups[0].id);

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

async function watchlistRows(gid: number) {
  const c = await common();
  const st = readStore();
  const g = st.groups.find((x) => x.id === gid) ?? st.groups[0];
  const all = [...c.screener.stock.rows, ...c.screener.etf.rows, ...c.screener.etf_leveraged.rows];
  const rows = [];
  for (const [id, w] of Object.entries(st.items[String(g.id)] ?? {})) {
    const d = (await inst(id)).detail;
    const sr = all.find((r: any) => r.instrument_id === Number(id));
    rows.push({
      ...d.instrument, note: w.note, added_at: w.added_at, score: sr?.score ?? d.score, score_change: null,
      last_price: d.last_price ? { date: d.last_price.date, close: d.last_price.close } : null, quote: null,
    });
  }
  return { as_of_date: c.meta.latest_score_date, group: g, rows };
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
    if (path === "/api/nextday") {
      const nd = (await common()).nextday?.[p.get("group") || "stock"];
      if (!nd) return { model: null, rows: [], note: "尚無次日預測" };
      let rows = nd.rows;
      if (p.get("include_ineligible") !== "true") rows = rows.filter((r: any) => r.score?.eligible);
      return { ...nd, rows, total_rows: rows.length };
    }
    if (path === "/api/sectors") return (await common()).sectors ?? { rows: [] };
    if (path === "/api/picks") return (await common()).picks[p.get("group") || "stock"];
    if (path === "/api/backtests") return (await common()).backtests;
    if (path === "/api/health") return (await common()).health;
    if (path === "/api/screener") return screener(await common(), p);
    if (path === "/api/watchlist") return watchlistRows(groupId(readStore(), p.get("group")));
    if (path === "/api/watchlists") {
      const st = readStore();
      return st.groups.map((g) => {
        const ids = Object.keys(st.items[String(g.id)] ?? {}).map(Number);
        return { ...g, count: ids.length, instrument_ids: ids };
      });
    }
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
      const st = readStore();
      const groups = st.groups.filter((g) => st.items[String(g.id)]?.[m![1]]).map((g) => g.id);
      return { ...d, watchlist: { in: groups.length > 0, groups } };
    }
  }
  if ((m = path.match(/^\/api\/instruments\/(\d+)\/report$/)) && method === "POST") {
    const r = (await inst(m[1])).report;
    if (!r) throw new Error("此標的尚無評分資料，無法產生報告");
    return r;
  }
  if (path === "/api/watchlists" && method === "POST") {
    const st = readStore();
    const id = Math.max(0, ...st.groups.map((g) => g.id)) + 1;
    const name = String(body?.name || `自選${st.groups.length + 1}`).slice(0, 32);
    st.groups.push({ id, name });
    st.items[String(id)] = {};
    writeStore(st);
    return { id, name, count: 0, instrument_ids: [] };
  }
  if ((m = path.match(/^\/api\/watchlists\/(\d+)$/))) {
    const st = readStore();
    const id = Number(m[1]);
    if (method === "PATCH") {
      const g = st.groups.find((x) => x.id === id);
      if (g && body?.name) g.name = String(body.name).slice(0, 32);
    } else if (method === "DELETE") {
      if (st.groups.length <= 1) throw new Error("至少要保留一個自選清單");
      st.groups = st.groups.filter((x) => x.id !== id);
      delete st.items[String(id)];
    }
    writeStore(st);
    return { ok: true };
  }
  if (path === "/api/watchlist" && method === "POST") {
    const st = readStore();
    const gid = String(body?.group_id ?? st.groups[0].id);
    st.items[gid] = st.items[gid] ?? {};
    const iid = String(body.instrument_id);
    st.items[gid][iid] = { note: body.note || st.items[gid][iid]?.note || "", added_at: st.items[gid][iid]?.added_at ?? new Date().toISOString() };
    writeStore(st);
    return { ok: true };
  }
  if ((m = path.match(/^\/api\/watchlist\/(\d+)$/))) {
    const st = readStore();
    const gid = String(groupId(st, p.get("group")));
    const items = st.items[gid] ?? {};
    if (method === "DELETE") delete items[m[1]];
    if (method === "PATCH" && items[m[1]]) items[m[1]].note = body?.note ?? "";
    st.items[gid] = items;
    writeStore(st);
    return { ok: true };
  }
  throw new Error(READONLY);
}
