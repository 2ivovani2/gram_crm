import React, { useEffect, useRef, useState } from "react";
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
  useEffect(()=>{void load();}, []);
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
  type LearningVideo = {id:number;slug:string;title:string;description:string;video_url:string;poster_url:string;duration_seconds:number;chapter_key:string;position_seconds:number;completed_at:string|null};
  type LearningData = {chapters:Array<{key:string;title:string}>;videos:LearningVideo[];progress:{completed:number;total:number;next_video_id:number|null}};
  const [data,setData]=useState<LearningData|null>(null);
  const [activeId,setActiveId]=useState<number|null>(null);
  const [error,setError]=useState("");
  const lastSent=useRef<Record<number,number>>({});
  useEffect(()=>{
    api<LearningData>("/learning").then(value=>{
      setData(value);
      const requested=new URLSearchParams(window.location.search).get("lesson");
      const selected=value.videos.find(video=>video.slug===requested)?.id||value.progress.next_video_id||value.videos[0]?.id||null;
      setActiveId(selected);
    }).catch(e=>setError((e as Error).message));
  },[]);
  const saveProgress=async(video:LearningVideo,position:number,event:"progress"|"ended"="progress",force=false)=>{
    const seconds=Math.max(0,Math.floor(position));
    if(!force&&event==="progress"&&lastSent.current[video.id]!==undefined&&Math.abs(seconds-lastSent.current[video.id])<10)return;
    lastSent.current[video.id]=seconds;
    try{
      const saved=await api<{position_seconds:number;completed_at:string|null}>(`/learning/${video.id}/progress`,{method:"PATCH",body:JSON.stringify({position_seconds:seconds,event})});
      setData(current=>{
        if(!current)return current;
        const videos=current.videos.map(item=>item.id===video.id?{...item,...saved}:item);
        const completed=videos.filter(item=>item.completed_at).length;
        const next=videos.find(item=>!item.completed_at)?.id||videos[0]?.id||null;
        return {...current,videos,progress:{...current.progress,completed,next_video_id:next}};
      });
    }catch(e){setError((e as Error).message)}
  };
  const active=data?.videos.find(video=>video.id===activeId)||data?.videos[0];
  const activeIndex=active&&data?data.videos.findIndex(video=>video.id===active.id):-1;
  const nextVideo=data&&activeIndex>=0?data.videos[activeIndex+1]:undefined;
  const percent=data?.progress.total?Math.round(data.progress.completed/data.progress.total*100):0;
  if(!data&&!error)return <section><div className="learning-skeleton wet-panel">Готовим маршрут обучения…</div></section>;
  return <section className="learning-page">
    <div className="section-head"><div><span className="eyebrow">БЫСТРЫЙ СТАРТ</span><h2>Видеообучение</h2></div><b>{data?.progress.completed||0} из {data?.progress.total||0}</b></div>
    <p className="section-lead">Идите по маршруту или откройте нужный урок. Gramly запомнит прогресс на всех устройствах.</p>
    {error&&<p className="composer-error" role="alert">{error}</p>}
    {active&&data?<div className="learning-workspace">
      <article className="learning-player wet-panel">
        <div className="learning-player-copy"><span>УРОК {activeIndex+1} ИЗ {data.videos.length}</span><h3>{active.title}</h3><p>{active.description}</p><em>{active.duration_seconds} сек.</em></div>
        <video key={active.id} controls playsInline preload="metadata" poster={active.poster_url||undefined} aria-label={active.title}
          onLoadedMetadata={event=>{const video=event.currentTarget;if(active.position_seconds>0&&active.position_seconds<video.duration-2)video.currentTime=active.position_seconds}}
          onTimeUpdate={event=>void saveProgress(active,event.currentTarget.currentTime)}
          onPause={event=>void saveProgress(active,event.currentTarget.currentTime,"progress",true)}
          onEnded={event=>void saveProgress(active,event.currentTarget.duration,"ended",true)}>
          <source src={active.video_url} type="video/mp4"/>Ваш Telegram не поддерживает встроенное видео. <a href={active.video_url} target="_blank" rel="noreferrer">Открыть урок</a>.
        </video>
        <footer><span>{active.completed_at?"✓ Урок просмотрен":active.position_seconds?`Продолжить с ${active.position_seconds} сек.`:"Можно начинать"}</span>{nextVideo&&<button className="button acid" onClick={()=>setActiveId(nextVideo.id)}>Следующий урок →</button>}</footer>
      </article>
      <aside className="learning-route wet-panel">
        <header><div><span>ВАШ ПРОГРЕСС</span><b>{percent}%</b></div><progress max="100" value={percent}>{percent}%</progress></header>
        {data.chapters.map(chapter=><section key={chapter.key}><h3>{chapter.title}</h3>{data.videos.filter(video=>video.chapter_key===chapter.key).map(video=><button className={video.id===active.id?"active":""} key={video.id} onClick={()=>setActiveId(video.id)}><i>{video.completed_at?"✓":String(data.videos.indexOf(video)+1).padStart(2,"0")}</i><span><b>{video.title}</b><small>{video.duration_seconds} сек.</small></span></button>)}</section>)}
      </aside>
    </div>:<div className="empty-state wet-panel"><div><h3>Видео временно недоступны</h3><p>Повторите открытие экрана позже.</p></div></div>}
  </section>;
}
