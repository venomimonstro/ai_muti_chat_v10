"use client";

import {FormEvent,useMemo,useState} from "react";
import {ApiError,api} from "../../lib/api";
import type {ChatMessage,Conversation} from "../../lib/types";
import {MarkdownMessage} from "../components/MarkdownMessage";
import {Icon} from "./Icons";

type CostGuardPayload={code?:string;detail?:string;estimated_max_rub?:string};
type PendingCost={maximum:string;label:string;execute:()=>Promise<void>};

const money=(value:string|null|undefined)=>{
 if(value==null)return "";
 const amount=Number(value);if(!Number.isFinite(amount))return `${value} ₽`;
 const tiny=amount!==0&&Math.abs(amount)<1;
 return `${amount.toLocaleString("ru-RU",{minimumFractionDigits:tiny?4:2,maximumFractionDigits:tiny?4:2})} ₽`;
};

export function MessageCard({conversationId,message,onConversation}:{conversationId:string;message:ChatMessage;onConversation:(value:Conversation)=>void}){
 const[editing,setEditing]=useState(false);const[value,setValue]=useState(message.content);const[busy,setBusy]=useState(false);const[error,setError]=useState("");const[details,setDetails]=useState(false);const[copied,setCopied]=useState(false);const[pendingCost,setPendingCost]=useState<PendingCost|null>(null);
 const sources=useMemo(()=>message.generation?.context?.citations??[],[message.generation]);
 const systemTier=message.generation?.provider==="system";
 const runPaidAction=async(path:string,body:Record<string,unknown>,key:string,label:string,onDone:(result:Conversation)=>void)=>{
  const request=(payload:Record<string,unknown>)=>api<Conversation>(path,{method:"POST",headers:{"Idempotency-Key":key},body:JSON.stringify(payload)});
  try{onDone(await request(body));return;}catch(reason){
   if(reason instanceof ApiError&&reason.status===409&&reason.payload&&typeof reason.payload==="object"){
    const payload=reason.payload as CostGuardPayload;
    if(payload.code==="cost_confirmation_required"&&payload.estimated_max_rub){
     const maximum=payload.estimated_max_rub;
     setPendingCost({maximum,label,execute:async()=>{onDone(await request({...body,confirm_cost:true,confirmed_max_rub:maximum}));}});
     return;
    }
   }
   throw reason;
  }
 };
 const confirmPendingCost=async()=>{if(!pendingCost||busy)return;const action=pendingCost;setBusy(true);setError("");try{await action.execute();setPendingCost(null);}catch(reason){setPendingCost(null);setError(reason instanceof Error?reason.message:"Не удалось выполнить повторный запрос");}finally{setBusy(false)}};
 const edit=async(e:FormEvent)=>{e.preventDefault();if(!value.trim())return;setBusy(true);setError("");try{const key=`web-edit:${crypto.randomUUID()}`;await runPaidAction(`/conversations/${conversationId}/messages/${message.id}/edit/`,{content:value.trim()},key,"Ответить заново на изменённый запрос",result=>{onConversation(result);setEditing(false)});}catch(reason){setError(reason instanceof Error?reason.message:"Не удалось изменить сообщение");}finally{setBusy(false)}};
 const regenerate=async()=>{if(busy)return;setBusy(true);setError("");try{const key=`web-regenerate:${crypto.randomUUID()}`;await runPaidAction(`/conversations/${conversationId}/messages/${message.id}/regenerate/`,{},key,"Создать другой вариант ответа",result=>onConversation(result));}catch(reason){setError(reason instanceof Error?reason.message:"Не удалось создать новый вариант");}finally{setBusy(false)}};
 const copy=async()=>{try{if(navigator.clipboard?.writeText)await navigator.clipboard.writeText(message.content);else{const area=document.createElement("textarea");area.value=message.content;area.style.position="fixed";area.style.opacity="0";document.body.appendChild(area);area.select();document.execCommand("copy");area.remove();}setCopied(true);window.setTimeout(()=>setCopied(false),1200);}catch{setError("Не удалось скопировать текст. Выделите его вручную.");}};
 const assistantBody=message.status==="streaming"?<div className="streamingText" aria-live="polite">{message.content||"Формируем ответ…"}</div>:<MarkdownMessage content={message.content||"Ответ не получен"}/>;
 const hasCost=message.generation?.cost_rub!==null&&message.generation?.cost_rub!==undefined;
 return <article id={`message-${message.id}`} className={`message ${message.role} ${message.status}`}>
  <div className="messageBody">{editing?<form className="messageEdit" onSubmit={edit}><textarea value={value} onChange={e=>setValue(e.target.value)} rows={5} autoFocus aria-label="Редактировать сообщение"/><div><button type="button" onClick={()=>{setEditing(false);setValue(message.content)}}>Отмена</button><button className="primarySmall" disabled={busy||!value.trim()}>{busy?"Проверяем…":"Сохранить и ответить заново"}</button></div></form>:message.role==="assistant"?assistantBody:<div className="userBubble">{message.content}</div>}</div>
  {message.status==="partial"&&<div className="messageState">Ответ остановлен. Уже полученный текст сохранён.</div>}{message.status==="failed"&&<div className="messageState error">Не удалось получить полный ответ. Проверьте уведомление выше и состояние баланса.</div>}{error&&<div className="inlineError" role="alert">{error}</div>}
  {!editing&&message.content&&<div className="messageActions"><button onClick={()=>void copy()} title="Копировать" aria-label={copied?"Скопировано":"Копировать"}><Icon name={copied?"check":"copy"}/></button>{message.role==="user"&&<button disabled={busy} onClick={()=>setEditing(true)} title="Изменить сообщение и получить новый ответ" aria-label="Изменить сообщение"><Icon name="pencil"/></button>}{message.role==="assistant"&&message.status!=="streaming"&&<button disabled={busy} onClick={()=>void regenerate()} title="Создать другой вариант ответа" aria-label="Другой вариант"><Icon name="retry"/></button>}{message.generation&&<button className="messageMetaButton" onClick={()=>setDetails(!details)} aria-expanded={details} title={systemTier?"Уровень System, токены и фактическая стоимость":"Модель, токены и фактическая стоимость"}>{message.generation.model}{hasCost?` · ${money(message.generation?.cost_rub??null)}`:""}</button>}</div>}
  {details&&message.generation&&<div className="messageDetails"><div><span>{systemTier?"Уровень":"Модель"}</span><b>{message.generation.model}</b></div>{!systemTier&&<div><span>Провайдер</span><b>{message.generation.provider||"—"}</b></div>}<div><span>Токены</span><b>{message.generation.input_tokens} → {message.generation.output_tokens}</b></div><div><span>Фактически списано</span><b>{money(message.generation.cost_rub)||"0,00 ₽"}</b></div>{message.generation.context?.routing?.explanation&&<p>{message.generation.context.routing.explanation}</p>}{sources.length>0&&<section><b>Источники · {sources.length}</b>{sources.slice(0,8).map(source=><div className="sourceRow" key={source.id}>{source.file_name}{source.source_location?.page?` · стр. ${source.source_location.page}`:""}</div>)}</section>}</div>}
  {pendingCost&&<div className="miniDialogBackdrop" role="presentation" onMouseDown={event=>{if(event.target===event.currentTarget&&!busy)setPendingCost(null)}}><div className="miniDialog costConfirmDialog" role="dialog" aria-modal="true" aria-labelledby={`cost-title-${message.id}`}><header><div><h2 id={`cost-title-${message.id}`}>{pendingCost.label}</h2><p>Это новый запрос к модели и он создаёт отдельный расход.</p></div><button type="button" className="iconButton" disabled={busy} onClick={()=>setPendingCost(null)} aria-label="Закрыть"><Icon name="x" size={16}/></button></header><div className="costConfirmAmount"><span>Максимум до запуска</span><strong>{money(pendingCost.maximum)}</strong></div><div className="costConfirmMeta"><Icon name="check" size={15}/><span>Сервис спишет только фактически подтверждённое использование. Если расход не подтверждён провайдером, резерв вернётся на баланс.</span></div><footer><button type="button" disabled={busy} onClick={()=>setPendingCost(null)}>Отмена</button><button type="button" className="dialogPrimary" disabled={busy} onClick={()=>void confirmPendingCost()}>{busy?"Запускаем…":"Продолжить"}</button></footer></div></div>}
 </article>;
}
