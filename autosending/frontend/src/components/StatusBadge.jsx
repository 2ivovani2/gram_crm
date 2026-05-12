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

export default function StatusBadge({ status }) {
  return (
    <span className={`badge badge-${status ?? "offline"}`}>
      <span className="badge-dot" />
      {LABELS[status] ?? status ?? "—"}
    </span>
  );
}
