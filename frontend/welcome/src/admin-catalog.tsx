import React, { useEffect, useRef, useState } from "react";
import { adminApi, requestId } from "./api";
import { ChainComposer, ChainPreview, validateChain } from "./editor/composer";
import { ExitDialog } from "./editor/exit-dialog";
import type { LeaveGuard } from "./editor/exit-dialog";
import { blankStep, hasPremium } from "./editor/types";
import type { Chain } from "./editor/types";

type Entry = {
  slug: string;
  revision: number;
  kind: "welcome" | "farewell";
  title: string;
  author: string;
  description: string;
  tags: string[];
  screenshots: string[];
  conversion: string | null;
  requires_business: boolean;
  spec: Chain;
};
type Item = Entry & { id: number; is_active: boolean };
const fresh = (): Entry => ({
  slug: `template-${requestId()}`,
  revision: 1,
  kind: "welcome",
  title: "",
  author: "Gramly",
  description: "",
  tags: [],
  screenshots: [],
  conversion: null,
  requires_business: false,
  spec: { first_delay_seconds: 0, steps: [blankStep()] },
});
const cleanEntry = (entry: Entry) => ({
  ...entry,
  tags: entry.tags.map((t) => t.trim()).filter(Boolean),
  spec: {
    ...entry.spec,
    steps: entry.spec.steps.map(
      ({ localId, id, editor_read_only, ...s }, i, steps) => ({
        ...s,
        delay_after_seconds: i === steps.length - 1 ? 0 : s.delay_after_seconds,
        attachments: s.attachments.map(
          ({ preview_url, media_id, id, ...m }) => m,
        ),
      }),
    ),
  },
});
function TemplateWizard({
  initial,
  expectedRevision,
  onBack,
  onChanged,
  onLeaveGuard,
}: {
  onLeaveGuard?: (guard: LeaveGuard | null) => void;
  initial: Entry;
  expectedRevision: number;
  onBack: () => void;
  onChanged: () => void;
}) {
  const [entry, setEntry] = useState(initial),
    [expected, setExpected] = useState(expectedRevision),
    [stage, setStage] = useState(0),
    [busy, setBusy] = useState(false),
    [uploading, setUploading] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [savedItem, setSavedItem] = useState<Item | null>(null),
    [exit, setExit] = useState(false);
  const baseline = useRef(JSON.stringify(initial));
  const dirty = JSON.stringify(entry) !== baseline.current;
  useEffect(() => {
    if (!dirty) return;
    const f = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", f);
    return () => window.removeEventListener("beforeunload", f);
  }, [dirty]);
  const leave = useRef(onBack);
  useEffect(() => {
    onLeaveGuard?.((next) => {
      if (busy || uploading) return;
      if (dirty) {
        leave.current = next;
        setExit(true);
      } else next();
    });
    return () => onLeaveGuard?.(null);
  }, [dirty, busy, uploading, onLeaveGuard]);
  useEffect(() => {
    if (error && stage === 0)
      document
        .querySelector<HTMLElement>('.template-workspace [aria-invalid="true"]')
        ?.focus();
  }, [error, stage]);
  const metadataError = () =>
    !entry.title.trim()
      ? "Укажите название шаблона."
      : !entry.description.trim()
        ? "Добавьте описание: кому и для чего подходит шаблон."
        : !entry.author.trim()
          ? "Укажите автора."
          : null;
  const go = (next: number) => {
    setError("");
    if (next > 0) {
      const problem = metadataError();
      if (problem) {
        setError(problem);
        setStage(0);
        return;
      }
    }
    if (next > 1) {
      const problem = validateChain(entry.spec);
      if (problem) {
        setError(problem);
        setStage(1);
        return;
      }
    }
    setStage(next);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };
  const save = async (publish = false): Promise<boolean> => {
    const problem = metadataError() || validateChain(entry.spec);
    if (problem) {
      setError(problem);
      setStage(metadataError() ? 0 : 1);
      return false;
    }
    setBusy(true);
    setError("");
    setNotice("");
    try {
      let item = savedItem;
      if (!item || dirty) {
        const cleaned = cleanEntry({ ...entry, revision: expected + 1 });
        item = await adminApi<Item>("/catalog", {
          method: "POST",
          body: JSON.stringify({ entry: cleaned, expected_revision: expected }),
        });
        const { id, is_active, ...next } = item;
        setEntry(next);
        baseline.current = JSON.stringify(next);
        setExpected(item.revision);
        setSavedItem(item);
        onChanged();
      }
      if (publish && !item.is_active) {
        item = await adminApi<Item>(`/catalog/${item.id}/publish`, {
          method: "POST",
        });
        setSavedItem(item);
        onChanged();
      }
      setNotice(
        publish
          ? "Шаблон опубликован и доступен в Mini App."
          : "Черновик шаблона сохранён. Клиенты пока его не видят.",
      );
      return true;
    } catch (e) {
      setError((e as Error).message);
      return false;
    } finally {
      setBusy(false);
    }
  };
  const update = (patch: Partial<Entry>) => {
    setEntry((e) => ({ ...e, ...patch }));
    setNotice("");
  };
  return (
    <section className="template-workspace">
      <header className="workspace-heading">
        <div>
          <p className="editor-status">
            {savedItem?.is_active && !dirty
              ? "Опубликован"
              : dirty
                ? "Есть несохранённые изменения"
                : "Черновик шаблона"}
          </p>
          <h2>
            {expectedRevision ? "Редактирование шаблона" : "Новый шаблон"}
          </h2>
          <p>Соберите цепочку, проверьте её и добавьте в каталог.</p>
        </div>
        <button
          disabled={busy || uploading}
          onClick={() => {
            leave.current = onBack;
            dirty ? setExit(true) : onBack();
          }}
        >
          К каталогу
        </button>
      </header>
      <nav className="editor-stages" aria-label="Создание шаблона">
        {["О шаблоне", "Сообщения", "Проверка"].map((s, i) => (
          <button
            key={s}
            disabled={busy || uploading}
            aria-current={stage === i ? "step" : undefined}
            onClick={() => go(i)}
          >
            {i + 1}. {s}
          </button>
        ))}
      </nav>
      {error && (
        <p role="alert" className="composer-error">
          {error}
        </p>
      )}
      {notice && (
        <p role="status" className="composer-notice">
          {notice}
        </p>
      )}
      {exit && (
        <ExitDialog
          title="Сохранить шаблон перед выходом?"
          busy={busy}
          onSave={async () => {
            if (await save()) leave.current();
          }}
          onDiscard={() => leave.current()}
          onCancel={() => setExit(false)}
        />
      )}
      <fieldset
        disabled={busy || uploading}
        inert={busy || uploading}
        className="workspace-fields"
      >
        {stage === 0 && (
          <div className="template-details">
            <label>
              Название
              <input
                name="template-title"
                aria-invalid={!!error && !entry.title.trim()}
                value={entry.title}
                maxLength={128}
                onChange={(e) => update({ title: e.target.value })}
                placeholder="Например, знакомство с сообществом"
              />
            </label>
            <label>
              Когда отправлять
              <select
                value={entry.kind}
                disabled={expectedRevision > 0 || !!savedItem}
                onChange={(e) =>
                  update({ kind: e.target.value as Entry["kind"] })
                }
              >
                <option value="welcome">При вступлении — приветствие</option>
                <option value="farewell">При выходе — прощание</option>
              </select>
            </label>
            <label>
              Описание для каталога
              <textarea
                name="template-description"
                aria-invalid={!!error && !entry.description.trim()}
                value={entry.description}
                maxLength={4000}
                onChange={(e) => update({ description: e.target.value })}
                placeholder="Кому подойдёт шаблон и что получит участник"
              />
            </label>
            <label>
              Категории
              <input
                value={entry.tags.join(", ")}
                onChange={(e) =>
                  update({
                    tags: e.target.value.split(",").map((s) => s.trim()),
                  })
                }
                placeholder="Сообщество, обучение"
              />
              <small>Несколько категорий можно разделить запятой.</small>
            </label>
            <label>
              Доступность
              <select
                value={entry.requires_business ? "business" : "all"}
                onChange={(e) =>
                  update({ requires_business: e.target.value === "business" })
                }
              >
                <option value="all">Free и Business</option>
                <option value="business">Только Business</option>
              </select>
            </label>
            <details className="editor-settings">
              <summary>Автор и дополнительные сведения</summary>
              <label>
                Автор
                <input
                  value={entry.author}
                  maxLength={80}
                  onChange={(e) => update({ author: e.target.value })}
                />
              </label>
              {entry.conversion && (
                <p>Сохранённые сведения о конверсии: {entry.conversion}</p>
              )}
            </details>
          </div>
        )}
        {stage === 1 && (
          <div className="composer-layout">
            <ChainComposer
              problem={error}
              value={entry.spec}
              onChange={(spec) => update({ spec, screenshots: [] })}
              scope="admin"
              onBusyChange={setUploading}
            />
            <div className="desktop-preview">
              <h3>Предпросмотр</h3>
              <ChainPreview chain={entry.spec} scope="admin" />
            </div>
          </div>
        )}
        {stage === 2 && (
          <div className="composer-layout">
            <div>
              <h3>Карточка в каталоге</h3>
              <article className="template-summary">
                <h4>{entry.title}</h4>
                <p>{entry.description}</p>
                <p>
                  {entry.kind === "welcome" ? "Приветствие" : "Прощание"} ·{" "}
                  {entry.spec.steps.length} сообщ. ·{" "}
                  {entry.requires_business ? "Business" : "Free и Business"}
                </p>
                <p>Автор: {entry.author}</p>
                {hasPremium(entry.spec) && <p>Содержит Premium-эмодзи</p>}
              </article>
              <p className="composer-notice">
                Сохранение оставляет версию скрытой. Публикация заменит текущую
                версию в каталоге. Уже созданные клиентские цепочки сохранятся.
              </p>
              <button type="button" onClick={() => go(0)}>
                Изменить описание
              </button>
              <button type="button" onClick={() => go(1)}>
                Изменить сообщения
              </button>
            </div>
            <div>
              <h3>Сообщения участнику</h3>
              <ChainPreview chain={entry.spec} scope="admin" />
            </div>
          </div>
        )}
      </fieldset>
      <footer className="editor-footer">
        <span>
          {busy
            ? "Сохраняем…"
            : uploading
              ? "Загружаем файл…"
              : dirty
                ? "Не сохранено"
                : savedItem?.is_active
                  ? "Опубликовано"
                  : "Черновик"}
        </span>
        <div className="composer-actions">
          {stage > 0 && (
            <button
              className="button ghost"
              disabled={busy || uploading}
              onClick={() => go(stage - 1)}
            >
              Назад
            </button>
          )}
          <button
            className="button ghost"
            disabled={busy || uploading || (!!savedItem?.is_active && !dirty)}
            onClick={() => void save()}
          >
            Сохранить черновик шаблона
          </button>
          {stage < 2 ? (
            <button
              className="button acid"
              disabled={busy || uploading}
              onClick={() => go(stage + 1)}
            >
              {stage === 0 ? "Далее: сообщения" : "Далее: проверка"}
            </button>
          ) : (
            <button
              className="button acid"
              disabled={busy || uploading || (!!savedItem?.is_active && !dirty)}
              onClick={() => void save(true)}
            >
              Опубликовать
            </button>
          )}
        </div>
      </footer>
    </section>
  );
}
export function AdminCatalog({
  onLeaveGuard,
}: {
  onLeaveGuard?: (guard: LeaveGuard | null) => void;
}) {
  const [items, setItems] = useState<Item[]>([]),
    [total, setTotal] = useState(0),
    [page, setPage] = useState(1),
    [query, setQuery] = useState(""),
    [kind, setKind] = useState(""),
    [state, setState] = useState("all"),
    [loading, setLoading] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const [draft, setDraft] = useState<Entry | null>(null),
    [expected, setExpected] = useState(0);
  const version = useRef(0);
  const load = async () => {
    const current = ++version.current;
    setLoading(true);
    try {
      const result = await adminApi<{ items: Item[]; total: number }>(
        `/catalog?${new URLSearchParams({ q: query, kind, state, page: String(page) })}`,
      );
      if (current === version.current) {
        setItems(result.items);
        setTotal(result.total);
        setError("");
      }
    } catch (e) {
      if (current === version.current) setError((e as Error).message);
    } finally {
      if (current === version.current) setLoading(false);
    }
  };
  useEffect(() => {
    void load();
    return () => {
      version.current++;
    };
  }, [query, kind, state, page]);
  const publish = async (item: Item, archive = false) => {
    if (
      !confirm(
        archive
          ? "Убрать шаблон из каталога? Клиентские цепочки сохранятся."
          : "Опубликовать шаблон для клиентов?",
      )
    )
      return;
    setBusy(true);
    try {
      await adminApi(`/catalog/${item.id}${archive ? "" : "/publish"}`, {
        method: archive ? "DELETE" : "POST",
      });
      setNotice(
        archive
          ? "Шаблон скрыт. Клиентские копии сохранены."
          : "Шаблон опубликован.",
      );
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  if (draft)
    return (
      <TemplateWizard
        onLeaveGuard={onLeaveGuard}
        initial={draft}
        expectedRevision={expected}
        onChanged={() => void load()}
        onBack={() => {
          setDraft(null);
          void load();
        }}
      />
    );
  return (
    <section className="catalog-admin">
      <div className="catalog-admin-actions">
        <button
          className="button acid"
          onClick={() => {
            setDraft(fresh());
            setExpected(0);
          }}
        >
          Добавить шаблон
        </button>
        <button className="button ghost" onClick={() => void load()}>
          Обновить
        </button>
      </div>
      <div className="catalog-admin-filters">
        <label>
          Поиск
          <input
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setPage(1);
            }}
            placeholder="Название шаблона"
          />
        </label>
        <label>
          Тип
          <select
            value={kind}
            onChange={(e) => {
              setKind(e.target.value);
              setPage(1);
            }}
          >
            <option value="">Все</option>
            <option value="welcome">Приветствия</option>
            <option value="farewell">Прощания</option>
          </select>
        </label>
        <label>
          Публикация
          <select
            value={state}
            onChange={(e) => {
              setState(e.target.value);
              setPage(1);
            }}
          >
            <option value="all">Все версии</option>
            <option value="published">Опубликованные</option>
            <option value="hidden">Черновики и скрытые</option>
          </select>
        </label>
      </div>
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      {loading ? (
        <p>Загружаем каталог…</p>
      ) : !items.length ? (
        <p>Шаблонов пока нет. Добавьте первый или измените фильтры.</p>
      ) : (
        items.map((item) => (
          <article className="catalog-admin-row" key={item.id}>
            <div>
              <h3>{item.title}</h3>
              <p>
                {item.kind === "welcome" ? "Приветствие" : "Прощание"} ·{" "}
                {item.is_active ? "Опубликован" : "Скрыт"} ·{" "}
                {item.spec.steps.length} сообщ.
              </p>
              <p>{item.description}</p>
              {hasPremium(item.spec) && <small>Содержит Premium-эмодзи</small>}
            </div>
            <div className="catalog-admin-actions">
              <button
                className="button ghost"
                disabled={busy}
                onClick={() => {
                  const { id, is_active, ...entry } = item;
                  setExpected(item.revision);
                  setDraft(structuredClone(entry));
                }}
              >
                Редактировать
              </button>
              <button
                className={`button ${item.is_active ? "ghost" : "acid"}`}
                disabled={busy}
                onClick={() => void publish(item, item.is_active)}
              >
                {item.is_active ? "Убрать из каталога" : "Опубликовать"}
              </button>
            </div>
          </article>
        ))
      )}
      <div className="catalog-admin-actions">
        <button
          className="button ghost"
          disabled={page === 1 || loading}
          onClick={() => setPage(page - 1)}
        >
          Назад
        </button>
        <span>
          Страница {page} · всего {total}
        </span>
        <button
          className="button ghost"
          disabled={page * 20 >= total || loading}
          onClick={() => setPage(page + 1)}
        >
          Далее
        </button>
      </div>
    </section>
  );
}
