import React, { useEffect, useState } from "react";
import { api, requestId } from "./api";
import { ChainComposer, ChainPreview, validateChain } from "./editor/composer";
import { blankStep } from "./editor/types";
import type { Chain } from "./editor/types";

type Bot = { id: number; username: string; display_name: string };
type Channel = { id: number; title: string; username: string };
type Contact = {
  id: number;
  bot_id: number;
  username: string;
  first_name: string;
  last_name: string;
  status: string;
  consented_at?: string;
  unsubscribed_at?: string;
  source_channel_id?: number;
};
type Broadcast = {
  id: number;
  bot_id: number;
  name: string;
  status: string;
  scheduled_at?: string;
  created_at: string;
  stats: Record<string, number>;
};

const statusLabel: Record<string, string> = {
  none: "Без согласия",
  active: "Активен",
  unsubscribed: "Отписался",
  blocked: "Заблокировал",
};

export function AudiencePage({ bots, activeBot, onBotChange }: { bots: Bot[]; activeBot: number | null; onBotChange: (id: number) => void }) {
  const [items, setItems] = useState<Contact[]>([]);
  const [channels, setChannels] = useState<Channel[]>([]);
  const [sourceChannel, setSourceChannel] = useState("");
  const [total, setTotal] = useState(0);
  const [status, setStatus] = useState("active");
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    const params = new URLSearchParams({ status_filter: status, q: query });
    if (activeBot) params.set("bot_id", String(activeBot));
    if (sourceChannel) params.set("source_channel_id", sourceChannel);
    api<{ items: Contact[]; total: number }>(`/audience?${params}`)
      .then((value) => { setItems(value.items); setTotal(value.total); setError(""); })
      .catch((e) => setError((e as Error).message));
  }, [activeBot, sourceChannel, status, query]);
  useEffect(() => {
    setSourceChannel("");
    if (!activeBot) { setChannels([]); return; }
    api<{channels: Channel[]}>(`/bots/${activeBot}`).then(value=>setChannels(value.channels)).catch(()=>setChannels([]));
  }, [activeBot]);
  const exportParams = new URLSearchParams();
  if (activeBot) exportParams.set("bot_id", String(activeBot));
  if (sourceChannel) exportParams.set("source_channel_id", sourceChannel);
  return <section className="audience-page">
    <div className="section-head"><div><span className="eyebrow">СОГЛАСИЕ ПОДТВЕРЖДЕНО</span><h2>База подписчиков</h2></div><b>{total}</b></div>
    <p className="section-lead">В базу попадают только люди, которые открыли клиентского бота и прошли проверку.</p>
    <div className="audience-filters wet-panel">
      <label>Бот<select value={activeBot || ""} onChange={e=>onBotChange(Number(e.target.value))}>{bots.map(bot=><option key={bot.id} value={bot.id}>@{bot.username}</option>)}</select></label>
      <label>Источник<select value={sourceChannel} onChange={e=>setSourceChannel(e.target.value)}><option value="">Все каналы</option>{channels.map(channel=><option key={channel.id} value={channel.id}>{channel.title}</option>)}</select></label>
      <label>Статус<select value={status} onChange={e=>setStatus(e.target.value)}><option value="active">Активные</option><option value="unsubscribed">Отписались</option><option value="blocked">Заблокировали</option><option value="all">Все</option></select></label>
      <label>Поиск<input value={query} onChange={e=>setQuery(e.target.value)} placeholder="Имя или username"/></label>
      <a className="button ghost" href={`/api/v1/audience.csv?${exportParams}`}>Скачать CSV</a>
    </div>
    {error&&<p className="composer-error" role="alert">{error}</p>}
    {!items.length?<div className="empty-state wet-panel"><div><h3>Подписчиков пока нет</h3><p>Добавьте в приветствие кнопку «Добавить в базу». После captcha люди появятся здесь.</p></div></div>:<div className="audience-list">{items.map(item=><article className="wet-panel" key={item.id}><div><strong>{[item.first_name,item.last_name].filter(Boolean).join(" ")||"Без имени"}</strong><small>@{item.username||"username не указан"}</small></div><span className={`consent-status ${item.status}`}>{statusLabel[item.status]||item.status}</span><small>{item.consented_at?new Date(item.consented_at).toLocaleString("ru-RU"):"Согласие не получено"}</small></article>)}</div>}
  </section>;
}

export function BroadcastsPage({ bots, activeBot, onBotChange }: { bots: Bot[]; activeBot: number | null; onBotChange: (id: number) => void }) {
  const [items, setItems] = useState<Broadcast[]>([]);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [schedule, setSchedule] = useState("");
  const [channels, setChannels] = useState<Channel[]>([]);
  const [sourceChannel, setSourceChannel] = useState("");
  const [audienceTotal, setAudienceTotal] = useState(0);
  const [chain, setChain] = useState<Chain>({ first_delay_seconds: 0, steps: [blankStep()] });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const load = () => api<{items: Broadcast[]}>("/broadcasts").then(v=>setItems(v.items)).catch(e=>setError((e as Error).message));
  useEffect(load, []);
  useEffect(()=>{
    setSourceChannel(""); setAudienceTotal(0);
    if(!activeBot){setChannels([]);return;}
    api<{channels:Channel[]}>(`/bots/${activeBot}`).then(value=>setChannels(value.channels)).catch(()=>setChannels([]));
  },[activeBot]);
  useEffect(()=>{
    if(!activeBot){setAudienceTotal(0);return;}
    const params=new URLSearchParams({bot_id:String(activeBot),status_filter:"active"});
    if(sourceChannel)params.set("source_channel_id",sourceChannel);
    api<{total:number}>(`/audience?${params}`).then(value=>setAudienceTotal(value.total)).catch(()=>setAudienceTotal(0));
  },[activeBot,sourceChannel]);
  const saveAndLaunch = async () => {
    if (!activeBot) { setError("Сначала подключите бота."); return; }
    const problem = validateChain(chain);
    if (problem) { setError(problem); return; }
    if (!audienceTotal) { setError("В выбранной аудитории пока нет активных подписчиков."); return; }
    if (!window.confirm(`Запустить рассылку для ${audienceTotal} получателей? Содержимое и список получателей после запуска изменить нельзя.`)) return;
    setBusy(true); setError("");
    try {
      const step = chain.steps[0];
      const draft = await api<{id:number}>("/broadcasts", {
        method:"POST",
        headers:{"Idempotency-Key":requestId()},
        body:JSON.stringify({
          bot_id:activeBot,
          name:name.trim()||"Новая рассылка",
          source_channel_id:sourceChannel?Number(sourceChannel):null,
          content:{payload:step.payload,attachments:step.attachments,keyboard:step.keyboard},
        }),
      });
      await api(`/broadcasts/${draft.id}/launch`, {method:"POST",body:JSON.stringify({scheduled_at:schedule?new Date(schedule).toISOString():null})});
      setCreating(false); setName(""); setSchedule(""); setSourceChannel(""); setChain({first_delay_seconds:0,steps:[blankStep()]});
      await load();
    } catch(e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  if (creating) return <section className="broadcast-editor"><button className="back-link" onClick={()=>setCreating(false)}>← К рассылкам</button><div className="section-head"><div><span className="eyebrow">НОВАЯ КАМПАНИЯ</span><h2>Сообщение подписчикам</h2></div></div>{error&&<p className="composer-error" role="alert">{error}</p>}<div className="broadcast-target wet-panel"><label>Бот<select value={activeBot||""} onChange={e=>onBotChange(Number(e.target.value))}>{bots.map(bot=><option key={bot.id} value={bot.id}>@{bot.username}</option>)}</select></label><label>Источник<select value={sourceChannel} onChange={e=>setSourceChannel(e.target.value)}><option value="">Все каналы</option>{channels.map(channel=><option key={channel.id} value={channel.id}>{channel.title}</option>)}</select></label><strong>{audienceTotal} получателей</strong></div><label className="broadcast-name">Название<input value={name} maxLength={128} onChange={e=>setName(e.target.value)} placeholder="Например, анонс вебинара"/></label><div className="composer-layout"><ChainComposer value={chain} onChange={setChain} scope="owner" maxSteps={1} allowSubscribe={false}/><div className="desktop-preview"><h3>Предпросмотр</h3><ChainPreview chain={chain} scope="owner"/></div></div><label className="broadcast-name">Отложить до<input type="datetime-local" value={schedule} onChange={e=>setSchedule(e.target.value)}/><small>Оставьте пустым, чтобы отправить сразу.</small></label><button className="button acid" disabled={busy||!audienceTotal} onClick={()=>void saveAndLaunch()}>{busy?"Готовим аудиторию…":schedule?`Запланировать для ${audienceTotal}`:`Запустить для ${audienceTotal}`}</button></section>;
  return <section><div className="section-head"><div><span className="eyebrow">ТОЛЬКО АКТИВНАЯ БАЗА</span><h2>Рассылки</h2></div><button className="button acid" onClick={()=>setCreating(true)}>Создать</button></div><p className="section-lead">Получатели фиксируются при запуске. В каждое сообщение автоматически добавляется отписка.</p>{error&&<p className="composer-error" role="alert">{error}</p>}{!items.length?<div className="empty-state wet-panel"><div><h3>Рассылок ещё нет</h3><p>Соберите первое сообщение и отправьте его подписчикам выбранного бота.</p></div></div>:<div className="broadcast-list">{items.map(item=><article className="wet-panel" key={item.id}><div><span>{item.status.toUpperCase()}</span><strong>{item.name}</strong><small>{new Date(item.scheduled_at||item.created_at).toLocaleString("ru-RU")}</small></div><div className="broadcast-stats"><b>{(item.stats.scheduled||0)+(item.stats.processing||0)+(item.stats.retry||0)}</b><span>ожидает</span><b>{item.stats.sent||0}</b><span>отправлено</span><b>{item.stats.failed||0}</b><span>ошибка</span><b>{item.stats.blocked||0}</b><span>заблокировал</span><b>{item.stats.unsubscribed||0}</b><span>отписался</span></div>{["scheduled","running"].includes(item.status)&&<button className="button ghost" onClick={()=>api(`/broadcasts/${item.id}/cancel`,{method:"POST"}).then(load).catch(e=>setError(e.message))}>Отменить</button>}</article>)}</div>}</section>;
}

export function LearningPage() {
  const [data,setData]=useState<any>(null);
  const [error,setError]=useState("");
  useEffect(()=>{api("/learning").then(setData).catch(e=>setError((e as Error).message))},[]);
  return <section><div className="section-head"><div><span className="eyebrow">БЫСТРЫЙ СТАРТ</span><h2>Обучение</h2></div></div><p className="section-lead">Пять коротких уроков с реальными сценариями Gramly. Видео открываются прямо внутри Telegram.</p>{error&&<p className="composer-error">{error}</p>}<div className="learning-list">{data?.videos?.length?data.videos.map((video:any)=><article className="wet-panel" key={video.id}><video controls playsInline preload="metadata" poster={video.poster_url||undefined}><source src={video.video_url} type="video/mp4"/>Ваш Telegram не поддерживает встроенное видео. <a href={video.video_url} target="_blank" rel="noreferrer">Открыть урок</a>.</video><span><b>{video.title}</b><small>{video.description}</small><em>{video.duration_seconds} сек.</em></span></article>):<div className="empty-state wet-panel"><div><h3>Видео временно недоступны</h3><p>Повторите открытие экрана позже или используйте подсказки owner-бота.</p></div></div>}</div></section>;
}
