import React, { useEffect, useState, useRef, lazy, Suspense } from "react";
import { createRoot } from "react-dom/client";
import { Alert, Badge, Button, Card, Empty, Spinner } from "@santi020k/lumen-react";
import "@santi020k/lumen-react/styles/critical.css";
import { ApiError, api, setCsrfToken } from "./api";
const AdminApp=lazy(()=>import("./admin").then(m=>({default:m.AdminApp})));
const FlowEditor=lazy(()=>import("./editor/flow-editor").then(m=>({default:m.FlowEditor})));
import { MessageCatalog } from "./catalog";
import { AudiencePage, BroadcastsPage, LearningPage } from "./audience";
import { loadTelegramSdk } from "./telegram-sdk";
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
        enableClosingConfirmation?(): void;
        disableClosingConfirmation?(): void;
        requestWriteAccess?(callback: (granted: boolean) => void): void;
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
type Plan = { slug: string; name: string; prices: { rub: string | null; xtr: number | null } };
type Checkout = { payment_id: number; invoice_url: string; status: string; expires_at: string };
type SubscriptionGate = { code: string; title: string; url: string; temporarily_unavailable?: boolean };
type AppTheme = "dark" | "light";

class AppErrorBoundary extends React.Component<
  { children: React.ReactNode },
  { failed: boolean }
> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch(error: Error) {
    console.error("GramlyHello render failed", error.name);
  }
  render() {
    if (!this.state.failed) return this.props.children;
    return <main className="outside"><Logo/><div className="outside-copy"><span className="eyebrow">ОШИБКА ИНТЕРФЕЙСА</span><h1>Кабинет не загрузился.</h1><p>Ваши настройки сохранены. Перезапустите экран — обычно этого достаточно.</p><Button className="button acid" onClick={()=>location.reload()}>Повторить</Button></div></main>;
  }
}

function formatCountdown(expiresAt: string, now: number): string {
  const seconds=Math.max(0,Math.ceil((new Date(expiresAt).getTime()-now)/1000));
  const minutes=Math.floor(seconds/60);const remainder=seconds%60;
  return seconds>0?`${String(minutes).padStart(2,"0")}:${String(remainder).padStart(2,"0")}`:"истёк";
}

const tabs = ["Главная", "Приветствие", "База", "Рассылки", "Шаблоны", "Боты", "Аналитика", "Обучение", "Подписка", "Партнёры", "Ещё"] as const;
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
  { tab: "Главная", label: "Главная", icon: "home" },
  { tab: "Приветствие", label: "Приветствие", icon: "flow" },
  { tab: "База", label: "База", icon: "bot" },
  { tab: "Рассылки", label: "Рассылки", icon: "chart" },
  { tab: "Ещё", label: "Ещё", icon: "more" },
];

function Logo({ control = false }: { control?: boolean }) {
  return <div className="logo"><span className="logo-mark">G</span><span>GramlyHello{control && <small> CONTROL</small>}</span></div>;
}

function Status({ ok, children }: { ok: boolean; children: React.ReactNode }) {
  return <Badge className={`status ${ok ? "ok" : "warn"}`} variant={ok?"success":"warning"}><i />{children}</Badge>;
}

function ErrorBox({ message }: { message: string }) {
  return <Alert className="error" role="alert" variant="destructive"><strong>Что-то пошло не так</strong><span>{message}</span></Alert>;
}

function OutsideTelegram({ username }: { username: string }) {
  return <main className="outside"><Logo /><div className="outside-copy"><span className="eyebrow">TELEGRAM MINI APP</span><h1>Управление приветствиями живёт внутри Telegram.</h1><p>Откройте GramlyHello через кнопку Mini App — там безопасно подтвердится ваш аккаунт и появятся только ваши боты.</p><a className="button acid" href={`https://t.me/${username}?startapp=dashboard`}>Открыть GramlyHello</a></div><div className="signal-map"><b>Telegram event</b><span>→</span><b>Gramly</b><span>→</span><b>Delivered</b></div></main>;
}

function Metric({ label, value }: { label: string; value: React.ReactNode }) {
  return <div className="metric"><span>{label}</span><strong>{value}</strong></div>;
}

function EmptyState({ title, text, action, onAction }: { title: string; text: string; action?: string; onAction?: () => void }) {
  return <Empty className="empty-state wet-panel"><span className="empty-icon"><Icon name="empty"/></span><div><h3>{title}</h3><p>{text}</p></div>{action&&onAction&&<Button className="button acid" onClick={onAction}>{action}<Icon name="arrow"/></Button>}</Empty>;
}


function MiniApp() {
  const tg = window.Telegram?.WebApp;
  const [theme,setTheme]=useState<AppTheme>(()=>document.documentElement.dataset.gramlyTheme==="light"?"light":"dark");
  const [ready, setReady] = useState(false); const [error,setError]=useState("");
  const [me,setMe]=useState<any>(null); const [dashboard,setDashboard]=useState<any>(null);
  const [bots,setBots]=useState<Bot[]>([]); const [flows,setFlows]=useState<Flow[]>([]);
  const [analytics,setAnalytics]=useState<any>(null); const [partners,setPartners]=useState<any>(null);
  const [plans,setPlans]=useState<Plan[]>([]); const [tab,setTab]=useState<Tab>("Главная");
  const [activeBot,setActiveBot]=useState<number|null>(null); const [editor,setEditor]=useState<Flow|null>(null);
  const [botUsername,setBotUsername]=useState("GramlyHelloBot");
  const [paymentBusy,setPaymentBusy]=useState<"crypto"|"stars"|null>(null);
  const [paymentMessage,setPaymentMessage]=useState("");
  const [paymentExpiresAt,setPaymentExpiresAt]=useState("");
  const [paymentClock,setPaymentClock]=useState(Date.now());
  const [subscriptionGate,setSubscriptionGate]=useState<SubscriptionGate|null>(null);
  const [secondaryError,setSecondaryError]=useState("");
  useEffect(()=>{
    api<{interface_bot_username:string}>("/public-config").then(value=>{if(value.interface_bot_username)setBotUsername(value.interface_bot_username)}).catch(()=>undefined);
    if(!tg?.initData){setReady(true);return;}
    tg.ready();
    tg.expand();
    let active=true;
    const bootstrap=async()=>{
      try {
        const session=await api<{csrf_token:string}>("/session/telegram",{method:"POST",body:JSON.stringify({init_data:tg.initData})});
        setCsrfToken(session.csrf_token);
        const [identity,summary,botList]=await Promise.all([api<any>("/me"),api<any>("/dashboard"),api<{bots:Bot[]}>("/bots")]);
        if(!active)return;
        setMe(identity);setDashboard(summary);setBots(botList.bots);
        const entry=new URLSearchParams(window.location.search);
        const requestedBot=Number(entry.get("bot_id")||entry.get("bot"));
        setActiveBot(botList.bots.find(bot=>bot.id===requestedBot)?.id||botList.bots[0]?.id||null);
        const requestedSection=entry.get("section");
        if(requestedSection==="catalog")setTab("Шаблоны");
        else if(requestedSection==="learning")setTab("Обучение");
        else if(requestedSection==="bots")setTab("Боты");
        setReady(true);
        const optional=await Promise.allSettled([api<{plans:Plan[]}>("/plans"),api<any>("/analytics"),api<any>("/referrals")]);
        if(!active)return;
        if(optional[0].status==="fulfilled")setPlans(optional[0].value.plans);
        if(optional[1].status==="fulfilled")setAnalytics(optional[1].value);
        if(optional[2].status==="fulfilled")setPartners(optional[2].value);
        if(optional.some(result=>result.status==="rejected"))setSecondaryError("Часть дополнительной статистики временно недоступна. Основные функции продолжают работать.");
      } catch(e) {
        if(!active)return;
        if(e instanceof ApiError&&e.status===403&&typeof e.detail==="object"&&e.detail&&"code" in e.detail&&e.detail.code==="news_subscription_required")setSubscriptionGate(e.detail as SubscriptionGate);
        else setError(e instanceof Error?e.message:"Не удалось загрузить кабинет.");
      } finally {
        if(active)setReady(true);
      }
    };
    void bootstrap();
    return()=>{active=false};
  },[]);
  useEffect(()=>{
    document.documentElement.dataset.gramlyTheme=theme;
    document.documentElement.dataset.theme=theme;
    try { localStorage.setItem("gramly-hello-theme",theme); } catch { /* Private storage mode. */ }
    const dark=theme==="dark";
    document.querySelector('meta[name="theme-color"]')?.setAttribute("content",dark?"#090c0a":"#dcded5");
    try { tg?.setHeaderColor?.(dark?"#111512":"#e9e7dc"); } catch { /* Legacy Telegram client. */ }
    try { tg?.setBackgroundColor?.(dark?"#090c0a":"#dcded5"); } catch { /* Legacy Telegram client. */ }
    try { tg?.setBottomBarColor?.(dark?"#111512":"#e9e7dc"); } catch { /* Legacy Telegram client. */ }
  },[theme,tg]);
  useEffect(()=>{if(!paymentExpiresAt)return;const timer=window.setInterval(()=>setPaymentClock(Date.now()),1000);return()=>window.clearInterval(timer)},[paymentExpiresAt]);
  useEffect(()=>{if(activeBot) api<{flows:Flow[]}>(`/bots/${activeBot}/flows`).then(r=>setFlows(r.flows)).catch(e=>setError(e.message));},[activeBot]);
  const openTab=(next:Tab)=>{tg?.HapticFeedback?.impactOccurred("light");setTab(next)};
  const openOwnerBot=()=>{const url=`https://t.me/${botUsername}`;if(tg?.openTelegramLink)tg.openTelegramLink(url);else location.href=url};
  const refreshAccess=async()=>{for(let attempt=0;attempt<6;attempt+=1){const identity=await api<any>("/me");setMe(identity);if(identity?.access?.plan==="business")return;await new Promise(resolve=>setTimeout(resolve,1500));}};
  const payCrypto=async()=>{setError("");setPaymentMessage("");setPaymentBusy("crypto");try{const checkout=await api<Checkout>("/payments/crypto",{method:"POST"});setPaymentExpiresAt(checkout.expires_at);setPaymentClock(Date.now());setPaymentMessage("Счёт создан. После оплаты Business включится автоматически.");if(tg?.openTelegramLink)tg.openTelegramLink(checkout.invoice_url);else location.href=checkout.invoice_url;}catch(e){setError((e as Error).message);}finally{setPaymentBusy(null)}};
  const payStars=async()=>{setError("");setPaymentMessage("");setPaymentBusy("stars");try{const checkout=await api<Checkout>("/payments/stars",{method:"POST"});setPaymentExpiresAt(checkout.expires_at);setPaymentClock(Date.now());if(tg?.openInvoice){tg.openInvoice(checkout.invoice_url,status=>{if(status==="paid"){setPaymentExpiresAt("");setPaymentMessage("Оплата получена. Активируем Business…");void refreshAccess().then(()=>setPaymentMessage("Business активирован."));}else if(status==="pending")setPaymentMessage("Платёж обрабатывается Telegram.");else if(status==="failed")setError("Telegram не смог провести платёж. Попробуйте ещё раз.");});}else location.href=checkout.invoice_url;}catch(e){setError((e as Error).message);}finally{setPaymentBusy(null)}};
  if(!ready) return <div className="loading"><Logo/><Spinner className="loader" aria-label="Синхронизируем кабинет"/><span>Синхронизируем кабинет…</span></div>;
  if(!tg?.initData) return <OutsideTelegram username={botUsername}/>;
  if(subscriptionGate) return <main className="outside"><Logo/><div className="outside-copy"><span className="eyebrow">ОБЯЗАТЕЛЬНАЯ ПОДПИСКА</span><h1>Оставайтесь в курсе обновлений Gramly.</h1><p>Подпишитесь на «{subscriptionGate.title}», затем вернитесь сюда и проверьте подписку. Статус сверяется напрямую с Telegram.</p><a className="button acid" href={subscriptionGate.url}>📣 Подписаться</a><button className="button" onClick={()=>api<{allowed:boolean}>("/news-subscription/check",{method:"POST"}).then(result=>{if(result.allowed)location.reload();else setError("Telegram пока не видит подписку. Подпишитесь и попробуйте снова.")}).catch(e=>setError(e.message))}>🔄 Проверить подписку</button>{error&&<ErrorBox message={error}/>}</div></main>;
  if(error && !me) return <main className="outside"><Logo/><ErrorBox message={error}/><button className="button" onClick={()=>location.reload()}>Повторить</button></main>;
  const firstName=me?.owner?.first_name||"друг";
  return <div className="app-shell mini-app">
    <header className="topbar"><div><span className="topbar-kicker">GRAMLY HELLO</span><strong>Привет, {firstName}</strong></div><div className="account"><button className="theme-toggle" onClick={()=>setTheme(value=>value==="dark"?"light":"dark")} aria-label={theme==="dark"?"Включить светлую тему":"Включить тёмную тему"}>{theme==="dark"?"☀":"☾"}</button><span className="account-avatar">{firstName.slice(0,1).toUpperCase()}</span><Status ok={me?.access?.plan === "business"}>{me?.access?.plan_name || "Free"}</Status></div></header>
    <main className="content">{error&&<ErrorBox message={error}/>} {secondaryError&&<ErrorBox message={secondaryError}/>}
      {(tab==="Приветствие"||tab==="Ещё")&&<button className="button ghost" onClick={()=>openTab("Шаблоны")}>Шаблоны сообщений <Icon name="arrow"/></button>}
      {tab==="Шаблоны"&&<><button className="back-link" onClick={()=>openTab("Приветствие")}>Назад к приветствиям</button><MessageCatalog bots={bots} botId={activeBot} onBotChange={setActiveBot} onInstalled={result=>{
        setEditor({id:result.flow_id,name:result.name,kind:result.kind,assignment_mode:"selected",versions:[{id:result.version_id,number:1,status:"draft",first_delay_seconds:0}]});
        openTab("Приветствие");
      }} onConnect={templateId=>{const url=`https://t.me/${botUsername}${templateId?`?start=catalog_${templateId}`:""}`;if(tg?.openTelegramLink)tg.openTelegramLink(url);else window.open(url,"_blank","noopener,noreferrer");}}/></>}
      {paymentExpiresAt&&<div className="payment-message" role="timer">Счёт действует ещё <b>{formatCountdown(paymentExpiresAt,paymentClock)}</b></div>}
      {tab==="Главная"&&<><Card as="section" variant="glass" className="hero-panel wet-panel"><div><span className="eyebrow">СЛЕДУЮЩИЙ ШАГ</span><h1>{!bots.length?"Подключите первого бота.":!(dashboard?.channels||0)?"Добавьте канал.":!flows.length?"Создайте приветствие.":"Приветствие под контролем."}</h1><p>{!bots.length?"Безопасно добавьте токен от BotFather — он хранится только в зашифрованном виде.":!(dashboard?.channels||0)?"Назначьте бота администратором канала и подтвердите подключение.":!flows.length?"Выберите шаблон или соберите сообщение с нуля.":"Проверьте активную цепочку и результаты последних доставок."}</p><Button className="button acid quick-action" onClick={!bots.length?openOwnerBot:()=>openTab((dashboard?.channels||0)?"Приветствие":"Боты")}>{!bots.length?"Подключить бота":!(dashboard?.channels||0)?"Открыть бота":"Настроить приветствие"} <Icon name="arrow"/></Button></div><div className="launch-checklist"><span className={bots.length?"done":""}>1. Бот</span><span className={(dashboard?.channels||0)>0?"done":""}>2. Канал</span><span className={flows.length?"done":""}>3. Приветствие</span><span className={flows.some(f=>f.versions.some(v=>v.status==="published"))?"done":""}>4. Запуск</span></div></Card><Card as="section" variant="glass" className="metrics wet-panel"><Metric label="Боты" value={dashboard?.bots??0}/><Metric label="Каналы" value={dashboard?.channels??0}/><Metric label="Подписчики" value={dashboard?.contacts??0}/><Metric label="Доставлено" value={dashboard?.delivered??0}/></Card></>}
      {tab==="Боты"&&<section><div className="section-head"><div><span className="eyebrow">ПОДКЛЮЧЕНИЯ</span><h2>Мои боты</h2></div></div>{!bots.length?<EmptyState title="Здесь появятся ваши боты" text="Откройте GramlyHello, отправьте токен от BotFather и завершите безопасное подключение по подсказкам." action="Подключить первого бота" onAction={openOwnerBot}/>:<div className="bot-list">{bots.map(bot=><Card as="article" variant="glass" className="wet-panel" key={bot.id}><div className="bot-id"><span className="avatar">{bot.display_name.slice(0,1)}</span><div><strong>@{bot.username||bot.display_name}</strong><small>{bot.channels} каналов · {bot.contacts} контактов</small></div></div><Status ok={bot.webhook_configured}>{bot.webhook_configured?"Webhook online":"Нужно подключить"}</Status><Button className="button ghost" onClick={()=>{setActiveBot(bot.id);openTab("Приветствие")}}>Приветствие <Icon name="arrow"/></Button></Card>)}</div>}</section>}
      {tab==="Приветствие"&&<section><div className="section-head"><div><span className="eyebrow">ГЛАВНЫЙ СЦЕНАРИЙ</span><h2>Приветствие</h2></div>{bots.length>0&&<select value={activeBot||""} onChange={e=>setActiveBot(Number(e.target.value))}>{bots.map(bot=><option key={bot.id} value={bot.id}>@{bot.username}</option>)}</select>}</div>{!bots.length?<EmptyState title="Сначала подключите бота" text="Приветствие принадлежит конкретному Telegram-боту. Подключение займёт пару минут." action="Перейти к подключению" onAction={openOwnerBot}/>:!flows.length?<EmptyState title="Создайте первое приветствие" text="Начните с готового шаблона или соберите своё сообщение в owner-боте." action="Выбрать шаблон" onAction={()=>openTab("Шаблоны")}/>:<div className="flow-list">{flows.map(flow=><button className="wet-panel" key={flow.id} onClick={()=>setEditor(flow)}><div><span>{flow.kind==="farewell"?"ПРОЩАНИЕ":"ПРИВЕТСТВИЕ"}</span><strong>{flow.name}</strong></div><div><small>{flow.versions.find(v=>v.status==="published")?"Опубликовано":"Черновик"}</small><Icon name="arrow"/></div></button>)}</div>}</section>}
      {tab==="База"&&<AudiencePage bots={bots} activeBot={activeBot} onBotChange={setActiveBot}/>}
      {tab==="Рассылки"&&<BroadcastsPage bots={bots} activeBot={activeBot} onBotChange={setActiveBot}/>}
      {tab==="Обучение"&&<LearningPage/>}
      {tab==="Аналитика"&&<section><div className="section-head"><div><span className="eyebrow">ТОЛЬКО РЕАЛЬНЫЕ ДАННЫЕ</span><h2>Аналитика</h2></div></div>{!((analytics?.deliveries?.completed||0)+(analytics?.deliveries?.failed||0)+(analytics?.deliveries?.partial||0)+(analytics?.rotation?.impressions||0)+(analytics?.rotation?.conversions||0))&&<EmptyState title="Данных пока нет" text="Статистика появится после первого события Telegram и попытки доставки цепочки."/>}<div className="metrics wet-panel"><Metric label="Успешные цепочки" value={analytics?.deliveries?.completed||0}/><Metric label="Ошибки" value={(analytics?.deliveries?.failed||0)+(analytics?.deliveries?.partial||0)}/><Metric label="Показы ротации" value={analytics?.rotation?.impressions||0}/><Metric label="Подписки" value={analytics?.rotation?.conversions||0}/></div><div className="note wet-panel">Telegram не передаёт пол и страну пользователя — поэтому мы не показываем выдуманные данные.</div></section>}
      {tab==="Ещё"&&<section><div className="section-head"><div><span className="eyebrow">НАСТРОЙКИ И ПОМОЩЬ</span><h2>Ещё</h2></div></div><div className="more-list"><button className="wet-panel" onClick={()=>openTab("Боты")}><span><b>Боты и каналы</b><small>{bots.length} подключено</small></span><Icon name="arrow"/></button><button className="wet-panel" onClick={()=>openTab("Аналитика")}><span><b>Аналитика</b><small>Доставки и конверсии</small></span><Icon name="arrow"/></button><button className="wet-panel" onClick={()=>openTab("Обучение")}><span><b>Обучение</b><small>Быстрый старт и видео</small></span><Icon name="arrow"/></button><button className="wet-panel" onClick={()=>openTab("Подписка")}><span><b>Тариф</b><small>{me?.access?.plan_name||"Free"}</small></span><Icon name="arrow"/></button><button className="wet-panel" onClick={()=>openTab("Партнёры")}><span><b>Партнёрская программа</b><small>{partners?.active_referrals||0} активных клиентов</small></span><Icon name="arrow"/></button><button className="wet-panel" onClick={()=>setTheme(value=>value==="dark"?"light":"dark")}><span><b>Оформление</b><small>{theme==="dark"?"Тёмная тема":"Светлая тема"} · изменить</small></span><span aria-hidden="true">{theme==="dark"?"☀":"☾"}</span></button></div></section>}
      {tab==="Подписка"&&<section><button className="back-link" onClick={()=>openTab("Ещё")}>← Назад</button><div className="section-head"><div><span className="eyebrow">FREE / BUSINESS</span><h2>Тариф</h2></div></div>{plans.filter(p=>p.slug==="business").map(plan=><div className="plan wet-panel" key={plan.slug}><div><Status ok={me?.access?.plan==="business"}>{me?.access?.plan_name}</Status><h3>{me?.access?.plan==="business"?"До 10 приветственных сообщений, без рекламы и с ротацией.":"До 10 приветственных сообщений, без рекламы и с ротацией каналов."}</h3><p>Business действует 30 дней. Выберите удобный способ оплаты.</p></div><div className="payment-options" aria-label="Способы оплаты">{plan.prices.xtr&&<button className="payment-option stars" disabled={paymentBusy!==null} onClick={()=>void payStars()}><span className="payment-symbol">⭐</span><span><b>{paymentBusy==="stars"?"Открываем счёт…":"Telegram Stars"}</b><small>{plan.prices.xtr} Stars · автопродление</small></span><Icon name="arrow"/></button>}{plan.prices.rub&&<button className="payment-option crypto" disabled={paymentBusy!==null} onClick={()=>void payCrypto()}><span className="payment-symbol">💎</span><span><b>{paymentBusy==="crypto"?"Создаём счёт…":"Crypto Pay"}</b><small>{plan.prices.rub} ₽ · USDT или TON</small></span><Icon name="arrow"/></button>}</div>{paymentMessage&&<div className="payment-message" role="status">{paymentMessage}</div>}</div>)}</section>}
      {tab==="Партнёры"&&<section><button className="back-link" onClick={()=>openTab("Ещё")}>← Назад</button><div className="section-head"><div><span className="eyebrow">ПАРТНЁРСКИЙ СЧЁТ</span><h2>Партнёрская программа</h2></div></div>{!(partners?.active_referrals||0)&&<EmptyState title="Активных рефералов пока нет" text="Поделитесь персональной ссылкой. Начисление появится после первой подтверждённой оплаты приглашённого клиента."/>}<div className="metrics wet-panel"><Metric label="Активные клиенты" value={partners?.active_referrals||0}/><Metric label="Доступно" value={`${partners?.balance_rub||0} ₽`}/></div><label className="copy-field wet-panel"><span>Ваша ссылка</span><input readOnly value={partners?.url||""}/><button onClick={()=>navigator.clipboard.writeText(partners?.url||"")}>Копировать</button></label></section>}
      {tab==="Ещё"&&<section className="wet-panel support-panel"><h2>Поддержка и обратная связь</h2><p>Расскажите: что нажали → что ожидали → что произошло. Приложите скриншот или видео, username бота и примерное время ошибки.</p><p>Не отправляйте токены, пароли и коды входа.</p><a className="button ghost" href="https://t.me/gramlymanager" onClick={event=>{if(tg?.openTelegramLink){event.preventDefault();tg.openTelegramLink("https://t.me/gramlymanager");}}}>Написать в поддержку</a></section>}
    </main>
    <nav className="bottom-nav" aria-label="Основные разделы">{navigation.map(item=>{const active=tab===item.tab||(item.tab==="Ещё"&&["Подписка","Партнёры","Боты","Аналитика","Обучение","Шаблоны"].includes(tab));return <button className={active?"active":""} aria-current={active?"page":undefined} onClick={()=>openTab(item.tab)} key={item.tab}><Icon name={item.icon}/><span>{item.label}</span></button>})}</nav>
    {editor&&<FlowDialog flow={editor} onClose={()=>{setEditor(null);if(activeBot)api<{flows:Flow[]}>(`/bots/${activeBot}/flows`).then(r=>setFlows(r.flows))}}/>}
  </div>;
}

function FlowDialog({flow,onClose}:{flow:Flow;onClose:()=>void}){
  const dialog=useRef<HTMLDialogElement>(null);
  useEffect(()=>{const node=dialog.current;node?.showModal();return()=>node?.close()},[]);
  return <dialog className="modal" ref={dialog} aria-label="Редактор цепочки" onCancel={event=>{if(event.target!==event.currentTarget)return;event.preventDefault();const close=dialog.current?.querySelector<HTMLButtonElement>('.workspace-heading>button');if(close)close.click();else onClose()}}><Suspense fallback={<p role="status">Загружаем редактор…</p>}><FlowEditor flow={flow} onClose={onClose}/></Suspense></dialog>;
}

const isAdmin = document.title.includes("Control");
const rootElement=document.getElementById("root");
if(rootElement){
  const root=createRoot(rootElement);
  const render=()=>root.render(<React.StrictMode><AppErrorBoundary>{isAdmin?<Suspense fallback={<p role="status">Загружаем админку…</p>}><AdminApp/></Suspense>:<MiniApp/>}</AppErrorBoundary></React.StrictMode>);
  if(isAdmin)render();
  else void loadTelegramSdk().then(render).catch(()=>root.render(<main className="outside"><Logo/><div className="outside-copy" role="alert"><h1>Telegram не отвечает.</h1><p>Не удалось загрузить подключение к Telegram. Проверьте соединение и повторите запуск. Ваши настройки сохранены.</p><button className="button acid" onClick={()=>location.reload()}>Повторить</button></div></main>));
}
