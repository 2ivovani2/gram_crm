import React, {useEffect, useState} from "react";
import {adminApi} from "./api";

type Account = {
  id:number; telegram_id:number; label:string; status:string; last_error:string;
  checked_at:string|null; cooldown_until:string|null; assigned_channels:number;
};
type Pool = {
  healthy:number; minimum:number; available:boolean;
  api_configured:boolean;
  alert_recipient_owner_id:number|null; items:Account[];
};
const statusLabel:Record<string,string> = {
  healthy:"Работает", checking:"Проверяется", revoked:"Сессия недействительна",
  cooldown:"Ограничение Telegram", unavailable:"Не отвечает", disabled:"Отключён",
};

export function AdminViewAccounts(){
  const [pool,setPool]=useState<Pool|null>(null);
  const [error,setError]=useState("");
  const [busy,setBusy]=useState("");
  const [recipient,setRecipient]=useState("i_vovani");
  const [confirmDelete,setConfirmDelete]=useState<number|null>(null);
  const load=async()=>{try{setPool(await adminApi<Pool>("/view/accounts"));setError("")}catch(exc){setError((exc as Error).message)}};
  useEffect(()=>{void load();const timer=window.setInterval(()=>void load(),30_000);return()=>window.clearInterval(timer)},[]);
  const upload=async(event:React.FormEvent<HTMLFormElement>)=>{
    event.preventDefault();const form=event.currentTarget;const file=(form.elements.namedItem("session") as HTMLInputElement)?.files?.[0];
    if(!file)return;
    setBusy("upload");setError("");
    try{const body=new FormData();body.append("file",file);await adminApi("/view/accounts",{method:"POST",body});form.reset();await load()}
    catch(exc){setError((exc as Error).message)}finally{setBusy("")}
  };
  const check=async()=>{setBusy("check");setError("");try{await adminApi("/view/accounts/check",{method:"POST"});await load()}catch(exc){setError((exc as Error).message)}finally{setBusy("")}};
  const toggle=async(account:Account)=>{setBusy(`account-${account.id}`);setError("");try{await adminApi(`/view/accounts/${account.id}/state`,{method:"POST",body:JSON.stringify({disabled:account.status!=="disabled"})});await load()}catch(exc){setError((exc as Error).message)}finally{setBusy("")}};
  const saveRecipient=async(event:React.FormEvent<HTMLFormElement>)=>{event.preventDefault();setBusy("recipient");setError("");try{await adminApi("/view/alert-recipient",{method:"POST",body:JSON.stringify({username:recipient})});await load()}catch(exc){setError((exc as Error).message)}finally{setBusy("")}};
  const remove=async(account:Account)=>{setBusy(`account-${account.id}`);setError("");try{await adminApi(`/view/accounts/${account.id}`,{method:"DELETE"});setConfirmDelete(null);await load()}catch(exc){setError((exc as Error).message)}finally{setBusy("")}};
  return <section className="view-pool">
    <div className={`view-pool-summary ${pool?.available?"ready":"stopped"}`} role="status">
      <div><span className="eyebrow">MTProto · GramlyView</span><h2>{pool?`${pool.healthy} / ${pool.minimum}`:"Проверяем пул…"}</h2><p>{pool?.available?"Аналитика доступна":pool&&!pool.api_configured?"Новые запросы остановлены: MTProto API ID/hash не настроены на сервере.":"Новые запросы аналитики приостановлены, пока рабочих аккаунтов меньше пяти."}</p></div>
      <button className="button ghost" type="button" disabled={!!busy} onClick={()=>void check()}>{busy==="check"?"Проверяем…":"Проверить сейчас"}</button>
    </div>
    {error&&<p className="view-pool-error" role="alert">{error} <button type="button" onClick={()=>void load()}>Повторить</button></p>}
    <div className="admin-grid">
      <form onSubmit={upload} className="view-pool-form"><h3>Добавить аккаунт</h3><p>Загрузите авторизованный Telethon .session. Файл хранится зашифрованным и недоступен для скачивания.</p><label>Файл сессии<input name="session" type="file" accept=".session" required/></label><button className="button acid" disabled={!!busy}>{busy==="upload"?"Проверяем в Telegram…":"Загрузить и проверить"}</button></form>
      <form onSubmit={saveRecipient} className="view-pool-form"><h3>Аварийные уведомления</h3><p>Выберите аккаунт, который уже открыл @gramly_view_bot. Перед назначением бот отправит проверочное сообщение.</p><label>Telegram username<input value={recipient} onChange={event=>setRecipient(event.target.value)} placeholder="i_vovani" required/></label><button className="button ghost" disabled={!!busy}>{busy==="recipient"?"Проверяем…":"Назначить получателя"}</button><small>{pool?.alert_recipient_owner_id?"Получатель назначен":"Получатель пока не назначен"}</small></form>
    </div>
    <div className="view-pool-list"><h3>Аккаунты</h3>{pool?.items.length?pool.items.map(account=><article key={account.id} className="view-pool-account"><div><b>{account.label||`ID ${account.telegram_id}`}</b><small>Telegram ID {account.telegram_id} · закрытых каналов {account.assigned_channels}</small></div><div><strong className={`view-pool-state ${account.status}`}>{statusLabel[account.status]||account.status}</strong><small>Проверен: {account.checked_at?new Date(account.checked_at).toLocaleString("ru-RU"):"ещё нет"}{account.last_error?` · ${account.last_error}`:""}</small></div><div className="view-pool-actions"><button type="button" className="button ghost" disabled={!!busy} onClick={()=>void toggle(account)}>{account.status==="disabled"?"Включить":"Отключить"}</button>{!account.assigned_channels&&(confirmDelete===account.id?<><button type="button" className="button ghost" disabled={!!busy} onClick={()=>setConfirmDelete(null)}>Отмена</button><button type="button" className="button ghost" disabled={!!busy} onClick={()=>void remove(account)}>Удалить сессию</button></>:<button type="button" className="button ghost" disabled={!!busy} onClick={()=>setConfirmDelete(account.id)}>Удалить</button>)}</div></article>):<p className="view-pool-empty">Аккаунтов пока нет. Загрузите пять рабочих сессий, чтобы открыть анализ.</p>}</div>
  </section>
}
