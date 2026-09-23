"use client";

import {useEffect,useMemo,useRef,useState} from "react";
import type {ChatMessage,Conversation} from "../../lib/types";
import {ConversationAssetsPanel} from "./ConversationAssetsPanel";
import {ErrorBoundary} from "./ErrorBoundary";
import {Icon} from "./Icons";
import {MessageCard} from "./MessageCard";

const isOptimistic=(message:ChatMessage)=>message.id.startsWith("local-user-")||message.id.startsWith("local-ai-");
const nearInTime=(a:ChatMessage,b:ChatMessage)=>Math.abs(new Date(a.created_at).getTime()-new Date(b.created_at).getTime())<=120000;

function displayMessages(messages:ChatMessage[]){
 const persisted=messages.filter(message=>!isOptimistic(message));
 return messages.filter(message=>{
  if(!isOptimistic(message))return true;
  return !persisted.some(server=>{
   if(server.role!==message.role||!nearInTime(server,message))return false;
   if(server.content===message.content)return true;
   // A final refresh can race the last streaming paint. Treat a non-empty
   // prefix as the same assistant answer, but never merge unrelated blank rows.
   if(message.role==="assistant"&&server.content&&message.content){
    return server.content.startsWith(message.content)||message.content.startsWith(server.content);
   }
   return false;
  });
 });
}

export function ChatThread({conversation,hasMore,loadingOlder,onLoadOlder,onConversation,onStarter}:{conversation:Conversation|null;hasMore:boolean;loadingOlder:boolean;onLoadOlder:()=>void;onConversation:(value:Conversation)=>void;onStarter:(value:string)=>void}){
 const ref=useRef<HTMLElement|null>(null);const[away,setAway]=useState(false);const previousCount=useRef(0);const pagingAnchor=useRef<{height:number;top:number}|null>(null);
 const messages=useMemo(()=>displayMessages(conversation?.messages??[]),[conversation?.messages]);
 const messageCount=messages.length;const lastMessage=messageCount?messages[messageCount-1]:null;const lastContentLength=lastMessage?.content.length??0;
 useEffect(()=>{const el=ref.current;if(!el)return;if(pagingAnchor.current&&messageCount>previousCount.current){const anchor=pagingAnchor.current;pagingAnchor.current=null;requestAnimationFrame(()=>{el.scrollTop=anchor.top+(el.scrollHeight-anchor.height)});}else{const nearBottom=el.scrollHeight-el.scrollTop-el.clientHeight<160;if(messageCount>=previousCount.current&&nearBottom)requestAnimationFrame(()=>{el.scrollTop=el.scrollHeight});}previousCount.current=messageCount;},[messageCount,lastContentLength]);
 useEffect(()=>{const el=ref.current;if(el)requestAnimationFrame(()=>{el.scrollTop=el.scrollHeight});previousCount.current=messages.length;pagingAnchor.current=null;},[conversation?.id]);
 const loadOlder=()=>{const el=ref.current;if(el)pagingAnchor.current={height:el.scrollHeight,top:el.scrollTop};onLoadOlder();};
 if(!conversation||messages.length===0)return <section className="emptyChat"><div className="emptyMark"><Icon name="spark" size={23}/></div><h1>Чем помочь сегодня?</h1><p className="emptySubhead">Опишите задачу своими словами. Сервис сам подберёт подходящую модель и постарается не расходовать лишние токены.</p><div className="starterGrid"><button onClick={()=>onStarter("Разбери документ и выдели главное. Сначала дай краткое резюме, затем ключевые выводы и риски.")}><span className="starterIcon"><Icon name="folder" size={16}/></span><span className="starterCopy"><b>Разобрать документ</b><small>Резюме, выводы и важные детали</small></span></button><button onClick={()=>onStarter("Помоги написать сильный текст. Сначала уточни цель и аудиторию, если это необходимо.")}><span className="starterIcon"><Icon name="pencil" size={16}/></span><span className="starterCopy"><b>Написать текст</b><small>Структура, редактура и финальная версия</small></span></button><button onClick={()=>onStarter("Помоги написать и проверить код. Найди ошибки, предложи решение и объясни важные изменения.")}><span className="starterIcon"><Icon name="zap" size={16}/></span><span className="starterCopy"><b>Помочь с кодом</b><small>Разработка, аудит и исправление ошибок</small></span></button><button onClick={()=>onStarter("Найди актуальную информацию в интернете, сравни источники и укажи ссылки на ключевые факты.")}><span className="starterIcon"><Icon name="search" size={16}/></span><span className="starterCopy"><b>Найти информацию</b><small>Актуальные данные с источниками</small></span></button></div></section>;
 return <section ref={ref} className="threadViewport" onScroll={e=>{const el=e.currentTarget;setAway(el.scrollHeight-el.scrollTop-el.clientHeight>260)}}><div className="threadInner"><ConversationAssetsPanel conversationId={conversation.id}/>{hasMore&&<button className="olderButton" disabled={loadingOlder} onClick={loadOlder}>{loadingOlder?"Загружаем…":"Показать более ранние сообщения"}</button>}{messages.map(message=><ErrorBoundary key={message.id}><MessageCard conversationId={conversation.id} message={message} onConversation={onConversation}/></ErrorBoundary>)}</div>{away&&<button className="jumpBottom" onClick={()=>{const el=ref.current;if(el)el.scrollTo({top:el.scrollHeight,behavior:"smooth"})}}><Icon name="arrowDown" size={16}/>К последнему</button>}</section>;
}
