import React, { useEffect, useRef, useState } from "react";
import { api, ApiError, requestId } from "../api";
import { ChainComposer, ChainPreview, validateChain } from "./composer";
import { ExitDialog } from "./exit-dialog";
import { hasPremium } from "./types";
import type { Chain, Step } from "./types";

type Document = {
  flow: {
    id: number;
    name: string;
    kind: string;
    bot_id: number;
    bot_username: string;
    channel_ids: number[];
  };
  version: {
    id: number;
    edit_revision: number;
    first_delay_seconds: number;
    button_style: string | null;
    auto_approve: boolean;
  };
  steps: Step[];
  channels: { id: number; title: string; can_invite_users: boolean }[];
};
function chainOf(doc: Document): Chain {
  return {
    first_delay_seconds: doc.version.first_delay_seconds,
    steps: doc.steps,
  };
}
export function FlowEditor({
  flow,
  onClose,
}: {
  flow: { id: number; name: string; kind: string };
  onClose: () => void;
}) {
  const [launched, setLaunched] = useState(false);
  const workspace = useRef<HTMLElement>(null);
  const [doc, setDoc] = useState<Document | null>(null),
    [chain, setChain] = useState<Chain | null>(null),
    [channels, setChannels] = useState<number[]>([]),
    [page, setPage] = useState<"edit" | "review">("edit");
  const [busy, setBusy] = useState(false),
    [uploading, setUploading] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [conflict, setConflict] = useState(false),
    [exit, setExit] = useState(false),
    [loaded, setLoaded] = useState(0);
  useEffect(() => {
    workspace.current?.closest(".modal")?.scrollTo({ top: 0 });
  }, [page]);
  const saved = useRef(""),
    retry = useRef<{ body: string; key: string } | null>(null),
    launchKey = useRef<{ body: string; key: string } | null>(null);
  const content = JSON.stringify({ chain });
  const dirty = !!doc && content !== saved.current;
  const apply = (next: Document) => {
    setDoc(next);
    setChain(chainOf(next));
    saved.current = JSON.stringify({
      chain: chainOf(next),
    });
    setLoaded((n) => n + 1);
    setConflict(false);
  };
  const load = async () => {
    setBusy(true);
    setError("");
    try {
      const next = await api<Document>(`/editor/flows/${flow.id}`);
      apply(next);
      setChannels(next.flow.channel_ids);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  useEffect(() => {
    void load();
  }, [flow.id]);
  useEffect(() => {
    if (!dirty) return;
    const before = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", before);
    window.Telegram?.WebApp?.enableClosingConfirmation?.();
    return () => {
      window.removeEventListener("beforeunload", before);
      window.Telegram?.WebApp?.disableClosingConfirmation?.();
    };
  }, [dirty]);
  const save = async (): Promise<Document | null> => {
    if (!chain || !doc) return null;
    const problem = validateChain(chain);
    if (problem) {
      setError(problem);
      setPage("edit");
      return null;
    }
    setBusy(true);
    setError("");
    setNotice("");
    const body = JSON.stringify({
      ...chain,
      button_style: null,
      expected_revision: doc.version.edit_revision,
    });
    if (retry.current?.body !== body)
      retry.current = { body, key: requestId() };
    try {
      const next = await api<Document>(`/editor/drafts/${doc.version.id}`, {
        method: "PUT",
        headers: { "Idempotency-Key": retry.current.key },
        body,
      });
      apply(next);
      setNotice("Черновик сохранён");
      retry.current = null;
      return next;
    } catch (e) {
      setError((e as Error).message);
      if (e instanceof ApiError && e.status === 409) setConflict(true);
      return null;
    } finally {
      setBusy(false);
    }
  };
  const review = async () => {
    if (dirty) {
      if (!(await save())) return;
    } else if (chain) {
      const problem = validateChain(chain);
      if (problem) {
        setError(problem);
        return;
      }
    }
    setPage("review");
    setNotice("");
    window.scrollTo({ top: 0, behavior: "smooth" });
  };
  const launch = async () => {
    if (!doc || !channels.length) return;
    setBusy(true);
    setError("");
    const body = JSON.stringify({
      expected_revision: doc.version.edit_revision,
      channel_ids: channels,
    });
    if (launchKey.current?.body !== body)
      launchKey.current = { body, key: requestId() };
    try {
      await api(`/editor/drafts/${doc.version.id}/launch`, {
        method: "POST",
        headers: { "Idempotency-Key": launchKey.current.key },
        body,
      });
      setLaunched(true);
    } catch (e) {
      setError((e as Error).message);
      if (e instanceof ApiError && e.status === 409) setConflict(true);
    } finally {
      setBusy(false);
    }
  };
  const total =
    (chain?.first_delay_seconds || 0) +
    (chain?.steps
      .slice(0, -1)
      .reduce((sum, s) => sum + s.delay_after_seconds, 0) || 0);
  if (launched)
    return (
      <section className="flow-workspace">
        <h2>Цепочка запущена</h2>
        <p role="status">
          Новая версия опубликована. Бот @{doc?.flow.bot_username} будет
          отправлять её участникам выбранных каналов.
        </p>
        <ul>
          {doc?.channels
            .filter((c) => channels.includes(c.id))
            .map((c) => (
              <li key={c.id}>{c.title}</li>
            ))}
        </ul>
        <button className="button primary" onClick={onClose}>
          Готово
        </button>
      </section>
    );
  return (
    <section className="flow-workspace" ref={workspace}>
      <header className="workspace-heading">
        <div>
          <p className="editor-status">
            Черновик · {dirty ? "Есть несохранённые изменения" : "Сохранён"}
          </p>
          <h2>{flow.name}</h2>
          <p>Настройте сообщения под свой канал.</p>
        </div>
        <button
          type="button"
          disabled={busy || uploading}
          onClick={() => (dirty ? setExit(true) : onClose())}
        >
          Закрыть
        </button>
      </header>
      {error && (
        <div role="alert" className="composer-error">
          {error}
          {conflict && (
            <button
              type="button"
              onClick={() => {
                if (
                  confirm(
                    "Загрузить актуальную версию? Несохранённые правки на этом экране будут отменены.",
                  )
                )
                  void load();
              }}
            >
              Загрузить актуальную версию
            </button>
          )}
        </div>
      )}
      {notice && (
        <p role="status" className="composer-notice">
          {notice}
        </p>
      )}
      {exit && (
        <ExitDialog
          title="Сохранить изменения перед выходом?"
          busy={busy}
          onSave={async () => {
            if (await save()) onClose();
          }}
          onDiscard={onClose}
          onCancel={() => setExit(false)}
        />
      )}
      {!doc || !chain ? (
        <p role="status">
          {busy ? "Загружаем черновик…" : "Не удалось загрузить черновик"}{" "}
          {!busy && <button onClick={() => void load()}>Повторить</button>}
        </p>
      ) : (
        <>
          <nav className="editor-stages" aria-label="Настройка цепочки">
            <button
              aria-current={page === "edit" ? "step" : undefined}
              disabled={busy || uploading}
              onClick={() => setPage("edit")}
            >
              1. Сообщения
            </button>
            <button
              aria-current={page === "review" ? "step" : undefined}
              disabled={busy || uploading}
              onClick={() => void review()}
            >
              2. Проверка и запуск
            </button>
          </nav>
          {page === "edit" ? (
            <fieldset
              disabled={busy || uploading}
              inert={busy || uploading}
              className="workspace-fields"
            >
              <div className="composer-layout">
                <div>
                  <ChainComposer
                    problem={error}
                    key={loaded}
                    value={chain}
                    onChange={setChain}
                    scope="owner"
                    onBusyChange={setUploading}
                  />
                </div>
                <div className="desktop-preview">
                  <h3>Как выглядит цепочка</h3>
                  <ChainPreview
                    chain={chain}
                    scope="owner"
                  />
                </div>
              </div>
            </fieldset>
          ) : (
            <div className="composer-layout">
              <div>
                <h3>Проверка перед запуском</h3>
                <p>
                  Отправляющий бот: <strong>@{doc.flow.bot_username}</strong>
                </p>
                <fieldset className="channel-picker">
                  <legend>Каналы для этой цепочки</legend>
                  {doc.channels.length ? (
                    doc.channels.map((c) => (
                      <label key={c.id}>
                        <input
                          type="checkbox"
                          disabled={busy}
                          checked={channels.includes(c.id)}
                          onChange={(e) =>
                            setChannels(
                              e.target.checked
                                ? [...channels, c.id]
                                : channels.filter((id) => id !== c.id),
                            )
                          }
                        />
                        <span>{c.title}</span>
                      </label>
                    ))
                  ) : (
                    <p>
                      У бота пока нет каналов. Подключите канал в owner-боте;
                      черновик уже сохранён.
                    </p>
                  )}
                </fieldset>
                {!channels.length && (
                  <p className="composer-notice">
                    Выберите хотя бы один канал для запуска. Черновик можно
                    оставить на потом.
                  </p>
                )}
                {flow.kind === "welcome" && (
                  <details className="editor-settings">
                    <summary>
                      {total <= 240
                        ? "Цепочка отправится до принятия заявки"
                        : "Перед отправкой потребуется разрешение пользователя"}
                    </summary>
                    <p>
                      {total <= 240
                        ? "Все сообщения укладываются в доступное время отправки по заявке."
                        : "Участник сначала получит предложение разрешить сообщения. Полная цепочка запустится после его подтверждения."}
                    </p>
                    {total > 240 && doc.version.auto_approve && (
                      <p>
                        Автопринятие включено: без подтверждения участник будет
                        принят, но цепочка не запустится.
                      </p>
                    )}
                    {total > 240 &&
                      channels.some(
                        (id) =>
                          doc.channels.find((c) => c.id === id)
                            ?.can_invite_users === false,
                      ) && (
                        <p>
                          У бота нет права приглашать пользователей в один из
                          выбранных каналов. Проверьте права перед запуском.
                        </p>
                      )}
                  </details>
                )}
                {hasPremium(chain) && (
                  <p className="composer-notice">
                    Содержит Premium-эмодзи. Для их отправки у владельца
                    отправляющего бота должен быть Telegram Premium либо у бота
                    — дополнительный username, приобретённый на Fragment.
                    Premium администратора каталога не передаётся клиентским
                    ботам; тариф Gramly Business этого ограничения не снимает.
                    Отображение в предпросмотре не подтверждает возможности
                    вашего бота.
                  </p>
                )}
              </div>
              <div>
                <h3>Предпросмотр</h3>
                <ChainPreview chain={chain} scope="owner" />
              </div>
            </div>
          )}
          <footer className="editor-footer">
            <span role="status">
              {busy
                ? "Сохраняем…"
                : uploading
                  ? "Загружаем файл…"
                  : dirty
                    ? "Изменения ещё не сохранены"
                    : "Черновик сохранён"}
            </span>
            <div className="composer-actions">
              <button
                className="button ghost"
                disabled={busy || uploading || conflict}
                onClick={() => void save()}
              >
                Сохранить черновик
              </button>
              {page === "edit" ? (
                <button
                  className="button primary"
                  disabled={busy || uploading || conflict}
                  onClick={() => void review()}
                >
                  Далее: предпросмотр
                </button>
              ) : (
                <button
                  className="button primary"
                  disabled={busy || !channels.length || conflict}
                  onClick={() => void launch()}
                >
                  Запустить на выбранных каналах
                </button>
              )}
            </div>
          </footer>
        </>
      )}
    </section>
  );
}
