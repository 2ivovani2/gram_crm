"use client";
import { useState } from "react";
import {
  ChevronDown, Key, Users, Hash, MessageSquare,
  Zap, Shield, AlertTriangle, BookOpen,
} from "lucide-react";

const SECTIONS = [
  {
    icon: Key,
    title: "1. Получение API-ключей",
    content: [
      "Каждому аккаунту Telegram нужны API-ключи:",
      "",
      "1. Откройте https://my.telegram.org в браузере",
      "2. Войдите с вашим номером телефона",
      "3. Перейдите в «API Development Tools»",
      "4. Заполните «App title» и «Short name» (любые названия)",
      "5. Нажмите «Create application»",
      "6. Скопируйте **API ID** (число) и **API Hash** (строка)",
      "",
      "Никогда не передавайте API Hash третьим лицам.",
    ].join("\n"),
  },
  {
    icon: Users,
    title: "2. Добавление аккаунтов",
    content: [
      "Перейдите на страницу **Аккаунты** и нажмите «Добавить»:",
      "",
      "**Шаг 1 — Данные:** API ID, API Hash и номер телефона (+7...).",
      "**Шаг 2 — Код:** нажмите «Отправить код» — Telegram пришлёт его в приложение или SMS.",
      "**Шаг 3 — Проверка:** введите код. Если включена 2FA — введите пароль.",
      "",
      "После авторизации статус станет «В сети». Добавьте 2–5+ аккаунтов для параллельных кампаний.",
    ].join("\n"),
  },
  {
    icon: Hash,
    title: "3. Добавление каналов",
    content: [
      "Перейдите на страницу **Каналы** для управления целевыми каналами.",
      "",
      "Поддерживаемые форматы: @channel_username, https://t.me/channel, t.me/channel.",
      "",
      "Нажмите на тогл, чтобы временно приостановить канал без удаления. Аккаунты должны иметь доступ к каналу.",
    ].join("\n"),
  },
  {
    icon: MessageSquare,
    title: "4. Создание шаблонов",
    content: [
      "Перейдите на страницу **Шаблоны** для управления пулом сообщений.",
      "",
      "Добавьте не менее 3–5 вариантов для разнообразия. Делайте сообщения разными по длине и структуре.",
      "",
      "**Ротация:** каждое сообщение используется один раз перед повторением. В кампаниях используются только активные шаблоны.",
    ].join("\n"),
  },
  {
    icon: Zap,
    title: "5. Запуск кампаний",
    content: [
      "Перейдите на страницу **Кампании** и нажмите «Новая кампания».",
      "",
      "Мастер из 4 шагов: Настройки → Аккаунты → Каналы → Шаблоны.",
      "",
      "После запуска все аккаунты работают параллельно: каждый независимо проходит по каналам, отправляет случайное сообщение, ждёт заданную задержку.",
      "",
      "Нажмите «Стоп» для плавной остановки после текущего раунда.",
    ].join("\n"),
  },
  {
    icon: Shield,
    title: "6. Защита от блокировки",
    content: [
      "Telegram имеет ограничения скорости и антиспам-системы.",
      "",
      "**Задержки:** новые аккаунты — 120–300с, старые (6+ мес) — 30–60с.",
      "**Качество:** используйте аккаунты с фото и описанием профиля (минимум 1–2 месяца активности).",
      "**Разнообразие:** никогда один текст дважды подряд, сообщения релевантны теме канала.",
      "",
      "AutoSending автоматически обрабатывает ограничения Telegram (FloodWait) — паузирует аккаунт и продолжает.",
    ].join("\n"),
  },
  {
    icon: AlertTriangle,
    title: "7. Мониторинг и логи",
    content: [
      "**Дашборд:** обновляется каждые 8 секунд. Показывает статистику, активные кампании и ленту событий.",
      "",
      "**Лента событий:** аккаунт, канал, фрагмент сообщения, статус, время для каждого действия.",
      "",
      "**Файлы:** data/logs/app.log (ротирующий, макс. 10МБ, 5 резервных файлов).",
    ].join("\n"),
  },
];

function Section({ section }) {
  const [open, setOpen] = useState(false);
  const Icon = section.icon;

  return (
    <div
      style={{
        background: "var(--surface)",
        border: `1px solid ${open ? "var(--accent-border)" : "var(--border)"}`,
        borderRadius: "var(--r-lg)",
        overflow: "hidden",
        transition: "border-color 150ms",
      }}
    >
      <button
        style={{
          width: "100%",
          display: "flex",
          alignItems: "center",
          gap: 12,
          padding: "14px 16px",
          textAlign: "left",
          background: "none",
          border: "none",
          cursor: "pointer",
        }}
        onClick={() => setOpen(o => !o)}
      >
        <div
          style={{
            width: 32,
            height: 32,
            borderRadius: "var(--r-sm)",
            background: open ? "var(--accent-bg)" : "var(--raised)",
            border: `1px solid ${open ? "var(--accent-border)" : "var(--border)"}`,
            color: open ? "var(--accent)" : "var(--text-4)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            flexShrink: 0,
            transition: "all 150ms",
          }}
        >
          <Icon size={15} />
        </div>
        <span style={{ flex: 1, fontSize: 13, fontWeight: 600, color: open ? "var(--text-1)" : "var(--text-2)" }}>
          {section.title}
        </span>
        <ChevronDown
          size={14}
          style={{
            color: "var(--text-4)",
            transform: open ? "rotate(180deg)" : "none",
            transition: "transform 200ms",
            flexShrink: 0,
          }}
        />
      </button>

      {open && (
        <div
          style={{
            padding: "0 16px 16px 16px",
            borderTop: "1px solid var(--border)",
          }}
        >
          <div style={{ paddingTop: 12, fontSize: 13, lineHeight: 1.7, color: "var(--text-3)" }}>
            {section.content.split("\n").map((line, i) => {
              if (!line.trim()) return <br key={i} />;
              const parts = line.split(/(\*\*[^*]+\*\*)/);
              return (
                <p key={i} style={{ marginBottom: 0 }}>
                  {parts.map((part, j) =>
                    part.startsWith("**") ? (
                      <strong key={j} style={{ color: "var(--text-2)", fontWeight: 600 }}>
                        {part.slice(2, -2)}
                      </strong>
                    ) : part
                  )}
                </p>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

export default function HelpPage() {
  return (
    <div style={{ maxWidth: 680, display: "flex", flexDirection: "column", gap: 20 }}>
      {/* Header */}
      <div>
        <h1 style={{ fontSize: 22, fontWeight: 600, letterSpacing: "-0.03em", color: "var(--text-1)", marginBottom: 2 }}>
          Помощь
        </h1>
        <p style={{ fontSize: 13, color: "var(--text-3)" }}>
          Полное руководство по использованию AutoSending
        </p>
      </div>

      {/* Quick start */}
      <div
        style={{
          background: "var(--accent-bg)",
          border: "1px solid var(--accent-border)",
          borderRadius: "var(--r-lg)",
          padding: "16px 20px",
        }}
      >
        <div style={{ display: "flex", alignItems: "flex-start", gap: 12 }}>
          <BookOpen size={15} style={{ color: "var(--accent)", marginTop: 1, flexShrink: 0 }} />
          <div>
            <div style={{ fontSize: 13, fontWeight: 600, color: "var(--accent-hi)", marginBottom: 10 }}>
              Быстрый старт — 6 шагов
            </div>
            <ol style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {[
                <>Получите ключи на <span style={{ fontFamily: "var(--font-mono)", color: "var(--accent-hi)" }}>my.telegram.org</span></>,
                "Добавьте аккаунты → авторизуйте по коду из Telegram",
                "Добавьте целевые каналы / группы",
                "Создайте шаблоны сообщений (3+ вариантов)",
                "Создайте кампанию → выберите аккаунты, каналы, шаблоны",
                "Нажмите Старт → следите на Дашборде",
              ].map((step, i) => (
                <li
                  key={i}
                  style={{ display: "flex", alignItems: "flex-start", gap: 10, fontSize: 13, color: "var(--text-2)", listStyle: "none" }}
                >
                  <span
                    style={{
                      flexShrink: 0,
                      width: 20,
                      height: 20,
                      borderRadius: "50%",
                      background: "var(--accent-bg)",
                      border: "1px solid var(--accent-border)",
                      color: "var(--accent)",
                      fontSize: 10,
                      fontWeight: 700,
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      marginTop: 2,
                    }}
                  >
                    {i + 1}
                  </span>
                  {step}
                </li>
              ))}
            </ol>
          </div>
        </div>
      </div>

      {/* Accordion */}
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        {SECTIONS.map((s, i) => <Section key={i} section={s} />)}
      </div>

      {/* Disclaimer */}
      <div
        style={{
          background: "var(--amber-bg)",
          border: "1px solid var(--amber-border)",
          borderRadius: "var(--r-lg)",
          padding: "14px 16px",
          display: "flex",
          gap: 10,
          alignItems: "flex-start",
        }}
      >
        <AlertTriangle size={14} style={{ color: "var(--amber)", marginTop: 1, flexShrink: 0 }} />
        <p style={{ fontSize: 12, color: "var(--text-3)", lineHeight: 1.6 }}>
          <strong style={{ color: "var(--amber)", fontWeight: 600 }}>Ответственное использование: </strong>
          AutoSending предназначен для легитимного маркетинга. Рассылка спама нарушает Условия Telegram и влечёт блокировку аккаунтов. Всегда получайте разрешение перед рассылкой. Авторы не несут ответственности за нарушения.
        </p>
      </div>
    </div>
  );
}
