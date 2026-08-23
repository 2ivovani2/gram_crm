import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";

type ActivationInfo = {
  status: string;
  channel_title: string;
  bot_username: string;
  fallback_url: string;
  already_granted: boolean;
};

function activationCoordinates() {
  const parts = location.pathname.split("/").filter(Boolean);
  return parts.length >= 3
    ? { bot_public_id: parts[1], token: parts[2] }
    : { bot_public_id: "", token: "" };
}

async function post<T>(path: string, body: Record<string, string>): Promise<T> {
  const response = await fetch(`/api/v1/activation/${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(String(payload.detail || "Не удалось продолжить"));
  return payload as T;
}

function ActivationApp() {
  const tg = window.Telegram?.WebApp;
  const coordinates = useMemo(activationCoordinates, []);
  const [info, setInfo] = useState<ActivationInfo | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "confirming" | "done" | "error">("loading");
  const [error, setError] = useState("");

  useEffect(() => {
    tg?.ready();
    tg?.expand();
    if (!tg?.initData || !coordinates.bot_public_id || !coordinates.token) {
      setError("Откройте эту страницу кнопкой из сообщения Telegram.");
      setState("error");
      return;
    }
    post<ActivationInfo>("session", { ...coordinates, init_data: tg.initData })
      .then((result) => {
        setInfo(result);
        setState(result.already_granted ? "done" : "ready");
      })
      .catch((reason: Error) => {
        setError(reason.message);
        setState("error");
      });
  }, [coordinates, tg]);

  const confirm = () => {
    if (!tg?.requestWriteAccess) {
      if (info?.fallback_url) location.href = info.fallback_url;
      return;
    }
    setState("confirming");
    tg.requestWriteAccess((granted) => {
      if (!granted) {
        setError("Без разрешения Telegram не сможет доставить длинную цепочку.");
        setState("ready");
        return;
      }
      post<{ status: string }>("confirm", { ...coordinates, init_data: tg.initData })
        .then(() => setState("done"))
        .catch((reason: Error) => {
          setError(reason.message);
          setState("ready");
        });
    });
  };

  return (
    <main className="activation-shell">
      <section className="activation-card" aria-live="polite">
        <div className="activation-mark">G</div>
        {state === "loading" && <><h1>Проверяем заявку…</h1><p>Это займёт несколько секунд.</p></>}
        {state !== "loading" && state !== "done" && <>
          <span className="eyebrow">GRAMLYHELLO</span>
          <h1>Один шаг до вступления</h1>
          <p>Разрешите боту отправлять сообщения — после этого заявка в «{info?.channel_title || "канал"}» будет обработана, а приветственная цепочка продолжится автоматически.</p>
          {error && <div className="activation-error">{error}</div>}
          {state !== "error" && <button className="button acid activation-action" disabled={state === "confirming"} onClick={confirm}>{state === "confirming" ? "Проверяем…" : "✅ Разрешить и продолжить"}</button>}
          {info?.fallback_url && <a className="activation-fallback" href={info.fallback_url}>↗️ Открыть бота вручную</a>}
        </>}
        {state === "done" && <>
          <span className="eyebrow">ГОТОВО</span>
          <h1>Заявка обрабатывается</h1>
          <p>Бот получил разрешение и запустил приветственную цепочку. Эту страницу можно закрыть.</p>
          <button className="button acid activation-action" onClick={() => tg?.close()}>Закрыть</button>
        </>}
      </section>
    </main>
  );
}

createRoot(document.getElementById("activation-root")!).render(<ActivationApp />);
