import React, {useEffect, useRef, useState} from "react";
import {adminApi} from "./api";

type Account = {id:number;telegram_id:number;label:string;status:string;last_error:string;checked_at:string|null;assigned_channels:number};
type Recipient = {owner_id:number;username:string;telegram_id:number};
type Pool = {healthy:number;minimum:number;available:boolean;api_configured:boolean;alert_recipients:Recipient[];items:Account[]};
type UploadItem = {id:string;file:File;status:"queued"|"uploading"|"done"|"error";error:string};
const MAX_SESSION_BYTES=1_048_576;
const statusLabel:Record<string,string>={healthy:"Работает",checking:"Проверяется",revoked:"Сессия недействительна",cooldown:"Ограничение Telegram",unavailable:"Не отвечает",disabled:"Отключён"};

export function AdminViewAccounts(){
  const [pool,setPool]=useState<Pool|null>(null);
  const [error,setError]=useState("");
  const [busy,setBusy]=useState("");
  const [files,setFiles]=useState<UploadItem[]>([]);
  const [dragging,setDragging]=useState(false);
  const [recipient,setRecipient]=useState("");
  const [confirmDelete,setConfirmDelete]=useState<number|null>(null);
  const [confirmRecipient,setConfirmRecipient]=useState<number|null>(null);
  const inputRef=useRef<HTMLInputElement>(null);
  const load=async()=>{try{setPool(await adminApi<Pool>("/view/accounts"));setError("")}catch(exc){setError((exc as Error).message)}};
  useEffect(()=>{void load();const timer=window.setInterval(()=>void load(),30_000);return()=>window.clearInterval(timer)},[]);
  const addFiles=(selected:FileList|File[])=>{
    const incoming=Array.from(selected).map(file=>{
      const error=!file.name.toLowerCase().endsWith(".session")?"Нужен файл .session":file.size>MAX_SESSION_BYTES?"Файл больше 1 МБ":"";
      return {id:`${file.name}:${file.size}:${file.lastModified}`,file,status:error?"error" as const:"queued" as const,error};
    });
    setFiles(current=>{
      const known=new Set(current.map(item=>item.id));
      return [...current,...incoming.filter(item=>!known.has(item.id))].slice(0,25);
    });
    if(files.length+incoming.length>25)setError("За один раз можно выбрать не более 25 файлов.");
    if(inputRef.current)inputRef.current.value="";
  };
  const upload=async()=>{
    const pending=files.filter(item=>item.status==="queued"||(item.status==="error"&&item.error!=="Нужен файл .session"&&item.error!=="Файл больше 1 МБ"));
    if(!pending.length)return;
    setBusy("upload");setError("");
    for(const item of pending){
      setFiles(current=>current.map(row=>row.id===item.id?{...row,status:"uploading",error:""}:row));
      try{
        const body=new FormData();body.append("file",item.file);
        await adminApi("/view/accounts",{method:"POST",body});
        setFiles(current=>current.map(row=>row.id===item.id?{...row,status:"done",error:""}:row));
      }catch(exc){
        setFiles(current=>current.map(row=>row.id===item.id?{...row,status:"error",error:(exc as Error).message}:row));
      }
    }
    setBusy("");await load();
  };
  const check=async()=>{setBusy("check");setError("");try{await adminApi("/view/accounts/check",{method:"POST"});await load()}catch(exc){setError((exc as Error).message)}finally{setBusy("")}};
  const toggle=async(account:Account)=>{setBusy("account");setError("");try{await adminApi(`/view/accounts/${account.id}/state`,{method:"POST",body:JSON.stringify({disabled:account.status!=="disabled"})});await load()}catch(exc){setError((exc as Error).message)}finally{setBusy("")}};
  const remove=async(account:Account)=>{setBusy("account");setError("");try{await adminApi(`/view/accounts/${account.id}`,{method:"DELETE"});setConfirmDelete(null);await load()}catch(exc){setError((exc as Error).message)}finally{setBusy("")}};
  const addRecipient=async(event:React.FormEvent<HTMLFormElement>)=>{event.preventDefault();setBusy("recipient");setError("");try{await adminApi("/view/alert-recipients",{method:"POST",body:JSON.stringify({username:recipient})});setRecipient("");await load()}catch(exc){setError((exc as Error).message)}finally{setBusy("")}};
  const removeRecipient=async(ownerId:number)=>{setBusy("recipient");setError("");try{await adminApi(`/view/alert-recipients/${ownerId}`,{method:"DELETE"});setConfirmRecipient(null);await load()}catch(exc){setError((exc as Error).message)}finally{setBusy("")}};
  const completed=files.filter(item=>item.status==="done").length;
  const failed=files.filter(item=>item.status==="error").length;
  const uploadable=files.some(item=>item.status==="queued"||(item.status==="error"&&item.error!=="Нужен файл .session"&&item.error!=="Файл больше 1 МБ"));
  return <section className="view-pool">
    <div className={`view-pool-summary ${pool?.available?"ready":"stopped"}`} role="status">
      <div><span className="eyebrow">MTProto · GramlyView</span><h2>{pool?`${pool.healthy} / ${pool.minimum}`:"Проверяем пул…"}</h2><p>{pool?.available?"Аналитика доступна":pool&&!pool.api_configured?"Новые запросы остановлены: MTProto API ID/hash не настроены на сервере.":`Новые запросы приостановлены: требуется ${pool?.minimum??3} рабочих аккаунта.`}</p></div>
      <button className="button ghost" type="button" disabled={!!busy} onClick={()=>void check()}>{busy==="check"?"Проверяем…":"Проверить сейчас"}</button>
    </div>
    {error&&<p className="view-pool-error" role="alert">{error} <button type="button" onClick={()=>void load()}>Обновить данные</button></p>}
    <div className="admin-grid">
      <section className="view-pool-form" aria-labelledby="view-upload-title">
        <div><span className="eyebrow">01 · ДОСТУП К АНАЛИТИКЕ</span><h3 id="view-upload-title">Добавить аккаунты</h3><p>Выберите авторизованные файлы Telethon. Каждый проверяется отдельно и хранится зашифрованным.</p></div>
        <div className={`view-pool-drop ${dragging?"dragging":""}`} onDragOver={event=>{event.preventDefault();setDragging(true)}} onDragLeave={()=>setDragging(false)} onDrop={event=>{event.preventDefault();setDragging(false);addFiles(event.dataTransfer.files)}}>
          <strong>Перетащите .session сюда</strong><span>или выберите файлы · до 1 МБ каждый</span>
          <label className="button ghost" htmlFor="view-session-files">Выбрать файлы</label>
          <input ref={inputRef} id="view-session-files" type="file" accept=".session" multiple onChange={event=>{if(event.target.files)addFiles(event.target.files)}} />
        </div>
        {files.length>0&&<div className="view-pool-upload-list" aria-live="polite">
          <div className="view-pool-upload-head"><strong>Выбрано: {files.length}</strong><span>{completed} добавлено · {failed} с ошибкой</span></div>
          {files.map(item=><div className="view-pool-upload-item" key={item.id}><div><strong>{item.file.name}</strong><small>{(item.file.size/1024).toFixed(0)} КБ</small></div><span className={`view-pool-upload-status ${item.status==="error"?"failed":item.status}`}>{item.status==="queued"?"Ожидает":item.status==="uploading"?"Проверяется…":item.status==="done"?"Добавлен":item.error||"Ошибка"}</span>{item.status!=="uploading"&&<button type="button" className="view-pool-remove-file" aria-label={`Убрать ${item.file.name}`} onClick={()=>setFiles(current=>current.filter(row=>row.id!==item.id))}>×</button>}</div>)}
        </div>}
        <button className="button acid" type="button" disabled={!!busy||!uploadable} onClick={()=>void upload()}>{busy==="upload"?`Проверяем · ${completed+failed} из ${files.length}`:failed?"Загрузить и повторить ошибки":"Загрузить и проверить"}</button>
      </section>
      <section className="view-pool-form" aria-labelledby="view-recipients-title">
        <div><span className="eyebrow">02 · ОПОВЕЩЕНИЯ</span><h3 id="view-recipients-title">Получатели алертов</h3><p>Каждый должен сначала открыть @gramly_view_bot. Перед добавлением бот отправит проверочное сообщение.</p></div>
        <form onSubmit={addRecipient} className="view-pool-recipient-form"><label>Telegram username<input value={recipient} onChange={event=>setRecipient(event.target.value)} placeholder="@username" required/></label><button className="button ghost" disabled={!!busy}>{busy==="recipient"?"Проверяем…":"Добавить"}</button></form>
        <div className="view-pool-recipient-list">{pool?.alert_recipients.length?pool.alert_recipients.map(person=><div className="view-pool-recipient" key={person.owner_id}><div><strong>{person.username?`@${person.username}`:`ID ${person.telegram_id}`}</strong><small>Telegram ID {person.telegram_id}</small></div>{confirmRecipient===person.owner_id?<div className="view-pool-actions"><button type="button" className="button ghost" onClick={()=>setConfirmRecipient(null)}>Отмена</button><button type="button" className="button ghost" disabled={!!busy} onClick={()=>void removeRecipient(person.owner_id)}>Подтвердить удаление</button></div>:<button type="button" className="button ghost" disabled={!!busy} onClick={()=>setConfirmRecipient(person.owner_id)}>Удалить</button>}</div>):<p className="view-pool-empty">Получателей пока нет. Добавьте человека, которому придёт сигнал о приостановке аналитики.</p>}</div>
      </section>
    </div>
    <div className="view-pool-list"><h3>Аккаунты</h3>{pool?.items.length?pool.items.map(account=><article key={account.id} className="view-pool-account"><div><b>{account.label||`ID ${account.telegram_id}`}</b><small>Telegram ID {account.telegram_id} · закрытых каналов {account.assigned_channels}</small></div><div><strong className={`view-pool-state ${account.status}`}>{statusLabel[account.status]||account.status}</strong><small>Проверен: {account.checked_at?new Date(account.checked_at).toLocaleString("ru-RU"):"ещё нет"}{account.last_error?` · ${account.last_error}`:""}</small></div><div className="view-pool-actions"><button type="button" className="button ghost" disabled={!!busy} onClick={()=>void toggle(account)}>{account.status==="disabled"?"Включить":"Отключить"}</button>{!account.assigned_channels&&(confirmDelete===account.id?<><button type="button" className="button ghost" disabled={!!busy} onClick={()=>setConfirmDelete(null)}>Отмена</button><button type="button" className="button ghost" disabled={!!busy} onClick={()=>void remove(account)}>Удалить сессию</button></>:<button type="button" className="button ghost" disabled={!!busy} onClick={()=>setConfirmDelete(account.id)}>Удалить</button>)}</div></article>):<p className="view-pool-empty">Аккаунтов пока нет. Загрузите три рабочих сессии, чтобы открыть анализ.</p>}</div>
  </section>
}
