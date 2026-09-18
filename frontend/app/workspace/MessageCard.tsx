"use client";

import {FormEvent,useState} from "react";
import {api} from "../../lib/api";
import type {ChatMessage,Conversation} from "../../lib/types";
import {MarkdownMessage} from "../components/MarkdownMessage";

const money=(value:string|null|undefined)=>value==null?"—":`${Number(value).toFixed(2).replace(".",",")} ₽`;

export function MessageCard({conversationId,message,onConversation}:{conversationId:string;message:ChatMessage;onConversation:(value:Conversation)=>void}){
  const [editing,setEditing]=useState(false);const [value,setValue]=useState(message.content);const [busy,setBusy]=useState(false);const [error,setError]=useState("");
  const edit=async(e:FormEvent)=>{e.preventDefault();if(!value.trim())return;setBusy(true);setError("");try{const result=await api<Conversation>(`/conversations/${conversationId}/messages/${message.id}/edit/`,{method:"POST",headers:{"Idempotency-Key":`web-edit:${crypto.randomUUID()}`},body:JSON.stringify({content:value.trim()})});onConversation(result);setEditing(false);}catch(reason){setError(reason instanceof Error?reason.message:"Не удалось изменить сообщение");}finally{setBusy(false);}};
  const regenerate=async()=>{setBusy(true);setError("");try{const result=await api<Conversation>(`/conversations/${conversationId}/messages/${message.id}/regenerate/`,{method:"POST",headers:{"Idempotency-Key":`web-regenerate:${crypto.randomUUID()}`},body:"{}"});onConversation(result);}catch(reason){setError(reason instanceof Error?reason.message:"Не удалось создать новый вариант");}finally{setBusy(false);}};
  return <article className={`proMessage ${message.role} ${message.status}`}>
    <header className="proMessageHead"><b>{message.role==="user"?"Вы":"AI Workspace"}</b><span>{new Date(message.created_at).toLocaleString("ru",{day:"numeric",month:"short",hour:"2-digit",minute:"2-digit"})}</span>{message.status==="partial"&&<em>Ответ прерван</em>}{message.status==="failed"&&<em>Ошибка</em>}</header>
    {editing?<form className="messageEdit" onSubmit={edit}><textarea value={value} onChange={e=>setValue(e.target.value)} rows={5} autoFocus/><div><button type="button" onClick={()=>{setEditing(false);setValue(message.content)}}>Отмена</button><button disabled={busy}>{busy?"Отправляем…":"Сохранить и получить новый ответ"}</button></div></form>:message.role==="assistant"?<MarkdownMessage content={message.content||"Ответ не получен"}/>:<div className="userMessageText">{message.content}</div>}
    {error&&<div className="messageInlineError">{error}</div>}
    {!editing&&message.content&&<footer className="proMessageActions"><button type="button" onClick={()=>navigator.clipboard.writeText(message.content)}>Копировать</button>{message.role==="user"&&<button type="button" disabled={busy} onClick={()=>setEditing(true)}>Изменить</button>}{message.role==="assistant"&&<button type="button" disabled={busy} onClick={regenerate}>{busy?"Создаём…":"Другой вариант"}</button>}{message.generation&&<span>{message.generation.model} · {money(message.generation.cost_rub)}</span>}</footer>}
  </article>;
}
