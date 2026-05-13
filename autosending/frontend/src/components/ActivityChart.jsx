"use client";

import {
  AreaChart, Area, XAxis, YAxis, Tooltip,
  ResponsiveContainer, CartesianGrid,
} from "recharts";

function ChartTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  return (
    <div
      style={{
        background: "linear-gradient(180deg, #15161a, #0e0f12)",
        border: "1px solid var(--line-3)",
        borderRadius: "var(--r-sm)",
        padding: "8px 12px",
        fontSize: 12,
        boxShadow: "var(--shadow-2)",
      }}
    >
      <div style={{ color: "var(--ink-4)", marginBottom: 2, fontFamily: "var(--mono)" }}>{label}</div>
      <div style={{ color: "var(--ink-1)", fontWeight: 600, fontFamily: "var(--display)" }}>
        {payload[0].value} событий
      </div>
    </div>
  );
}

export default function ActivityChart({ data, height = 200 }) {
  if (!data?.length) {
    return (
      <div style={{ height, display: "flex", alignItems: "center", justifyContent: "center" }}>
        <span style={{ fontSize: 12.5, color: "var(--ink-4)" }}>
          Нет данных — запустите кампанию
        </span>
      </div>
    );
  }

  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 8, right: 4, left: -24, bottom: 4 }}>
        <defs>
          <linearGradient id="autoSendingGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%"   stopColor="var(--gold)" stopOpacity={0.34} />
            <stop offset="100%" stopColor="var(--gold)" stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid stroke="var(--line-1)" strokeDasharray="2 3" vertical={false} />
        <XAxis
          dataKey="time"
          tick={{ fill: "var(--ink-5)", fontSize: 10, fontFamily: "var(--mono)" }}
          axisLine={false}
          tickLine={false}
        />
        <YAxis
          tick={{ fill: "var(--ink-5)", fontSize: 10, fontFamily: "var(--mono)" }}
          axisLine={false}
          tickLine={false}
          allowDecimals={false}
        />
        <Tooltip content={<ChartTooltip />} cursor={{ stroke: "var(--gold-edge)", strokeWidth: 1 }} />
        <Area
          type="monotone"
          dataKey="value"
          stroke="var(--gold)"
          fill="url(#autoSendingGrad)"
          strokeWidth={1.8}
          dot={false}
          activeDot={{ r: 4, fill: "var(--gold)", stroke: "var(--bg-1)", strokeWidth: 2 }}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}
