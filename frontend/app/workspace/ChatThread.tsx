"use client";

import {useEffect,useRef,useState} from "react";
import type {Conversation} from "../../lib/types";
import {ErrorBoundary} from "./ErrorBoundary";
import {Icon} from "./Icons";
import {MessageCard} from "./MessageCard";

export function ChatThread({conversation,hasMore,loadingOlder,onLoadOlder,onConversation,onStarter}:{conversation:Conversation|null;hasMore:boolean;loadingOlder:boolean;onLoadOlder:()=>void;onConversation:(value:Conversation)=>void;onStarter:(value:string)=>void}){
 const ref=useRef<HTMLElement|null>(null);const[away,setAway]=useState(false);const previousCount=useRef(0);
 useEffect(()=>{const el=ref.current;if(!el)return;const nearBottom=el.scrollHeight-el.scrollTop-el.clientHeight<160;if(conversation&&conversation.messages.length>previousCount.current&&nearBottom)requestAnimationFrame(()=>{el.scrollTop=el.scrollHeight});previousCount.current=conversation?.messages.length??0;},[conversation?.messages.length,conversation]);
 useEffect(()=>{const el=ref.current;if(el)requestAnimationFrame(()=>{el.scrollTop=el.scrollHeight});},[conversation?.id]);
 if(!conversation||conversation.messages.length===0)return <section className="emptyChat"><div className="emptyMark"><Icon name="spark" size={26}/></div><h1>Чем могу помочь?</h1><div className="starterGrid"><button onClick={()=>onStarter("Разбери документ и выдели главное")}>Разобрать документ</button><button onClick={()=>onStarter("Помоги написать сильный текст")}>Написать текст</button><button onClick={()=>onStarter("Помоги написать и проверить код")}>Помочь с кодом</button><button onClick={()=>onStarter("Найди актуальную информацию и укажи источники")}>Найти информацию</button></div></section>;
 return <section ref={ref} className="threadViewport" onScroll={e=>{const el=e.currentTarget;setAway(el.scrollHeight-el.scrollTop-el.clientHeight>260)}}><div className="threadInner">{hasMore&&<button className="olderButton" disabled={loadingOlder} onClick={onLoadOlder}>{loadingOlder?"Загружаем…":"Показать более ранние сообщения"}</button>}{conversation.messages.map(message=><ErrorBoundary key={message.id}><MessageCard conversationId={conversation.id} message={message} onConversation={onConversation}/></ErrorBoundary>)}</div>{away&&<button className="jumpBottom" onClick={()=>{const el=ref.current;if(el)el.scrollTo({top:el.scrollHeight,behavior:"smooth"})}}><Icon name="arrowDown" size={16}/>К последнему</button>}</section>;
}
