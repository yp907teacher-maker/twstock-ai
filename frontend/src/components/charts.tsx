import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ComposedChart,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

const C = {
  ink: "#1b2126",
  muted: "#6c737a",
  grid: "#ebe9e1",
  accent: "#0e5a61",
  ma20: "#c58b1b",
  ma60: "#7a5ea8",
  up: "#c62f28",
  down: "#1d8549",
  vol: "#c9d6d6",
};
const tick = { fontSize: 11, fill: C.muted };
const fmtDate = (d: string) => d?.slice(2, 10) ?? "";

export function PriceChart({ rows }: { rows: any[] }) {
  if (!rows.length) return <div className="empty">尚無價格資料</div>;
  return (
    <ResponsiveContainer width="100%" height={340}>
      <ComposedChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
        <CartesianGrid stroke={C.grid} vertical={false} />
        <XAxis dataKey="d" tick={tick} tickFormatter={fmtDate} minTickGap={40} />
        <YAxis yAxisId="p" domain={["auto", "auto"]} tick={tick} width={56} orientation="right" />
        <YAxis yAxisId="v" hide domain={[0, (max: number) => max * 4]} />
        <Tooltip
          contentStyle={{ fontSize: 12, borderRadius: 8 }}
          formatter={(v: any, n: any) => [typeof v === "number" ? v.toLocaleString("zh-TW") : v, n]}
          labelFormatter={(l: any) => `交易日 ${l}`}
        />
        <Legend wrapperStyle={{ fontSize: 12 }} />
        <Bar yAxisId="v" dataKey="v" name="成交量（股）" fill={C.vol} isAnimationActive={false} />
        <Line yAxisId="p" dataKey="c" name="收盤" stroke={C.ink} dot={false} strokeWidth={1.6} isAnimationActive={false} />
        <Line yAxisId="p" dataKey="ma20" name="20 日均線" stroke={C.ma20} dot={false} strokeWidth={1.2} isAnimationActive={false} />
        <Line yAxisId="p" dataKey="ma60" name="60 日均線" stroke={C.ma60} dot={false} strokeWidth={1.2} isAnimationActive={false} />
      </ComposedChart>
    </ResponsiveContainer>
  );
}

export function MiniLine({ data, height = 70 }: { data: { d: string; v: number }[]; height?: number }) {
  if (!data?.length) return null;
  const up = data[data.length - 1].v >= data[0].v;
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ top: 4, right: 0, bottom: 0, left: 0 }}>
        <YAxis hide domain={["auto", "auto"]} />
        <XAxis dataKey="d" hide />
        <Tooltip contentStyle={{ fontSize: 11 }} labelFormatter={(l: any) => l} formatter={(v: any) => [Number(v).toLocaleString("zh-TW"), "收盤"]} />
        <Line dataKey="v" stroke={up ? C.up : C.down} dot={false} strokeWidth={1.6} isAnimationActive={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}

const SERIES: Record<string, { name: string; color: string; dash?: string }> = {
  score_base: { name: "評分策略（基準成本）", color: "#0e5a61" },
  score_high: { name: "評分策略（高成本）", color: "#0e5a61", dash: "5 4" },
  momentum_base: { name: "簡單動能（基準成本）", color: "#c58b1b" },
  momentum_high: { name: "簡單動能（高成本）", color: "#c58b1b", dash: "5 4" },
  universe_equal_weight: { name: "等權全體合格標的（未計成本）", color: "#8a8f94" },
  market_proxy: { name: "市場 ETF 代理", color: "#7a5ea8" },
};

export function EquityChart({ curves }: { curves: Record<string, { d: string; v: number }[]> }) {
  const keys = Object.keys(curves).filter((k) => SERIES[k]);
  const map = new Map<string, any>();
  for (const k of keys) for (const p of curves[k]) map.set(p.d, { ...(map.get(p.d) || { d: p.d }), [k]: p.v });
  const data = [...map.values()].sort((a, b) => (a.d < b.d ? -1 : 1));
  return (
    <ResponsiveContainer width="100%" height={320}>
      <LineChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
        <CartesianGrid stroke={C.grid} vertical={false} />
        <XAxis dataKey="d" tick={tick} tickFormatter={fmtDate} minTickGap={50} />
        <YAxis tick={tick} width={48} orientation="right" tickFormatter={(v) => `${((v - 1) * 100).toFixed(0)}%`} domain={["auto", "auto"]} />
        <ReferenceLine y={1} stroke="#999" />
        <Tooltip contentStyle={{ fontSize: 12 }} formatter={(v: any, n: any) => [`${((Number(v) - 1) * 100).toFixed(1)}%`, n]} />
        <Legend wrapperStyle={{ fontSize: 12 }} />
        {keys.map((k) => (
          <Line key={k} dataKey={k} name={SERIES[k].name} stroke={SERIES[k].color} strokeDasharray={SERIES[k].dash}
                dot={false} strokeWidth={k.startsWith("score") ? 2 : 1.3} isAnimationActive={false} />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}

export function RevenueChart({ rows }: { rows: { period_end: string; value: number | null }[] }) {
  if (!rows.length) return <div className="empty small">無月營收資料</div>;
  const data = rows.map((r) => ({ d: r.period_end.slice(0, 7), v: r.value ? r.value / 1e8 : null }));
  return (
    <ResponsiveContainer width="100%" height={200}>
      <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
        <CartesianGrid stroke={C.grid} vertical={false} />
        <XAxis dataKey="d" tick={tick} minTickGap={20} />
        <YAxis tick={tick} width={48} orientation="right" />
        <Tooltip contentStyle={{ fontSize: 12 }} formatter={(v: any) => [`${Number(v).toFixed(2)} 億元`, "月營收"]} />
        <Bar dataKey="v" fill={C.accent} isAnimationActive={false} />
      </BarChart>
    </ResponsiveContainer>
  );
}

export function FlowChart({ rows }: { rows: any[] }) {
  if (!rows.length) return <div className="empty small">無法人資料</div>;
  const data = rows.map((r) => ({ d: r.date, f: r.foreign_net != null ? r.foreign_net / 1000 : null, t: r.trust_net != null ? r.trust_net / 1000 : null }));
  return (
    <ResponsiveContainer width="100%" height={200}>
      <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
        <CartesianGrid stroke={C.grid} vertical={false} />
        <XAxis dataKey="d" tick={tick} tickFormatter={fmtDate} minTickGap={30} />
        <YAxis tick={tick} width={56} orientation="right" />
        <ReferenceLine y={0} stroke="#999" />
        <Tooltip contentStyle={{ fontSize: 12 }} formatter={(v: any, n: any) => [`${Number(v).toLocaleString("zh-TW")} 張`, n]} />
        <Legend wrapperStyle={{ fontSize: 12 }} />
        <Bar dataKey="f" name="外資淨買超" isAnimationActive={false}>
          {data.map((d, i) => <Cell key={i} fill={(d.f ?? 0) >= 0 ? C.up : C.down} />)}
        </Bar>
        <Bar dataKey="t" name="投信淨買超" fill="#c58b1b" isAnimationActive={false} />
      </BarChart>
    </ResponsiveContainer>
  );
}

export function ReliabilityChart({ points }: { points: { pred: number; actual: number; n: number }[] }) {
  if (!points?.length) return <div className="empty small">樣本不足</div>;
  return (
    <ResponsiveContainer width="100%" height={220}>
      <ScatterChart margin={{ top: 8, right: 8, bottom: 8, left: 0 }}>
        <CartesianGrid stroke={C.grid} />
        <XAxis type="number" dataKey="pred" name="預測機率" domain={[0, 1]} tick={tick} tickFormatter={(v) => `${v * 100}%`} />
        <YAxis type="number" dataKey="actual" name="實際上漲比例" domain={[0, 1]} tick={tick} width={44} tickFormatter={(v) => `${v * 100}%`} />
        <ReferenceLine segment={[{ x: 0, y: 0 }, { x: 1, y: 1 }]} stroke="#999" strokeDasharray="4 4" />
        <Tooltip contentStyle={{ fontSize: 12 }} formatter={(v: any, n: any) => [typeof v === "number" ? `${(v * 100).toFixed(1)}%` : v, n]} />
        <Scatter data={points} fill={C.accent} />
      </ScatterChart>
    </ResponsiveContainer>
  );
}
