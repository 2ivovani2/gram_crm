/** Load Telegram without letting an unavailable third-party host block HTML parsing. */
export async function loadTelegramSdk(timeoutMs = 8000): Promise<void> {
  if (window.Telegram?.WebApp) return;
  const parameters = new URLSearchParams(location.hash.slice(1));
  if (!parameters.has("tgWebAppData") && !parameters.has("tgWebAppVersion")) return;
  await new Promise<void>((resolve, reject) => {
    const script = document.createElement("script");
    let settled = false;
    const finish = (error?: Error) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      script.onload = null;
      script.onerror = null;
      if (error) { script.remove(); reject(error); } else resolve();
    };
    const unavailable = () => new Error("Не удалось подключиться к Telegram. Проверьте соединение и повторите запуск Mini App. Ваши настройки сохранены.");
    const timer = window.setTimeout(() => finish(unavailable()), timeoutMs);
    script.async = true;
    script.src = "https://telegram.org/js/telegram-web-app.js?63";
    script.onload = () => finish(window.Telegram?.WebApp ? undefined : unavailable());
    script.onerror = () => finish(unavailable());
    document.head.append(script);
  });
}
