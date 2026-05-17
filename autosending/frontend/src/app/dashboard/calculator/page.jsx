"use client";
import { useEffect, useState } from "react";
import { getAccounts } from "@/lib/api";
import { Calculator, TrendingUp, Users, AlertTriangle, CheckCircle2, Plus } from "lucide-react";

/* ── Tier config (mirrors backend _get_caps logic) ───────────────────────── */
const TIERS = [
  {
    id: "veteran",
    label: "Ветеран",
    desc: "до 6B · рег. начало 2023",
    daily: 750,
    color: "#9b6dff",
    bg: "rgba(155,109,255,0.10)",
    border: "rgba(155,109,255,0.25)",
    test: (id) => id < 6_000_000_000,
  },
  {
    id: "expert",
    label: "Опытный",
    desc: "6B–7B · конец 2023 / нач. 2024",
    daily: 600,
    color: "var(--gold-hi)",
    bg: "var(--gold-tint)",
    border: "var(--gold-edge)",
    test: (id) => id >= 6_000_000_000 && id < 7_000_000_000,
  },
  {
    id: "growing",
    label: "Растущий",
    desc: "7B–7.5B · 2024",
    daily: 470,
    color: "var(--emerald)",
    bg: "var(--emerald-tint)",
    border: "var(--emerald-edge)",
    test: (id) => id >= 7_000_000_000 && id < 7_500_000_000,
  },
  {
    id: "newbie",
    label: "Новичок",
    desc: "> 7.5B · 2025+",
    daily: 330,
    color: "var(--ink-3)",
    bg: "var(--surface-2)",
    border: "var(--line-2)",
    test: (id) => id >= 7_500_000_000,
  },
];

function tierOf(acc) {
  if (acc.tg_user_id) {
    return TIERS.find((t) => t.test(acc.tg_user_id)) ?? TIERS[3];
  }
  // fallback by messages_sent
  const s = acc.messages_sent ?? 0;
  if (s >= 5000) return TIERS[0];
  if (s >= 2000) return TIERS[1];
  if (s >= 500)  return TIERS[2];
  return TIERS[3];
}

function fmt(n) {
  return Math.round(n).toLocaleString("ru-RU");
}

/* ── Main page ────────────────────────────────────────────────────────────── */
export default function CalculatorPage() {
  const [accounts, setAccounts] = useState([]);
  const [target, setTarget]     = useState(5000);
  const [loading, setLoading]   = useState(true);

  useEffect(() => {
    getAccounts()
      .then((data) => setAccounts(data.filter((a) => a.is_active && a.status !== "error")))
      .finally(() => setLoading(false));
  }, []);

  // Group active accounts by tier
  const tierCounts = TIERS.map((t) => ({
    ...t,
    count: accounts.filter((a) => tierOf(a).id === t.id).length,
  }));

  const totalCapacity = tierCounts.reduce((s, t) => s + t.count * t.daily, 0);
  const gap = Math.max(0, target - totalCapacity);
  const pct = Math.min(100, totalCapacity / Math.max(target, 1) * 100);
  const isEnough = totalCapacity >= target;

  // How many more accounts needed (spread evenly across Опытный/Растущий as default recommendation)
  const needed = TIERS.map((t) => {
    const extra = Math.ceil(gap / t.daily);
    return { ...t, extra };
  });

  // Manual "what-if" inputs
  const [what, setWhat] = useState({ veteran: 0, expert: 0, growing: 0, newbie: 0 });
  const whatCapacity = TIERS.reduce((s, t) => s + (what[t.id] || 0) * t.daily, 0);

  return (
    <div className="animate-fade-in" style={{ display: "flex", flexDirection: "column", gap: 24 }}>
      {/* Header */}
      <div className="page-head">
        <div>
          <h1 className="page-title">Калькулятор</h1>
          <p className="page-sub">Сколько аккаунтов нужно для вашей цели рассылки</p>
        </div>
      </div>

      {/* Target input */}
      <div className="card card-no-hover" style={{ padding: "20px 24px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 16 }}>
          <div style={{ width: 32, height: 32, borderRadius: 8, background: "var(--gold-tint)", border: "1px solid var(--gold-edge)", display: "flex", alignItems: "center", justifyContent: "center", color: "var(--gold-hi)" }}>
            <TrendingUp size={15} />
          </div>
          <span style={{ fontSize: 14, fontWeight: 600, color: "var(--ink-1)" }}>Цель на сутки</span>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 16, flexWrap: "wrap" }}>
          <input
            type="number"
            min={100}
            max={100000}
            step={100}
            value={target}
            onChange={(e) => setTarget(Math.max(100, Number(e.target.value) || 100))}
            style={{
              width: 140, height: 48, borderRadius: 10,
              background: "var(--surface-2)", border: "1px solid var(--line-3)",
              color: "var(--ink-1)", fontFamily: "var(--mono)", fontSize: 22,
              fontWeight: 700, textAlign: "center", outline: "none",
            }}
          />
          <span style={{ fontSize: 14, color: "var(--ink-3)" }}>сообщений / сутки</span>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            {[1000, 2000, 5000, 10000].map((v) => (
              <button
                key={v}
                onClick={() => setTarget(v)}
                className="btn-sm"
                style={{
                  background: target === v ? "var(--surface-3)" : "transparent",
                  border: "1px solid " + (target === v ? "var(--line-3)" : "var(--line-2)"),
                  color: target === v ? "var(--ink-1)" : "var(--ink-4)",
                  fontWeight: target === v ? 600 : 400,
                  cursor: "pointer", borderRadius: 6,
                  height: 30, padding: "0 12px", fontSize: 12,
                }}
              >
                {v.toLocaleString("ru-RU")}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Current capacity */}
      <div className="card card-no-hover" style={{ padding: "20px 24px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 18 }}>
          <div style={{ width: 32, height: 32, borderRadius: 8, background: "rgba(52,199,89,0.1)", border: "1px solid rgba(52,199,89,0.2)", display: "flex", alignItems: "center", justifyContent: "center", color: "var(--emerald)" }}>
            <Users size={15} />
          </div>
          <span style={{ fontSize: 14, fontWeight: 600, color: "var(--ink-1)" }}>Текущий парк аккаунтов</span>
        </div>

        {loading ? (
          <div className="skeleton" style={{ height: 80, borderRadius: 10 }} />
        ) : accounts.length === 0 ? (
          <div style={{ color: "var(--ink-4)", fontSize: 13 }}>Нет активных аккаунтов — добавьте их в разделе «Аккаунты»</div>
        ) : (
          <>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))", gap: 10, marginBottom: 20 }}>
              {tierCounts.map((t) => (
                <div
                  key={t.id}
                  style={{
                    padding: "12px 16px",
                    borderRadius: 10,
                    background: t.bg,
                    border: `1px solid ${t.border}`,
                  }}
                >
                  <div style={{ fontSize: 11, color: t.color, fontWeight: 600, marginBottom: 6 }}>{t.label}</div>
                  <div style={{ fontSize: 24, fontWeight: 700, color: "var(--ink-1)", fontFamily: "var(--mono)", lineHeight: 1 }}>
                    {t.count}
                  </div>
                  <div style={{ fontSize: 11, color: "var(--ink-4)", marginTop: 4 }}>
                    ≈ {fmt(t.count * t.daily)} / сутки
                  </div>
                </div>
              ))}
            </div>

            {/* Progress bar */}
            <div style={{ marginBottom: 8, display: "flex", justifyContent: "space-between", fontSize: 12.5 }}>
              <span style={{ color: "var(--ink-3)" }}>Текущая мощность</span>
              <span style={{ fontFamily: "var(--mono)", fontWeight: 700, color: isEnough ? "var(--emerald)" : "var(--coral)" }}>
                {fmt(totalCapacity)} / {fmt(target)}
              </span>
            </div>
            <div style={{ height: 8, borderRadius: 99, background: "var(--surface-3)", overflow: "hidden" }}>
              <div
                style={{
                  height: "100%",
                  width: `${pct}%`,
                  borderRadius: 99,
                  background: isEnough
                    ? "linear-gradient(90deg, var(--emerald), #5ece7b)"
                    : "linear-gradient(90deg, var(--coral), #ff8a65)",
                  transition: "width 0.4s ease",
                }}
              />
            </div>

            <div
              style={{
                marginTop: 14,
                padding: "11px 14px",
                borderRadius: 9,
                display: "flex", alignItems: "center", gap: 10,
                background: isEnough ? "var(--emerald-tint)" : "var(--coral-tint)",
                border: `1px solid ${isEnough ? "var(--emerald-edge)" : "var(--coral-edge)"}`,
                fontSize: 13, fontWeight: 500,
                color: isEnough ? "var(--emerald)" : "var(--coral)",
              }}
            >
              {isEnough
                ? <><CheckCircle2 size={15} /> Текущего парка достаточно — мощность превышает цель на {fmt(totalCapacity - target)} сообщений</>
                : <><AlertTriangle size={15} /> Не хватает ≈ {fmt(gap)} сообщений в сутки — нужно добавить аккаунты</>
              }
            </div>
          </>
        )}
      </div>

      {/* How many needed to close the gap */}
      {!loading && !isEnough && (
        <div className="card card-no-hover" style={{ padding: "20px 24px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 18 }}>
            <div style={{ width: 32, height: 32, borderRadius: 8, background: "var(--coral-tint)", border: "1px solid var(--coral-edge)", display: "flex", alignItems: "center", justifyContent: "center", color: "var(--coral)" }}>
              <Plus size={15} />
            </div>
            <span style={{ fontSize: 14, fontWeight: 600, color: "var(--ink-1)" }}>Сколько добавить для закрытия цели</span>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))", gap: 10 }}>
            {needed.map((t) => (
              <div
                key={t.id}
                style={{ padding: "14px 16px", borderRadius: 10, background: "var(--surface-1)", border: "1px solid var(--line-2)" }}
              >
                <div style={{ fontSize: 11, color: t.color, fontWeight: 600, marginBottom: 4 }}>{t.label}</div>
                <div style={{ fontSize: 12, color: "var(--ink-4)", marginBottom: 10 }}>{t.desc} · {fmt(t.daily)}/сут каждый</div>
                <div style={{ fontSize: 22, fontWeight: 700, fontFamily: "var(--mono)", color: "var(--ink-1)" }}>
                  +{t.extra} <span style={{ fontSize: 13, color: "var(--ink-4)", fontFamily: "inherit", fontWeight: 400 }}>аккаунт{t.extra % 10 === 1 && t.extra !== 11 ? "" : t.extra % 10 < 5 && (t.extra < 10 || t.extra > 20) ? "а" : "ов"}</span>
                </div>
              </div>
            ))}
          </div>
          <div style={{ marginTop: 12, fontSize: 12, color: "var(--ink-5)" }}>
            * Цифры указаны при использовании только одного тира — реальный миксованный парк потребует пропорционально меньше
          </div>
        </div>
      )}

      {/* What-if calculator */}
      <div className="card card-no-hover" style={{ padding: "20px 24px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 18 }}>
          <div style={{ width: 32, height: 32, borderRadius: 8, background: "var(--surface-2)", border: "1px solid var(--line-2)", display: "flex", alignItems: "center", justifyContent: "center", color: "var(--ink-3)" }}>
            <Calculator size={15} />
          </div>
          <span style={{ fontSize: 14, fontWeight: 600, color: "var(--ink-1)" }}>Конструктор — задай свой микс</span>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: 12, marginBottom: 18 }}>
          {TIERS.map((t) => (
            <div key={t.id}>
              <div style={{ fontSize: 11, color: t.color, fontWeight: 600, marginBottom: 6 }}>
                {t.label} <span style={{ color: "var(--ink-5)", fontWeight: 400 }}>· {fmt(t.daily)}/сут</span>
              </div>
              <input
                type="number"
                min={0}
                max={100}
                value={what[t.id]}
                onChange={(e) => setWhat({ ...what, [t.id]: Math.max(0, Number(e.target.value) || 0) })}
                style={{
                  width: "100%", height: 42, borderRadius: 8,
                  background: "var(--surface-2)", border: `1px solid ${t.border}`,
                  color: "var(--ink-1)", fontFamily: "var(--mono)", fontSize: 18,
                  fontWeight: 600, textAlign: "center", outline: "none",
                }}
              />
            </div>
          ))}
        </div>

        {/* What-if result */}
        {Object.values(what).some((v) => v > 0) && (() => {
          const total = TIERS.reduce((s, t) => s + (what[t.id] || 0), 0);
          const cap = whatCapacity;
          const ok = cap >= target;
          return (
            <div
              style={{
                padding: "14px 16px", borderRadius: 10,
                background: ok ? "var(--emerald-tint)" : "var(--coral-tint)",
                border: `1px solid ${ok ? "var(--emerald-edge)" : "var(--coral-edge)"}`,
                display: "flex", flexDirection: "column", gap: 4,
              }}
            >
              <div style={{ fontSize: 13, fontWeight: 600, color: ok ? "var(--emerald)" : "var(--coral)", display: "flex", alignItems: "center", gap: 8 }}>
                {ok ? <CheckCircle2 size={14} /> : <AlertTriangle size={14} />}
                {total} аккаунт{total % 10 === 1 && total !== 11 ? "" : total % 10 < 5 && (total < 10 || total > 20) ? "а" : "ов"} → ≈ {fmt(cap)} сообщений / сутки
              </div>
              <div style={{ fontSize: 12, color: "var(--ink-3)", paddingLeft: 22 }}>
                {ok
                  ? `Цель в ${fmt(target)} покрыта с запасом +${fmt(cap - target)}`
                  : `До цели ${fmt(target)} не хватает ≈ ${fmt(target - cap)} сообщений`
                }
              </div>
            </div>
          );
        })()}
      </div>

      {/* Tier reference */}
      <div className="card card-no-hover" style={{ padding: "20px 24px" }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: "var(--ink-2)", marginBottom: 14 }}>Справочник тиров</div>
        <div style={{ display: "flex", flexDirection: "column", gap: 0, borderRadius: 10, overflow: "hidden", border: "1px solid var(--line-2)" }}>
          {[
            ["Тир", "Telegram ID", "Период", "Лимит/сессию", "≈ мощность"],
            ...TIERS.map((t) => [t.label, t.desc.split("·")[0].trim(), t.desc.split("·")[1]?.trim() ?? "", `${t.id === "veteran" ? 55 : t.id === "expert" ? 45 : t.id === "growing" ? 35 : 25} отправок`, `≈ ${fmt(t.daily)} / сутки`]),
          ].map((row, i) => (
            <div
              key={i}
              style={{
                display: "grid", gridTemplateColumns: "1fr 1.2fr 1.5fr 1fr 1fr",
                padding: "10px 16px",
                background: i === 0 ? "var(--surface-2)" : "transparent",
                borderBottom: i < TIERS.length ? "1px solid var(--line-2)" : "none",
                fontSize: i === 0 ? 11 : 12.5,
                color: i === 0 ? "var(--ink-4)" : "var(--ink-2)",
                fontWeight: i === 0 ? 600 : 400,
                fontFamily: i === 0 ? "inherit" : "inherit",
              }}
            >
              {row.map((cell, j) => (
                <span key={j} style={{ color: j === 0 && i > 0 ? TIERS[i - 1].color : undefined, fontWeight: j === 0 && i > 0 ? 600 : undefined }}>
                  {cell}
                </span>
              ))}
            </div>
          ))}
        </div>
        <div style={{ marginTop: 10, fontSize: 11.5, color: "var(--ink-5)", lineHeight: 1.6 }}>
          Мощность рассчитана с учётом работы/отдыха (60/50 мин), задержек между каналами и −30% на FloodWait и переподключения.
        </div>
      </div>
    </div>
  );
}
