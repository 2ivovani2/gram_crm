"use client";
import { useEffect, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { getStats, getLogs } from "@/lib/api";
import StatusBadge from "@/components/StatusBadge";
import {
  Users, Hash, MessageSquare, Zap, Send, Activity,
  TrendingUp, RefreshCw, LogIn, AlertCircle, CheckCircle,
  Clock, Radio,
} from "lucide-react";
import toast from "react-hot-toast";

// recharts (~500 KB) loaded lazily — does not block initial render
const ActivityChart = dynamic(() => import("@/components/ActivityChart"), {
  ssr: false,
  loading: () => <div style={{ height: 120 }} />,
});

const METRICS = [
  { key: "total_accounts",    label: "Аккаунтов",        icon: Users },
  { key: "online_accounts",   label: "Онлайн",            icon: Activity },
  { key: "total_channels",    label: "Каналов",           icon: Hash },
  { key: "total_messages",    label: "Шаблонов",          icon: MessageSquare },
  { key: "running_campaigns", label: "Активных кампаний", icon: Zap },
  { key: "messages_sent",     label: "Отправлено",        icon: Send },
];

function formatTime(ts) {
  if (!ts) return "—";
  return new Date(ts).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

const ACTION_META = {
  join_channel: { icon: LogIn,        label: "Вступление" },
  send_message: { icon: Send,         label: "Отправка"   },
  comment:      { icon: MessageSquare, label: "Комментарий" },
};

function LogRow({ entry }) {
  const meta = ACTION_META[entry.action_type] ?? { icon: Activity, label: entry.action_type };
  const Icon = meta.icon;
  const ok = entry.status === "success";

  return (
    <div style={{
      display: "grid",
      gridTemplateColumns: "18px 90px 1fr auto",
      alignItems: "center",
      gap: 10,
      padding: "7px 16px",
      borderBottom: "1px solid var(--border)",
      fontSize: 12,
      minWidth: 0,
    }}>
      {/* Status dot */}
      <div style={{
        width: 6, height: 6, borderRadius: "50%", flexShrink: 0,
        background: ok ? "var(--green)" : entry.status === "error" ? "var(--red)" : "var(--amber)",
        boxShadow: ok ? "0 0 4px var(--green)" : entry.status === "error" ? "0 0 4px var(--red)" : "none",
      }} />

      {/* Phone */}
      <span style={{ color: "var(--text-2)", fontWeight: 500, fontFamily: "var(--mono)", fontSize: 11, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {entry.account_phone ?? "—"}
      </span>

      {/* Action + channel */}
      <div style={{ minWidth: 0 }}>
        <span style={{
          display: "inline-flex", alignItems: "center", gap: 3,
          fontSize: 10, fontWeight: 600, letterSpacing: "0.04em",
          color: "var(--text-4)", marginRight: 6,
          textTransform: "uppercase",
        }}>
          <Icon size={9} />
          {meta.label}
        </span>
        <span style={{ color: ok ? "var(--text-2)" : "var(--text-3)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {entry.channel_url ?? "—"}
        </span>
        {entry.message && (
          <div style={{ color: "var(--text-4)", fontSize: 11, marginTop: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {entry.message.slice(0, 80)}{entry.message.length > 80 ? "…" : ""}
          </div>
        )}
        {!ok && entry.error_message && (
          <div style={{ color: "var(--red)", fontSize: 10, marginTop: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {entry.error_message.slice(0, 100)}
          </div>
        )}
      </div>

      {/* Time */}
      <span style={{ flexShrink: 0, fontSize: 10, color: "var(--text-4)", fontFamily: "var(--mono)" }}>
        {formatTime(entry.timestamp)}
      </span>
    </div>
  );
}


export default function Dashboard() {
  const [stats, setStats]       = useState(null);
  const [logs, setLogs]         = useState([]);
  const [loading, setLoading]   = useState(true);
  const [chartData, setChartData] = useState([]);
  const [refreshing, setRefreshing] = useState(false);
  const [isLive, setIsLive]     = useState(false);
  const logRef = useRef(null);

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
    <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>

      {/* Header */}
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between" }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 600, letterSpacing: "-0.03em", color: "var(--text-1)", marginBottom: 2 }}>
            Дашборд
          </h1>
          <p style={{ fontSize: 13, color: "var(--text-3)" }}>
            Мониторинг в реальном времени · обновление каждые 10с
          </p>
        </div>
        <button onClick={() => load(true)} disabled={refreshing} className="btn-ghost" style={{ gap: 6 }}>
          <RefreshCw size={12} style={{ animation: refreshing ? "spin 0.6s linear infinite" : "none" }} />
          Обновить
        </button>
      </div>

      {/* Metrics */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 10 }}>
        {METRICS.map(({ key, label, icon: Icon }, i) => (
          <div key={key} className="card animate-fade-in" style={{ animationDelay: `${i * 35}ms`, padding: "14px 18px" }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 10 }}>
              <div style={{
                width: 28, height: 28,
                background: "var(--raised)", border: "1px solid var(--border)",
                borderRadius: "var(--r-sm)",
                display: "flex", alignItems: "center", justifyContent: "center",
                color: "var(--text-3)",
              }}>
                <Icon size={12} />
              </div>
            </div>
            <div style={{ fontSize: 24, fontWeight: 700, color: "var(--text-1)", letterSpacing: "-0.04em", lineHeight: 1, marginBottom: 3, fontVariantNumeric: "tabular-nums" }}>
              {loading
                ? <div className="skeleton" style={{ height: 26, width: 36, borderRadius: 4 }} />
                : (stats?.[key] ?? 0).toLocaleString("ru-RU")}
            </div>
            <div style={{ fontSize: 11, color: "var(--text-3)" }}>{label}</div>
          </div>
        ))}
      </div>

      {/* Chart */}
      <div className="card card-static animate-fade-in" style={{ animationDelay: "220ms", padding: "16px 20px" }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 14 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <TrendingUp size={13} style={{ color: "var(--text-3)" }} />
            <span style={{ fontSize: 13, fontWeight: 600, color: "var(--text-2)" }}>Успешных отправок по времени</span>
          </div>
          {isLive && (
            <div style={{
              display: "inline-flex", alignItems: "center", gap: 5,
              fontSize: 11, fontWeight: 600, color: "var(--green)",
              background: "var(--green-bg)", border: "1px solid var(--green-border)",
              borderRadius: 100, padding: "2px 8px",
            }}>
              <Radio size={9} />
              Live
            </div>
          )}
        </div>
        <ActivityChart data={chartData} />
      </div>

      {/* Live log */}
      <div className="card card-static animate-fade-in" style={{ animationDelay: "260ms" }}>
        {/* Log header */}
        <div style={{
          display: "flex", alignItems: "center", justifyContent: "space-between",
          padding: "12px 16px", borderBottom: "1px solid var(--border)",
        }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <Activity size={13} style={{ color: "var(--text-3)" }} />
            <span style={{ fontSize: 13, fontWeight: 600, color: "var(--text-2)" }}>Лента событий</span>
            {isLive && (
              <div style={{
                display: "inline-flex", alignItems: "center", gap: 4,
                fontSize: 10, fontWeight: 700, color: "var(--green)",
                background: "var(--green-bg)", border: "1px solid var(--green-border)",
                borderRadius: 100, padding: "2px 7px", letterSpacing: "0.05em",
              }}>
                <span style={{
                  width: 5, height: 5, borderRadius: "50%", background: "var(--green)",
                  animation: "pulse 1.5s ease-in-out infinite",
                }} />
                LIVE
              </div>
            )}
          </div>
          {/* Mini counters */}
          <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
            {[
              { label: `${sentToday} отправлено`, color: "var(--green)",  border: "var(--green-border)",  bg: "var(--green-bg)" },
              { label: `${joinOk} вступлений`,   color: "var(--blue)",   border: "var(--blue-border)",   bg: "var(--blue-bg)", hide: joinOk === 0 },
              { label: `${errors} ошибок`,        color: "var(--red)",    border: "var(--red-border)",    bg: "var(--red-bg)",  hide: errors === 0 },
            ].filter(s => !s.hide).map((s, i) => (
              <span key={i} style={{
                display: "inline-flex", alignItems: "center",
                height: 20, padding: "0 7px", borderRadius: 100,
                background: s.bg, border: `1px solid ${s.border}`, color: s.color,
                fontSize: 10, fontWeight: 600,
              }}>
                {s.label}
              </span>
            ))}
          </div>
        </div>

        {/* Column headers */}
        <div style={{
          display: "grid",
          gridTemplateColumns: "18px 90px 1fr auto",
          gap: 10,
          padding: "6px 16px",
          borderBottom: "1px solid var(--border)",
          background: "var(--overlay)",
        }}>
          {["", "Аккаунт", "Действие / Канал", "Время"].map((h, i) => (
            <span key={i} style={{ fontSize: 10, fontWeight: 700, letterSpacing: "0.07em", textTransform: "uppercase", color: "var(--text-4)" }}>
              {h}
            </span>
          ))}
        </div>

        {/* Log rows */}
        <div ref={logRef} style={{ maxHeight: 380, overflowY: "auto" }}>
          {loading ? (
            <div style={{ display: "flex", flexDirection: "column", gap: 0 }}>
              {Array.from({ length: 8 }).map((_, i) => (
                <div key={i} className="skeleton" style={{ height: 38, margin: "4px 16px", borderRadius: 4 }} />
              ))}
            </div>
          ) : logs.length === 0 ? (
            <div className="empty" style={{ padding: "40px 24px" }}>
              <div className="empty-icon"><Activity size={16} /></div>
              <span className="empty-title">Нет событий</span>
              <span className="empty-sub">Запустите кампанию — здесь появятся логи в реальном времени</span>
            </div>
          ) : (
            logs.map((entry, i) => <LogRow key={entry.id ?? i} entry={entry} />)
          )}
        </div>
      </div>

      <style>{`
        @keyframes pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.3; }
        }
      `}</style>
    </div>
  );
}
