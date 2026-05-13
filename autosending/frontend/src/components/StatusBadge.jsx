const LABELS = {
  online:  "В сети",
  offline: "Не в сети",
  working: "Работает",
  pending: "Ожидание",
  error:   "Ошибка",
  banned:  "Заблокирован",
  running: "Запущена",
  idle:    "Ожидание",
  stopped: "Остановлена",
  success: "Успех",
  info:    "Инфо",
};

export default function StatusBadge({ status, pulse }) {
  const cls = `badge badge-${status ?? "offline"}`;
  return (
    <span className={cls}>
      {pulse
        ? <span className="live-dot" />
        : <span className="badge-dot" />
      }
      {LABELS[status] ?? status ?? "—"}
    </span>
  );
}
