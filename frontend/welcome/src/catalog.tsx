import React, { useEffect, useRef, useState } from "react";
import { api, requestId } from "./api";
import "./catalog.css";

type CatalogBot = { id: number; username: string; display_name: string };
export type InstalledTemplate = { flow_id: number; version_id: number; name: string; kind: string };
type Template = {
  id: number; title: string; kind: string; author: string; description: string;
  tags: string[]; screenshots: string[]; conversion: string | null; requires_business: boolean;
  step_count: number; first_delay_seconds: number;
  steps: Array<{ text: string; delay_after_seconds: number; media_types: string[] }>;
};
type CatalogPage = { items: Template[]; total: number; page: number; tags: string[] };

function interval(seconds: number): string {
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor(seconds % 86400 / 3600);
  const minutes = Math.floor(seconds % 3600 / 60);
  return [days, hours, minutes, seconds % 60].map(value => String(value).padStart(2, "0")).join(":");
}

export function MessageCatalog({ bots, botId, onBotChange, onInstalled, onConnect }: {
  bots: CatalogBot[]; botId: number | null; onBotChange: (id: number) => void;
  onInstalled: (value: InstalledTemplate) => void; onConnect: (templateId?: number) => void;
}) {
  const [kind, setKind] = useState("welcome");
  const [tag, setTag] = useState("");
  const [page, setPage] = useState(1);
  const [retry, setRetry] = useState(0);
  const [data, setData] = useState<CatalogPage | null>(null);
  const [selected, setSelected] = useState<Template | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const keys = useRef(new Map<string, string>());
  const detailHeading = useRef<HTMLHeadingElement>(null);
  const detailCard = useRef<HTMLElement>(null);
  const returnToTemplate = useRef<number | null>(null);

  useEffect(() => {
    if (selected) {
      returnToTemplate.current = selected.id;
      detailHeading.current?.focus({preventScroll: true});
      detailCard.current?.scrollIntoView({block: "start"});
    } else if (returnToTemplate.current !== null) {
      const trigger = document.querySelector<HTMLButtonElement>(`[data-template-id="${returnToTemplate.current}"]`);
      trigger?.focus({preventScroll: true});
      trigger?.scrollIntoView({block: "nearest"});
      returnToTemplate.current = null;
    }
  }, [selected]);

  useEffect(() => {
    let active = true;
    setLoading(true); setError("");
    const query = new URLSearchParams({ kind, page: String(page), tag });
    api<CatalogPage>(`/catalog?${query}`).then(value => {
      if (active) setData(value);
    }).catch(reason => { if (active) setError(String(reason.message || reason)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [kind, tag, page, retry]);

  const install = async () => {
    if (!selected || !botId || busy) return;
    const identity = `${botId}:${selected.id}`;
    if (!keys.current.has(identity)) keys.current.set(identity, requestId());
    setBusy(true); setError("");
    try {
      const result = await api<InstalledTemplate>(`/catalog/${selected.id}/install`, {
        method: "POST", headers: { "Idempotency-Key": keys.current.get(identity)! },
        body: JSON.stringify({ bot_id: botId }),
      });
      onInstalled(result);
    } catch (reason: any) { setError(String(reason.message || reason)); }
    finally { setBusy(false); }
  };

  return <section className="message-catalog" aria-busy={loading || busy}>
    <div className="section-head"><h2>Шаблоны сообщений</h2></div>
    <p className="muted">Добавьте новую цепочку и настройте под свой канал. Действующие цепочки и назначения не изменятся.</p>
    {bots.length > 0 && <label className="catalog-bot">Для бота<select disabled={busy} value={botId || ""} onChange={event => onBotChange(Number(event.target.value))}>
      {bots.map(bot => <option key={bot.id} value={bot.id}>@{bot.username || bot.display_name}</option>)}
    </select></label>}
    {error && <div className="error" role="alert">{error} {!selected && <button className="button ghost" onClick={() => setRetry(value => value + 1)}>Повторить загрузку</button>}</div>}
    {selected ? <article ref={detailCard} className="wet-panel catalog-detail">
      <button className="button ghost" disabled={busy} onClick={() => { setSelected(null); setError(""); }}>Назад к шаблонам</button>
      <h3 ref={detailHeading} tabIndex={-1}>{selected.title}</h3><p>{selected.description}</p>
      <p className="muted">Автор: {selected.author} · Конверсия: {selected.conversion || "Нет данных"}</p>
      {selected.requires_business && <p>Для этого шаблона необходим Business.</p>}
      <p>Первое сообщение через {interval(selected.first_delay_seconds)} <small>(дни:часы:минуты:секунды)</small></p>
      <ol className="catalog-messages">{selected.steps.map((step, index) => <li key={index}>
        <div className="catalog-message">{step.text}</div>
        {step.media_types.length > 0 && <small>Вложения: {step.media_types.join(", ")}</small>}
        {index < selected.steps.length - 1 && <small>До следующего сообщения: {interval(step.delay_after_seconds)}</small>}
      </li>)}</ol>
      {selected.screenshots.map((url, index) => <img className="catalog-screenshot" loading="lazy" key={url} src={url} alt={`Демонстрация шаблона ${selected.title}, ${index + 1}`} />)}
      <p className="muted">Создадим неназначенный черновик. Проверьте текст, опубликуйте цепочку и выберите каналы в редакторе.</p>
      {botId ? <button className="button primary" disabled={busy} onClick={install}>{busy ? "Создаём черновик…" : "Добавить цепочку"}</button>
        : <button className="button primary" onClick={() => onConnect(selected.id)}>Подключить бота с этим шаблоном</button>}
      {bots.length > 0 && <button className="button ghost" disabled={busy} onClick={() => onConnect(selected.id)}>Новый бот из этого шаблона</button>}
    </article> : <>
      <div className="catalog-filters"><label>Тип<select aria-label="Тип шаблона" value={kind} onChange={event => { setKind(event.target.value); setPage(1); setTag(""); }}>
        <option value="welcome">Приветственные</option><option value="farewell">Прощальные</option>
      </select></label><label>Категория<select aria-label="Категория шаблона" value={tag} onChange={event => { setTag(event.target.value); setPage(1); }}>
        <option value="">Все категории</option>{data?.tags.map(value => <option key={value}>{value}</option>)}
      </select></label></div>
      {loading ? <p role="status">Загружаем шаблоны…</p> : !error && <>
        {!data?.items.length ? <div className="wet-panel"><h3>Пока нет подходящих шаблонов</h3><p>Попробуйте другую категорию или тип сообщения.</p></div>
          : <div className="catalog-list">{data.items.map(template => <article className="wet-panel" key={template.id}>
            {template.screenshots[0] && <img className="catalog-thumbnail" loading="lazy" src={template.screenshots[0]} alt={`Сообщения шаблона ${template.title}`} />}
            <h3>{template.title}</h3><p>{template.description}</p><p className="muted">{template.step_count} сообщ. · {template.requires_business ? "Business" : "Free и Business"}</p>
            <p className="muted">Автор: {template.author} · Конверсия: {template.conversion || "Нет данных"}</p>
            <button className="button ghost" data-template-id={template.id} onClick={() => { setSelected(template); setError(""); }}>Посмотреть сообщения</button>
          </article>)}</div>}
        {!!data?.total && <nav className="catalog-pages" aria-label="Страницы каталога">
          <button className="button ghost" disabled={page === 1} onClick={() => setPage(value => value - 1)}>Назад</button>
          <span>{page} / {Math.ceil(data.total / 5)}</span>
          <button className="button ghost" disabled={page * 5 >= data.total} onClick={() => setPage(value => value + 1)}>Далее</button>
        </nav>}
      </>}
    </>}
  </section>;
}
