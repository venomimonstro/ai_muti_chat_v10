"use client";

import {FormEvent,useMemo,useState} from "react";
import {api} from "../../lib/api";
import type {ChatMessage,Conversation} from "../../lib/types";
import {MarkdownMessage} from "../components/MarkdownMessage";
import {Icon} from "./Icons";

const money=(value:string|null|undefined)=>value==null?"":`${Number(value).toFixed(2).replace(".",",")} ₽`;

export function MessageCard({conversationId,message,onConversation}:{conversationId:string;message:ChatMessage;onConversation:(value:Conversation)=>void}){
 const[editing,setEditing]=useState(false);const[value,setValue]=useState(message.content);const[busy,setBusy]=useState(false);const[error,setError]=useState("");const[details,setDetails]=useState(false);const[copied,setCopied]=useState(false);
 const sources=useMemo(()=>message.generation?.context?.citations??[],[message.generation]);
 const edit=async(e:FormEvent)=>{e.preventDefault();if(!value.trim())return;setBusy(true);setError("");try{const result=await api<Conversation>(`/conversations/${conversationId}/messages/${message.id}/edit/`,{method:"POST",headers:{"Idempotency-Key":`web-edit:${crypto.randomUUID()}`},body:JSON.stringify({content:value.trim()})});onConversation(result);setEditing(false);}catch(reason){setError(reason instanceof Error?reason.message:"Не удалось изменить сообщение");}finally{setBusy(false)}};
 const regenerate=async()=>{setBusy(true);setError("");try{const result=await api<Conversation>(`/conversations/${conversationId}/messages/${message.id}/regenerate/`,{method:"POST",headers:{"Idempotency-Key":`web-regenerate:${crypto.randomUUID()}`},body:"{}"});onConversation(result);}catch(reason){setError(reason instanceof Error?reason.message:"Не удалось создать новый вариант");}finally{setBusy(false)}};
 const copy=async()=>{await navigator.clipboard.writeText(message.content);setCopied(true);window.setTimeout(()=>setCopied(false),1200)};
 return <article className={`message ${message.role} ${message.status}`}>
  <div className="messageBody">{editing?<form className="messageEdit" onSubmit={edit}><textarea value={value} onChange={e=>setValue(e.target.value)} rows={5} autoFocus/><div><button type="button" onClick={()=>{setEditing(false);setValue(message.content)}}>Отмена</button><button className="primarySmall" disabled={busy}>{busy?"Отправляем…":"Сохранить и ответить заново"}</button></div></form>:message.role==="assistant"?<MarkdownMessage content={message.content|| (message.status==="streaming"?"":"Ответ не получен")}/>:<div className="userBubble">{message.content}</div>}</div>
  {message.status==="partial"&&<div className="messageState">Ответ остановлен. Уже полученный текст сохранён.</div>}{message.status==="failed"&&<div className="messageState error">Не удалось получить ответ.</div>}{error&&<div className="inlineError">{error}</div>}
  {!editing&&message.content&&<div className="messageActions"><button onClick={()=>void copy()} title="Копировать" aria-label="Копировать"><Icon name={copied?"check":"copy"}/></button>{message.role==="user"&&<button disabled={busy} onClick={()=>setEditing(true)} title="Изменить сообщение" aria-label="Изменить сообщение"><Icon name="pencil"/></button>}{message.role==="assistant"&&<button disabled={busy} onClick={()=>void regenerate()} title="Другой вариант" aria-label="Другой вариант"><Icon name="retry"/></button>}{message.generation&&<button className="messageMetaButton" onClick={()=>setDetails(!details)}>{message.generation.model}{message.generation.cost_rub?` · ${money(message.generation.cost_rub)}`:""}</button>}</div>}
  {details&&message.generation&&<div className="messageDetails"><div><span>Модель</span><b>{message.generation.model}</b></div><div><span>Провайдер</span><b>{message.generation.provider||"—"}</b></div><div><span>Токены</span><b>{message.generation.input_tokens} → {message.generation.output_tokens}</b></div><div><span>Стоимость</span><b>{money(message.generation.cost_rub)||"—"}</b></div>{message.generation.context?.routing?.explanation&&<p>{message.generation.context.routing.explanation}</p>}{sources.length>0&&<section><b>Источники · {sources.length}</b>{sources.slice(0,8).map(source=><div className="sourceRow" key={source.id}>{source.file_name}{source.source_location?.page?` · стр. ${source.source_location.page}`:""}</div>)}</section>}</div>}
 </article>;
}
