// 格式化工具：台股慣例「紅漲綠跌」

export const pct = (v: number | null | undefined, digits = 1, sign = true) =>
  v === null || v === undefined || Number.isNaN(v) ? "—" : `${sign && v > 0 ? "+" : ""}${(v * 100).toFixed(digits)}%`;

export const num = (v: number | null | undefined, digits = 2) =>
  v === null || v === undefined || Number.isNaN(v)
    ? "—"
    : v.toLocaleString("zh-TW", { minimumFractionDigits: digits, maximumFractionDigits: digits });

export const yi = (v: number | null | undefined) =>
  v === null || v === undefined ? "—" : `${(v / 1e8).toLocaleString("zh-TW", { maximumFractionDigits: 2 })} 億`;

export const lots = (v: number | null | undefined) =>
  v === null || v === undefined ? "—" : `${Math.round(v / 1000).toLocaleString("zh-TW")} 張`;

export const updown = (v: number | null | undefined) =>
  v === null || v === undefined || Math.abs(v) < 1e-12 ? "flat" : v > 0 ? "up" : "down";

export const dt = (iso: string | null | undefined) => {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("zh-TW", { timeZone: "Asia/Taipei", hour12: false, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
};

export const FEATURE_PCT = new Set([
  "ret_5", "ret_20", "ret_60", "ret_120", "px_ma20", "px_ma60", "ma20_slope", "vol_20", "vol_60", "down_vol_60",
  "max_dd_120", "rev_yoy", "rev_yoy_3m", "eps_growth", "gross_margin", "roe", "debt_ratio", "margin_chg_20", "inst_persist",
]);

export const featureValue = (k: string, v: number | null | undefined) => {
  if (v === null || v === undefined) return "—";
  if (FEATURE_PCT.has(k)) return pct(v, 1, false);
  if (k === "turnover_20") return yi(v);
  if (k === "foreign_20" || k === "trust_20") return v.toFixed(3);
  return num(v, 2);
};
