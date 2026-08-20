import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { api, setCsrfToken } from "./api";
import { AdminApp } from "./admin";
import "./styles.css";

declare global {
  interface Window {
    Telegram?: {
      WebApp?: {
        initData: string;
        colorScheme?: "light" | "dark";
        ready(): void;
        expand(): void;
        close(): void;
        setHeaderColor?(color: string): void;
        setBackgroundColor?(color: string): void;
        setBottomBarColor?(color: string): void;
        openTelegramLink?(url: string): void;
        openInvoice?(url: string, callback?: (status: "paid" | "cancelled" | "failed" | "pending") => void): void;
        onEvent?(event: string, callback: () => void): void;
        offEvent?(event: string, callback: () => void): void;
        HapticFeedback?: { impactOccurred(style: "light" | "medium" | "heavy"): void };
      };
    };
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
type Plan = { slug: string; name: string; prices: { rub: string | null; xtr: number | null } };
type Checkout = { payment_id: number; invoice_url: string; status: string };

const tabs = ["Обзор", "Боты", "Цепочки", "Аналитика", "Подписка", "Партнёры", "Ещё"] as const;
type Tab = typeof tabs[number];
type IconName = "home" | "bot" | "flow" | "chart" | "more" | "arrow" | "empty";

function Icon({ name }: { name: IconName }) {
  const paths: Record<IconName, React.ReactNode> = {
    home: <><path d="M3.5 10.5 12 3l8.5 7.5"/><path d="M5.5 9v11h13V9M9.5 20v-6h5v6"/></>,
    bot: <><rect x="4" y="7" width="16" height="13" rx="4"/><path d="M12 3v4M8 13h.01M16 13h.01M8.5 17h7"/></>,
    flow: <><circle cx="6" cy="6" r="2.5"/><circle cx="18" cy="18" r="2.5"/><path d="M8.5 6h4a4 4 0 0 1 4 4v5.5M13 13l3.5 3.5L20 13"/></>,
    chart: <><path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/></>,
    more: <><circle cx="5" cy="12" r="1.5"/><circle cx="12" cy="12" r="1.5"/><circle cx="19" cy="12" r="1.5"/></>,
    arrow: <><path d="M5 12h14M14 7l5 5-5 5"/></>,
    empty: <><path d="M4 7.5h16v11H4z"/><path d="M8 7.5V5h8v2.5M8 13h8"/></>,
  };
  return <svg className="ui-icon" viewBox="0 0 24 24" aria-hidden="true">{paths[name]}</svg>;
}

const navigation: Array<{ tab: Tab; label: string; icon: IconName }> = [
  { tab: "Обзор", label: "Главная", icon: "home" },
  { tab: "Боты", label: "Боты", icon: "bot" },
  { tab: "Цепочки", label: "Цепочки", icon: "flow" },
  { tab: "Аналитика", label: "Данные", icon: "chart" },
  { tab: "Ещё", label: "Ещё", icon: "more" },
];

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

function EmptyState({ title, text, action, onAction }: { title: string; text: string; action?: string; onAction?: () => void }) {
  return <div className="empty-state wet-panel"><span className="empty-icon"><Icon name="empty"/></span><div><h3>{title}</h3><p>{text}</p></div>{action&&onAction&&<button className="button acid" onClick={onAction}>{action}<Icon name="arrow"/></button>}</div>;
}

function FlowEditor({ flow, onClose }: { flow: Flow; onClose(): void }) {
  const [data, setData] = useState<{version:{id:number;first_delay_seconds:number;timeline_seconds:number;join_request_compatible:boolean};steps:Step[]} | null>(null);
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
      <div className="editor-meta"><span>Версия {data.version.id}</span><span>Старт через {data.version.first_delay_seconds} сек.</span><span>Заявка: {data.version.timeline_seconds} / 240 сек.</span><button className="button acid" onClick={publish}>Опубликовать</button></div>
      {!data.version.join_request_compatible&&<div className="error" role="alert"><strong>Цепочка длиннее четырёх минут</strong><span>Сократите задержку до первого сообщения или паузы между шагами, чтобы Telegram не закрыл временный чат заявки.</span></div>}
      {!data.steps.length&&<EmptyState title="В цепочке пока нет шагов" text="Добавьте первое сообщение ниже — оно станет началом приветственного сценария."/>}<div className="steps">{data.steps.map((step,index)=><article key={step.id} draggable={editing!==step.id} onDragStart={()=>setDragged(step.id)} onDragOver={(event)=>event.preventDefault()} onDrop={()=>{if(dragged && dragged!==step.id){const ids=data.steps.map(i=>i.id);const from=ids.indexOf(dragged);ids.splice(from,1);ids.splice(index,0,dragged);void reorder(ids)}}}>
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
  const [plans,setPlans]=useState<Plan[]>([]); const [tab,setTab]=useState<Tab>("Обзор");
  const [activeBot,setActiveBot]=useState<number|null>(null); const [editor,setEditor]=useState<Flow|null>(null);
  const [botUsername,setBotUsername]=useState("GramlyHelloBot");
  const [paymentBusy,setPaymentBusy]=useState<"crypto"|"stars"|null>(null);
  const [paymentMessage,setPaymentMessage]=useState("");
  useEffect(()=>{
    api<{interface_bot_username:string}>("/public-config").then(value=>{if(value.interface_bot_username)setBotUsername(value.interface_bot_username)}).catch(()=>undefined);
    if(!tg?.initData){setReady(true);return;}
    const syncTelegramChrome=()=>{
      const dark=tg.colorScheme!=="light";
      document.documentElement.dataset.telegramTheme=dark?"dark":"light";
      try { tg.setHeaderColor?.(dark?"#111512":"#eef1e8"); } catch { /* Legacy Telegram client. */ }
      try { tg.setBackgroundColor?.(dark?"#090c0a":"#e8ece4"); } catch { /* Legacy Telegram client. */ }
      try { tg.setBottomBarColor?.(dark?"#111512":"#f2f4ef"); } catch { /* Legacy Telegram client. */ }
    };
    syncTelegramChrome();
    tg.onEvent?.("themeChanged",syncTelegramChrome);
    tg.ready();
    tg.expand();
    api<{csrf_token:string}>("/session/telegram",{method:"POST",body:JSON.stringify({init_data:tg.initData})}).then((session)=>{setCsrfToken(session.csrf_token);return Promise.all([api<any>("/me"),api<any>("/dashboard"),api<{bots:Bot[]}>("/bots"),api<{plans:Plan[]}>("/plans"),api<any>("/analytics"),api<any>("/referrals")]);}).then(([identity,summary,botList,planList,analyticsData,referralData])=>{setMe(identity);setDashboard(summary);setBots(botList.bots);setPlans(planList.plans);setAnalytics(analyticsData);setPartners(referralData);setActiveBot(botList.bots[0]?.id||null);setReady(true)}).catch((e)=>{setError(e.message);setReady(true)});
    return()=>tg.offEvent?.("themeChanged",syncTelegramChrome);
  },[]);
  useEffect(()=>{if(activeBot) api<{flows:Flow[]}>(`/bots/${activeBot}/flows`).then(r=>setFlows(r.flows)).catch(e=>setError(e.message));},[activeBot]);
  const openTab=(next:Tab)=>{tg?.HapticFeedback?.impactOccurred("light");setTab(next)};
  const openOwnerBot=()=>{const url=`https://t.me/${botUsername}`;if(tg?.openTelegramLink)tg.openTelegramLink(url);else location.href=url};
  const refreshAccess=async()=>{for(let attempt=0;attempt<6;attempt+=1){const identity=await api<any>("/me");setMe(identity);if(identity?.access?.plan==="business")return;await new Promise(resolve=>setTimeout(resolve,1500));}};
  const payCrypto=async()=>{setError("");setPaymentMessage("");setPaymentBusy("crypto");try{const checkout=await api<Checkout>("/payments/crypto",{method:"POST"});setPaymentMessage("Счёт создан. После оплаты Business включится автоматически.");if(tg?.openTelegramLink)tg.openTelegramLink(checkout.invoice_url);else location.href=checkout.invoice_url;}catch(e){setError((e as Error).message);}finally{setPaymentBusy(null)}};
  const payStars=async()=>{setError("");setPaymentMessage("");setPaymentBusy("stars");try{const checkout=await api<Checkout>("/payments/stars",{method:"POST"});if(tg?.openInvoice){tg.openInvoice(checkout.invoice_url,status=>{if(status==="paid"){setPaymentMessage("Оплата получена. Активируем Business…");void refreshAccess().then(()=>setPaymentMessage("Business активирован."));}else if(status==="pending")setPaymentMessage("Платёж обрабатывается Telegram.");else if(status==="failed")setError("Telegram не смог провести платёж. Попробуйте ещё раз.");});}else location.href=checkout.invoice_url;}catch(e){setError((e as Error).message);}finally{setPaymentBusy(null)}};
  if(!ready) return <div className="loading"><Logo/><div className="loader"/><span>Синхронизируем кабинет…</span></div>;
  if(!tg?.initData) return <OutsideTelegram username={botUsername}/>;
  if(error && !me) return <main className="outside"><Logo/><ErrorBox message={error}/><button className="button" onClick={()=>location.reload()}>Повторить</button></main>;
  const firstName=me?.owner?.first_name||"друг";
  return <div className="app-shell mini-app">
    <header className="topbar"><div><span className="topbar-kicker">GRAMLY HELLO</span><strong>Привет, {firstName}</strong></div><div className="account"><span className="account-avatar">{firstName.slice(0,1).toUpperCase()}</span><Status ok={me?.access?.plan === "business"}>{me?.access?.plan_name || "Free"}</Status></div></header>
    <main className="content">{error&&<ErrorBox message={error}/>}
      {tab==="Обзор"&&<><section className="hero-panel wet-panel"><div><span className="eyebrow">СЕЙЧАС В GRAMLY</span><h1>{bots.length?"Ваши приветствия работают.":"Начнём с первого бота."}</h1><p>{bots.length?"Мы принимаем события Telegram, выбираем сценарий и доставляем сообщение каждому новому участнику.":"Подключите Telegram-бота, добавьте его в канал и соберите первую цепочку приветствия."}</p><button className="button acid quick-action" onClick={bots.length?()=>openTab("Цепочки"):openOwnerBot}>{bots.length?"Открыть цепочки":"Подключить бота"} <Icon name="arrow"/></button></div><div className="route"><span>СОБЫТИЕ</span><i/><span>GRAMLY</span><i/><span>ДОСТАВЛЕНО</span></div></section><section className="metrics wet-panel"><Metric label="Боты" value={dashboard?.bots??0}/><Metric label="Каналы" value={dashboard?.channels??0}/><Metric label="Контакты" value={dashboard?.contacts??0}/><Metric label="Доставлено" value={dashboard?.delivered??0}/></section></>}
      {tab==="Боты"&&<section><div className="section-head"><div><span className="eyebrow">ПОДКЛЮЧЕНИЯ</span><h2>Мои боты</h2></div></div>{!bots.length?<EmptyState title="Здесь появятся ваши боты" text="Откройте GramlyHello, отправьте токен от BotFather и завершите безопасное подключение по подсказкам." action="Подключить первого бота" onAction={openOwnerBot}/>:<div className="bot-list">{bots.map(bot=><article className="wet-panel" key={bot.id}><div className="bot-id"><span className="avatar">{bot.display_name.slice(0,1)}</span><div><strong>@{bot.username||bot.display_name}</strong><small>{bot.channels} каналов · {bot.contacts} контактов</small></div></div><Status ok={bot.webhook_configured}>{bot.webhook_configured?"Webhook online":"Нужно подключить"}</Status><button className="button ghost" onClick={()=>{setActiveBot(bot.id);openTab("Цепочки")}}>Цепочки <Icon name="arrow"/></button></article>)}</div>}</section>}
      {tab==="Цепочки"&&<section><div className="section-head"><div><span className="eyebrow">КОНТЕНТ</span><h2>Цепочки</h2></div>{bots.length>0&&<select value={activeBot||""} onChange={e=>setActiveBot(Number(e.target.value))}>{bots.map(bot=><option key={bot.id} value={bot.id}>@{bot.username}</option>)}</select>}</div>{!bots.length?<EmptyState title="Сначала подключите бота" text="Цепочка всегда принадлежит конкретному Telegram-боту. Подключение займёт пару минут." action="Перейти к подключению" onAction={openOwnerBot}/>:!flows.length?<EmptyState title="У этого бота ещё нет цепочек" text="Создайте приветствие или прощание в GramlyHello — после сохранения оно сразу появится здесь." action="Создать цепочку" onAction={openOwnerBot}/>:<div className="flow-list">{flows.map(flow=><button className="wet-panel" key={flow.id} onClick={()=>setEditor(flow)}><div><span>{flow.kind==="farewell"?"ПРОЩАНИЕ":"ПРИВЕТСТВИЕ"}</span><strong>{flow.name}</strong></div><div><small>{flow.versions.find(v=>v.status==="published")?"Опубликовано":"Черновик"}</small><Icon name="arrow"/></div></button>)}</div>}</section>}
      {tab==="Аналитика"&&<section><div className="section-head"><div><span className="eyebrow">ТОЛЬКО РЕАЛЬНЫЕ ДАННЫЕ</span><h2>Аналитика</h2></div></div>{!((analytics?.deliveries?.completed||0)+(analytics?.deliveries?.failed||0)+(analytics?.deliveries?.partial||0)+(analytics?.rotation?.impressions||0)+(analytics?.rotation?.conversions||0))&&<EmptyState title="Данных пока нет" text="Статистика появится после первого события Telegram и попытки доставки цепочки."/>}<div className="metrics wet-panel"><Metric label="Успешные цепочки" value={analytics?.deliveries?.completed||0}/><Metric label="Ошибки" value={(analytics?.deliveries?.failed||0)+(analytics?.deliveries?.partial||0)}/><Metric label="Показы ротации" value={analytics?.rotation?.impressions||0}/><Metric label="Подписки" value={analytics?.rotation?.conversions||0}/></div><div className="note wet-panel">Telegram не передаёт пол и страну пользователя — поэтому мы не показываем выдуманные данные.</div></section>}
      {tab==="Ещё"&&<section><div className="section-head"><div><span className="eyebrow">ВАШ КАБИНЕТ</span><h2>Ещё</h2></div></div><div className="more-list"><button className="wet-panel" onClick={()=>openTab("Подписка")}><span><b>Тариф</b><small>{me?.access?.plan_name||"Free"}</small></span><Icon name="arrow"/></button><button className="wet-panel" onClick={()=>openTab("Партнёры")}><span><b>Партнёрская программа</b><small>{partners?.active_referrals||0} активных клиентов</small></span><Icon name="arrow"/></button></div></section>}
      {tab==="Подписка"&&<section><button className="back-link" onClick={()=>openTab("Ещё")}>← Назад</button><div className="section-head"><div><span className="eyebrow">FREE / BUSINESS</span><h2>Тариф</h2></div></div>{plans.filter(p=>p.slug==="business").map(plan=><div className="plan wet-panel" key={plan.slug}><div><Status ok={me?.access?.plan==="business"}>{me?.access?.plan_name}</Status><h3>{me?.access?.plan==="business"?"Без рекламы. Ротация включена.":"Уберите рекламу и включите ротацию каналов."}</h3><p>Business действует 30 дней. Выберите удобный способ оплаты.</p></div><div className="payment-options" aria-label="Способы оплаты">{plan.prices.xtr&&<button className="payment-option stars" disabled={paymentBusy!==null} onClick={()=>void payStars()}><span className="payment-symbol">⭐</span><span><b>{paymentBusy==="stars"?"Открываем счёт…":"Telegram Stars"}</b><small>{plan.prices.xtr} Stars · автопродление</small></span><Icon name="arrow"/></button>}{plan.prices.rub&&<button className="payment-option crypto" disabled={paymentBusy!==null} onClick={()=>void payCrypto()}><span className="payment-symbol">💎</span><span><b>{paymentBusy==="crypto"?"Создаём счёт…":"Crypto Pay"}</b><small>{plan.prices.rub} ₽ · USDT или TON</small></span><Icon name="arrow"/></button>}</div>{paymentMessage&&<div className="payment-message" role="status">{paymentMessage}</div>}</div>)}</section>}
      {tab==="Партнёры"&&<section><button className="back-link" onClick={()=>openTab("Ещё")}>← Назад</button><div className="section-head"><div><span className="eyebrow">ПАРТНЁРСКИЙ СЧЁТ</span><h2>Партнёрская программа</h2></div></div>{!(partners?.active_referrals||0)&&<EmptyState title="Активных рефералов пока нет" text="Поделитесь персональной ссылкой. Начисление появится после первой подтверждённой оплаты приглашённого клиента."/>}<div className="metrics wet-panel"><Metric label="Активные клиенты" value={partners?.active_referrals||0}/><Metric label="Доступно" value={`${partners?.balance_rub||0} ₽`}/></div><label className="copy-field wet-panel"><span>Ваша ссылка</span><input readOnly value={partners?.url||""}/><button onClick={()=>navigator.clipboard.writeText(partners?.url||"")}>Копировать</button></label></section>}
    </main>
    <nav className="bottom-nav" aria-label="Основные разделы">{navigation.map(item=>{const active=tab===item.tab||(item.tab==="Ещё"&&(tab==="Подписка"||tab==="Партнёры"));return <button className={active?"active":""} aria-current={active?"page":undefined} onClick={()=>openTab(item.tab)} key={item.tab}><Icon name={item.icon}/><span>{item.label}</span></button>})}</nav>
    {editor&&<div className="modal"><FlowEditor flow={editor} onClose={()=>{setEditor(null);if(activeBot)api<{flows:Flow[]}>(`/bots/${activeBot}/flows`).then(r=>setFlows(r.flows))}}/></div>}
  </div>;
}

const isAdmin = document.title.includes("Control");
createRoot(document.getElementById("root")!).render(<React.StrictMode>{isAdmin?<AdminApp/>:<MiniApp/>}</React.StrictMode>);
