"use client";

import {
  AreaChart, Area, XAxis, YAxis, Tooltip,
  ResponsiveContainer, CartesianGrid,
} from "recharts";

function ChartTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  return (
    <div style={{
      background: "var(--surface)", border: "1px solid var(--border-s)",
      borderRadius: "var(--r-sm)", padding: "8px 12px", fontSize: 12,
    }}>
      <div style={{ color: "var(--text-3)", marginBottom: 2 }}>{label}</div>
      <div style={{ color: "var(--text-1)", fontWeight: 600 }}>{payload[0].value} событий</div>
    </div>
  );
}

export default function ActivityChart({ data }) {
  if (!data?.length) {
    return (
      <div style={{ height: 120, display: "flex", alignItems: "center", justifyContent: "center" }}>
        <span style={{ fontSize: 12, color: "var(--text-4)" }}>Нет данных — запустите кампанию</span>
      </div>
    );
  }

  return (
    <ResponsiveContainer width="100%" height={120}>
      <AreaChart data={data} margin={{ top: 4, right: 0, left: -24, bottom: 0 }}>
        <defs>
          <linearGradient id="areaGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%"   stopColor="var(--green)" stopOpacity={0.25} />
            <stop offset="100%" stopColor="var(--green)" stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" vertical={false} />
        <XAxis dataKey="time" tick={{ fill: "var(--text-4)", fontSize: 9 }} axisLine={false} tickLine={false} />
        <YAxis tick={{ fill: "var(--text-4)", fontSize: 9 }} axisLine={false} tickLine={false} allowDecimals={false} />
        <Tooltip content={<ChartTooltip />} />
        <Area type="monotone" dataKey="value"
          stroke="var(--green)" fill="url(#areaGrad)" strokeWidth={1.5}
          dot={false} activeDot={{ r: 3, fill: "var(--green)", stroke: "var(--surface)", strokeWidth: 2 }}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}
