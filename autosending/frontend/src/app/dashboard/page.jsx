"use client";
import { useEffect, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { getStats, getLogs } from "@/lib/api";
import {
  Users, Hash, MessageSquare, Zap, Send, Activity,
  TrendingUp, RefreshCw, LogIn, AlertCircle, CheckCircle2,
  ArrowUpRight, ArrowDownRight, Gauge, Heart, Download, Plus,
} from "lucide-react";
import toast from "react-hot-toast";

// recharts is lazy-loaded — does not block initial render
const ActivityChart = dynamic(() => import("@/components/ActivityChart"), {
  ssr: false,
  loading: () => <div style={{ height: 220 }} />,
});

function formatTime(ts) {
  if (!ts) return "—";
  return new Date(ts).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}
function formatRel(ts) {
  if (!ts) return "—";
  const d = Math.floor((Date.now() - new Date(ts).getTime()) / 1000);
  if (d < 5) return "только что";
  if (d < 60) return d + "с назад";
  if (d < 3600) return Math.floor(d/60) + "м назад";
  if (d < 86400) return Math.floor(d/3600) + "ч назад";
  return Math.floor(d/86400) + "д назад";
}

const ACTION_META = {
  join_channel: { icon: LogIn,         label: "вступление", color: "var(--azure)" },
  send_message: { icon: Send,          label: "отправка",   color: "var(--gold-hi)" },
  comment:      { icon: MessageSquare, label: "коммент.",   color: "var(--plum)" },
};

function LogRow({ entry }) {
  const meta = ACTION_META[entry.action_type] ?? { icon: Activity, label: entry.action_type, color: "var(--ink-3)" };
  const Icon = meta.icon;
  const ok = entry.status === "success";

  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "8px 132px 90px 1fr 70px",
        alignItems: "center",
        gap: 14,
        padding: "12px 22px",
        borderBottom: "1px solid var(--line-1)",
        minWidth: 0,
        transition: "background 120ms",
      }}
    >
      <span
        style={{
          width: 6, height: 6, borderRadius: "50%",
          background: ok ? "var(--emerald)" : entry.status === "error" ? "var(--coral)" : "var(--amber)",
          boxShadow: ok ? "0 0 8px rgba(142,201,154,0.6)" : entry.status === "error" ? "0 0 8px rgba(232,146,128,0.6)" : "none",
        }}
      />

      <span
        style={{
          fontFamily: "var(--mono)", fontSize: 12, color: "var(--ink-2)",
          overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
        }}
      >
        {entry.account_phone ?? "—"}
      </span>

      <span
        style={{
          display: "inline-flex", alignItems: "center", gap: 5,
          fontSize: 11, fontWeight: 500, color: meta.color,
        }}
      >
        <Icon size={11} strokeWidth={2} />
        {meta.label}
      </span>

      <div style={{ minWidth: 0 }}>
        <div style={{ fontSize: 12.5, color: "var(--ink-1)", fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {entry.channel_url ?? "—"}
        </div>
        {entry.message && (
          <div style={{ fontSize: 11.5, color: "var(--ink-4)", marginTop: 2, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            «{entry.message.length > 70 ? entry.message.slice(0,70) + "…" : entry.message}»
          </div>
        )}
        {!ok && entry.error_message && (
          <div style={{ fontSize: 11.5, color: "var(--coral)", marginTop: 2, fontFamily: "var(--mono)" }}>
            {entry.error_message}
          </div>
        )}
      </div>

      <span style={{ fontSize: 11, color: "var(--ink-4)", fontFamily: "var(--mono)", textAlign: "right" }}>
        {formatRel(entry.timestamp)}
      </span>
    </div>
  );
}

const METRIC_DEFS = [
  { key: "messages_sent",     label: "Отправлено всего",  icon: Send,          accent: false },
  { key: "running_campaigns", label: "Активных кампаний", icon: Zap,           accent: true  },
  { key: "online_accounts",   label: "Аккаунтов онлайн",  icon: Activity,      accent: false, format: (s) => `${s.online_accounts}/${s.total_accounts}` },
  { key: "total_channels",    label: "Каналов",            icon: Hash,          accent: false },
  { key: "total_messages",    label: "Шаблонов",           icon: MessageSquare, accent: false },
  { key: "total_accounts",    label: "Всего аккаунтов",   icon: Users,         accent: false },
];

export default function Dashboard() {
  const [stats, setStats]       = useState(null);
  const [logs, setLogs]         = useState([]);
  const [loading, setLoading]   = useState(true);
  const [chartData, setChartData] = useState([]);
  const [refreshing, setRefreshing] = useState(false);
  const [isLive, setIsLive]     = useState(false);

  const load = async (manual = false) => {
    if (manual) setRefreshing(true);
    try {
      const [data, logData] = await Promise.all([
        getStats(),
        getLogs({ limit: 60 }),
      ]);
      setStats(data);
      setLogs(logData);
      setIsLive((data.running_campaigns ?? 0) > 0);

      const counts = {};
      logData.forEach(a => {
        if (a.action_type === "send_message" && a.status === "success") {
          const t = formatTime(a.timestamp);
          counts[t] = (counts[t] || 0) + 1;
        }
      });
      setChartData(
        Object.entries(counts).slice(-16).map(([time, value]) => ({ time, value }))
      );
    } catch {
      if (manual) toast.error("Не удалось загрузить данные");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => {
    load();
    const id = setInterval(load, 10000);
    return () => clearInterval(id);
  }, []);

  const sentToday = logs.filter(l => l.action_type === "send_message" && l.status === "success").length;
  const joinOk    = logs.filter(l => l.action_type === "join_channel" && l.status === "success").length;
  const errors    = logs.filter(l => l.status === "error").length;

  return (
    <div className="animate-fade-in" style={{ display: "flex", flexDirection: "column", gap: 24 }}>
      {/* Header */}
      <div className="page-head">
        <div>
          <h1 className="page-title">Обзор</h1>
          <p className="page-sub">
            {isLive
              ? `${stats?.running_campaigns} кампани${stats?.running_campaigns === 1 ? "я" : "и"} активны · мониторинг в реальном времени`
              : "Все системы в покое — запустите кампанию, чтобы начать"}
          </p>
        </div>
        <div className="page-actions" style={{ display: "flex", gap: 8 }}>
          <button onClick={() => load(true)} disabled={refreshing} className="btn-ghost">
            <RefreshCw size={13} style={{ animation: refreshing ? "spin 0.7s linear infinite" : "none" }} />
            Обновить
          </button>
        </div>
      </div>

      {/* Metric grid */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 14 }}>
        {METRIC_DEFS.map(({ key, label, icon: Icon, accent, format }, i) => (
          <div key={key} className="stat animate-fade-in" style={{ animationDelay: `${i * 35}ms` }}>
            <div className="stat-row">
              <span className="stat-label">{label}</span>
              <div
                className="stat-icon"
                style={accent ? { color: "var(--gold)", background: "var(--gold-tint)", borderColor: "var(--gold-edge)" } : undefined}
              >
                <Icon size={13} />
              </div>
            </div>
            <div className="stat-value">
              {loading
                ? <div className="skeleton" style={{ height: 32, width: 80, borderRadius: 4 }} />
                : format
                  ? (stats ? format(stats) : "—")
                  : (stats?.[key] ?? 0).toLocaleString("ru-RU")
              }
            </div>
          </div>
        ))}
      </div>

      {/* Chart */}
      <div className="card card-no-hover">
        <div className="section-head">
          <div className="section-title">
            <TrendingUp size={15} />
            Успешных отправок по времени
          </div>
          {isLive && (
            <span className="badge badge-success">
              <span className="live-dot" />
              LIVE
            </span>
          )}
        </div>
        <div style={{ padding: "8px 14px 16px" }}>
          <ActivityChart data={chartData} height={220} />
        </div>
      </div>

      {/* Live log */}
      <div className="card card-no-hover">
        <div className="section-head">
          <div className="section-title">
            <Activity size={15} />
            Лента событий
          </div>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <span className="badge badge-success"><CheckCircle2 size={11} /> {sentToday} отправлено</span>
            {joinOk > 0 && <span className="badge badge-info"><LogIn size={11} /> {joinOk} вступлений</span>}
            {errors > 0 && <span className="badge badge-error"><AlertCircle size={11} /> {errors} ошибки</span>}
          </div>
        </div>

        <div style={{ maxHeight: 480, overflowY: "auto" }}>
          {loading ? (
            <div style={{ padding: "8px 16px", display: "flex", flexDirection: "column", gap: 8 }}>
              {Array.from({ length: 8 }).map((_, i) => (
                <div key={i} className="skeleton" style={{ height: 44, borderRadius: 6 }} />
              ))}
            </div>
          ) : logs.length === 0 ? (
            <div className="empty">
              <div className="empty-icon"><Activity size={22} /></div>
              <span className="empty-title">Тишина в эфире</span>
              <span className="empty-sub">Запустите кампанию — здесь появятся события в реальном времени</span>
            </div>
          ) : (
            logs.map((entry, i) => <LogRow key={entry.id ?? i} entry={entry} />)
          )}
        </div>
      </div>
    </div>
  );
}
