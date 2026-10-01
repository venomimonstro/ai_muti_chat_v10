"use client";

import {useCallback,useEffect,useMemo,useRef,useState} from "react";
import {api} from "../../lib/api";
import {mergeChatMessages} from "../../lib/chat-state";
import type {ChatMessage,Conversation} from "../../lib/types";
import {ConversationAssetsPanel} from "./ConversationAssetsPanel";
import {ErrorBoundary} from "./ErrorBoundary";
import {Icon} from "./Icons";
import {MessageCard} from "./MessageCard";

const INITIAL_RENDER_LIMIT=60;
const RENDER_STEP=40;
const RECOVERY_POLL_MS=2000;
const SERVER_GENERATION_EVENT="aiws:server-generation-active";
const isOptimistic=(message:ChatMessage)=>message.id.startsWith("local-user-")||message.id.startsWith("local-ai-");
const isOptimisticUser=(message:ChatMessage)=>message.id.startsWith("local-user-");
const isOptimisticAssistant=(message:ChatMessage)=>message.id.startsWith("local-ai-");

type WorkspacePage={conversation:Conversation;has_more:boolean;next_before:string|null};

function displayMessages(messages:ChatMessage[]){
 const seenIds=new Set<string>();
 const unique=messages.filter(message=>{if(seenIds.has(message.id))return false;seenIds.add(message.id);return true;});

 const preacceptedGhosts=new Set<string>();
 // Workspace adds an optimistic user+assistant pair before cost preview / backend
 // prepare. Until the first real delta arrives, that pair is not proof that the
 // server accepted the turn. Hide the pair while the assistant is still empty so
 // preview/validation/network rejection cannot leave a fake sent message and an
 // eternal blank assistant in the thread. Routing/progress text remains visible in
 // the composer status area; on the first delta both bubbles appear immediately.
 for(let index=0;index<unique.length-1;index+=1){
  const user=unique[index];const assistant=unique[index+1];
  if(isOptimisticUser(user)&&isOptimisticAssistant(assistant)&&assistant.status==="streaming"&&!assistant.content&&!assistant.pending_generation_id){
   preacceptedGhosts.add(user.id);preacceptedGhosts.add(assistant.id);
  }
 }
 return mergeChatMessages(unique,[]).filter(message=>!preacceptedGhosts.has(message.id));
}

function serverGenerationInFlight(conversation:Conversation|null){
 if(!conversation)return null;
 return [...conversation.messages].reverse().find(message=>{
  if(isOptimistic(message)||message.role!=="assistant"||!message.generation)return false;
  const generationState=String(message.generation.state??"");
  return message.status==="streaming"||generationState==="queued"||generationState==="running";
 })??null;
}

function hasServerGenerationInFlight(conversation:Conversation|null){
 return serverGenerationInFlight(conversation)!==null;
}

function broadcastServerGeneration(conversationId:string,active:boolean){
 if(typeof window==="undefined")return;
 window.dispatchEvent(new CustomEvent(SERVER_GENERATION_EVENT,{detail:{conversationId,active}}));
}

export function ChatThread({conversation,hasMore,loadingOlder,onLoadOlder,onConversation,onStarter,busy=false}:{busy?:boolean;conversation:Conversation|null;hasMore:boolean;loadingOlder:boolean;onLoadOlder:()=>void;onConversation:(value:Conversation)=>void;onStarter:(value:string)=>void}){
 const ref=useRef<HTMLElement|null>(null);const[away,setAway]=useState(false);const[renderLimit,setRenderLimit]=useState(INITIAL_RENDER_LIMIT);const[cancellingRecovered,setCancellingRecovered]=useState(false);const[recoveryError,setRecoveryError]=useState("");const following=useRef(true);const previousCount=useRef(0);const pagingAnchor=useRef<{height:number;top:number}|null>(null);const conversationHandler=useRef(onConversation);
 conversationHandler.current=onConversation;
 const stableOnConversation=useCallback((value:Conversation)=>conversationHandler.current(value),[]);
 const messages=useMemo(()=>displayMessages(conversation?.messages??[]),[conversation?.messages]);
 const visibleMessages=useMemo(()=>messages.slice(Math.max(0,messages.length-renderLimit)),[messages,renderLimit]);
 const hiddenLoaded=Math.max(0,messages.length-visibleMessages.length);
 const messageCount=messages.length;const lastMessage=messageCount?messages[messageCount-1]:null;const lastContentLength=lastMessage?.content.length??0;
 const recoveredActiveMessage=serverGenerationInFlight(conversation);const recoveryNeeded=recoveredActiveMessage!==null;
 useEffect(()=>{const el=ref.current;if(!el)return;if(pagingAnchor.current&&messageCount>previousCount.current){const anchor=pagingAnchor.current;pagingAnchor.current=null;requestAnimationFrame(()=>{el.scrollTop=anchor.top+(el.scrollHeight-anchor.height)});}else{if(messageCount>=previousCount.current&&following.current)requestAnimationFrame(()=>{el.scrollTop=el.scrollHeight});}previousCount.current=messageCount;},[messageCount,lastContentLength]);
 useEffect(()=>{following.current=true;setAway(false);setRenderLimit(INITIAL_RENDER_LIMIT);setCancellingRecovered(false);setRecoveryError("");const el=ref.current;if(el)requestAnimationFrame(()=>{el.scrollTop=el.scrollHeight});previousCount.current=messages.length;pagingAnchor.current=null;},[conversation?.id]);
 useEffect(()=>{const id=conversation?.id;if(!id)return;const timer=window.setTimeout(()=>broadcastServerGeneration(id,recoveryNeeded),0);return()=>{window.clearTimeout(timer);broadcastServerGeneration(id,false)}},[conversation?.id,recoveryNeeded]);
 // A browser reload detaches the SSE transport while the backend deliberately keeps
 // the same Generation running. Poll only when the freshly loaded server snapshot
 // contains an actual persisted in-flight assistant. Ordinary live SSE uses local
 // optimistic messages and therefore does not create a competing poll loop.
 useEffect(()=>{if(!conversation?.id||!recoveryNeeded)return;let active=true;let timer:number|null=null;const refresh=async()=>{if(!active)return;if(typeof document!=="undefined"&&document.visibilityState==="hidden"){timer=window.setTimeout(()=>void refresh(),RECOVERY_POLL_MS*2);return}try{const page=await api<WorkspacePage>(`/conversation-workspace/${conversation.id}/?limit=60`);if(!active)return;stableOnConversation(page.conversation);if(hasServerGenerationInFlight(page.conversation)){timer=window.setTimeout(()=>void refresh(),RECOVERY_POLL_MS)}else{setCancellingRecovered(false)}}catch{if(active)timer=window.setTimeout(()=>void refresh(),RECOVERY_POLL_MS*2)}};timer=window.setTimeout(()=>void refresh(),500);return()=>{active=false;if(timer!==null)window.clearTimeout(timer)}},[conversation?.id,recoveryNeeded,stableOnConversation]);
 const cancelRecovered=async()=>{const generationId=recoveredActiveMessage?.generation?.id;if(!conversation?.id||!generationId||cancellingRecovered)return;setCancellingRecovered(true);setRecoveryError("");try{await api(`/conversations/${conversation.id}/messages/cancel/`,{method:"POST",body:JSON.stringify({generation_id:generationId})});const page=await api<WorkspacePage>(`/conversation-workspace/${conversation.id}/?limit=60`);stableOnConversation(page.conversation);if(!hasServerGenerationInFlight(page.conversation))setCancellingRecovered(false)}catch(reason){setCancellingRecovered(false);setRecoveryError(reason instanceof Error?reason.message:"Не удалось остановить ответ. Попробуйте ещё раз.")}};
 useEffect(()=>{const stop=(event:Event)=>{if((event as CustomEvent<{conversationId:string}>).detail?.conversationId===conversation?.id)void cancelRecovered()};window.addEventListener("aiws:stop-generation",stop);return()=>window.removeEventListener("aiws:stop-generation",stop)},[conversation?.id,recoveredActiveMessage?.generation?.id,cancellingRecovered]);
 const loadOlder=()=>{const el=ref.current;if(el)pagingAnchor.current={height:el.scrollHeight,top:el.scrollTop};onLoadOlder();};
 if(!conversation||messages.length===0)return <section className="emptyChat"><div className="emptyMark"><Icon name="spark" size={23}/></div><h1>Чем помочь сегодня?</h1><p className="emptySubhead">Задайте вопрос или опишите задачу.</p><div className="starterGrid"><button onClick={()=>onStarter("Разбери документ и выдели главное. Сначала дай краткое резюме, затем ключевые выводы и риски.")}><span className="starterIcon"><Icon name="folder" size={16}/></span><span className="starterCopy"><b>Разобрать документ</b><small>Резюме, выводы и важные детали</small></span></button><button onClick={()=>onStarter("Помоги написать сильный текст. Сначала уточни цель и аудиторию, если это необходимо.")}><span className="starterIcon"><Icon name="pencil" size={16}/></span><span className="starterCopy"><b>Написать текст</b><small>Структура, редактура и финальная версия</small></span></button><button onClick={()=>onStarter("Помоги написать и проверить код. Найди ошибки, предложи решение и объясни важные изменения.")}><span className="starterIcon"><Icon name="zap" size={16}/></span><span className="starterCopy"><b>Помочь с кодом</b><small>Разработка, аудит и исправление ошибок</small></span></button><button onClick={()=>onStarter("Найди актуальную информацию в интернете, сравни источники и укажи ссылки на ключевые факты.")}><span className="starterIcon"><Icon name="search" size={16}/></span><span className="starterCopy"><b>Найти информацию</b><small>Актуальные данные с источниками</small></span></button></div></section>;
 return <section ref={ref} className="threadViewport" onScroll={e=>{const el=e.currentTarget;const distance=el.scrollHeight-el.scrollTop-el.clientHeight;following.current=distance<100;setAway(distance>180)}}><div className="threadInner"><ConversationAssetsPanel conversationId={conversation.id}/>{recoveryError&&<div className="inlineError" role="alert">{recoveryError}</div>}{recoveryNeeded&&<button className="olderButton" disabled={cancellingRecovered} onClick={()=>void cancelRecovered()}>{cancellingRecovered?"Останавливаю ответ…":"Ответ продолжается на сервере · Остановить"}</button>}{hiddenLoaded>0&&<button className="olderButton" onClick={()=>setRenderLimit(limit=>limit+RENDER_STEP)}>Показать ещё {Math.min(RENDER_STEP,hiddenLoaded)} загруженных сообщений</button>}{hiddenLoaded===0&&hasMore&&<button className="olderButton" disabled={loadingOlder} onClick={loadOlder}>{loadingOlder?"Загружаем…":"Показать более ранние сообщения"}</button>}{visibleMessages.map(message=><ErrorBoundary key={message.id}><MessageCard conversationId={conversation.id} message={message} actionsDisabled={busy||recoveryNeeded} onConversation={stableOnConversation}/></ErrorBoundary>)}</div>{away&&<button className="jumpBottom" aria-label="Перейти к последнему сообщению" onClick={()=>{const el=ref.current;if(el){following.current=true;setAway(false);el.scrollTo({top:el.scrollHeight,behavior:window.matchMedia("(prefers-reduced-motion: reduce)").matches?"instant":"smooth"})}}}><Icon name="arrowDown" size={16}/>К последнему</button>}</section>;
}
