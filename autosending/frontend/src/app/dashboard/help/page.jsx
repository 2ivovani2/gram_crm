"use client";
import { useState } from "react";
import {
  ChevronDown, Key, Users, Hash, MessageSquare,
  Zap, ShieldCheck, AlertTriangle, Rocket, Search,
  MessageCircleQuestion,
} from "lucide-react";

const SECTIONS = [
  {
    icon: Key,
    title: "Получение API-ключей",
    duration: "2 мин",
    content: [
      "Каждому Telegram-аккаунту нужны личные API-ключи:",
      "",
      "1. Откройте https://my.telegram.org в браузере",
      "2. Войдите по номеру телефона",
      "3. Перейдите в «API Development Tools»",
      "4. Заполните «App title» и «Short name» (любые)",
      "5. Нажмите «Create application»",
      "6. Скопируйте **API ID** (число) и **API Hash** (строка)",
      "",
      "Никогда не передавайте API Hash третьим лицам.",
    ].join("\n"),
  },
  {
    icon: Users,
    title: "Добавление аккаунтов",
    duration: "1 мин",
    content: [
      "Перейдите на страницу **Аккаунты** и нажмите «Добавить»:",
      "",
      "**Шаг 1 — Данные.** API ID, API Hash и номер телефона (+7...).",
      "**Шаг 2 — Код.** Нажмите «Отправить код» — Telegram пришлёт его в приложение или SMS.",
      "**Шаг 3 — Проверка.** Введите код. Если включена 2FA — введите облачный пароль.",
      "",
      "После авторизации статус станет «В сети». Добавьте 2–5+ аккаунтов для параллельных кампаний.",
    ].join("\n"),
  },
  {
    icon: Hash,
    title: "Добавление каналов",
    duration: "30 сек",
    content: [
      "Перейдите на страницу **Каналы** для управления целевыми каналами.",
      "",
      "Поддерживаемые форматы: **@channel_username**, **https://t.me/channel**, **t.me/channel**, **t.me/+invite**.",
      "",
      "Нажмите на тогл, чтобы временно приостановить канал без удаления. Аккаунты должны иметь доступ к каналу.",
      "",
      "Массовый импорт CSV поддерживает до 5000 строк за раз — каждая строка проверяется на корректность.",
    ].join("\n"),
  },
  {
    icon: MessageSquare,
    title: "Создание шаблонов",
    duration: "5 мин",
    content: [
      "Перейдите на страницу **Шаблоны** для управления пулом сообщений.",
      "",
      "Добавьте минимум 3–5 вариантов для естественности — разной длины, тона и структуры.",
      "",
      "**Ротация:** каждое сообщение используется один раз перед повторением. В кампаниях используются только активные шаблоны.",
    ].join("\n"),
  },
  {
    icon: Zap,
    title: "Запуск кампаний",
    duration: "2 мин",
    content: [
      "Откройте **Кампании** → «Новая кампания». Мастер из 4 шагов: настройки → аккаунты → каналы → шаблоны.",
      "",
      "После запуска все аккаунты работают параллельно: каждый независимо проходит каналы, отправляет случайное сообщение и ждёт указанную задержку.",
      "",
      "Нажмите «Стоп» для плавной остановки после текущего раунда.",
    ].join("\n"),
  },
  {
    icon: ShieldCheck,
    title: "Защита от блокировки",
    duration: "важно",
    content: [
      "Telegram имеет ограничения и антиспам-системы. Несколько правил:",
      "",
      "**Задержки.** Новые аккаунты — 120–300с, старые (6+ мес) — 30–60с.",
      "**Качество.** Аккаунты с фото и описанием профиля; минимум 1–2 месяца активности.",
      "**Разнообразие.** Никогда один текст дважды подряд; сообщения релевантны теме канала.",
      "",
      "AutoSending автоматически обрабатывает FloodWait — паузирует аккаунт и продолжает.",
    ].join("\n"),
  },
  {
    icon: AlertTriangle,
    title: "Мониторинг и логи",
    duration: "1 мин",
    content: [
      "**Обзор** обновляется каждые 10 секунд. Показывает статистику, активные кампании и ленту событий.",
      "",
      "**Лента событий** содержит аккаунт, канал, фрагмент сообщения, статус и время для каждого действия.",
      "",
      "**Файлы логов:** `data/logs/app.log` (ротирующий, макс. 10МБ, 5 резервных файлов).",
    ].join("\n"),
  },
];

function Section({ section, isOpen, onToggle }) {
  const Icon = section.icon;

  return (
    <div
      className="card"
      style={{
        borderColor: isOpen ? "var(--gold-edge)" : "var(--line-2)",
        background: isOpen
          ? "linear-gradient(180deg, var(--gold-tint), rgba(232,201,138,0.005))"
          : undefined,
      }}
    >
      <button
        style={{
          width: "100%",
          display: "flex",
          alignItems: "center",
          gap: 14,
          padding: "16px 22px",
          textAlign: "left",
          background: "none",
          border: "none",
          cursor: "pointer",
          color: "inherit",
        }}
        onClick={onToggle}
      >
        <div
          style={{
            width: 36, height: 36, borderRadius: 9,
            background: isOpen
              ? "linear-gradient(180deg, var(--gold-hi), var(--gold))"
              : "var(--surface-2)",
            border: `1px solid ${isOpen ? "var(--gold)" : "var(--line-2)"}`,
            color: isOpen ? "var(--on-gold)" : "var(--ink-3)",
            display: "flex", alignItems: "center", justifyContent: "center",
            flexShrink: 0,
            transition: "all 220ms var(--ease)",
            boxShadow: isOpen ? "0 6px 16px -4px var(--gold-glow), 0 1px 0 0 rgba(255,255,255,0.3) inset" : "none",
          }}
        >
          <Icon size={15} strokeWidth={1.8} />
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <span style={{ fontSize: 14.5, fontWeight: 600, color: "var(--ink-1)", letterSpacing: "-0.012em" }}>
              {section.title}
            </span>
            <span style={{ fontSize: 11, color: "var(--ink-5)", fontFamily: "var(--mono)" }}>· {section.duration}</span>
          </div>
        </div>
        <ChevronDown
          size={15}
          style={{
            color: "var(--ink-3)",
            transform: isOpen ? "rotate(180deg)" : "none",
            transition: "transform 220ms var(--ease)",
            flexShrink: 0,
          }}
        />
      </button>

      {isOpen && (
        <div
          className="animate-fade-in"
          style={{
            padding: "16px 22px 22px 72px",
            borderTop: "1px solid var(--line-1)",
          }}
        >
          {section.content.split("\n").map((line, i) => {
            if (!line.trim()) return <div key={i} style={{ height: 6 }} />;
            const parts = line.split(/(\*\*[^*]+\*\*)/);
            return (
              <p key={i} style={{ fontSize: 13.5, color: "var(--ink-2)", lineHeight: 1.7, margin: "4px 0" }}>
                {parts.map((part, j) =>
                  part.startsWith("**") ? (
                    <strong key={j} style={{ color: "var(--ink-1)", fontWeight: 600 }}>
                      {part.slice(2, -2)}
                    </strong>
                  ) : part
                )}
              </p>
            );
          })}
        </div>
      )}
    </div>
  );
}

export default function HelpPage() {
  const [open, setOpen] = useState(0);
  const [query, setQuery] = useState("");

  const filtered = query
    ? SECTIONS.filter(s => (s.title + " " + s.content).toLowerCase().includes(query.toLowerCase()))
    : SECTIONS;

  return (
    <div className="animate-fade-in" style={{ maxWidth: 880, display: "flex", flexDirection: "column", gap: 22 }}>
      {/* Header */}
      <div className="page-head">
        <div>
          <h1 className="page-title">Документация</h1>
          <p className="page-sub">Полное руководство по AutoSending — от первого аккаунта до масштабных кампаний</p>
        </div>
        <div className="page-actions">
          <button className="btn-ghost"><MessageCircleQuestion size={14} /> Связаться с поддержкой</button>
        </div>
      </div>

      {/* Search */}
      <div className="card card-no-hover">
        <div style={{ padding: "18px 22px" }}>
          <div style={{ position: "relative" }}>
            <Search size={15} style={{ position: "absolute", left: 16, top: "50%", transform: "translateY(-50%)", color: "var(--ink-4)", pointerEvents: "none" }} />
            <input
              className="inp"
              style={{ height: 46, paddingLeft: 44, fontSize: 14 }}
              placeholder="Поиск по документации, например «FloodWait»…"
              value={query}
              onChange={e => setQuery(e.target.value)}
            />
          </div>
        </div>
      </div>

      {/* Quick start banner */}
      <div
        className="card card-no-hover"
        style={{
          background: "linear-gradient(135deg, var(--gold-tint) 0%, rgba(232,201,138,0.02) 60%, transparent 100%)",
          borderColor: "var(--gold-edge)",
        }}
      >
        <div style={{ padding: "26px 28px", display: "flex", gap: 20, alignItems: "flex-start" }}>
          <div
            style={{
              width: 52, height: 52, borderRadius: 13,
              background: "linear-gradient(180deg, var(--gold-hi), var(--gold-deep))",
              display: "flex", alignItems: "center", justifyContent: "center",
              flexShrink: 0,
              boxShadow: "0 1px 0 0 rgba(255,255,255,0.3) inset, 0 8px 24px -6px var(--gold-glow)",
            }}
          >
            <Rocket size={22} strokeWidth={1.8} style={{ color: "var(--on-gold)" }} />
          </div>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 17, fontWeight: 600, color: "var(--ink-1)", fontFamily: "var(--display)", letterSpacing: "-0.018em", marginBottom: 4 }}>
              Быстрый старт за 6 шагов
            </div>
            <div style={{ fontSize: 13, color: "var(--ink-3)", marginBottom: 18, lineHeight: 1.55 }}>
              Полная настройка от пустого аккаунта до первой запущенной кампании — около 15 минут
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 8 }}>
              {[
                <>Получить ключи на <span style={{ fontFamily: "var(--mono)", color: "var(--gold-hi)", fontWeight: 600 }}>my.telegram.org</span></>,
                "Авторизовать 2–5 аккаунтов",
                "Добавить целевые каналы",
                "Создать 3–5 шаблонов",
                "Собрать кампанию (4 шага)",
                "Запустить и следить в обзоре",
              ].map((step, i) => (
                <div
                  key={i}
                  style={{
                    display: "flex", alignItems: "flex-start", gap: 10,
                    padding: "10px 12px",
                    background: "rgba(0,0,0,0.22)",
                    border: "1px solid var(--line-2)",
                    borderRadius: 8,
                  }}
                >
                  <span
                    style={{
                      width: 22, height: 22, flexShrink: 0,
                      borderRadius: 6,
                      background: "var(--gold-tint)", border: "1px solid var(--gold-edge)",
                      color: "var(--gold-hi)", fontSize: 11, fontWeight: 700, fontFamily: "var(--mono)",
                      display: "flex", alignItems: "center", justifyContent: "center",
                    }}
                  >
                    {i + 1}
                  </span>
                  <span style={{ fontSize: 12.5, color: "var(--ink-2)", lineHeight: 1.4 }}>{step}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>

      {/* Sections */}
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {filtered.map((s, i) => (
          <Section
            key={i}
            section={s}
            isOpen={open === i}
            onToggle={() => setOpen(open === i ? -1 : i)}
          />
        ))}
      </div>

      {/* Disclaimer */}
      <div
        style={{
          background: "var(--amber-tint)", border: "1px solid var(--amber-edge)",
          borderRadius: "var(--r-lg)",
          padding: "16px 22px",
          display: "flex", gap: 14, alignItems: "flex-start",
        }}
      >
        <AlertTriangle size={16} style={{ color: "var(--amber)", marginTop: 2, flexShrink: 0 }} />
        <p style={{ fontSize: 12.5, color: "var(--ink-2)", lineHeight: 1.6, margin: 0 }}>
          <strong style={{ color: "var(--amber)", fontWeight: 600 }}>Ответственное использование: </strong>
          AutoSending предназначен для легитимного маркетинга. Рассылка спама нарушает Условия Telegram и влечёт блокировку аккаунтов. Всегда получайте разрешение перед массовой рассылкой. Авторы не несут ответственности за нарушения.
        </p>
      </div>
    </div>
  );
}
