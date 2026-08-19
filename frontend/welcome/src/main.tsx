import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { adminApi, api, setCsrfToken } from "./api";
import "./styles.css";

declare global {
  interface Window {
    Telegram?: { WebApp?: { initData: string; ready(): void; expand(): void; close(): void } };
  }
}

type Bot = {
  id: number; username: string; display_name: string; webhook_configured: boolean;
  auto_approve: boolean; channels: number; contacts: number;
};
type Flow = {
  id: number; name: string; kind: string; assignment_mode: string;
  versions: Array<{ id: number; number: number; status: string; first_delay_seconds: number }>;
};
type Step = { id: number; position: number; payload: Record<string, unknown>; delay_after_seconds: number; attachments: Array<{id:number;type:string;name:string}> };

const tabs = ["Обзор", "Боты", "Цепочки", "Аналитика", "Подписка", "Партнёры"] as const;
type Tab = typeof tabs[number];

function Logo({ control = false }: { control?: boolean }) {
  return <div className="logo"><span className="logo-mark">G</span><span>GramlyHello{control && <small> CONTROL</small>}</span></div>;
}

function Status({ ok, children }: { ok: boolean; children: React.ReactNode }) {
  return <span className={`status ${ok ? "ok" : "warn"}`}><i />{children}</span>;
}

function ErrorBox({ message }: { message: string }) {
  return <div className="error" role="alert"><strong>Что-то пошло не так</strong><span>{message}</span></div>;
}

function OutsideTelegram({ username }: { username: string }) {
  return <main className="outside"><Logo /><div className="outside-copy"><span className="eyebrow">TELEGRAM MINI APP</span><h1>Управление приветствиями живёт внутри Telegram.</h1><p>Откройте GramlyHello через кнопку Mini App — там безопасно подтвердится ваш аккаунт и появятся только ваши боты.</p><a className="button acid" href={`https://t.me/${username}?startapp=dashboard`}>Открыть GramlyHello</a></div><div className="signal-map"><b>Telegram event</b><span>→</span><b>Gramly</b><span>→</span><b>Delivered</b></div></main>;
}

function Metric({ label, value }: { label: string; value: React.ReactNode }) {
  return <div className="metric"><span>{label}</span><strong>{value}</strong></div>;
}

function FlowEditor({ flow, onClose }: { flow: Flow; onClose(): void }) {
  const [data, setData] = useState<{version:{id:number;first_delay_seconds:number};steps:Step[]} | null>(null);
  const [error, setError] = useState("");
  const [dragged, setDragged] = useState<number | null>(null);
  const [editing, setEditing] = useState<number | null>(null);
  const load = () => api<NonNullable<typeof data>>(`/flows/${flow.id}/draft`).then(setData).catch((e) => setError(e.message));
  useEffect(() => { void load(); }, [flow.id]);

  const reorder = async (ids: number[]) => {
    if (!data) return;
    const optimistic = ids.map((id, position) => ({...data.steps.find((item) => item.id === id)!, position}));
    setData({...data, steps: optimistic});
    try { await api(`/drafts/${data.version.id}/reorder`, {method:"POST", body:JSON.stringify({step_ids:ids})}); }
    catch (e) { setError((e as Error).message); void load(); }
  };
  const move = (index: number, delta: number) => {
    if (!data) return; const next = [...data.steps]; const target = index + delta;
    if (target < 0 || target >= next.length) return;
    [next[index], next[target]] = [next[target], next[index]]; void reorder(next.map((item) => item.id));
  };
  const add = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault(); if (!data) return;
    const form = event.currentTarget; const values = new FormData(form);
    try {
      await api(`/drafts/${data.version.id}/steps`, {method:"POST", body:JSON.stringify({text:values.get("text"),delay_after_seconds:Number(values.get("delay"))})});
      form.reset(); await load();
    } catch (e) { setError((e as Error).message); }
  };
  const save = async (event: React.FormEvent<HTMLFormElement>, step: Step) => {
    event.preventDefault(); const values = new FormData(event.currentTarget);
    try {
      await Promise.all([
        api(`/steps/${step.id}/content`, {method:"POST", body:JSON.stringify({text:values.get("text")})}),
        api(`/steps/${step.id}/delay`, {method:"POST", body:JSON.stringify({delay_seconds:Number(values.get("delay"))})}),
      ]);
      setEditing(null); await load();
    } catch (e) { setError((e as Error).message); }
  };
  const copy = async (stepId: number) => { try { await api(`/steps/${stepId}/copy`, {method:"POST"}); await load(); } catch(e) { setError((e as Error).message); } };
  const remove = async (stepId: number) => { if (!confirm("Удалить шаг из черновика?")) return; try { await api(`/steps/${stepId}`, {method:"DELETE"}); await load(); } catch(e) { setError((e as Error).message); } };
  const publish = async () => {
    if (!data || !confirm("Опубликовать эту версию цепочки?")) return;
    try { await api(`/drafts/${data.version.id}/publish`, {method:"POST"}); onClose(); }
    catch (e) { setError((e as Error).message); }
  };
  return <section className="editor">
    <header><div><span className="eyebrow">{flow.kind === "farewell" ? "FAREWELL" : "WELCOME"}</span><h2>{flow.name}</h2></div><button className="icon-button" onClick={onClose} aria-label="Закрыть">×</button></header>
    {error && <ErrorBox message={error}/>} {!data ? <div className="skeleton">Загружаем черновик…</div> : <>
      <div className="editor-meta"><span>Версия {data.version.id}</span><span>Старт через {data.version.first_delay_seconds} сек.</span><button className="button acid" onClick={publish}>Опубликовать</button></div>
      <div className="steps">{data.steps.map((step,index)=><article key={step.id} draggable={editing!==step.id} onDragStart={()=>setDragged(step.id)} onDragOver={(event)=>event.preventDefault()} onDrop={()=>{if(dragged && dragged!==step.id){const ids=data.steps.map(i=>i.id);const from=ids.indexOf(dragged);ids.splice(from,1);ids.splice(index,0,dragged);void reorder(ids)}}}>
        <div className="step-index">{String(index+1).padStart(2,"0")}</div>
        {editing===step.id ? <form className="step-edit" onSubmit={event=>save(event,step)}><textarea name="text" defaultValue={String(step.payload.text||step.payload.caption||"")} required/><label>Пауза, сек.<input name="delay" type="number" min="0" max="86400" defaultValue={step.delay_after_seconds}/></label><div><button className="button acid">Сохранить</button><button type="button" className="button ghost" onClick={()=>setEditing(null)}>Отмена</button></div></form> : <div className="step-body"><strong>{String(step.payload.text || step.payload.caption || "Медиа-сообщение")}</strong><span>{step.attachments.length ? `${step.attachments.length} вложений` : "Без вложений"} · задержка {step.delay_after_seconds} сек.</span></div>}
        <div className="step-actions"><button onClick={()=>move(index,-1)} aria-label="Выше">↑</button><button onClick={()=>move(index,1)} aria-label="Ниже">↓</button><button onClick={()=>setEditing(step.id)} aria-label="Изменить">✎</button><button onClick={()=>void copy(step.id)} aria-label="Копировать">⧉</button><button onClick={()=>void remove(step.id)} aria-label="Удалить">×</button><span className="drag">⋮⋮</span></div>
      </article>)}</div>
      <form className="step-form" onSubmit={add}><span className="eyebrow">НОВЫЙ ШАГ</span><textarea name="text" placeholder="Текст сообщения. Переменные: {first_name}, {username}" required/><label>Пауза после шага, сек.<input name="delay" type="number" min="0" max="86400" defaultValue="1"/></label><button className="button acid">Добавить в цепочку</button></form>
    </>}
  </section>;
}

function MiniApp() {
  const tg = window.Telegram?.WebApp;
  const [ready, setReady] = useState(false); const [error,setError]=useState("");
  const [me,setMe]=useState<any>(null); const [dashboard,setDashboard]=useState<any>(null);
  const [bots,setBots]=useState<Bot[]>([]); const [flows,setFlows]=useState<Flow[]>([]);
  const [analytics,setAnalytics]=useState<any>(null); const [partners,setPartners]=useState<any>(null);
  const [plans,setPlans]=useState<any[]>([]); const [tab,setTab]=useState<Tab>("Обзор");
  const [activeBot,setActiveBot]=useState<number|null>(null); const [editor,setEditor]=useState<Flow|null>(null);
  const [botUsername,setBotUsername]=useState("GramlyHelloBot");
  useEffect(()=>{ api<{interface_bot_username:string}>("/public-config").then(value=>{if(value.interface_bot_username)setBotUsername(value.interface_bot_username)}).catch(()=>undefined); if(!tg?.initData){setReady(true);return;} tg.ready();tg.expand(); api<{csrf_token:string}>("/session/telegram",{method:"POST",body:JSON.stringify({init_data:tg.initData})}).then((session)=>{setCsrfToken(session.csrf_token);return Promise.all([api<any>("/me"),api<any>("/dashboard"),api<{bots:Bot[]}>("/bots"),api<{plans:any[]}>("/plans"),api<any>("/analytics"),api<any>("/referrals")]);}).then(([identity,summary,botList,planList,analyticsData,referralData])=>{setMe(identity);setDashboard(summary);setBots(botList.bots);setPlans(planList.plans);setAnalytics(analyticsData);setPartners(referralData);setActiveBot(botList.bots[0]?.id||null);setReady(true)}).catch((e)=>{setError(e.message);setReady(true)});},[]);
  useEffect(()=>{if(activeBot) api<{flows:Flow[]}>(`/bots/${activeBot}/flows`).then(r=>setFlows(r.flows)).catch(e=>setError(e.message));},[activeBot]);
  if(!ready) return <div className="loading"><Logo/><div className="loader"/><span>Синхронизируем кабинет…</span></div>;
  if(!tg?.initData) return <OutsideTelegram username={botUsername}/>;
  if(error && !me) return <main className="outside"><Logo/><ErrorBox message={error}/><button className="button" onClick={()=>location.reload()}>Повторить</button></main>;
  return <div className="app-shell"><header className="topbar"><Logo/><div className="account"><span>@{me?.owner?.username || "telegram"}</span><Status ok={me?.access?.plan === "business"}>{me?.access?.plan_name || "Free"}</Status></div></header><nav className="tabs" aria-label="Разделы">{tabs.map(item=><button className={tab===item?"active":""} onClick={()=>setTab(item)} key={item}>{item}</button>)}</nav><main className="content">{error&&<ErrorBox message={error}/>} {tab==="Обзор"&&<><section className="hero-panel"><div><span className="eyebrow">LIVE OPERATIONS</span><h1>Приветствия под контролем.</h1><p>Событие Telegram проходит через надёжную очередь, персонализируется и доставляется без участия CRM.</p></div><div className="route"><span>EVENT</span><i/><span>GRAMLY</span><i/><span>DELIVERED</span></div></section><section className="metrics"><Metric label="Боты" value={dashboard?.bots??0}/><Metric label="Каналы" value={dashboard?.channels??0}/><Metric label="Контакты" value={dashboard?.contacts??0}/><Metric label="Доставлено" value={dashboard?.delivered??0}/></section></>}{tab==="Боты"&&<section><div className="section-head"><div><span className="eyebrow">CONNECTED SURFACES</span><h2>Мои боты</h2></div></div><div className="bot-list">{bots.map(bot=><article key={bot.id}><div className="bot-id"><span className="avatar">{bot.display_name.slice(0,1)}</span><div><strong>@{bot.username||bot.display_name}</strong><small>{bot.channels} каналов · {bot.contacts} контактов</small></div></div><Status ok={bot.webhook_configured}>{bot.webhook_configured?"Webhook online":"Нужно подключить"}</Status><button className="button ghost" onClick={()=>{setActiveBot(bot.id);setTab("Цепочки")}}>Цепочки</button></article>)}</div></section>}{tab==="Цепочки"&&<section><div className="section-head"><div><span className="eyebrow">CONTENT ENGINE</span><h2>Цепочки</h2></div><select value={activeBot||""} onChange={e=>setActiveBot(Number(e.target.value))}>{bots.map(bot=><option key={bot.id} value={bot.id}>@{bot.username}</option>)}</select></div><div className="flow-list">{flows.map(flow=><button key={flow.id} onClick={()=>setEditor(flow)}><div><span>{flow.kind.toUpperCase()}</span><strong>{flow.name}</strong></div><div><small>{flow.versions.find(v=>v.status==="published")?"Опубликовано":"Черновик"}</small><b>→</b></div></button>)}</div></section>}{tab==="Аналитика"&&<section><div className="section-head"><div><span className="eyebrow">REAL DATA ONLY</span><h2>Аналитика</h2></div></div><div className="metrics"><Metric label="Успешные цепочки" value={analytics?.deliveries?.completed||0}/><Metric label="Ошибки" value={(analytics?.deliveries?.failed||0)+(analytics?.deliveries?.partial||0)}/><Metric label="Показы ротации" value={analytics?.rotation?.impressions||0}/><Metric label="Подписки" value={analytics?.rotation?.conversions||0}/></div><div className="note">Telegram не передаёт пол и страну пользователя. Мы не дорисовываем недостоверные данные.</div></section>}{tab==="Подписка"&&<section><div className="section-head"><div><span className="eyebrow">FREE / BUSINESS</span><h2>Тариф</h2></div></div><div className="plan"><div><Status ok={me?.access?.plan==="business"}>{me?.access?.plan_name}</Status><h3>{me?.access?.plan==="business"?"Без рекламы. Ротация включена.":"Полный функционал с рекламой Gramly."}</h3></div>{plans.filter(p=>p.slug==="business").map(plan=><div className="price" key={plan.id}><strong>{plan.price_rub?`${plan.price_rub} ₽`:"Цена настраивается"}</strong><span>30 дней</span></div>)}</div></section>}{tab==="Партнёры"&&<section><div className="section-head"><div><span className="eyebrow">PARTNER LEDGER</span><h2>Партнёрская программа</h2></div></div><div className="metrics"><Metric label="Активные клиенты" value={partners?.active_referrals||0}/><Metric label="Доступно" value={`${partners?.balance_rub||0} ₽`}/></div><label className="copy-field"><span>Ваша ссылка</span><input readOnly value={partners?.url||""}/><button onClick={()=>navigator.clipboard.writeText(partners?.url||"")}>Копировать</button></label></section>}</main>{editor&&<div className="modal"><FlowEditor flow={editor} onClose={()=>{setEditor(null);if(activeBot)api<{flows:Flow[]}>(`/bots/${activeBot}/flows`).then(r=>setFlows(r.flows))}}/></div>}</div>;
}

function AdminApp() {
  const [overview,setOverview]=useState<any>(null); const [content,setContent]=useState<any>(null); const [ads,setAds]=useState<any>(null); const [plans,setPlans]=useState<any>(null); const [error,setError]=useState(""); const [mode,setMode]=useState("Контент");
  const reload=()=>Promise.all([adminApi<any>("/overview"),adminApi<any>("/content"),adminApi<any>("/advertising"),adminApi<any>("/plans")]).then(([o,c,a,p])=>{setOverview(o);setContent(c);setAds(a);setPlans(p)}).catch(e=>setError(e.message));
  useEffect(()=>{void reload()},[]);
  const submit=async(event:React.FormEvent<HTMLFormElement>,path:string)=>{event.preventDefault();const form=new FormData(event.currentTarget);const body:Record<string,unknown>=Object.fromEntries(form.entries());for(const [key,value] of Object.entries(body)){if(key.endsWith("_at")&&typeof value==="string"&&value)body[key]=new Date(value).toISOString();if(["sort_order","weight","price_xtr"].includes(key))body[key]=value===""?null:Number(value);if(["price_rub","referral_base_rub"].includes(key)&&value==="")body[key]=null;if(["is_active","is_onboarding","crypto_pay_enabled","stars_enabled"].includes(key))body[key]=value==="true";}try{await adminApi(path,{method:"POST",body:JSON.stringify(body)});event.currentTarget.reset();await reload()}catch(e){setError((e as Error).message)}};
  return <div className="admin-shell"><aside><Logo control/><nav>{["Контент","Объявления","Советы","Реклама","Тарифы"].map(item=><button className={mode===item?"active":""} onClick={()=>setMode(item)} key={item}>{item}</button>)}</nav><div className="secure"><i/>VPN + Authentik<br/><small>gramly-owners only</small></div></aside><main><header><div><span className="eyebrow">OWNER CONTROL</span><h1>{mode}</h1></div><Status ok>Защищено</Status></header>{error&&<ErrorBox message={error}/>}<div className="admin-metrics"><Metric label="Владельцы" value={overview?.owners||0}/><Metric label="Инструкции" value={overview?.manuals||0}/><Metric label="Объявления" value={overview?.announcements||0}/><Metric label="Ошибки доставки" value={overview?.notification_failures||0}/></div>{mode==="Контент"&&<div className="admin-grid"><form onSubmit={e=>submit(e,"/manuals")}><h2>Новая Telegraph-инструкция</h2><input name="slug" placeholder="welcome-basics" required/><input name="title" placeholder="Название" required/><input name="telegraph_url" type="url" placeholder="https://telegra.ph/..." required/><textarea name="description" placeholder="Короткое описание"/><label><input name="is_onboarding" type="checkbox" value="true"/> Показывать при первом входе</label><button className="button acid">Сохранить</button></form><div className="records">{content?.manuals?.map((item:any)=><article key={item.id}><strong>{item.title}</strong><span>{item.slug}</span><a href={item.telegraph_url}>Открыть</a></article>)}</div></div>}{mode==="Объявления"&&<div className="admin-grid"><form onSubmit={e=>submit(e,"/announcements")}><h2>Новое объявление</h2><input name="title" placeholder="Заголовок" required/><textarea name="body" placeholder="Сообщение" required/><select name="audience"><option value="all">Все</option><option value="free">Free</option><option value="business">Business</option></select><input name="starts_at" type="datetime-local" required/><input name="button_text" placeholder="Текст кнопки"/><input name="button_url" type="url" placeholder="https://..."/><button className="button acid">Запланировать</button></form><div className="records">{content?.announcements?.map((item:any)=><article key={item.id}><strong>{item.title}</strong><span>{item.audience} · {new Date(item.starts_at).toLocaleDateString()}</span><p>{item.body}</p></article>)}</div></div>}{mode==="Советы"&&<div className="admin-grid"><form onSubmit={e=>submit(e,"/tips")}><h2>Новый совет</h2><input name="feature_key" placeholder="flows" required/><textarea name="text" placeholder="Практический совет" required/><input name="sort_order" type="number" defaultValue="100"/><input name="is_active" type="hidden" value="true"/><button className="button acid">Добавить</button></form><div className="records">{content?.tips?.map((item:any)=><article key={item.id}><strong>{item.feature_key}</strong><span>#{item.sort_order}</span><p>{item.text}</p></article>)}</div></div>}{mode==="Реклама"&&<div className="admin-grid"><form onSubmit={e=>submit(e,"/advertising")}><h2>Новый креатив Free</h2><input name="name" placeholder="Название" required/><textarea name="text" placeholder="Рекламный текст" required/><input name="cta_text" placeholder="CTA"/><input name="cta_url" type="url" placeholder="https://..."/><input name="weight" type="number" defaultValue="1"/><input name="is_active" type="hidden" value="true"/><button className="button acid">Добавить</button></form><div className="records">{ads?.creatives?.map((item:any)=><article key={item.id}><strong>{item.name}</strong><span>{item.impressions} показов · {item.clicks} кликов</span><p>{item.text}</p></article>)}</div></div>}{mode==="Тарифы"&&<div className="records wide">{plans?.plans?.map((plan:any)=><article key={plan.id}><strong>{plan.display_name}</strong><span>{plan.slug}</span><p>{plan.price_rub?`${plan.price_rub} ₽`:"RUB checkout выключен"} · {plan.price_xtr?`${plan.price_xtr} XTR`:"Stars выключены"}</p></article>)}</div>}</main></div>;
}

const isAdmin = document.title.includes("Control");
createRoot(document.getElementById("root")!).render(<React.StrictMode>{isAdmin?<AdminApp/>:<MiniApp/>}</React.StrictMode>);
